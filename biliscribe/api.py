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
from pathlib import Path

import httpx
import qrcode

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
CACHE_TTL = 24 * 3600  # 字幕缓存 24 小时, 避免重复请求触发频控
CONNECT_TIMEOUT = 8.0
READ_TIMEOUT = 12.0
TRANSIENT_RETRIES = 2

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


class BiliError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def subtitle_resource_id(subtitle_url: str) -> str:
    """从字幕 URL 中提取 /prod/ 后的资源 id(去掉 auth_key 等 query)。"""
    tail = subtitle_url.split("/prod/")[-1]
    return tail.split("?")[0]


def track_belongs_to_video(track: dict, aid: int | None, cid: int) -> bool:
    """校验一条字幕轨是否确实属于当前视频。

    B 站 player/v2 会概率性返回*其他视频*的字幕轨(2026-09 实测: bvid 形式
    约 1/6 概率, aid 形式几乎必现)。AI 中文字幕资源 id 形如 "{aid}{cid}{hex}",
    可据此校验归属; 英文轨多为 32 位纯 hex(无数字归属段), 无法校验时放行。
    """
    rid = subtitle_resource_id(track.get("subtitle_url", ""))
    if not rid:
        return False
    if aid:
        prefix = f"{aid}{cid}"
        head = rid[: len(prefix)]
        if head.isdigit():
            # 带数字归属段的资源 id: 必须严格等于本视频 aid+cid
            return head == prefix
        # 归属段位置出现字母 => 纯 hex 资源 id 方案(常见英文轨), 无归属段可校验
        return True
    return True


# 链接末尾容易被一起复制进来的中英文标点(成对括号的右半等)
_URL_TAIL_PUNCT = ".,;:!?，。；：！？、）)】」』》>\"'’”…·"


def extract_urls(text: str) -> list[str]:
    """从一段文本(支持换行/逗号分隔多个链接)中提取 B 站视频 URL。"""
    # 中文句读/成对右括号直接视为链接边界, 避免尾随标点污染短链
    urls = re.findall(r"https?://[^\s,，;；。！？!?、）)】」』》\"'’”…]+", text)
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
                d = r.json()
                code = d.get("code", 0)
                if code in RATE_ERRORS:
                    wait = 0.6 * (2 ** attempt) + random.random() * 0.25
                    time.sleep(wait)
                    continue
                if code != 0:
                    raise BiliError(code, d.get("message", "未知错误"))
                return d.get("data", {})
            except (httpx.HTTPError, ValueError) as e:
                last_exc = e
                if isinstance(e, httpx.HTTPError):
                    self._reconnect()
                if attempt < min(retries, TRANSIENT_RETRIES):
                    time.sleep(0.5 * (2 ** attempt))
        raise BiliError(-1, f"请求失败: {last_exc}")

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
        d = self._get_json(f"https://api.bilibili.com/x/web-interface/view?{key}")
        return {
            "bvid": d["bvid"], "aid": d["aid"], "cid": d["cid"],
            "title": d["title"], "owner": d["owner"]["name"],
            "duration": d["duration"], "tname": d.get("tname", ""),
            "pic": d.get("pic", ""),
            "pages": [{"page": p["page"], "cid": p["cid"], "part": p["part"],
                       "duration": p["duration"]} for p in d["pages"]],
        }

    def get_subtitle_list(self, bvid: str | None, aid: int | None, cid: int,
                          on_log=None) -> list[dict]:
        """字幕列表对高频请求敏感(实测频控时会随机返回 0/1/2 条), 且 player/v2
        会概率性返回*其他视频*的字幕轨。策略:
        1. 只用 bvid 形式查询(aid 形式实测几乎必然串到其他视频, av 号链接在
           view 接口之后也能拿到规范 bvid);
        2. 每轮按资源 id 做归属校验, 整轮全部轨通过才采用, 否则退避重试;
        3. 仅把校验通过的结果落盘缓存, 读到历史脏缓存自动丢弃重拉。"""
        def log(m):
            if on_log:
                on_log(m)

        cf = CACHE_DIR / f"{cid}_list.json"
        if cf.exists() and time.time() - cf.stat().st_mtime < CACHE_TTL:
            try:
                cached = json.loads(cf.read_text(encoding="utf-8"))
                if cached and all(track_belongs_to_video(t, aid, cid) for t in cached):
                    return cached
                log("本地字幕缓存校验未通过(疑似其他视频数据), 重新拉取")
                cf.unlink()
            except (json.JSONDecodeError, OSError):
                pass

        if not bvid:
            # view 接口对 av 号也会返回规范 bvid; 没有 bvid 就无法安全查询
            raise BiliError(-1, "缺少 bvid, 无法安全获取字幕列表")

        # 实测: 必须先打开视频页完成会话关联, 否则字幕列表会被裁剪
        self.prime_video_page(bvid, aid)

        query = f"bvid={bvid}&cid={cid}"
        # 限流空列表 / 串轨都靠退避重试解决; 每轮重新预热视频页
        waits = [0, 1, 2]
        partial: dict[str, dict] = {}
        saw_foreign_track = False
        for rnd, w in enumerate(waits):
            if w:
                time.sleep(w)
                self.prime_video_page(bvid, aid, force=True)
            # 实测: 附加 web_location 参数会使字幕列表被裁剪, 用最简查询
            d = self._get_json(
                f"https://api.bilibili.com/x/player/v2?{query}",
                referer=f"https://www.bilibili.com/video/{bvid}")
            subs = d.get("subtitle", {}).get("subtitles", []) or []
            if not subs:
                continue
            good = [t for t in subs if track_belongs_to_video(t, aid, cid)]
            bad = len(subs) - len(good)
            if bad:
                saw_foreign_track = True
                log(f"字幕接口返回了其他视频的字幕轨, 正在重试"
                    f"({rnd + 1}/{len(waits)})")
            for t in good:
                partial.setdefault(t.get("lan", str(len(partial))), t)
            if good and bad == 0:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cf.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
                return good

        if partial:
            # 多轮中至少有部分轨(如中文轨)通过校验: 用并集兜底, 不丢可用结果
            log("部分字幕轨多次重试仍未通过归属校验, 仅导出校验通过的轨")
            merged = list(partial.values())
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
            return merged
        if not saw_foreign_track:
            return []
        raise BiliError(-1, "字幕接口多次返回其他视频的数据, 请稍后重试")

    def fetch_subtitle(self, subtitle_url: str, bvid: str) -> list[dict]:
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url
        # 缓存键用稳定资源 id(含 aid+cid 归属段), 不含时效 auth_key:
        # 既避免同轨反复下载, 又天然隔离不同视频
        rid = subtitle_resource_id(subtitle_url)
        h = hashlib.md5(rid.encode()).hexdigest()[:16]
        cf = CACHE_DIR / f"{h}_body.json"
        if cf.exists() and time.time() - cf.stat().st_mtime < CACHE_TTL:
            try:
                return json.loads(cf.read_text(encoding="utf-8"))
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
                r.raise_for_status()
                body = r.json().get("body", []) or []
                break
            except (httpx.HTTPError, ValueError):
                self._reconnect()
                if attempt == 2:
                    body = []
                if attempt < TRANSIENT_RETRIES:
                    time.sleep(0.5 * (2 ** attempt))
        if body:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cf.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
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
