# -*- coding: utf-8 -*-
"""回归: B 站 player/v2 概率返回其他视频字幕轨的归属校验与重试。
纯离线, 可直接 `python tests/test_subtitle_guard.py` 运行。"""
import json
import hashlib
import sys
import tempfile
import time
from urllib.parse import quote_from_bytes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import biliscribe.api as api
from biliscribe.api import (BiliClient, BiliError, ForeignSubtitleError,
                            parse_web_subtitle_response,
                            track_belongs_to_video)

AID = 117237407029445
CID = 41706587832
BVID = "BV1V3Yn6wENr"
ZH_GOOD = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/" + f"{AID}{CID}" + "e16daccabd23056845eff8887d2793e0?auth_key=aaa"
ZH_BAD = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337329541521726472939880a6877ed40000000000000000000000?auth_key=bbb"
EN_GOOD = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/3edf4c2f0f19b96ccd9259f653d571a7?auth_key=ccc"
ZH_FOREIGN_OPAQUE = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/160542018915680499092528a97cdc69a1be6c94eaf5e5021780?auth_key=ddd"


def t(url, lan="ai-zh", doc="中文"):
    return {"lan": lan, "lan_doc": doc, "subtitle_url": url}


def encoded_v2_url(path, auth_key="test-signature", key_index=0):
    prefix, key = api._SUBTITLE_V2_KEYS[key_index]
    plain = (prefix + path).encode("utf-8")
    cipher = bytes(byte ^ ord(key[index % len(key)])
                   for index, byte in enumerate(plain))
    return "//subtitle.bilibili.com/" + quote_from_bytes(cipher, safe="") + \
        "?auth_key=" + auth_key


# ---------- 归属判定 ----------

def test_track_owner_check():
    assert track_belongs_to_video(t(ZH_GOOD), AID, CID) is True
    assert track_belongs_to_video(t(ZH_BAD), AID, CID) is False
    # 英文轨纯 hex 资源 id(归属段位置含字母), 无法校验时放行
    assert track_belongs_to_video(t(EN_GOOD, "ai-en", "English"), AID, CID) is True
    # 空 URL 拒绝
    assert track_belongs_to_video(t(""), AID, CID) is False
    # 无 aid 无法校验, 放行(不应误杀)
    assert track_belongs_to_video(t(ZH_BAD), None, CID) is True
    assert track_belongs_to_video(
        {**t(EN_GOOD, "ai-en", "English"), "author_mid": 999},
        AID, CID, owner_mid=123) is False
    assert track_belongs_to_video(
        {**t(EN_GOOD, "ai-en", "English"), "author_mid": 123},
        AID, CID, owner_mid=123) is True
    assert track_belongs_to_video(
        t(EN_GOOD, "ai-en", "English"), AID, CID, owner_mid=123) is False


def test_chinese_track_with_mismatched_numeric_prefix_is_rejected():
    # 中文 AI 轨通常编码 aid+cid; 长数字前缀但不匹配时是串轨, 不能放行。
    assert track_belongs_to_video(t(ZH_FOREIGN_OPAQUE), AID, CID) is False


# ---------- get_subtitle_list 重试/缓存 ----------

class _Harness:
    def __init__(self, responses):
        self.tmp = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent.parent / ".tmp")
        self._old_cache = api.CACHE_DIR
        api.CACHE_DIR = Path(self.tmp.name)
        self.client = BiliClient(setup_fingerprint=False)
        # 断网隔离: 预热与 sleep 全部空转
        self.client.prime_video_page = lambda *a, **k: None
        self.client._get_modern_subtitle_list = lambda *a, **k: []
        self._old_sleep = time.sleep
        time.sleep = lambda *a, **k: None
        self.responses = list(responses)
        self.calls = []
        self.client._get_json = self._fake

    def _fake(self, url, referer=None, retries=2):
        self.calls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        time.sleep = self._old_sleep
        self.client.close()
        api.CACHE_DIR = self._old_cache
        self.tmp.cleanup()


def v2_data(*tracks):
    return {"subtitle": {"subtitles": list(tracks)}}


def test_foreign_track_is_reported_immediately():
    h = _Harness([
        v2_data(t(ZH_BAD)),
    ])
    try:
        logs = []
        try:
            h.client.get_subtitle_list(BVID, AID, CID, on_log=logs.append)
        except ForeignSubtitleError:
            pass
        else:
            raise AssertionError("串轨字幕必须触发专用异常")
        assert len(h.calls) == 1
        # 只允许 bvid 形式, 不得回退 aid 形式
        assert all("aid=" not in u for u in h.calls), h.calls
        assert all("_ts=" in u for u in h.calls), h.calls
        assert len({u.split("_ts=", 1)[1] for u in h.calls}) == len(h.calls), h.calls
        assert any("其他视频" in m for m in logs)
        # 串轨数据不得落盘
        cache_key = hashlib.sha256(f"{BVID}:{AID}:{CID}".encode()).hexdigest()[:24]
        cf = Path(h.tmp.name) / f"{cache_key}_list.json"
        assert not cf.exists()
    finally:
        h.close()
    print("[PASS] 串轨数据被拒绝且不写入缓存")


def test_all_rounds_bad_raises():
    h = _Harness([v2_data(t(ZH_BAD))])
    try:
        raised = False
        try:
            h.client.get_subtitle_list(BVID, AID, CID)
        except BiliError as e:
            raised = True
            assert "其他视频" in e.message
        assert raised
        assert len(h.calls) == 1
        # 全部失败时不得写缓存
        cache_key = hashlib.sha256(f"{BVID}:{AID}:{CID}".encode()).hexdigest()[:24]
        assert not (Path(h.tmp.name) / f"{cache_key}_list.json").exists()
    finally:
        h.close()
    print("[PASS] 连续串轨时明确报错, 绝不导出错误字幕")


def test_dirty_cache_discarded():
    h = _Harness([v2_data(t(ZH_GOOD), t(EN_GOOD, "ai-en", "English"))])
    try:
        # 预置旧版本写入的脏缓存
        cache_key = hashlib.sha256(f"{BVID}:{AID}:{CID}".encode()).hexdigest()[:24]
        cf = Path(h.tmp.name) / f"{cache_key}_list.json"
        cf.write_text(json.dumps([t(ZH_BAD)]), encoding="utf-8")
        tracks = h.client.get_subtitle_list(BVID, AID, CID)
        assert {x["subtitle_url"] for x in tracks} == {ZH_GOOD, EN_GOOD}
        assert len(h.calls) == 1  # 不信任脏缓存, 重新请求
    finally:
        h.close()
    print("[PASS] 历史脏缓存被识别并丢弃")


def test_empty_then_success():
    h = _Harness([
        v2_data(),                                              # 限流空列表
        v2_data(t(ZH_GOOD), t(EN_GOOD, "ai-en", "English")),    # 退避后成功
    ])
    try:
        tracks = h.client.get_subtitle_list(BVID, AID, CID)
        assert len(tracks) == 2
    finally:
        h.close()
    print("[PASS] 空列表退避重试行为保持正常")


def _pb_varint(value):
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _pb_bytes(number, value):
    value = value.encode() if isinstance(value, str) else value
    return _pb_varint(number << 3 | 2) + _pb_varint(len(value)) + value


def _pb_varint_field(number, value):
    return _pb_varint(number << 3) + _pb_varint(value)


def test_modern_web_subtitle_protobuf_is_preferred():
    encoded_url = encoded_v2_url(
        f"/bfs/ai_subtitle/prod/{AID}{CID}fixture.json")
    item = (_pb_varint_field(1, 2005328430358528768) +
            _pb_bytes(2, "2005328430358528768") +
            _pb_bytes(3, "ai-zh") + _pb_bytes(4, "中文") +
            _pb_bytes(5, encoded_url))
    payload = _pb_bytes(1, _pb_bytes(3, item))
    tracks = parse_web_subtitle_response(payload)
    assert tracks == [{
        "id": 2005328430358528768, "id_str": "2005328430358528768",
        "lan": "ai-zh", "lan_doc": "中文",
        "subtitle_url": encoded_url,
    }]

    h = _Harness([])
    try:
        modern = tracks
        h.client._get_modern_subtitle_list = lambda *a, **k: modern
        h.client._get_json = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("现代接口已有字幕时不得调用旧接口"))
        result = h.client.get_subtitle_list(BVID, AID, CID)
        assert "_owner_key" not in result[0]
        assert track_belongs_to_video(result[0], AID, CID)
        foreign = dict(result[0], subtitle_url=ZH_BAD)
        assert not track_belongs_to_video(foreign, AID, CID)
    finally:
        h.close()
    print("[PASS] Protobuf 现代字幕接口优先且按资源 ID 校验归属")


def test_encoded_subtitle_url_is_decoded_before_download():
    h = _Harness([])
    try:
        url = encoded_v2_url(f"/bfs/ai_subtitle/prod/{AID}{CID}fixture.json")
        calls = []

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"body": [{"from": 0, "to": 1, "content": "mock"}]}

        def fake_get(request_url, headers=None):
            calls.append(request_url)
            return _Resp()

        h.client.client.get = fake_get
        assert api.normalize_subtitle_url(url).startswith(
            "https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/")
        assert h.client.fetch_subtitle(url, BVID)[0]["content"] == "mock"
        assert calls and calls[0].startswith("https://aisubtitle.hdslb.com/")
        assert "subtitle.bilibili.com" not in calls[0]
    finally:
        h.close()
    print("[PASS] 编码字幕地址按播放器规则解码后下载")


def test_second_rotating_subtitle_key_matches_player_sample():
    # Public player samples beginning with %01%1B%5C= use the second key pair.
    url = encoded_v2_url("/bfs/ai_subtitle/prod/fixture.json", key_index=1)
    assert url.startswith("//subtitle.bilibili.com/%01%1B%5C")
    assert api.normalize_subtitle_url(url) == \
        "https://aisubtitle.hdslb.com/bfs/ai_subtitle/prod/fixture.json?auth_key=test-signature"
    print("[PASS] 第二组轮换密钥兼容播放器样例")


def test_unknown_encoded_subtitle_url_is_rejected():
    try:
        api.normalize_subtitle_url("//subtitle.bilibili.com/%01opaque")
    except BiliError as exc:
        assert "无法解码" in exc.message
    else:
        raise AssertionError("未知编码字幕地址不得直接请求")
    print("[PASS] 未知编码字幕地址被拒绝")
def test_foreign_track_routes_to_asr_immediately():
    """确认串轨后立即转 ASR，不等待空字幕或继续导出。"""
    h = _Harness([v2_data(t(ZH_BAD))])
    try:
        try:
            h.client.get_subtitle_list(BVID, AID, CID)
        except ForeignSubtitleError:
            pass
        else:
            raise AssertionError("串轨字幕必须触发本地 ASR 路由")
        assert len(h.calls) == 1
    finally:
        h.close()
    print("[PASS] 确认串轨后立即转入本地 ASR")


def test_all_rounds_empty_is_no_subtitle():
    h = _Harness([v2_data()] * 3)
    try:
        assert h.client.get_subtitle_list(BVID, AID, CID) == []
        assert len(h.calls) == 3
    finally:
        h.close()
    print("[PASS] 持续空列表按无字幕处理, 不误报串轨")


def test_no_bvid_raises():
    h = _Harness([])
    try:
        raised = False
        try:
            h.client.get_subtitle_list(None, AID, CID)
        except BiliError:
            raised = True
        assert raised
    finally:
        h.close()
    print("[PASS] 缺少 bvid 时拒绝查询(避免 aid 形式串轨)")


def test_body_cache_key_is_stable_rid():
    """同轨 auth_key 刷新(完整 URL 变, 资源 id 不变)应命中同一 body 缓存。"""
    h = _Harness([])
    try:
        n = {"get": 0}

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"body": [{"from": 1, "to": 2, "content": "正确视频字幕"}]}

        def fake_get(url, headers=None):
            n["get"] += 1
            return _Resp()

        h.client.client.get = fake_get
        b1 = h.client.fetch_subtitle(ZH_GOOD, BVID)
        url2 = ZH_GOOD.replace("auth_key=aaa", "auth_key=zzz")
        b2 = h.client.fetch_subtitle(url2, BVID)
        assert b1 == b2 == [{"from": 1, "to": 2, "content": "正确视频字幕"}]
        assert n["get"] == 1, "auth_key 刷新后应命中缓存, 不应重复下载"
    finally:
        h.close()
    print("[PASS] body 缓存按稳定资源 id 命中, 不受 auth_key 刷新影响")


def test_empty_body_is_not_cached():
    """临时空正文不得把后续成功响应锁死为 24 小时空缓存。"""
    h = _Harness([])
    try:
        bodies = [[], [{"from": 1, "to": 2, "content": "稍后可用"}]]
        calls = {"get": 0}

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"body": bodies.pop(0)}

        def fake_get(url, headers=None):
            calls["get"] += 1
            return _Resp()

        h.client.client.get = fake_get
        assert h.client.fetch_subtitle(ZH_GOOD, BVID) == [{
            "from": 1, "to": 2, "content": "稍后可用"}]
        assert h.client.fetch_subtitle(ZH_GOOD, BVID) == [{
            "from": 1, "to": 2, "content": "稍后可用"}]
        assert calls["get"] == 2
    finally:
        h.close()
    print("[PASS] 临时空正文不写缓存, 后续成功响应可恢复")


def test_stale_empty_body_cache_is_discarded():
    """升级前留下的空正文缓存必须被丢弃并重新下载。"""
    h = _Harness([])
    try:
        rid = api.subtitle_resource_id(ZH_GOOD)
        cf = api.CACHE_DIR / f"{hashlib.md5(rid.encode()).hexdigest()[:16]}_body.json"
        cf.write_text("[]", encoding="utf-8")
        calls = {"get": 0}

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"body": [{"from": 1, "to": 2, "content": "缓存已清除"}]}

        def fake_get(url, headers=None):
            calls["get"] += 1
            return _Resp()

        h.client.client.get = fake_get
        assert h.client.fetch_subtitle(ZH_GOOD, BVID)[0]["content"] == "缓存已清除"
        assert calls["get"] == 1
    finally:
        h.close()
    print("[PASS] 历史空正文缓存被清除并重新下载")


if __name__ == "__main__":
    test_track_owner_check()
    test_foreign_track_is_reported_immediately()
    test_all_rounds_bad_raises()
    test_dirty_cache_discarded()
    test_empty_then_success()
    test_modern_web_subtitle_protobuf_is_preferred()
    test_encoded_subtitle_url_is_decoded_before_download()
    test_second_rotating_subtitle_key_matches_player_sample()
    test_unknown_encoded_subtitle_url_is_rejected()
    test_foreign_track_routes_to_asr_immediately()
    test_all_rounds_empty_is_no_subtitle()
    test_no_bvid_raises()
    test_body_cache_key_is_stable_rid()
    test_empty_body_is_not_cached()
    test_stale_empty_body_cache_is_discarded()
    print("\n字幕归属校验回归全部通过")
