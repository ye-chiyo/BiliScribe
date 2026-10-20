# -*- coding: utf-8 -*-
"""Deterministic mock benchmark for cache and request behavior.

This is intentionally a small stdlib script so it can run even when pytest is
not installed: ``python tests/benchmark_mock.py``.
"""
import time
from collections import Counter
import sys
import tempfile
import re
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import biliscribe.api as api
from biliscribe.api import BiliClient


def run(videos=10, pages=5, cache_hit=False):
    old_cache = api.CACHE_DIR
    cache_dir = tempfile.TemporaryDirectory(
        dir=Path(__file__).resolve().parent.parent / ".tmp")
    api.CACHE_DIR = Path(cache_dir.name)
    client = BiliClient(setup_fingerprint=False)
    client.prime_video_page = lambda *args, **kwargs: None
    client._get_modern_subtitle_list = lambda *args, **kwargs: []
    calls = Counter()

    def fake_json(url, **kwargs):
        calls["list"] += 1
        cid = int(re.search(r"cid=(\d+)", url).group(1))
        aid = int(re.search(r"BV(\d+)", url).group(1)) + 1 if "BV" in url else 1
        return {"subtitle": {"subtitles": [
            {"lan": lan, "subtitle_url":
             f"//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{aid}{cid}{suffix}"}
            for lan, suffix in (("ai-zh", "zhabcdef"), ("ai-en", "enabcdef"),
                                ("ai-es", "esabcdef"))]}}

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            calls["body"] += 1
            return {"body": [{"from": 0, "to": 1, "content": "mock"}]}

    client._get_json = fake_json
    client.client.get = lambda *args, **kwargs: Response()
    started = time.perf_counter()
    def pass_once():
        for video in range(videos):
            bvid = f"BV{video:010d}"
            for page in range(pages):
                cid = video * 100 + page
                client.get_subtitle_list(bvid, video + 1, cid)
                for suffix in ("zhabcdef", "enabcdef", "esabcdef"):
                    client.fetch_subtitle(
                        f"//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{video + 1}{cid}{suffix}",
                        bvid)
    with patch.object(time, "sleep"):
        if cache_hit:
            pass_once()
            calls.clear()
        pass_once()
    elapsed = time.perf_counter() - started
    client.close()
    api.CACHE_DIR = old_cache
    cache_dir.cleanup()
    requests = dict(calls)
    return {"videos": videos, "pages_per_video": pages, "languages_per_page": 3,
            "cache_hit": cache_hit, "elapsed_ms": round(elapsed * 1000, 2),
            "request_count": sum(requests.values()), "requests": requests,
            "retries": 0, "cache_hit_rate": 1.0 if cache_hit else 0.0,
            "concurrency": 1, "queue": 0, "errors": 0}


if __name__ == "__main__":
    for hit in (False, True):
        for pages in (1, 5):
            print(run(pages=pages, cache_hit=hit))

