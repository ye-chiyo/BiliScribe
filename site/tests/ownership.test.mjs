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
                                    legacyCode = 0, legacyStatus = 200, legacyRaw, modernTracks, body = [], fmts = ['md'], repeat = 1, bodyStatus = 200, info, inputs, beforeExtract } = {}) {
  const bvid = `BVTEST${String(++scenarioNumber).padStart(6, '0')}`;
  const external = globalThis.fetch;
  const timer = globalThis.setTimeout;
  const originalNow = Date.now;
  const requests = [];
  globalThis.setTimeout = (callback, _delay, ...args) => timer(callback, 0, ...args);
  globalThis.fetch = async url => {
    const parsed = new URL(String(url));
    requests.push(parsed);
    if (parsed.pathname.endsWith('/qrcode/generate')) return responseJson({ code: 0,
      data: { qrcode_key: 'mock-ownership-qr', url: 'https://passport.bilibili.com/mock' } });
    if (parsed.pathname.endsWith('/qrcode/poll')) return new Response(JSON.stringify({ code: 0, data: { code: 0 } }), {
      headers: [['set-cookie', 'SESSDATA=mock-ownership-session; Path=/; HttpOnly']] });
    if (parsed.pathname === '/x/web-interface/view') return responseJson({ code: 0, data: info ? info(parsed, bvid) : {
      bvid, aid: 117505221724658, title: 'AI 观众提问', owner: { name: 'UP', mid: 123 },
      pages: [{ page: 1, cid: 43061546452, part: '正片' }], duration: 824,
    } });
    if (parsed.pathname === '/x/v2/subtitle/web/view') {
      if (!info) {
        assert.equal(parsed.searchParams.get('pid'), '117505221724658');
        assert.equal(parsed.searchParams.get('oid'), '43061546452');
      }
      return new Response(modernTracks ? protobufTracks(typeof modernTracks === 'function' ? modernTracks(parsed) : modernTracks) : modern, { status: modernStatus });
    }
    if (parsed.pathname === '/x/player/v2') return new Response(legacyRaw ?? JSON.stringify({ code: legacyCode,
      message: 'mock API failure', data: { subtitle: { subtitles: typeof legacy === 'function' ? legacy(parsed) : legacy } } }),
      { status: legacyStatus, headers: { 'content-type': 'application/json' } });
    if (parsed.hostname === 'www.bilibili.com') return new Response('<html></html>');
    if (parsed.pathname === '/x/frontend/finger/spi') return responseJson({ code: 0,
      data: { b_3: 'mock-device3', b_4: 'mock-device4' } });
    if (parsed.hostname === 'aisubtitle.hdslb.com') return new Response(JSON.stringify({ body: typeof body === 'function' ? body(parsed) : body }),
      { status: typeof bodyStatus === 'function' ? bodyStatus(parsed) : bodyStatus, headers: { 'content-type': 'application/json' } });
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
    const results = [];
    for (let index = 0; index < (inputs?.length || repeat); index++) {
    beforeExtract?.(index);
    const response = await worker.fetch(request('/api/extract', {
      method: 'POST', headers: { origin, cookie: session, 'content-type': 'application/json' },
      body: JSON.stringify({ urls: `https://www.bilibili.com/video/${bvid}/`, fmts, ...(typeof inputs?.[index] === 'function' ? inputs[index](bvid) : inputs?.[index]) }),
    }), env);
    assert.equal(response.status, 200);
    results.push((await response.json()).results[0]);
    }
    return { result: results.at(-1), results, requests };
  } finally {
    globalThis.fetch = external;
    globalThis.setTimeout = timer;
    Date.now = originalNow;
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


test('repeat extraction reuses source-bound opaque metadata and body', async () => {
  const { results, requests } = await extractionScenario({ repeat: 2,
    modernTracks: [{ lan: 'ai-zh', subtitle_url: encodedTrackUrl() }],
    body: [{ from: 1, to: 2, content: 'cached sample' }] });
  assert.deepEqual(results.map(result => result.status), ['ok', 'ok']);
  assert.equal(requests.filter(url => url.pathname === '/x/v2/subtitle/web/view').length, 1);
  assert.equal(requests.filter(url => url.hostname === 'aisubtitle.hdslb.com').length, 1);
  assert.match(results[1].files[0].content, /cached sample/);
});


for (const oldStatus of [200, 403]) test(`signature or empty body ${oldStatus} refreshes metadata once before export`, async () => {
  let metadataCalls = 0;
  const { result } = await extractionScenario({
    modernTracks: () => [{ lan: 'ai-zh', subtitle_url: encodedTrackUrl().replace('mock-signature', ++metadataCalls === 1 ? 'old' : 'new') }],
    bodyStatus: url => url.searchParams.get('auth_key') === 'old' ? oldStatus : 200,
    body: url => url.searchParams.get('auth_key') === 'old' ? [] : [{ from: 1, to: 2, content: 'refreshed signature' }], fmts: ['txt'],
  });
  assert.equal(result.status, 'ok');
  assert.equal(result.files[0].content, 'refreshed signature');
  assert.equal(metadataCalls, 2);
});


test('same opaque resource is isolated across videos and pages with five formats', async () => {
  let currentVideo, firstVideo, currentCid;
  const { results, requests } = await extractionScenario({
    info: (url, bvid) => {
      firstVideo ||= bvid;
      currentVideo = url.searchParams.get('bvid') || bvid;
      return { bvid: currentVideo, aid: 117505221724658, title: 'format/test',
        owner: { name: 'UP', mid: 123 }, duration: 10,
        pages: [{ page: 1, cid: 43061546452, part: 'one' }, { page: 2, cid: 43061546453, part: 'two' }] };
    },
    modernTracks: url => {
      currentCid = url.searchParams.get('oid');
      return [{ lan: 'ai-zh', subtitle_url: encodedTrackUrl() },
        { lan: 'ai-en', subtitle_url: encodedTrackUrl('f'.repeat(32)) }];
    },
    body: () => [{ from: 1.9996, to: 3.0001, content: currentVideo !== firstVideo ? 'video two page one'
      : currentCid === '43061546453' ? 'video one page two' : 'video one page one' }],
    fmts: ['srt', 'vtt', 'txt', 'json', 'md'],
    inputs: [{ pages: '1' }, { pages: '1' }, { pages: '2' },
      { urls: 'https://www.bilibili.com/video/BV11gYd6UEPv/?p=1' },
      { urls: 'https://www.bilibili.com/video/BV11gYd6UEPv/?p=1', langs: ['all'] },
      { urls: 'https://www.bilibili.com/video/BV11gYd6UEPv/?p=1', langs: ['en'] }],
  });
  const texts = ['video one page one', 'video one page one', 'video one page two', 'video two page one'];
  results.slice(0, 4).forEach((result, index) => {
    assert.equal(result.status, 'ok');
    assert.equal(result.files.length, 5);
    const formats = Object.fromEntries(result.files.map(file => [file.format, file.content]));
    const text = texts[index];
    assert.equal(formats.txt, text);
    assert.equal(formats.srt, `1\n00:00:02,000 --> 00:00:03,000\n${text}\n`);
    assert.equal(formats.vtt, `WEBVTT\n\n00:00:02.000 --> 00:00:03.000\n${text}\n`);
    assert.equal(JSON.parse(formats.json).body[0].content, text);
    assert.ok(formats.md.includes(`**[0:01]** ${text}`));
  });
  assert.equal(results[4].tracks, 2);
  assert.equal(results[5].tracks, 1);
  assert.ok(results[5].files.every(file => file.name.includes('.ai-en.')));
  assert.equal(requests.filter(url => url.pathname === '/x/v2/subtitle/web/view').length, 3);
  assert.equal(requests.filter(url => url.hostname === 'aisubtitle.hdslb.com').length, 4);
});


for (const shift of [25 * 3600_000, -3600_000]) test(`expired or future proof is requeried after clock shift ${shift}`, async () => {
  const start = Date.now();
  let available = true;
  const { results, requests } = await extractionScenario({ repeat: 2,
    beforeExtract: index => { if (index) { available = false; Date.now = () => start + shift; } },
    modernTracks: () => available ? [{ lan: 'ai-zh', subtitle_url: encodedTrackUrl() }] : [],
    body: [{ from: 1, to: 2, content: 'initial valid' }],
  });
  assert.equal(results[0].status, 'ok');
  assert.equal(results[1].status, 'no_subtitle');
  assert.deepEqual(results[1].files, []);
  assert.equal(requests.filter(url => url.pathname === '/x/v2/subtitle/web/view').length, 2);
  assert.equal(requests.filter(url => url.hostname === 'aisubtitle.hdslb.com').length, 1);
});


test('legacy transient unverified track can recover', async () => {
  let round = 0;
  const { result } = await extractionScenario({ legacy: () => [{ lan: 'ai-zh',
    subtitle_url: ++round === 1 ? encodedTrackUrl()
      : '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11750522172465843061546452fixture' }],
    body: [{ from: 0, to: 1, content: '重试取得正确字幕' }], fmts: ['txt'] });
  assert.equal(result.status, 'ok');
  assert.equal(result.files[0].content, '重试取得正确字幕');
});


test('modern conflict survives legacy HTTP failure', async () => {
  const { result, requests } = await extractionScenario({ modernTracks: [{ lan: 'ai-zh',
    subtitle_url: '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337329541521726472939880ffffffffffffffffffffffffffffffff' }],
    legacyStatus: 503 });
  assert.equal(result.status, 'error');
  assert.equal(result.reason, 'ownership_conflict');
  assert.ok(requests.every(url => url.hostname !== 'aisubtitle.hdslb.com'));
});

for (const [status, reason] of [[401, 'auth_required'], [429, 'rate_limited']]) {
  test(`body HTTP ${status} retains specific reason`, async () => {
    const { result } = await extractionScenario({ modernTracks: [{ lan: 'ai-zh',
      subtitle_url: encodedTrackUrl() }], bodyStatus: status });
    assert.equal(result.status, 'error');
    assert.equal(result.reason, reason);
    assert.deepEqual(result.files, []);
  });
}

for (const [status, raw, reason] of [[401, '{}', 'auth_required'], [429, '{}', 'rate_limited'],
  [200, '{broken', 'subtitle_parse_error'], [200, '{}', 'subtitle_api_error']]) {
  test(`legacy HTTP ${status} payload retains ${reason}`, async () => {
    const { result, requests } = await extractionScenario({ legacyStatus: status, legacyRaw: raw });
    assert.equal(result.status, 'error');
    assert.equal(result.reason, reason);
    assert.deepEqual(result.files, []);
    assert.ok(requests.every(url => url.hostname !== 'aisubtitle.hdslb.com'));
  });
}
