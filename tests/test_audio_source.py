# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from biliscribe.audio import BilibiliAudioSource


class FakeYDL:
    def __init__(self, options, calls):
        self.options = options
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, url, download):
        self.calls.append((url, download, self.options))
        target = Path(self.options["outtmpl"].replace("%(ext)s", "m4a"))
        target.write_bytes(b"downloaded audio")
        for hook in self.options["progress_hooks"]:
            hook({"status": "finished", "downloaded_bytes": target.stat().st_size})
        return {"id": "BV123_p2", "webpage_url": url,
                "duration": 30}


def test_bilibili_audio_is_downloaded_converted_and_cleaned(tmp_path, monkeypatch):
    import biliscribe.audio as audio

    calls = []
    created_wavs = []

    def fake_run(command, **kwargs):
        wav_path = Path(command[-1])
        wav_path.write_bytes(b"16khz mono wav")
        created_wavs.append(wav_path)

    monkeypatch.setattr(audio.shutil, "which", lambda name: name)
    monkeypatch.setattr(audio.subprocess, "run", fake_run)
    source = BilibiliAudioSource(
        cookies={"SESSDATA": "secret", "buvid3": "device"},
        max_audio_mb=1,
        max_duration_seconds=60,
        temp_dir=tmp_path,
        ydl_factory=lambda options: FakeYDL(options, calls),
    )

    with source.open_audio(bvid="BV123", aid=456, page={"page": 2, "duration": 30}) as wav:
        assert wav.is_file()
        assert wav.suffix == ".wav"
        assert wav.read_bytes() == b"16khz mono wav"
        assert calls[0][0] == "https://www.bilibili.com/video/BV123/?p=2"
        options = calls[0][2]
        assert options["format"] == "bestaudio/best"
        assert options["noplaylist"] is True
        cookie_path = Path(options["cookiefile"])
        assert "SESSDATA\tsecret" in cookie_path.read_text(encoding="utf-8")
        assert "buvid3\tdevice" in cookie_path.read_text(encoding="utf-8")
        temp_path = wav.parent

    assert not temp_path.exists()
    assert not cookie_path.exists()
    assert not created_wavs[0].exists()


def test_wrong_video_download_is_rejected(tmp_path):
    class WrongYDL(FakeYDL):
        def extract_info(self, url, download):
            return {"id": "BV999_p2", "webpage_url": url, "duration": 30}

    source = BilibiliAudioSource(
        cookies={}, temp_dir=tmp_path,
        ydl_factory=lambda options: WrongYDL(options, []),
    )
    with pytest.raises(RuntimeError, match="归属校验失败"):
        with source.open_audio(bvid="BV123", aid=456,
                               page={"page": 2, "duration": 30}):
            pass
    assert list(tmp_path.iterdir()) == []


def test_duration_limit_rejects_before_starting_download(tmp_path):
    calls = []
    source = BilibiliAudioSource(
        cookies={}, max_duration_seconds=30, temp_dir=tmp_path,
        ydl_factory=lambda options: calls.append(options),
    )

    with pytest.raises(ValueError, match="时长超过限制"):
        with source.open_audio(bvid="BV123", aid=456,
                               page={"page": 1, "duration": 31}):
            pass

    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_download_size_limit_cleans_temporary_files(tmp_path, monkeypatch):
    import biliscribe.audio as audio

    def oversized_ytdlp(options):
        class Oversized(FakeYDL):
            def extract_info(self, url, download):
                target = Path(self.options["outtmpl"].replace("%(ext)s", "m4a"))
                target.write_bytes(b"0123456789")
                hook = self.options["progress_hooks"][0]
                hook({"status": "downloading", "downloaded_bytes": 10})

        return Oversized(options, [])

    monkeypatch.setattr(audio.shutil, "which", lambda name: name)
    source = BilibiliAudioSource(
        cookies={}, max_audio_mb=0.000005, max_duration_seconds=60,
        temp_dir=tmp_path, ydl_factory=oversized_ytdlp,
    )

    with pytest.raises(ValueError, match="下载音频超过大小限制"):
        with source.open_audio(bvid="BV123", aid=456,
                               page={"page": 1, "duration": 30}):
            pass

    assert list(tmp_path.iterdir()) == []
