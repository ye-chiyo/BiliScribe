import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import worker from '../dist/server/index.js';

const origin = 'https://biliscribe.example';
let scenarioNumber = 0;
const request = (path, options = {}) => new Request(`${origin}${path}`, options);
const responseJson = data => new Response(JSON.stringify(data),
  { headers: { 'content-type': 'application/json' } });

function encodedTrackUrl(resource = 'e8edb3c0dc6f558b5b433ba24ec806be', keyIndex = 1) {
  // Fixed public player samples, independent of the product's decoder constants.
  const pairs = [
    ['nP](wOFRvU.+<fjS{jn-!$D|Dz&",zT`', '=CFxYRn{.y|uVyO$uh&sikph?N.ilF/`bilibili'],
    ['Bn"q~|albg@]Go~ACgyDvKnd+)_D}^&J?', "Cu~L!xs~f^&r@'vh=q]q{eeng*sEg^kp#Jbilibili"],
  ];
  const [prefix, key] = pairs[keyIndex];
  const plain = new TextEncoder().encode(prefix + '/bfs/ai_subtitle/prod/' + resource);
  const encoded = [...plain].map((byte, index) => String.fromCharCode(byte ^ key.charCodeAt(index % key.length))).join('');
  return `//subtitle.bilibili.com/${encodeURIComponent(encoded)}?auth_key=mock-signature`;
}

function protobufTracks(tracks) {
  const varint = value => {
    const result = [];
    while (value > 127) { result.push((value & 127) | 128); value >>>= 7; }
    return Uint8Array.from([...result, value]);
  };
  const field = (number, value) => {
    const data = typeof value === 'string' ? new TextEncoder().encode(value) : value;
    return Uint8Array.from([...varint(number * 8 + 2), ...varint(data.length), ...data]);
  };
  const rows = tracks.flatMap(track => [...field(3, Uint8Array.from([
    ...field(3, track.lan), ...field(5, track.subtitle_url),
  ]))]);
  return field(1, Uint8Array.from(rows));
}

async function extractionScenario({ legacy = [], modern = new Uint8Array([10, 0]), modernStatus = 200,
                                    legacyCode = 0, modernTracks, body = [], fmts = ['md'] } = {}) {
  const bvid = `BVTEST${String(++scenarioNumber).padStart(6, '0')}`;
  const external = globalThis.fetch;
  const timer = globalThis.setTimeout;
  const requests = [];
  globalThis.setTimeout = (callback, _delay, ...args) => timer(callback, 0, ...args);
  globalThis.fetch = async url => {
    const parsed = new URL(String(url));
    requests.push(parsed);
    if (parsed.pathname.endsWith('/qrcode/generate')) return responseJson({ code: 0,
      data: { qrcode_key: 'mock-ownership-qr', url: 'https://passport.bilibili.com/mock' } });
    if (parsed.pathname.endsWith('/qrcode/poll')) return new Response(JSON.stringify({ code: 0, data: { code: 0 } }), {
      headers: [['set-cookie', 'SESSDATA=mock-ownership-session; Path=/; HttpOnly']] });
    if (parsed.pathname === '/x/web-interface/view') return responseJson({ code: 0, data: {
      bvid, aid: 117505221724658, title: 'AI 观众提问', owner: { name: 'UP', mid: 123 },
      pages: [{ page: 1, cid: 43061546452, part: '正片' }], duration: 824,
    } });
    if (parsed.pathname === '/x/v2/subtitle/web/view') {
      assert.equal(parsed.searchParams.get('pid'), '117505221724658');
      assert.equal(parsed.searchParams.get('oid'), '43061546452');
      return new Response(modernTracks ? protobufTracks(modernTracks) : modern, { status: modernStatus });
    }
    if (parsed.pathname === '/x/player/v2') return responseJson({ code: legacyCode,
      message: 'mock API failure', data: { subtitle: { subtitles: legacy } } });
    if (parsed.hostname === 'www.bilibili.com') return new Response('<html></html>');
    if (parsed.pathname === '/x/frontend/finger/spi') return responseJson({ code: 0,
      data: { b_3: 'mock-device3', b_4: 'mock-device4' } });
    if (parsed.hostname === 'aisubtitle.hdslb.com') return responseJson({ body });
    throw new Error(`Unexpected external request: ${parsed.hostname}${parsed.pathname}`);
  };
  try {
    const env = { BILI_COOKIE_KEY: randomBytes(32).toString('base64url') };
    await worker.fetch(request('/api/login/start', { method: 'POST', headers: { origin } }), env);
    const polled = await worker.fetch(request('/api/login/poll', {
      headers: { cookie: 'bs_qr=mock-ownership-qr' },
    }), env);
    const session = polled.headers.getSetCookie().find(value => value.startsWith('bs_auth='))
      .split(';', 1)[0];
    const response = await worker.fetch(request('/api/extract', {
      method: 'POST', headers: { origin, cookie: session, 'content-type': 'application/json' },
      body: JSON.stringify({ urls: `https://www.bilibili.com/video/${bvid}/`, fmts }),
    }), env);
    assert.equal(response.status, 200);
    return { result: (await response.json()).results[0], requests };
  } finally {
    globalThis.fetch = external;
    globalThis.setTimeout = timer;
  }
}

test('legacy opaque subtitle reports unverified instead of foreign ownership', async () => {
  const { result, requests } = await extractionScenario({ legacy: [{ lan: 'ai-zh',
    subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/e8edb3c0dc6f558b5b433ba24ec806be' }] });
  assert.equal(result.status, 'error');
  assert.equal(result.reason, 'ownership_unverified');
  assert.match(result.message, /无法验证/);
  assert.doesNotMatch(result.message, /其他视频/);
  assert.deepEqual(result.files, []);
  assert.ok(requests.every(url => url.hostname !== 'aisubtitle.hdslb.com'));
});

for (const [settings, reason] of [
  [{ modern: new Uint8Array([10, 128]) }, 'subtitle_parse_error'],
  [{ modernStatus: 503 }, 'network_error'],
  [{ modernStatus: 401 }, 'auth_required'],
  [{ modernStatus: 429 }, 'rate_limited'],
  [{ legacyCode: -101 }, 'auth_required'],
  [{ legacyCode: -412 }, 'rate_limited'],
]) {
  test(`failed metadata remains ${reason} instead of absent subtitles`, async () => {
    const { result, requests } = await extractionScenario(settings);
    assert.equal(result.status, 'error');
    assert.equal(result.reason, reason);
    assert.deepEqual(result.files, []);
    assert.ok(requests.every(url => url.hostname !== 'aisubtitle.hdslb.com'));
  });
}

test('empty metadata is distinguishable from unverified tracks', async () => {
  const { result } = await extractionScenario();
  assert.equal(result.status, 'no_subtitle');
  assert.equal(result.reason, 'subtitle_absent');
  assert.deepEqual(result.files, []);
});

for (const [lan, reason] of [['ai-en', 'language_unavailable'], ['ai-zh', 'subtitle_body_empty']]) {
  test(`${reason} is observable through the extraction response`, async () => {
    const { result } = await extractionScenario({ modernTracks: [{ lan,
      subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11750522172465843061546452fixture' }] });
    assert.equal(result.status, 'no_subtitle');
    assert.equal(result.reason, reason);
    assert.deepEqual(result.files, []);
  });
}

test('modern explicit ownership conflict persists after empty fallback', async () => {
  const { result, requests } = await extractionScenario({ modernTracks: [{ lan: 'ai-zh',
    subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337329541521726472939880ffffffffffffffffffffffffffffffff' }] });
  assert.equal(result.status, 'error');
  assert.equal(result.reason, 'ownership_conflict');
  assert.deepEqual(result.files, []);
  assert.ok(requests.every(url => url.hostname !== 'aisubtitle.hdslb.com'));
});

for (const resource of ['e8edb3c0dc6f558b5b433ba24ec806be', '01234567890123456789012345678901'])
for (const keyIndex of [0, 1]) test(`modern opaque ${resource.slice(0, 4)} exports with key ${keyIndex}`, async () => {
  const { result, requests } = await extractionScenario({
    modernTracks: [{ lan: 'ai-zh', subtitle_url: encodedTrackUrl(resource, keyIndex) }],
    body: [{ from: 1.25, to: 2.5, content: '固定字幕样本' }], fmts: ['txt'],
  });
  assert.equal(result.status, 'ok');
  assert.equal(result.tracks, 1);
  assert.equal(result.files.length, 1);
  assert.equal(result.files[0].content, '固定字幕样本');
  assert.ok(requests.every(url => url.pathname !== '/x/player/v2'));
  assert.ok(requests.some(url => url.pathname === '/bfs/ai_subtitle/prod/' + resource),
    requests.filter(url => url.hostname === 'aisubtitle.hdslb.com').map(url => url.pathname).join(','));
});

for (const source of ['modern', 'legacy']) test(`${source} mixed list exports only matching resource`, async () => {
  const tracks = [
    { lan: 'ai-zh', subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11750522172465843061546452fixture' },
    { lan: 'ai-zh', subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337329541521726472939880ffffffffffffffffffffffffffffffff' },
  ];
  const { result, requests } = await extractionScenario({
    ...(source === 'modern' ? { modernTracks: tracks } : { legacy: tracks }),
    body: [{ from: 1, to: 2, content: '仅导出匹配轨道' }], fmts: ['txt'],
  });
  assert.equal(result.status, 'ok');
  assert.equal(result.tracks, 1);
  assert.equal(result.files[0].content, '仅导出匹配轨道');
  assert.ok(requests.every(url => !url.pathname.includes('11337329541521726472939880')));
});

for (const url of ['//subtitle.bilibili.com/%01opaque',
  'https://untrusted.example/bfs/ai_subtitle/prod/e8edb3c0dc6f558b5b433ba24ec806be',
  'https://aisubtitle.hdslb.com/unknown/prod/e8edb3c0dc6f558b5b433ba24ec806be']) {
  test(`unsupported modern address ${url.split('/')[2]} is not authorized`, async () => {
    const { result, requests } = await extractionScenario({ modernTracks: [{ lan: 'ai-zh', subtitle_url: url }] });
    assert.equal(result.status, 'error');
    assert.equal(result.reason, 'ownership_unverified');
    assert.deepEqual(result.files, []);
    assert.ok(requests.every(url => url.hostname !== 'aisubtitle.hdslb.com'));
  });
}

test('legacy opaque track cannot claim modern provenance in its own fields', async () => {
  const { result, requests } = await extractionScenario({ legacy: [{ lan: 'ai-zh',
    subtitle_url: encodedTrackUrl(), source: 'modern', _owner_key: 'claimed-owner',
    policy_version: 1, author_mid: 123 }] });
  assert.equal(result.status, 'error');
  assert.equal(result.reason, 'ownership_unverified');
  assert.ok(requests.every(url => url.hostname !== 'aisubtitle.hdslb.com'));
});

test('legacy author conflict cannot be overridden by matching resource prefix', async () => {
  const { result, requests } = await extractionScenario({ legacy: [{ lan: 'ai-zh', author_mid: 999,
    subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11750522172465843061546452fixture' }] });
  assert.equal(result.status, 'error');
  assert.equal(result.reason, 'ownership_conflict');
  assert.ok(requests.every(url => url.hostname !== 'aisubtitle.hdslb.com'));
});
