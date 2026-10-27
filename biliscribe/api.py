# -*- coding: utf-8 -*-
"""B 站薄客户端: 链接解析 / 视频信息 / 字幕列表与下载 / Web 扫码登录。
仅依赖 httpx 与 qrcode, 接口行为以 bilibili-API-collect 文档与实测为准。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import re
import time
from dataclasses import dataclass
from urllib.parse import unquote_to_bytes, urlsplit, urlunsplit
from pathlib import Path

import httpx
import qrcode

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
CACHE_TTL = 24 * 3600  # 字幕缓存 24 小时, 避免重复请求触发频控
CONNECT_TIMEOUT = 8.0
READ_TIMEOUT = 12.0
TRANSIENT_RETRIES = 2
SUBTITLE_POLICY_VERSION = 2


@dataclass(frozen=True)
class SubtitleApproval:
    source: str
    bvid: str
    aid: int | None
    cid: int
    fingerprint: str
    approved_at: float
    policy_version: int = SUBTITLE_POLICY_VERSION


def _atomic_json_write(path: Path, value) -> None:
    """Write cache data atomically so a killed process cannot leave JSON half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _cache_fresh(path: Path, ttl: int = CACHE_TTL) -> bool:
    try:
        return time.time() - path.stat().st_mtime < ttl
    except OSError:
        return False

def cleanup_cache(max_age: int = CACHE_TTL) -> int:
    """删除超过 TTL 的缓存文件, 返回清理数量。服务启动时调用一次。"""
    removed = 0
    if not CACHE_DIR.exists():
        return 0
    cutoff = time.time() - max_age
    for f in CACHE_DIR.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"av(\d+)", re.I)
SHORT_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+")
PAGE_RE = re.compile(r"[?&]p=(\d+)")

# 触发风控/鉴权类错误码
AUTH_ERRORS = {-101, -102, -111, -658}
RATE_ERRORS = {-412, -352, -799, 88008}

# The web player uses two rotating key pairs for subtitle_url_v2.  These are
# path obfuscation keys, not credentials; the signed auth_key remains a query
# parameter and is never logged or persisted separately.
_SUBTITLE_V2_KEYS = (
    (r'nP](wOFRvU.+<fjS{jn-!$D|Dz&",zT`',
     r'=CFxYRn{.y|uVyO$uh&sikph?N.ilF/`bilibili'),
    (r'Bn"q~|albg@]Go~ACgyDvKnd+)_D}^&J?',
     r"Cu~L!xs~f^&r@'vh=q]q{eeng*sEg^kp#Jbilibili"),
)


def _decode_subtitle_v2_path(path: str) -> str:
    """Decode the web player's percent-escaped XOR subtitle path."""
    try:
        encoded = path[1:] if path.startswith("/") else path
        cipher = unquote_to_bytes(encoded)
    except ValueError as exc:
        raise BiliError(-1, "字幕资源地址编码无效") from exc
    for prefix, key in _SUBTITLE_V2_KEYS:
        plain = bytes(byte ^ ord(key[index % len(key)])
                      for index, byte in enumerate(cipher))
        prefix_bytes = prefix.encode("ascii")
        if plain.startswith(prefix_bytes):
            decoded = plain[len(prefix_bytes):].decode("utf-8")
            if decoded.startswith("/bfs/"):
                return decoded
    raise BiliError(-1, "字幕资源地址无法解码")


def normalize_subtitle_url(subtitle_url: str) -> str:
    """Return a safe, directly downloadable subtitle JSON URL."""
    raw = str(subtitle_url or "").strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise BiliError(-1, "字幕资源地址不受支持") from exc
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or port not in (None, 443)
            or not (host == "bilibili.com" or host.endswith(".bilibili.com")
                    or host == "hdslb.com" or host.endswith(".hdslb.com"))):
        raise BiliError(-1, "字幕资源地址不受支持")
    if host == "subtitle.bilibili.com":
        path = _decode_subtitle_v2_path(parsed.path)
        return urlunsplit(("https", "aisubtitle.hdslb.com", path,
                           parsed.query, ""))
    return urlunsplit(("https", parsed.netloc, parsed.path,
                       parsed.query, parsed.fragment))


def _protobuf_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(payload):
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift > 63:
            raise ValueError("protobuf varint is too long")
    raise ValueError("unterminated protobuf varint")


def _protobuf_fields(payload: bytes) -> list[tuple[int, int, int | bytes]]:
    """Decode the wire types used by Bilibili's subtitle metadata response."""
    fields = []
    offset = 0
    while offset < len(payload):
        key, offset = _protobuf_varint(payload, offset)
        number, wire_type = key >> 3, key & 7
        if number <= 0:
            raise ValueError("invalid protobuf field number")
        if wire_type == 0:
            value, offset = _protobuf_varint(payload, offset)
            fields.append((number, wire_type, value))
        elif wire_type in (1, 5):
            size = 8 if wire_type == 1 else 4
            end = offset + size
            if end > len(payload):
                raise ValueError("truncated protobuf fixed field")
            fields.append((number, wire_type, payload[offset:end]))
            offset = end
        elif wire_type == 2:
            length, offset = _protobuf_varint(payload, offset)
            end = offset + length
            if end > len(payload):
                raise ValueError("truncated protobuf bytes field")
            fields.append((number, wire_type, payload[offset:end]))
            offset = end
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire_type}")
    return fields


def parse_web_subtitle_response(payload: bytes) -> list[dict]:
    """Parse the current ``/x/v2/subtitle/web/view`` metadata response."""
    top = _protobuf_fields(payload)
    data = next((v for n, w, v in top if n == 1 and w == 2), None)
    if not isinstance(data, bytes):
        return []
    tracks = []
    for number, wire_type, item in _protobuf_fields(data):
        if number != 3 or wire_type != 2 or not isinstance(item, bytes):
            continue
        fields = _protobuf_fields(item)

        def text(field_number: int) -> str | None:
            value = next((v for n, w, v in fields
                          if n == field_number and w == 2), None)
            return value.decode("utf-8") if isinstance(value, bytes) else None

        lan, subtitle_url = text(3), text(5)
        if not lan or not subtitle_url:
            continue
        ident = next((v for n, w, v in fields if n == 1 and w == 0), None)
        tracks.append({"id": ident, "id_str": text(2), "lan": lan,
                       "lan_doc": text(4), "subtitle_url": subtitle_url})
    return tracks


class BiliError(Exception):
    def __init__(self, code: int, message: str, *, reason: str | None = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.reason = reason or ("auth_required" if code in AUTH_ERRORS else
                                 "rate_limited" if code in RATE_ERRORS else
                                 "subtitle_api_error")


class ForeignSubtitleError(BiliError):
    """字幕接口返回了可确认属于其他视频的字幕轨。"""

    def __init__(self, message: str = "字幕接口返回了其他视频的数据"):
        super().__init__(-1001, message, reason="ownership_conflict")


class UnverifiedSubtitleError(BiliError):
    """A track exists, but the available evidence cannot establish ownership."""

    def __init__(self):
        super().__init__(-1002, "已找到字幕轨，但无法验证其属于当前视频；请稍后重试。",
                         reason="ownership_unverified")


def _request_failure_reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in (401, 403):
            return "auth_required"
        if exc.response.status_code == 429:
            return "rate_limited"
    return "network_error" if isinstance(exc, httpx.HTTPError) else "subtitle_parse_error"


def subtitle_resource_id(subtitle_url: str) -> str:
    """Return a stable resource id for legacy and encoded subtitle URLs."""
    raw = str(subtitle_url or "")
    try:
        raw = normalize_subtitle_url(raw)
    except BiliError:
        pass
    path = urlsplit(raw).path
    tail = path.split("/prod/")[-1]
    return tail.split("?")[0]


def _track_key(track: dict) -> str:
    """Return a stable, non-secret identity for a subtitle resource."""
    return subtitle_resource_id(str(track.get("subtitle_url", "")))


def track_belongs_to_video(track: dict, aid: int | None, cid: int,
                           owner_mid: int | None = None) -> bool:
    """Check resource-carried ownership; opaque IDs need request provenance."""
    return track_rejection_reason(track, aid, cid, owner_mid) is None


def _author_rejection_reason(track: dict, owner_mid: int | None) -> str | None:
    if owner_mid is not None and track.get("author_mid") is not None:
        try:
            if int(track["author_mid"]) != int(owner_mid):
                return "ownership_conflict"
        except (TypeError, ValueError):
            return "ownership_unverified"
    return None


def _opaque_resource(track: dict) -> bool:
    try:
        url = normalize_subtitle_url(track.get("subtitle_url", ""))
    except BiliError:
        return False
    return (urlsplit(url).path.startswith("/bfs/ai_subtitle/prod/")
            and re.fullmatch(r"[0-9a-fA-F]{32}", subtitle_resource_id(url)) is not None)


def _track_fingerprint(track: dict) -> str:
    # Bind the complete normalized URL, including its current signature. A new
    # signed URL must be discovered again; its stable resource may cache later.
    values = [normalize_subtitle_url(track.get("subtitle_url", "")),
              str(track.get("id", "")), str(track.get("id_str", "")),
              str(track.get("lan", "")), str(track.get("author_mid", ""))]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def track_rejection_reason(track: dict, aid: int | None, cid: int,
                           owner_mid: int | None = None) -> str | None:
    """Separate an explicit identity conflict from missing ownership evidence."""
    author_reason = _author_rejection_reason(track, owner_mid)
    if author_reason:
        return author_reason
    try:
        url = normalize_subtitle_url(track.get("subtitle_url", ""))
    except BiliError:
        return "ownership_unverified"
    rid = subtitle_resource_id(url)
    if not rid or re.fullmatch(r"[0-9a-fA-F]{32}", rid):
        return "ownership_unverified"
    if aid:
        prefix = f"{aid}{cid}"
        if rid.startswith(prefix):
            return None
        if rid[:len(prefix)].isdigit() or re.fullmatch(r"\d{20,30}[0-9a-fA-F]{32}", rid):
            return "ownership_conflict"
    return "ownership_unverified"


# 链接末尾容易被一起复制进来的中英文标点(成对括号的右半等)
_URL_TAIL_PUNCT = ".,;:!?，。；：！？、）)】」』》>\"'’”…·"


def extract_urls(text: str) -> list[str]:
    """从一段文本(支持换行/逗号分隔多个链接)中提取 B 站视频 URL。"""
    # 中文句读/成对右括号直接视为链接边界, 避免尾随标点污染短链
    urls = re.findall(r"https?://[^\s,，;；。！？!、）)】」』》\"'’”…]+", text)
    out = []
    for u in urls:
        u = u.rstrip(_URL_TAIL_PUNCT)
        if "bilibili.com" in u or "b23.tv" in u:
            out.append(u)
    return out


def parse_video_url(url: str) -> tuple[str | None, int | None, int | None]:
    """返回 (bvid, aid, page)。短链需先经 BiliClient.resolve 展开。"""
    m = BV_RE.search(url)
    bvid = m.group(1) if m else None
    ma = AV_RE.search(url)
    aid = int(ma.group(1)) if ma else None
    mp = PAGE_RE.search(url)
    page = int(mp.group(1)) if mp else 1
    return bvid, aid, page


class BiliClient:
    def __init__(self, cookies: dict | None = None, setup_fingerprint: bool = True):
        self._cookies = dict(cookies or {})
        self.client = self._new_client()
        self._primed: set[str] = set()
        self._video_info_cache: dict[str, dict] = {}
        self._track_approvals: dict[tuple, SubtitleApproval] = {}
        if setup_fingerprint:
            self._setup_fingerprint()
        self._apply_cookies()

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": UA,
                     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                     "Accept": "application/json, text/plain, */*"},
            follow_redirects=True,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT,
                                   write=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT),
        )
    def _apply_cookies(self):
        for name, value in self._cookies.items():
            self.client.cookies.set(name, value, domain=".bilibili.com", path="/")

    def _reconnect(self):
        self._cookies.update(self.cookies_dict())
        self.client.close()
        self.client = self._new_client()
        self._apply_cookies()

    def prime_video_page(self, bvid: str | None, aid: int | None, force: bool = False):
        """模拟浏览器先打开视频页(HTML): 服务端借此完成会话关联/补种 cookie,
        实测不做这一步直接请求 player/v2 时字幕列表会被裁剪。"""
        key = bvid or (f"av{aid}" if aid else None)
        if not key or (key in self._primed and not force):
            return
        try:
            self.client.get(f"https://www.bilibili.com/video/{key}",
                            headers={"Referer": "https://www.bilibili.com/"})
            time.sleep(0.4)
            self._primed.add(key)
        except Exception:
            pass

    def _setup_fingerprint(self):
        """模拟真实浏览器: 访问首页拿 b_nut, 再取 buvid3/buvid4 设备指纹。
        实测缺少设备指纹时, 频控状态下字幕列表会被裁剪为空。"""
        try:
            self.client.get("https://www.bilibili.com/",
                            headers={"Referer": "https://www.bilibili.com/"})
            spi = self.client.get(
                "https://api.bilibili.com/x/frontend/finger/spi",
                headers={"Referer": "https://www.bilibili.com/"}).json()["data"]
            self.client.cookies.set("buvid3", spi["b_3"],
                                    domain=".bilibili.com", path="/")
            self.client.cookies.set("buvid4", spi["b_4"],
                                    domain=".bilibili.com", path="/")
        except Exception:
            pass  # 指纹失败不应阻断主流程

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------- 基础 ----------
    def _get_json(self, url: str, referer: str = "https://www.bilibili.com/",
                  retries: int = 2):
        last_exc = None
        for attempt in range(min(retries, TRANSIENT_RETRIES) + 1):
            try:
                r = self.client.get(url, headers={"Referer": referer})
                r.raise_for_status()
                d = r.json()
                if not isinstance(d, dict):
                    raise ValueError("invalid API response")
                code = d.get("code")
                if code in RATE_ERRORS:
                    last_exc = BiliError(code, "B站接口限流，请稍后重试。")
                    wait = 0.6 * (2 ** attempt) + random.random() * 0.25
                    time.sleep(wait)
                    continue
                if code != 0:
                    raise BiliError(code if isinstance(code, int) else -1,
                                    d.get("message", "未知错误"))
                return d.get("data", {})
            except (httpx.HTTPError, ValueError) as e:
                last_exc = e
                if isinstance(e, httpx.HTTPError):
                    self._reconnect()
                if attempt < min(retries, TRANSIENT_RETRIES):
                    time.sleep(0.5 * (2 ** attempt))
        if isinstance(last_exc, BiliError):
            raise last_exc
        raise BiliError(-1, "B站接口请求失败，请稍后重试。",
                        reason=_request_failure_reason(last_exc)) from None

    def resolve(self, url: str) -> str:
        """展开 b23.tv 短链, 返回最终 URL。"""
        url = url.strip().rstrip(_URL_TAIL_PUNCT)
        if SHORT_RE.search(url):
            r = self.client.get(url, headers={"Referer": "https://www.bilibili.com/"})
            return str(r.url).rstrip(_URL_TAIL_PUNCT)
        return url

    # ---------- 视频与字幕 ----------
    def get_video_info(self, bvid: str | None = None, aid: int | None = None) -> dict:
        key = f"bvid={bvid}" if bvid else f"aid={aid}"
        if key in self._video_info_cache:
            return self._video_info_cache[key]
        d = self._get_json(f"https://api.bilibili.com/x/web-interface/view?{key}")
        info = {
            "bvid": d["bvid"], "aid": d["aid"], "cid": d["cid"],
            "title": d["title"], "owner": d["owner"]["name"],
            "owner_mid": d["owner"].get("mid"),
            "duration": d["duration"], "tname": d.get("tname", ""),
            "pic": d.get("pic", ""),
            "pages": [{"page": p["page"], "cid": p["cid"], "part": p["part"],
                       "duration": p["duration"]} for p in d["pages"]],
        }
        self._video_info_cache[key] = info
        self._video_info_cache.setdefault(f"bvid={info['bvid']}", info)
        self._video_info_cache.setdefault(f"aid={info['aid']}", info)
        return info

    def _approve_tracks(self, tracks: list[dict], bvid: str, aid: int | None,
                        cid: int, owner_mid: int | None, source: str) -> list[dict]:
        """Associate accepted wire tracks with the request that obtained them."""
        approved = {}
        for track in tracks:
            reason = track_rejection_reason(track, aid, cid, owner_mid)
            modern_opaque = (source == "modern" and _opaque_resource(track)
                             and _author_rejection_reason(track, owner_mid) is None)
            if not track.get("lan") or (reason is not None and not modern_opaque):
                continue
            item = dict(track)
            fingerprint = _track_fingerprint(item)
            self._track_approvals[(bvid, aid, cid, fingerprint)] = SubtitleApproval(
                source, bvid, aid, cid, fingerprint, time.time())
            approved[_track_key(item)] = item
        return list(approved.values())

    def validate_track(self, track: dict, bvid: str, aid: int | None,
                       cid: int, owner_mid: int | None = None) -> SubtitleApproval:
        """Require a current client-created approval for this exact track."""
        reason = track_rejection_reason(track, aid, cid, owner_mid)
        if reason == "ownership_conflict":
            raise ForeignSubtitleError()
        try:
            fingerprint = _track_fingerprint(track)
        except BiliError:
            raise UnverifiedSubtitleError() from None
        approval = self._track_approvals.get((bvid, aid, cid, fingerprint))
        if (approval is None or approval.policy_version != SUBTITLE_POLICY_VERSION
                or not 0 <= time.time() - approval.approved_at < CACHE_TTL
                or _author_rejection_reason(track, owner_mid) is not None
                or (reason is not None and not (
                    approval.source == "modern" and _opaque_resource(track)))):
            raise UnverifiedSubtitleError()
        return approval

    def fetch_track(self, track: dict, bvid: str, aid: int | None, cid: int,
                    owner_mid: int | None = None) -> list[dict]:
        """Download only a track approved for this video's selected page."""
        self.validate_track(track, bvid, aid, cid, owner_mid)
        body = self.fetch_subtitle(track["subtitle_url"], bvid, cache_scope=(bvid, aid, cid))
        self.validate_track(track, bvid, aid, cid, owner_mid)
        return body

    def _cache_tracks(self, path: Path, tracks: list[dict], bvid: str,
                      aid: int | None, cid: int) -> None:
        entries = []
        for track in tracks:
            fingerprint = _track_fingerprint(track)
            proof = self._track_approvals[(bvid, aid, cid, fingerprint)]
            entries.append({"track": track, "source": proof.source,
                            "context": [proof.bvid, proof.aid, proof.cid],
                            "fingerprint": fingerprint, "approved_at": proof.approved_at})
        _atomic_json_write(path, {"version": SUBTITLE_POLICY_VERSION,
            "bvid": bvid, "aid": aid, "cid": cid, "entries": entries})

    def _restore_tracks(self, cached, bvid: str, aid: int | None, cid: int,
                        owner_mid: int | None) -> list[dict]:
        # Serialized proofs are consistency records, not signatures against local tampering.
        if (not isinstance(cached, dict) or cached.get("version") != SUBTITLE_POLICY_VERSION
                or (cached.get("bvid"), cached.get("aid"), cached.get("cid")) != (bvid, aid, cid)
                or not isinstance(cached.get("entries"), list) or not cached["entries"]):
            return []
        restored = []
        try:
            for entry in cached["entries"]:
                track, source, stamp = entry["track"], entry["source"], entry["approved_at"]
                if (not isinstance(track, dict) or not track.get("lan")
                        or entry.get("context") != [bvid, aid, cid]
                        or source not in ("modern", "legacy")
                        or not isinstance(stamp, (int, float)) or isinstance(stamp, bool)
                        or not 0 <= time.time() - stamp < CACHE_TTL):
                    return []
                fingerprint = _track_fingerprint(track)
                reason = track_rejection_reason(track, aid, cid, owner_mid)
                if (fingerprint != entry["fingerprint"] or _author_rejection_reason(track, owner_mid)
                        or (reason is not None and not (source == "modern" and _opaque_resource(track)))):
                    return []
                restored.append((dict(track), SubtitleApproval(source, bvid, aid, cid, fingerprint, stamp)))
        except (KeyError, TypeError, ValueError, BiliError):
            return []
        for track, proof in restored:
            self._track_approvals[(bvid, aid, cid, proof.fingerprint)] = proof
        return list({_track_key(track): track for track, _ in restored}.values())

    def get_subtitle_list(self, bvid: str | None, aid: int | None, cid: int,
                          on_log=None, owner_mid: int | None = None,
                          force_refresh: bool = False) -> list[dict]:
        """字幕列表对高频请求敏感(实测频控时会随机返回 0/1/2 条), 且 player/v2
        会概率性返回*其他视频*的字幕轨。策略:
        1. 只用 bvid 形式查询(aid 形式实测几乎必然串到其他视频, av 号链接在
           view 接口之后也能拿到规范 bvid);
        2. 每轮按资源 id 做归属校验; 区分明确冲突与证据不足，并向编排层保留错误;
        3. 仅把校验通过的结果落盘缓存, 读到历史脏缓存自动丢弃重拉。"""
        def log(m):
            if on_log:
                on_log(m)

        # Include the video identity in the cache key.  A CID is not a global
        # identifier and can otherwise collide with another video's page.
        cache_key = hashlib.sha256(
            f"{bvid or ''}:{aid or ''}:{cid}".encode("utf-8")
        ).hexdigest()[:24]
        cf = CACHE_DIR / f"{cache_key}_list.json"
        # Do not read the legacy CID-only key: it cannot distinguish videos
        # when a resource id is opaque, so using it could return another
        # video's subtitle. Existing legacy files expire through normal cleanup.
        if not force_refresh and _cache_fresh(cf):
            try:
                cached = json.loads(cf.read_text(encoding="utf-8"))
                restored = self._restore_tracks(cached, bvid, aid, cid, owner_mid)
                if restored:
                    return restored
                log("本地字幕缓存校验未通过(疑似其他视频数据), 重新拉取")
                cf.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                cf.unlink(missing_ok=True)

        if not bvid:
            # view 接口对 av 号也会返回规范 bvid; 没有 bvid 就无法安全查询
            raise BiliError(-1, "缺少 bvid, 无法安全获取字幕列表")

        # 实测: 必须先打开视频页完成会话关联, 否则字幕列表会被裁剪
        self.prime_video_page(bvid, aid)

        # 当前播放器对部分视频只通过 Protobuf 接口返回完整 AI 轨道。
        # 仍须从解码后的资源 ID 校验 aid/cid, 通过后才进入缓存。
        failure = None
        try:
            modern = self._get_modern_subtitle_list(bvid, aid, cid)
        except (BiliError, httpx.HTTPError, ValueError) as exc:
            modern = []
            if isinstance(exc, BiliError):
                failure = exc
            elif isinstance(exc, httpx.HTTPError):
                failure = BiliError(-1, "字幕接口访问失败，请稍后重试。",
                                    reason=_request_failure_reason(exc))
            else:
                failure = BiliError(-1, "字幕元数据解析失败，请稍后重试。", reason="subtitle_parse_error")
            log(f"现代字幕接口不可用, 回退旧接口: {failure.message}")
        if modern:
            verified = self._approve_tracks(modern, bvid, aid, cid, owner_mid, "modern")
            if verified:
                self._cache_tracks(cf, verified, bvid, aid, cid)
                return verified
            reasons = [track_rejection_reason(t, aid, cid, owner_mid) for t in modern]
            failure = (ForeignSubtitleError() if "ownership_conflict" in reasons
                       else UnverifiedSubtitleError())

        # 限流空列表靠退避重试; 每轮重新预热视频页。验证失败不得转为无字幕。
        waits = [0, 1, 2]
        for rnd, w in enumerate(waits):
            if w:
                time.sleep(w)
                self.prime_video_page(bvid, aid, force=True)
            # 实测: 附加 web_location 参数会使字幕列表被裁剪, 用最简查询
            # 每一轮必须生成新的缓存破坏参数; 复用同一 URL 会让 CDN
            # 持续返回上一轮的错误字幕列表, 使重试失去意义。
            query = f"bvid={bvid}&cid={cid}&_ts={time.time_ns()}-{rnd}"
            try:
                d = self._get_json(
                    f"https://api.bilibili.com/x/player/v2?{query}",
                    referer=f"https://www.bilibili.com/video/{bvid}")
            except BiliError as exc:
                if failure is None:
                    failure = exc
                continue
            subs = d.get("subtitle", {}).get("subtitles", []) or []
            if not subs:
                continue
            good = [t for t in subs if track_rejection_reason(t, aid, cid, owner_mid) is None]
            bad = len(subs) - len(good)
            if good:
                unique = list({_track_key(t): t for t in good if _track_key(t)}.values())
                approved = self._approve_tracks(unique, bvid, aid, cid, owner_mid, "legacy")
                self._cache_tracks(cf, approved, bvid, aid, cid)
                return approved
            if bad:
                if any(track_rejection_reason(t, aid, cid, owner_mid) == "ownership_conflict"
                       for t in subs):
                    log("字幕接口返回了明确不属于当前视频的字幕轨")
                    failure = ForeignSubtitleError()
                elif failure is None:
                    failure = UnverifiedSubtitleError()

        if failure is not None:
            raise failure
        return []

    def invalidate_subtitle_cache(self, bvid: str | None, aid: int | None,
                                  cid: int) -> None:
        """Discard cached subtitle URLs so a later request gets fresh signatures."""
        key = hashlib.sha256(
            f"{bvid or ''}:{aid or ''}:{cid}".encode("utf-8")
        ).hexdigest()[:24]
        (CACHE_DIR / f"{key}_list.json").unlink(missing_ok=True)

    def _get_modern_subtitle_list(self, bvid: str, aid: int | None,
                                  cid: int) -> list[dict]:
        if not aid:
            return []
        response = self.client.get(
            "https://api.bilibili.com/x/v2/subtitle/web/view",
            params={"oid": cid, "pid": aid,
                    "context_ext": '{"video_type":1}', "type": 1,
                    "cur_production_type": 0, "playlist_switch": 0,
                    "arc_video_language": "cmn", "preferred_language": "ai-zh",
                    "web_location": 1315873},
            headers={"Referer": f"https://www.bilibili.com/video/{bvid}",
                     "Accept": "application/octet-stream"})
        response.raise_for_status()
        return parse_web_subtitle_response(response.content)

    def fetch_subtitle(self, subtitle_url: str, bvid: str, *,
                       cache_scope: tuple | None = None) -> list[dict]:
        """Low-level download; production exports must use fetch_track approval."""
        subtitle_url = normalize_subtitle_url(subtitle_url)
        # 缓存键用稳定资源 id(含 aid+cid 归属段), 不含时效 auth_key:
        # 既避免同轨反复下载, 又天然隔离不同视频
        rid = subtitle_resource_id(subtitle_url)
        cacheable = cache_scope is not None or re.fullmatch(r"[0-9a-fA-F]{32}", rid) is None
        parsed = urlsplit(subtitle_url)
        stable_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        h = (hashlib.sha256(json.dumps([*cache_scope, stable_url]).encode()).hexdigest()[:24]
             if cache_scope is not None else hashlib.md5(rid.encode()).hexdigest()[:16])
        cf = CACHE_DIR / f"{h}_body.json"
        if cacheable and _cache_fresh(cf):
            try:
                cached = json.loads(cf.read_text(encoding="utf-8"))
                if cached:
                    return cached
                # 旧版本可能已经把临时空正文写入缓存; 读到后立即清除,
                # 避免升级后仍被 24 小时空缓存锁死。
                cf.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                pass
        # 注意: aisubtitle 字幕 JSON 没有 api.bilibili.com 的 {code,data} 包裹,
        # 不能走 _get_json, 需直接解析
        referer = f"https://www.bilibili.com/video/{bvid}"
        body: list[dict] = []
        for attempt in range(TRANSIENT_RETRIES + 1):
            try:
                r = self.client.get(subtitle_url, headers={"Referer": referer})
                if r.status_code == 403:
                    # 403 多为会话关联过期: 重新预热视频页再试, 而非原样重放
                    if bvid:
                        self.prime_video_page(bvid, None, force=True)
                    if attempt < TRANSIENT_RETRIES:
                        time.sleep(0.5 * (2 ** attempt))
                        continue
                    raise BiliError(-1, "字幕正文访问失败，请刷新字幕后重试。",
                                    reason="subtitle_signature_expired")
                r.raise_for_status()
                body = r.json().get("body", []) or []
                if body:
                    break
                # 200 + 空正文可能是字幕 CDN 的临时空响应; 不把它缓存,
                # 也给本次调用一个机会等到正文恢复。
                if attempt < TRANSIENT_RETRIES:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                break
            except (httpx.HTTPError, ValueError) as exc:
                self._reconnect()
                if attempt == TRANSIENT_RETRIES:
                    raise BiliError(-1, "字幕正文接口请求失败，请稍后重试。",
                                    reason=_request_failure_reason(exc)) from None
                if attempt < TRANSIENT_RETRIES:
                    time.sleep(0.5 * (2 ** attempt))
        # 只有实际拿到正文才缓存; 空响应必须允许下一次提取重新请求。
        if body and cacheable:
            _atomic_json_write(cf, body)
        return body

    def nav(self) -> dict:
        """三态返回: 已登录 / 未登录(isLogin=False) / 校验失败(isLogin=None,
        多为断网或被限流, 不应误报成未登录)。"""
        try:
            d = self._get_json("https://api.bilibili.com/x/web-interface/nav")
        except BiliError as e:
            if e.code in AUTH_ERRORS:
                return {"isLogin": False}
            return {"isLogin": None, "error": e.message}
        return {"isLogin": bool(d.get("isLogin")), "uname": d.get("uname", ""),
                "mid": d.get("mid", 0)}

    def cookies_dict(self) -> dict:
        return {c.name: c.value for c in self.client.cookies.jar}

    def polite_sleep(self):
        """批量请求间的礼貌限速。"""
        time.sleep(0.5 + random.random() * 0.5)


class QRLogin:
    """Web 扫码登录: 必须在同一个 client 上 generate -> poll。"""

    def __init__(self):
        self.client = httpx.Client(
            headers={"User-Agent": UA, "Accept": "application/json, text/plain, */*"},
            follow_redirects=True,
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT,
                                   write=CONNECT_TIMEOUT, pool=CONNECT_TIMEOUT))
        self.key: str | None = None

    def close(self):
        self.client.close()

    def generate(self) -> tuple[str, str]:
        """返回 (qrcode_key, 二维码 PNG data URL)。"""
        r = self.client.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
            params={"web_location": "333.1331"},
            headers={"Referer": "https://passport.bilibili.com/login/h5-adapted"})
        d = r.json()["data"]
        self.key = d["qrcode_key"]
        url = d["url"]
        img = qrcode.QRCode(box_size=10, border=2)
        img.add_data(url)
        img.make(fit=True)
        buf = io.BytesIO()
        img.make_image().save(buf, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return self.key, data_url

    def poll(self) -> dict:
        """轮询一次。state: waiting / scanned / success / expired。"""
        r = self.client.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": self.key},
            headers={"Referer": "https://passport.bilibili.com/login/h5-adapted"})
        data = r.json()["data"]
        code = data.get("code")
        if code == 0:
            jar = self.client.cookies.jar
            sess = next((c for c in jar if c.name == "SESSDATA"), None)
            return {"state": "success",
                    "cookies": {c.name: c.value for c in jar},
                    "expires": sess.expires if sess else None}
        if code == 86090:
            return {"state": "scanned"}
        if code == 86038:
            return {"state": "expired"}
        return {"state": "waiting"}
