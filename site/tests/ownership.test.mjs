import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import worker from '../dist/server/index.js';

const origin = 'https://biliscribe.example';
let scenarioNumber = 0;
const request = (path, options = {}) => new Request(`${origin}${path}`, options);
const responseJson = data => new Response(JSON.stringify(data),
  { headers: { 'content-type': 'application/json' } });

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
                                    legacyCode = 0, modernTracks } = {}) {
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
    if (parsed.pathname === '/x/v2/subtitle/web/view') return new Response(
      modernTracks ? protobufTracks(modernTracks) : modern, { status: modernStatus });
    if (parsed.pathname === '/x/player/v2') return responseJson({ code: legacyCode,
      message: 'mock API failure', data: { subtitle: { subtitles: legacy } } });
    if (parsed.hostname === 'www.bilibili.com') return new Response('<html></html>');
    if (parsed.pathname === '/x/frontend/finger/spi') return responseJson({ code: 0,
      data: { b_3: 'mock-device3', b_4: 'mock-device4' } });
    if (parsed.hostname === 'aisubtitle.hdslb.com') return responseJson({ body: [] });
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
      body: JSON.stringify({ urls: `https://www.bilibili.com/video/${bvid}/` }),
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
