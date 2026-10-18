# -*- coding: utf-8 -*-
"""回归: B 站 player/v2 概率返回其他视频字幕轨的归属校验与重试。
纯离线, 可直接 `python tests/test_subtitle_guard.py` 运行。"""
import json
import hashlib
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import biliscribe.api as api
from biliscribe.api import BiliClient, BiliError, track_belongs_to_video

AID = 117237407029445
CID = 41706587832
BVID = "BV1V3Yn6wENr"
ZH_GOOD = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/" + f"{AID}{CID}" + "e16daccabd23056845eff8887d2793e0?auth_key=aaa"
ZH_BAD = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337329541521726472939880a6877ed40000000000000000000000?auth_key=bbb"
EN_GOOD = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/3edf4c2f0f19b96ccd9259f653d571a7?auth_key=ccc"
ZH_FOREIGN_OPAQUE = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/160542018915680499092528a97cdc69a1be6c94eaf5e5021780?auth_key=ddd"


def t(url, lan="ai-zh", doc="中文"):
    return {"lan": lan, "lan_doc": doc, "subtitle_url": url}


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


def test_chinese_track_with_mismatched_numeric_prefix_is_rejected():
    # 中文 AI 轨通常编码 aid+cid; 长数字前缀但不匹配时是串轨, 不能放行。
    assert track_belongs_to_video(t(ZH_FOREIGN_OPAQUE), AID, CID) is False


# ---------- get_subtitle_list 重试/缓存 ----------

class _Harness:
    def __init__(self, responses):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_cache = api.CACHE_DIR
        api.CACHE_DIR = Path(self.tmp.name)
        self.client = BiliClient(setup_fingerprint=False)
        # 断网隔离: 预热与 sleep 全部空转
        self.client.prime_video_page = lambda *a, **k: None
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


def test_retry_until_owned():
    h = _Harness([
        v2_data(t(ZH_BAD)),                                   # 第 1 轮: 串到其他视频
        v2_data(t(ZH_GOOD), t(EN_GOOD, "ai-en", "English")),  # 第 2 轮: 正确
    ])
    try:
        logs = []
        tracks = h.client.get_subtitle_list(BVID, AID, CID, on_log=logs.append)
        assert {x["subtitle_url"] for x in tracks} == {ZH_GOOD, EN_GOOD}
        assert len(h.calls) == 2
        # 只允许 bvid 形式, 不得回退 aid 形式
        assert all("aid=" not in u for u in h.calls), h.calls
        assert all("_ts=" in u for u in h.calls), h.calls
        assert any("其他视频" in m for m in logs)
        # 缓存落盘且为校验通过的结果
        cache_key = hashlib.sha256(f"{BVID}:{AID}:{CID}".encode()).hexdigest()[:24]
        cf = Path(h.tmp.name) / f"{cache_key}_list.json"
        assert cf.exists()
        cached = json.loads(cf.read_text(encoding="utf-8"))
        assert all(track_belongs_to_video(x, AID, CID) for x in cached)
    finally:
        h.close()
    print("[PASS] 串轨后自动重试, 拿到正确字幕且只缓存正确结果")


def test_all_rounds_bad_raises():
    h = _Harness([v2_data(t(ZH_BAD))] * 3)
    try:
        raised = False
        try:
            h.client.get_subtitle_list(BVID, AID, CID)
        except BiliError as e:
            raised = True
            assert "其他视频" in e.message
        assert raised
        assert len(h.calls) == 3
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


if __name__ == "__main__":
    test_track_owner_check()
    test_retry_until_owned()
    test_all_rounds_bad_raises()
    test_dirty_cache_discarded()
    test_empty_then_success()
    test_all_rounds_empty_is_no_subtitle()
    test_no_bvid_raises()
    test_body_cache_key_is_stable_rid()
    print("\n字幕归属校验回归全部通过")
