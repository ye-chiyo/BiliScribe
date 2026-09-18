# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biliscribe.api import extract_urls, parse_video_url
from biliscribe.service import _pick_tracks, parse_pages_spec
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
    # 指定语言不存在时退回全部
    assert len(_pick_tracks(tracks, ["ja"])) == 4


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
