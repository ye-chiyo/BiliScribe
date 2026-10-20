import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import worker from '../dist/server/index.js';

const origin = 'https://biliscribe.example';
const env = { BILI_COOKIE_KEY: randomBytes(32).toString('base64url') };
const request = (path, options = {}) => new Request(`${origin}${path}`, options);
const responseJson = data => new Response(JSON.stringify(data),
  { headers: { 'content-type': 'application/json' } });
const varint = value => {
  let n = BigInt(value), bytes = [];
  while (n > 0x7fn) { bytes.push(Number(n & 0x7fn) | 0x80); n >>= 7n; }
  bytes.push(Number(n));
  return Uint8Array.from(bytes);
};
const pbBytes = (number, value) => {
  const data = new TextEncoder().encode(value);
  return Uint8Array.from([...varint((number << 3) | 2), ...varint(data.length), ...data]);
};
const pbVarint = (number, value) => Uint8Array.from([
  ...varint(number << 3), ...varint(value),
]);
const pbRaw = (number, data) => Uint8Array.from([
  ...varint((number << 3) | 2), ...varint(data.length), ...data,
]);
const concat = (...parts) => Uint8Array.from(parts.flatMap(part => [...part]));
const encodedV2Url = (path, keyIndex = 0) => {
  const keys = [
    ['nP](wOFRvU.+<fjS{jn-!$D|Dz&",zT`', '=CFxYRn{.y|uVyO$uh&sikph?N.ilF/`bilibili'],
    ['Bn"q~|albg@]Go~ACgyDvKnd+)_D}^&J?', "Cu~L!xs~f^&r@'vh=q]q{eeng*sEg^kp#Jbilibili"],
  ];
  const [prefix, key] = keys[keyIndex];
  const plain = new TextEncoder().encode(prefix + path);
  let cipher = '';
  for (let i = 0; i < plain.length; i++)
    cipher += String.fromCharCode(plain[i] ^ key.charCodeAt(i % key.length));
  return `//subtitle.bilibili.com/${encodeURIComponent(cipher)}?auth_key=test-signature`;
};
const webSubtitleResponse = () => {
  const track = concat(pbVarint(1, '2005328430358528768'), pbBytes(2, '2005328430358528768'),
    pbBytes(3, 'ai-zh'), pbBytes(4, '中文'),
    pbBytes(5, encodedV2Url('/bfs/ai_subtitle/prod/123456fixture.json', 1)));
  const data = pbRaw(3, track);
  return new Response(pbRaw(1, data), {
    headers: { 'content-type': 'application/octet-stream' },
  });
};

test('private cloud page and unauthenticated extract', async () => {
  const page = await worker.fetch(request('/'), env);
  assert.equal(page.status, 200);
  const html = await page.text();
  assert.match(html, /B站视频字幕云端提取/);
  assert.match(html, /Markdown 笔记/);
  const result = await worker.fetch(request('/api/extract', {
    method: 'POST', headers: { origin, 'content-type': 'application/json' },
    body: JSON.stringify({ urls: 'https://www.bilibili.com/video/BV11gYd6UEPv/' }),
  }), env);
  assert.equal(result.status, 401);
});

test('QR login and Chinese Markdown extraction', async () => {
  const originalFetch = globalThis.fetch;
  let legacySubtitleCalls = 0;
  let subtitleBodyUrl = '';
  globalThis.fetch = async url => {
    const value = String(url);
    if (value.includes('/qrcode/generate')) return responseJson({ code: 0,
      data: { qrcode_key: 'test-key', url: 'https://passport.bilibili.com/h5-app/passport/login/scan?key=test-key' } });
    if (value.includes('/qrcode/poll')) return new Response(JSON.stringify({ code: 0, data: { code: 0 } }), {
      headers: [['set-cookie', 'SESSDATA=test-session; Path=/; HttpOnly'],
        ['set-cookie', 'bili_jct=test-csrf; Path=/']],
    });
    if (value.includes('/x/web-interface/view?')) return responseJson({ code: 0, data: {
      bvid: 'BV11gYd6UEPv', aid: 123, title: '测试视频', owner: { name: 'UP' },
      pages: [{ page: 1, cid: 456, part: '正片' }], duration: 60,
    } });
    if (value.includes('/x/v2/subtitle/web/view?')) return webSubtitleResponse();
    if (value.includes('/x/player/v2?')) {
      legacySubtitleCalls += 1;
      return responseJson({ code: 0, data: {
      subtitle: { subtitles: [{ lan: 'ai-zh', lan_doc: '中文',
        subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/123456abcdef' },
      { lan: 'ai-zh', lan_doc: '中文',
        subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/123456abcdef' },
      { lan: 'ai-en', lan_doc: 'English',
        subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/abcdef' }] },
      } });
    }
    if (value.includes('aisubtitle.hdslb.com')) {
      subtitleBodyUrl = value;
      return responseJson({
      body: [{ from: 1, to: 3, content: '你好，世界' }],
      });
    }
    if (value.includes('/video/BV11gYd6UEPv')) return new Response('<html></html>');
    throw new Error(`Unexpected fetch: ${value}`);
  };
  try {
    const started = await worker.fetch(request('/api/login/start', {
      method: 'POST', headers: { origin },
    }), env);
    assert.equal(started.status, 200);
    assert.match(started.headers.get('set-cookie'), /bs_qr=test-key/);
    const polled = await worker.fetch(request('/api/login/poll', {
      headers: { cookie: 'bs_qr=test-key' },
    }), env);
    assert.equal((await polled.json()).state, 'success');
    const session = polled.headers.getSetCookie().find(value => value.startsWith('bs_auth='))
      .split(';', 1)[0];
    assert.ok(session);

    const extracted = await worker.fetch(request('/api/extract', {
      method: 'POST', headers: { origin, cookie: session, 'content-type': 'application/json' },
      body: JSON.stringify({ urls: 'https://www.bilibili.com/video/BV11gYd6UEPv/', langs: [' zh '] }),
    }), env);
    assert.equal(extracted.status, 200);
    const result = (await extracted.json()).results[0];
    assert.equal(result.status, 'ok');
    assert.equal(result.files.length, 1);
    assert.match(result.files[0].name, /\.ai-zh\.md$/);
    assert.match(result.files[0].content, /你好，世界/);
    assert.equal(legacySubtitleCalls, 0);
    assert.match(subtitleBodyUrl, /^https:\/\/aisubtitle\.hdslb\.com\/bfs\//);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('empty modern metadata explains foreign legacy tracks', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async url => {
    const value = String(url);
    if (value.includes('/qrcode/generate')) return responseJson({ code: 0,
      data: { qrcode_key: 'empty-modern-key', url: 'https://passport.bilibili.com/scan?key=empty-modern-key' } });
    if (value.includes('/qrcode/poll')) return new Response(JSON.stringify({ code: 0, data: { code: 0 } }), {
      headers: [['set-cookie', 'SESSDATA=test-session-empty; Path=/; HttpOnly'],
        ['set-cookie', 'bili_jct=test-csrf-empty; Path=/']],
    });
    if (value.includes('/x/web-interface/view?')) return responseJson({ code: 0, data: {
      bvid: 'BV1NG9xBUEju', aid: 116488438549326, title: 'RAG 测试视频', owner: { name: 'UP' },
      pages: [{ page: 1, cid: 37950522219, part: '正片' }], duration: 60,
    } });
    if (value.includes('/x/v2/subtitle/web/view?'))
      return new Response(new Uint8Array([0x0a, 0x00]), {
        headers: { 'content-type': 'application/octet-stream' },
      });
    if (value.includes('/x/player/v2?')) return responseJson({ code: 0, data: {
      subtitle: { subtitles: [{ lan: 'ai-zh', lan_doc: '中文',
        subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337255728481126470056562e970799dc7a23cadb28ed27227cc3ddb' }] },
    } });
    if (value.includes('/video/BV1NG9xBUEju')) return new Response('<html></html>');
    throw new Error(`Unexpected fetch: ${value}`);
  };
  try {
    const started = await worker.fetch(request('/api/login/start', {
      method: 'POST', headers: { origin },
    }), env);
    assert.equal(started.status, 200);
    const polled = await worker.fetch(request('/api/login/poll', {
      headers: { cookie: 'bs_qr=empty-modern-key' },
    }), env);
    const session = polled.headers.getSetCookie().find(value => value.startsWith('bs_auth='))
      .split(';', 1)[0];
    const extracted = await worker.fetch(request('/api/extract', {
      method: 'POST', headers: { origin, cookie: session, 'content-type': 'application/json' },
      body: JSON.stringify({ urls: 'https://www.bilibili.com/video/BV1NG9xBUEju/' }),
    }), env);
    assert.equal(extracted.status, 200);
    const result = (await extracted.json()).results[0];
    assert.equal(result.status, 'error');
    assert.match(result.message, /未找到属于当前视频的可用字幕轨/);
    assert.match(result.message, /播放器确实显示/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
