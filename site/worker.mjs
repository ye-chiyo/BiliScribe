const PAGE_HTML = /*__HTML__*/;
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36';
const ORIGIN = 'https://www.bilibili.com/';
const COOKIE_NAME = 'bs_auth';
const QR_COOKIE = 'bs_qr';
const MAX_URLS = 3;
const MAX_PAGES = 8;
const CONNECT_TIMEOUT_MS = 8000;
const READ_TIMEOUT_MS = 12000;
const TRANSIENT_RETRIES = 2;
const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
const CACHE_MAX_ENTRIES = 500;
const CACHE_MAX_BYTES = 16 * 1024 * 1024;
const subtitleCache = new Map();
let cacheBytes = 0;
let biliRequestTail = Promise.resolve();

function json(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), {
    status, headers: { 'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store', ...headers },
  });
}

function readCookie(request, name) {
  const part = (request.headers.get('cookie') || '').split(';')
    .map(x => x.trim()).find(x => x.startsWith(`${name}=`));
  return part ? part.slice(name.length + 1) : null;
}

function siteCookie(name, value, age) {
  return `${name}=${value}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${age}`;
}

function bytesToBase64(bytes) {
  let result = '';
  for (const byte of bytes) result += String.fromCharCode(byte);
  return btoa(result).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}

function base64ToBytes(value) {
  const binary = atob(value.replaceAll('-', '+').replaceAll('_', '/'));
  return Uint8Array.from(binary, char => char.charCodeAt(0));
}

async function keyFor(env) {
  if (!env.BILI_COOKIE_KEY) throw new Error('云端凭证密钥尚未配置');
  return crypto.subtle.importKey('raw', base64ToBytes(env.BILI_COOKIE_KEY),
    'AES-GCM', false, ['encrypt', 'decrypt']);
}

async function seal(value, env) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = new TextEncoder().encode(JSON.stringify(value));
  const encrypted = new Uint8Array(await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv }, await keyFor(env), data));
  return `${bytesToBase64(iv)}.${bytesToBase64(encrypted)}`;
}

async function unseal(value, env) {
  if (!value) return null;
  try {
    const [a, b] = value.split('.');
    const raw = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: base64ToBytes(a) },
      await keyFor(env), base64ToBytes(b));
    return JSON.parse(new TextDecoder().decode(raw));
  } catch { return null; }
}

function parseSetCookies(response) {
  const lines = response.headers.getSetCookie?.() || [];
  const result = {};
  for (const line of lines) {
    const pair = line.split(';', 1)[0];
    const equals = pair.indexOf('=');
    if (equals > 0) result[pair.slice(0, equals)] = pair.slice(equals + 1);
  }
  return result;
}

function cookieHeader(cookies) {
  return Object.entries(cookies || {}).map(([key, value]) => `${key}=${value}`).join('; ');
}

async function biliGet(url, cookies = {}, referer = ORIGIN, options = {}) {
  let lastError;
  for (let attempt = 0; attempt <= TRANSIENT_RETRIES; attempt += 1) {
    let release;
    try {
      const turn = new Promise(resolve => { release = resolve; });
      const previous = biliRequestTail;
      biliRequestTail = previous.then(() => turn);
      await previous;
      await new Promise(resolve => setTimeout(resolve, 150));
      const response = await fetch(url, {
        headers: { 'user-agent': UA, 'accept': 'application/json, text/plain, */*',
          'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
          referer, ...(Object.keys(cookies).length ? { cookie: cookieHeader(cookies) } : {}) },
        redirect: options.redirect || 'follow',
        signal: AbortSignal.timeout(READ_TIMEOUT_MS),
      });
      release();
      if (!response.ok && !(options.redirect === 'manual' && [301, 302, 303, 307, 308].includes(response.status)))
        throw new Error(`B站请求失败 (${response.status})`);
      return response;
    } catch (error) {
      if (typeof release === 'function') release();
      lastError = error;
      if (attempt < TRANSIENT_RETRIES) await new Promise(resolve => setTimeout(resolve, 400 * (2 ** attempt)));
    }
  }
  throw lastError;
}

async function biliJson(url, cookies = {}, referer = ORIGIN) {
  const response = await biliGet(url, cookies, referer);
  const payload = await response.json();
  if (payload.code !== 0) throw new Error(payload.message || `B站接口错误 ${payload.code}`);
  return payload.data || {};
}

function sameOrigin(request) {
  const origin = request.headers.get('origin');
  return !origin || origin === new URL(request.url).origin;
}

async function loginStart() {
  const response = await biliGet('https://passport.bilibili.com/x/passport-login/web/qrcode/generate?web_location=333.1331',
    {}, 'https://passport.bilibili.com/login/h5-adapted');
  const payload = await response.json();
  if (payload.code !== 0 || !payload.data?.qrcode_key) throw new Error('B站二维码生成失败');
  return json({ qr_url: payload.data.url }, 200,
    { 'set-cookie': siteCookie(QR_COOKIE, encodeURIComponent(payload.data.qrcode_key), 180) });
}

async function loginPoll(request, env) {
  const key = readCookie(request, QR_COOKIE);
  if (!key) return json({ state: 'none' });
  const response = await biliGet(`https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=${key}`,
    {}, 'https://passport.bilibili.com/login/h5-adapted');
  const payload = await response.json();
  const code = payload.data?.code;
  if (code === 86090) return json({ state: 'scanned' });
  if (code === 86038) return json({ state: 'expired' });
  if (code !== 0) return json({ state: 'waiting' });
  const cookies = parseSetCookies(response);
  if (!cookies.SESSDATA) throw new Error('扫码已确认，但 B站未返回登录凭证');
  const token = await seal({ cookies, savedAt: Date.now() }, env);
  const headers = new Headers({ 'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store' });
  headers.append('set-cookie', siteCookie(COOKIE_NAME, token, 30 * 86400));
  headers.append('set-cookie', siteCookie(QR_COOKIE, '', 0));
  return new Response(JSON.stringify({ state: 'success' }), { headers });
}

async function status(request, env) {
  const session = await unseal(readCookie(request, COOKIE_NAME), env);
  if (!session?.cookies?.SESSDATA) return json({ logged_in: false, uname: '', expires: null });
  try {
    const nav = await biliJson('https://api.bilibili.com/x/web-interface/nav', session.cookies);
    return json({ logged_in: !!nav.isLogin, uname: nav.uname || '',
      expires: Math.floor(session.savedAt / 1000) + 30 * 86400 });
  } catch (error) {
    return json({ logged_in: false, offline: true, uname: '', error: String(error.message || error) });
  }
}

function videoId(url) {
  const value = new URL(url);
  if (value.hostname === 'b23.tv') return { short: true };
  if (!['www.bilibili.com', 'bilibili.com', 'm.bilibili.com'].includes(value.hostname))
    throw new Error('仅支持 B站视频链接');
  const bvid = value.pathname.match(/\/video\/(BV[0-9A-Za-z]{10})/i)?.[1];
  const aid = value.pathname.match(/\/video\/av(\d+)/i)?.[1];
  if (!bvid && !aid) throw new Error('链接中未找到 BV/av 号');
  return { bvid, aid, linkedPage: Number(value.searchParams.get('p') || 0) };
}

async function resolveVideo(rawUrl) {
  let url = rawUrl;
  if (videoId(url).short) {
    const response = await biliGet(url, {}, ORIGIN, { redirect: 'manual' });
    const location = response.headers.get('location');
    if (!location) throw new Error('短链展开失败');
    url = new URL(location, url).href;
  }
  return videoId(url);
}

function parsePages(spec, total, linkedPage) {
  if (!spec || spec === 'all' || spec === '*') {
    return linkedPage ? (linkedPage <= total ? [linkedPage] : [])
      : Array.from({ length: total }, (_, i) => i + 1);
  }
  const pages = new Set();
  for (const part of spec.replaceAll('，', ',').split(',')) {
    const match = part.trim().match(/^(\d+)(?:-(\d+))?$/);
    if (!match) continue;
    const a = Number(match[1]), b = Number(match[2] || match[1]);
    for (let i = a; i <= Math.min(b, total); i++) pages.add(i);
  }
  return [...pages].filter(page => page > 0 && page <= total).sort((a, b) => a - b);
}

function resourceId(url) {
  return String(url || '').split('/prod/').pop().split('?')[0];
}

function cacheRead(key) {
  const item = subtitleCache.get(key);
  if (!item || Date.now() - item.savedAt >= CACHE_TTL_MS) {
    if (item) cacheBytes -= item.bytes;
    subtitleCache.delete(key);
    return null;
  }
  return item.value;
}

function cacheWrite(key, value) {
  const encoded = JSON.stringify(value);
  const bytes = new TextEncoder().encode(encoded).byteLength;
  if (bytes > CACHE_MAX_BYTES) return;
  const previous = subtitleCache.get(key);
  if (previous) cacheBytes -= previous.bytes;
  while (subtitleCache.size >= CACHE_MAX_ENTRIES || cacheBytes + bytes > CACHE_MAX_BYTES) {
    const oldest = subtitleCache.keys().next().value;
    if (oldest === undefined) break;
    cacheBytes -= subtitleCache.get(oldest).bytes;
    subtitleCache.delete(oldest);
  }
  subtitleCache.set(key, { savedAt: Date.now(), value, bytes });
  cacheBytes += bytes;
}

function uniqueTracks(tracks) {
  const seen = new Set();
  return tracks.filter(track => {
    const key = resourceId(track.subtitle_url) || `${track.lan || ''}:${track.lan_doc || ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function languageMatches(value, wanted) {
  const lan = String(value || '').toLowerCase();
  return wanted.some(lang => {
    const item = String(lang || '').trim().toLowerCase();
    return lan === item || lan.split(/[-_]/).includes(item);
  });
}

function belongs(track, aid, cid) {
  const id = resourceId(track.subtitle_url);
  if (!id) return false;
  const prefix = `${aid}${cid}`;
  const lan = String(track.lan || '').toLowerCase();
  if (/(^|[-_])zh([-_]|$)/.test(lan)) return id.startsWith(prefix);
  const head = id.slice(0, prefix.length);
  return !/^\d+$/.test(head) || head === prefix;
}

async function subtitleTracks(bvid, aid, cid, cookies) {
  const referer = `https://www.bilibili.com/video/${bvid}`;
  const cacheKey = `tracks:${bvid}:${aid}:${cid}`;
  const cached = cacheRead(cacheKey);
  if (cached && cached.every(track => belongs(track, aid, cid))) return cached;
  let sawForeign = false;
  for (const delay of [0, 2000, 5000]) {
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    const data = await biliJson(`https://api.bilibili.com/x/player/v2?bvid=${bvid}&cid=${cid}&_ts=${Date.now()}-${Math.random().toString(36).slice(2)}`,
      cookies, referer);
    const tracks = data.subtitle?.subtitles || [];
    if (!tracks.length) continue;
    const owned = tracks.filter(track => belongs(track, aid, cid));
    const good = uniqueTracks(owned);
    sawForeign ||= owned.length !== tracks.length;
    if (owned.length && owned.length === tracks.length) {
      cacheWrite(cacheKey, good);
      return good;
    }
  }
  if (sawForeign) throw new Error('字幕接口多次返回其他视频的字幕轨，请稍后重试');
  return [];
}

async function primeSession(cookies, bvid) {
  try {
    const home = await biliGet(ORIGIN, cookies);
    Object.assign(cookies, parseSetCookies(home));
    const spi = await biliJson('https://api.bilibili.com/x/frontend/finger/spi', cookies);
    if (spi.b_3) cookies.buvid3 = spi.b_3;
    if (spi.b_4) cookies.buvid4 = spi.b_4;
    const page = await biliGet(`https://www.bilibili.com/video/${bvid}`, cookies);
    Object.assign(cookies, parseSetCookies(page));
  } catch {
    // Fingerprint warm-up is best-effort; the subtitle request still gets a chance.
  }
}

async function subtitleBody(url, bvid, cookies) {
  const absolute = new URL(url.startsWith('//') ? `https:${url}` : url);
  if (absolute.protocol !== 'https:' ||
      !(absolute.hostname.endsWith('.hdslb.com') || absolute.hostname.endsWith('.bilibili.com')))
    throw new Error('字幕资源地址不受支持');
  const key = `body:${resourceId(absolute.href)}`;
  const cached = cacheRead(key);
  if (cached) return cached;
  const response = await biliGet(absolute.href, cookies,
    `https://www.bilibili.com/video/${bvid}`);
  const data = await response.json();
  const body = Array.isArray(data.body) ? data.body : [];
  if (body.length) cacheWrite(key, body);
  return body;
}

function safeName(value, max = 60) {
  return (String(value || '').replace(/[\\/:*?"<>|\r\n\t]/g, '_').trim().replace(/^\.+|\.+$/g, '')
    || 'untitled').slice(0, max);
}

function stamp(value, srt = true) {
  let ms = Math.max(0, Math.round(Number(value || 0) * 1000));
  const h = Math.floor(ms / 3600000); ms %= 3600000;
  const m = Math.floor(ms / 60000); ms %= 60000;
  const s = Math.floor(ms / 1000); ms %= 1000;
  const n = x => String(x).padStart(2, '0');
  return `${n(h)}:${n(m)}:${n(s)}${srt ? ',' : '.'}${String(ms).padStart(3, '0')}`;
}

function render(format, body, meta) {
  const rows = body.filter(row => String(row.content || '').trim());
  if (format === 'json') return JSON.stringify({ meta, body }, null, 2);
  if (format === 'txt') return rows.map(row => String(row.content).trim()).join('\n');
  if (format === 'srt') return rows.map((row, i) =>
    `${i + 1}\n${stamp(row.from)} --> ${stamp(row.to)}\n${String(row.content).trim()}\n`).join('\n');
  if (format === 'vtt') return `WEBVTT\n\n${rows.map(row =>
    `${stamp(row.from, false)} --> ${stamp(row.to, false)}\n${String(row.content).trim()}\n`).join('\n')}`;
  if (format === 'md') {
    const lines = [`# ${meta.title}`, '', `- UP主: ${meta.owner}`,
      `- 链接: https://www.bilibili.com/video/${meta.bvid}`,
      `- 字幕语言: ${meta.lan_doc}`, ''];
    for (const row of rows) {
      const seconds = Math.max(0, Math.floor(Number(row.from || 0)));
      const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
      const time = h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
        : `${m}:${String(s).padStart(2, '0')}`;
      lines.push(`**[${time}]** ${String(row.content).trim()}`, '');
    }
    return lines.join('\n');
  }
  throw new Error(`不支持的格式: ${format}`);
}

async function extractOne(rawUrl, fmts, pagesSpec, langs, cookies) {
  const parsed = await resolveVideo(rawUrl);
  const query = parsed.bvid ? `bvid=${parsed.bvid}` : `aid=${parsed.aid}`;
  const info = await biliJson(`https://api.bilibili.com/x/web-interface/view?${query}`, cookies);
  await primeSession(cookies, info.bvid);
  const pages = parsePages(pagesSpec, info.pages.length, parsed.linkedPage);
  if (!pages.length) throw new Error('没有有效的分 P 页码');
  if (pages.length > MAX_PAGES) throw new Error(`云端单次最多处理 ${MAX_PAGES} 个分 P，请缩小范围`);
  const result = { url: rawUrl, bvid: info.bvid, title: info.title, owner: info.owner?.name || '',
    pic: info.pic || '', duration: info.duration || 0, pages_total: info.pages.length,
    pages_done: 0, tracks: 0, files: [], no_subtitle_pages: [], status: 'ok' };
  for (const pageNumber of pages) {
    const page = info.pages.find(item => item.page === pageNumber);
    let tracks = await subtitleTracks(info.bvid, info.aid, page.cid, cookies);
    if (!langs.some(lang => String(lang).trim().toLowerCase() === 'all'))
      tracks = tracks.filter(track => languageMatches(track.lan, langs));
    if (!tracks.length) result.no_subtitle_pages.push(pageNumber);
    for (const track of tracks) {
      const body = await subtitleBody(track.subtitle_url, info.bvid, cookies);
      if (!body.length) continue;
      const lan = track.lan || 'unk';
      const meta = { title: info.title, owner: result.owner, bvid: info.bvid,
        lan_doc: track.lan_doc || lan, page: pageNumber, part: page.part };
      const stem = info.pages.length > 1
        ? `P${String(pageNumber).padStart(2, '0')}-${safeName(page.part, 40)}.${lan}`
        : `${safeName(info.title)}_${info.bvid}.${lan}`;
      for (const format of fmts) result.files.push({ name: `${stem}.${format}`,
        content: render(format, body, meta), format });
      result.tracks++;
    }
    result.pages_done++;
  }
  if (!result.files.length) result.status = 'no_subtitle';
  return result;
}

async function extract(request, env) {
  const session = await unseal(readCookie(request, COOKIE_NAME), env);
  if (!session?.cookies?.SESSDATA) return json({ error: 'not_logged_in' }, 401);
  const input = await request.json();
  const urls = String(input.urls || '').match(/https?:\/\/[^\s,，;；。！？!?、）)】」』》"'’”…]+/g) || [];
  if (!urls.length || urls.length > MAX_URLS) return json({ error: `请输入 1–${MAX_URLS} 个 B站视频链接` }, 400);
  const fmts = [...new Set(Array.isArray(input.fmts) ? input.fmts : ['md'])];
  if (!fmts.length || fmts.some(fmt => !['srt', 'vtt', 'txt', 'json', 'md'].includes(fmt)))
    return json({ error: '请选择有效的导出格式' }, 400);
  const langs = Array.isArray(input.langs) && input.langs.length ? input.langs : ['zh'];
  const results = [];
  for (const url of urls) {
    try { results.push(await extractOne(url, fmts, input.pages || 'all', langs, session.cookies)); }
    catch (error) { results.push({ url, status: 'error', message: String(error.message || error), files: [] }); }
  }
  return json({ results });
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === '/') return new Response(PAGE_HTML,
      { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' } });
    if (!path.startsWith('/api/')) return new Response('Not found', { status: 404 });
    if (request.method === 'POST' && !sameOrigin(request)) return json({ error: 'forbidden' }, 403);
    try {
      if (path === '/api/status' && request.method === 'GET') return status(request, env);
      if (path === '/api/login/start' && request.method === 'POST') return loginStart();
      if (path === '/api/login/poll' && request.method === 'GET') return loginPoll(request, env);
      if (path === '/api/extract' && request.method === 'POST') return extract(request, env);
      return json({ error: 'not_found' }, 404);
    } catch (error) {
      return json({ error: String(error.message || error) }, 502);
    }
  },
};
