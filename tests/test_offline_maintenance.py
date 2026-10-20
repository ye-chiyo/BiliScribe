# -*- coding: utf-8 -*-
"""Dependency-light regression tests for maintenance work.

Run with ``python -m unittest tests.test_offline_maintenance``.  The suite
uses only stdlib mocks and never contacts Bilibili.
"""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import biliscribe.api as api
from biliscribe.api import BiliClient, BiliError, track_belongs_to_video
from biliscribe.service import _pick_tracks
from biliscribe.subtitles import _hms, safe_filename


GOOD = "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/123456abc?auth_key=one"


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return {"body": self.body}


class OfflineMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent.parent / ".tmp")
        self.old_cache = api.CACHE_DIR
        api.CACHE_DIR = Path(self.tmp.name)
        self.client = BiliClient(setup_fingerprint=False)
        self.client.prime_video_page = lambda *args, **kwargs: None

    def tearDown(self):
        self.client.close()
        api.CACHE_DIR = self.old_cache
        self.tmp.cleanup()

    def test_video_info_is_deduplicated_for_bvid_and_aid(self):
        calls = []

        def fake(url, **kwargs):
            calls.append(url)
            return {"bvid": "BV1234567890", "aid": 123, "cid": 456,
                    "title": "t", "owner": {"name": "u"}, "duration": 1,
                    "pages": [{"page": 1, "cid": 456, "part": "p", "duration": 1}]}

        self.client._get_json = fake
        self.client.get_video_info(bvid="BV1234567890")
        self.client.get_video_info(aid=123)
        self.assertEqual(len(calls), 1)

    def test_subtitle_cache_is_video_scoped_and_deduplicated(self):
        good = {"lan": "ai-zh", "subtitle_url": GOOD}
        self.client._get_json = lambda *args, **kwargs: {
            "subtitle": {"subtitles": [good, good]}}
        with patch.object(time, "sleep"):
            tracks = self.client.get_subtitle_list("BV1234567890", 123, 456)
        self.assertEqual(len(tracks), 1)
        files = list(Path(self.tmp.name).glob("*_list.json"))
        self.assertTrue(any("1234567890" not in f.name for f in files))
        # A different bvid must not read the first video's scoped cache.
        self.client._get_json = lambda *args, **kwargs: {"subtitle": {"subtitles": []}}
        with patch.object(time, "sleep"):
            self.assertEqual(self.client.get_subtitle_list("BV9999999999", 123, 456), [])

    def test_empty_body_is_not_cached_and_untrusted_host_rejected(self):
        count = {"n": 0}
        bodies = [[], [{"from": 1, "to": 2, "content": "稍后可用"}]]

        def fake_get(*args, **kwargs):
            count["n"] += 1
            return FakeResponse(bodies.pop(0))

        self.client.client.get = fake_get
        expected = [{"from": 1, "to": 2, "content": "稍后可用"}]
        self.assertEqual(self.client.fetch_subtitle(GOOD, "BV1234567890"), expected)
        self.assertEqual(self.client.fetch_subtitle(GOOD.replace("auth_key=one", "auth_key=two"), "BV1234567890"), expected)
        self.assertEqual(count["n"], 2)
        with self.assertRaises(BiliError):
            self.client.fetch_subtitle("https://evil.example/prod/123", "BV1234567890")

    def test_stale_empty_body_cache_is_discarded(self):
        rid = api.subtitle_resource_id(GOOD)
        cache = Path(self.tmp.name) / f"{hashlib.md5(rid.encode()).hexdigest()[:16]}_body.json"
        cache.write_text("[]", encoding="utf-8")
        calls = {"n": 0}

        def fake_get(*args, **kwargs):
            calls["n"] += 1
            return FakeResponse([{"from": 1, "to": 2, "content": "缓存已清除"}])

        self.client.client.get = fake_get
        self.assertEqual(self.client.fetch_subtitle(GOOD, "BV1234567890")[0]["content"], "缓存已清除")
        self.assertEqual(calls["n"], 1)

    def test_shared_language_and_safety_rules(self):
        tracks = [{"lan": "ai-zh"}, {"lan": "en-US"}, {"lan": "ai-es"}]
        self.assertEqual([x["lan"] for x in _pick_tracks(tracks, ["zh"])], ["ai-zh"])
        self.assertEqual(_hms(59.9996), "00:01:00,000")
        self.assertEqual(safe_filename("../secret:video"), "_secret_video")


if __name__ == "__main__":
    unittest.main()

