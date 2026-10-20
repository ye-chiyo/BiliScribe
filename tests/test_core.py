# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biliscribe.api import extract_urls, parse_video_url
from biliscribe.cli import build_parser
from biliscribe.service import _pick_tracks, extract_one, parse_pages_spec
from biliscribe.service import JobManager
from biliscribe.web.server import ExtractReq
from biliscribe.subtitles import (_hms, safe_filename, to_md, to_srt, to_txt,
                                  to_vtt)

BODY = [
    {"from": 1.21, "to": 3.85, "content": "你好"},
    {"from": 3.9, "to": 6.1, "content": "世界"},
]


def test_srt():
    s = to_srt(BODY)
    assert s.startswith("1\n00:00:01,210 --> 00:00:03,850\n你好")
    assert "2\n00:00:03,900 --> 00:00:06,100\n世界" in s


def test_vtt():
    assert to_vtt(BODY).startswith("WEBVTT")
    assert "00:00:01.210 --> 00:00:03.850" in to_vtt(BODY)


def test_txt():
    assert to_txt(BODY) == "你好\n世界"


def test_safe_filename():
    assert safe_filename('a/b:c*d?e"f<g>h|i') == "a_b_c_d_e_f_g_h_i"
    assert safe_filename("") == "untitled"


def test_parse_url():
    bvid, aid, page = parse_video_url(
        "https://www.bilibili.com/video/BV11gYd6UEPv/?p=3&x=1")
    assert bvid == "BV11gYd6UEPv" and page == 3
    bvid2, _, page2 = parse_video_url("https://b23.tv/abc123")
    assert bvid2 is None and page2 == 1  # 短链需 resolve 后再解析


def test_pages_spec():
    assert parse_pages_spec("all", 5) == [1, 2, 3, 4, 5]
    assert parse_pages_spec("1,3,5-7", 10) == [1, 3, 5, 6, 7]
    assert parse_pages_spec("8,99", 6) == []


def test_hms_carry():
    # 毫秒四舍五入到 1000 时必须进位, 不得出现 ,1000
    assert _hms(59.9996) == "00:01:00,000"
    assert _hms(3599.9995) == "01:00:00,000"
    assert _hms(1.21) == "00:00:01,210"
    assert _hms(1.21, False) == "00:00:01.210"
    assert _hms(-5) == "00:00:00,000"


def test_missing_fields_tolerant():
    # 单条缺 content / 时间字段不应 KeyError, 空文本条目跳过
    body = [{"from": 1, "to": 2}, {"from": 2, "to": 3, "content": "保留"}]
    s = to_srt(body)
    assert s == "1\n00:00:02,000 --> 00:00:03,000\n保留\n"
    assert to_vtt(body).startswith("WEBVTT")
    assert to_txt(body) == "保留"


def test_md_timestamp_hour():
    md = to_md([{"from": 3723, "to": 3725, "content": "长视频"}],
               {"title": "t"})
    assert "**[1:02:03]**" in md


def test_pick_tracks_lang_family():
    tracks = [{"lan": "ai-zh", "lan_doc": "中文"},
              {"lan": "zh-CN", "lan_doc": "中文(简体)"},
              {"lan": "ai-en", "lan_doc": "English"},
              {"lan": "ai-es", "lan_doc": "Español"}]
    picked = _pick_tracks(tracks, ["zh"])
    assert {t["lan"] for t in picked} == {"ai-zh", "zh-CN"}
    assert {t["lan"] for t in _pick_tracks(tracks, ["en"])} == {"ai-en"}
    assert len(_pick_tracks(tracks, ["all"])) == 4
    # 指定语言不存在时不得导出其他语言
    assert _pick_tracks(tracks, ["ja"]) == []
    assert _pick_tracks([{"lan": "cmn", "lan_doc": "中文"}], ["zh"])[0]["lan"] == "cmn"


def test_linked_page_and_language_filter():
    import tempfile

    class Client:
        def __init__(self):
            self.cids = []

        def resolve(self, url):
            return url

        def get_video_info(self, **kwargs):
            return {"bvid": "BV11gYd6UEPv", "aid": 123, "title": "测试",
                    "owner": "UP", "pages": [
                        {"page": 1, "cid": 101, "part": "一"},
                        {"page": 2, "cid": 102, "part": "二"}]}

        def get_subtitle_list(self, bvid, aid, cid, on_log=None):
            self.cids.append(cid)
            return [{"lan": "ai-en", "lan_doc": "English",
                     "subtitle_url": "https://example.com/subtitle"}]

        def fetch_subtitle(self, url, bvid):
            raise AssertionError("不应下载未选语言")

    with tempfile.TemporaryDirectory() as temp:
        client = Client()
        result = extract_one(client,
            "https://www.bilibili.com/video/BV11gYd6UEPv/?p=2",
            ["txt"], "all", ["zh"], Path(temp), lambda msg: None)
        assert client.cids == [102]
        assert result["status"] == "no_subtitle"
        assert result["no_subtitle_pages"] == [2]


def test_extract_writes_selected_language():
    import tempfile

    class Client:
        def resolve(self, url):
            return url

        def get_video_info(self, **kwargs):
            return {"bvid": "BV11gYd6UEPv", "aid": 123, "title": "测试",
                    "owner": "UP", "pages": [
                        {"page": 1, "cid": 101, "part": "正片"}]}

        def get_subtitle_list(self, bvid, aid, cid, on_log=None):
            return [{"lan": "ai-zh", "lan_doc": "中文",
                     "subtitle_url": "https://example.com/subtitle"}]

        def invalidate_subtitle_cache(self, bvid, aid, cid):
            pass

        def fetch_subtitle(self, url, bvid):
            return BODY

        def polite_sleep(self):
            pass

    with tempfile.TemporaryDirectory() as temp:
        result = extract_one(Client(),
            "https://www.bilibili.com/video/BV11gYd6UEPv/",
            ["srt", "txt"], "all", ["zh"], Path(temp), lambda msg: None)
        assert result["status"] == "ok"
        assert len(result["files"]) == 2
        srt = next(Path(f) for f in result["files"] if f.endswith(".srt"))
        assert srt.read_bytes().startswith(b"\xef\xbb\xbf")
        assert "你好" in srt.read_text(encoding="utf-8-sig")


def test_extract_reports_empty_body_separately():
    import tempfile

    class Client:
        def resolve(self, url):
            return url

        def get_video_info(self, **kwargs):
            return {"bvid": "BV11gYd6UEPv", "aid": 123, "title": "测试",
                    "owner": "UP", "pages": [
                        {"page": 1, "cid": 101, "part": "正片"}]}

        def get_subtitle_list(self, bvid, aid, cid, on_log=None, force_refresh=False):
            return [{"lan": "ai-zh", "lan_doc": "中文",
                     "subtitle_url": "https://example.com/subtitle"}]

        def invalidate_subtitle_cache(self, bvid, aid, cid):
            pass

        def fetch_subtitle(self, url, bvid):
            return []

        def polite_sleep(self):
            pass

    with tempfile.TemporaryDirectory() as temp:
        result = extract_one(Client(),
            "https://www.bilibili.com/video/BV11gYd6UEPv/",
            ["txt"], "all", ["zh"], Path(temp), lambda msg: None)
        assert result["status"] == "no_subtitle"
        assert result["no_subtitle_pages"] == []
        assert result["empty_body_pages"] == [1]
        assert "正文接口" in result["message"]


def test_extract_refreshes_tracks_after_empty_body():
    import tempfile

    class Client:
        def __init__(self):
            self.refreshed = False
            self.fetches = []

        def resolve(self, url):
            return url

        def get_video_info(self, **kwargs):
            return {"bvid": "BV11gYd6UEPv", "aid": 123, "title": "测试",
                    "owner": "UP", "pages": [
                        {"page": 1, "cid": 101, "part": "正片"}]}

        def get_subtitle_list(self, bvid, aid, cid, on_log=None, force_refresh=False):
            url = "https://example.com/new-subtitle" if self.refreshed else "https://example.com/expired-subtitle"
            return [{"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": url}]

        def invalidate_subtitle_cache(self, bvid, aid, cid):
            self.refreshed = True

        def fetch_subtitle(self, url, bvid):
            self.fetches.append(url)
            return BODY if self.refreshed else []

        def polite_sleep(self):
            pass

    with tempfile.TemporaryDirectory() as temp:
        client = Client()
        result = extract_one(client,
            "https://www.bilibili.com/video/BV11gYd6UEPv/",
            ["txt"], "all", ["zh"], Path(temp), lambda msg: None)
        assert result["status"] == "ok"
        assert len(result["files"]) == 1
        assert client.fetches == ["https://example.com/expired-subtitle",
                                  "https://example.com/new-subtitle"]


def test_default_markdown_chinese():
    import tempfile

    args = build_parser().parse_args(["extract", "https://www.bilibili.com/video/BV11gYd6UEPv/"])
    assert args.fmts == "md" and args.langs == "zh"
    req = ExtractReq(urls="https://www.bilibili.com/video/BV11gYd6UEPv/")
    assert req.fmts == ["md"] and req.langs == ["zh"]

    with tempfile.TemporaryDirectory() as temp:
        mgr = JobManager()
        mgr._ensure_worker = lambda: None
        mgr.create(req.urls, out_dir=temp)
        queued = mgr._q.get_nowait()
        assert queued[2] == ["md"] and queued[4] == ["zh"]


def test_expired_credentials_are_reported():
    import tempfile
    import time
    import biliscribe.auth as auth
    old_file = auth.CRED_FILE
    with tempfile.TemporaryDirectory() as temp:
        auth.CRED_FILE = Path(temp) / "cookies.json"
        auth.CRED_FILE.write_text('{"cookies": {}, "uname": "old", "expires": 1}', encoding="utf-8")
        status = auth.login_status()
        assert status["expired"] is True
        assert status["logged_in"] is False
    auth.CRED_FILE = old_file


def test_pages_spec_garbage():
    assert parse_pages_spec("1,a,3,xx", 5) == [1, 3]
    assert parse_pages_spec("a-b,2", 5) == [2]


def test_extract_urls_tail_punct():
    text = "看看这个 https://b23.tv/abc123。还有 https://www.bilibili.com/video/BV11gYd6UEPv/）"
    urls = extract_urls(text)
    assert urls == ["https://b23.tv/abc123",
                    "https://www.bilibili.com/video/BV11gYd6UEPv/"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
    print("all tests passed")
