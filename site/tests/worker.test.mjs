import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import worker from '../dist/server/index.js';

const origin = 'https://biliscribe.example';
const env = { BILI_COOKIE_KEY: randomBytes(32).toString('base64url') };
const request = (path, options = {}) => new Request(`${origin}${path}`, options);
const responseJson = data => new Response(JSON.stringify(data),
  { headers: { 'content-type': 'application/json' } });

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
    if (value.includes('/x/player/v2?')) return responseJson({ code: 0, data: {
      subtitle: { subtitles: [{ lan: 'ai-zh', lan_doc: '中文',
        subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/123456abcdef' },
      { lan: 'ai-zh', lan_doc: '中文',
        subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/123456abcdef' },
      { lan: 'ai-en', lan_doc: 'English',
        subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/abcdef' }] },
    } });
    if (value.includes('aisubtitle.hdslb.com')) return responseJson({
      body: [{ from: 1, to: 3, content: '你好，世界' }],
    });
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
  } finally {
    globalThis.fetch = originalFetch;
  }
});
