# -*- coding: utf-8 -*-
"""Optional Bilibili audio download and temporary WAV preparation."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping
from urllib.parse import parse_qs, urlparse


class BilibiliAudioSource:
    """Download one Bilibili page and expose a temporary 16 kHz mono WAV."""

    def __init__(self, *, cookies: Mapping[str, str],
                 max_audio_mb: float = 512, max_duration_seconds: int = 7200,
                 temp_dir: Path | str | None = None,
                 ydl_factory: Callable | None = None):
        self.cookies = dict(cookies)
        self.max_audio_bytes = int(max_audio_mb * 1024 * 1024)
        self.max_duration_seconds = max_duration_seconds
        self.temp_dir = Path(temp_dir) if temp_dir else None
        self.ydl_factory = ydl_factory

    @contextmanager
    def open_audio(self, *, bvid: str, aid: int, page: Mapping) -> Iterator[Path]:
        del aid
        duration = float(page.get("duration") or 0)
        if duration <= 0:
            raise ValueError("无法确认分 P 时长，拒绝下载音频")
        if duration > self.max_duration_seconds:
            raise ValueError(f"音频时长超过限制 {self.max_duration_seconds} 秒")
        if self.max_audio_bytes <= 0:
            raise ValueError("音频大小限制必须大于 0")

        with tempfile.TemporaryDirectory(prefix="biliscribe-audio-",
                                         dir=self.temp_dir) as temp:
            work_dir = Path(temp)
            cookie_file = work_dir / "cookies.txt"
            cookie_file.write_text(self._netscape_cookies(), encoding="utf-8")
            audio_template = str(work_dir / "source.%(ext)s")
            options = {
                "format": "bestaudio/best",
                "outtmpl": audio_template,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "cookiefile": str(cookie_file),
                "http_headers": {"Referer": f"https://www.bilibili.com/video/{bvid}/"},
                "max_filesize": self.max_audio_bytes,
                "progress_hooks": [self._progress_hook],
            }
            factory = self.ydl_factory or self._default_ydl_factory
            try:
                with factory(options) as downloader:
                    download_info = downloader.extract_info(
                        f"https://www.bilibili.com/video/{bvid}/?p={int(page['page'])}",
                        download=True,
                    )
                self._validate_download_info(download_info, bvid=bvid, page=page)
            except ValueError:
                raise
            except Exception as exc:
                raise RuntimeError(f"B 站音频下载失败: {exc}") from exc

            audio_files = [path for path in work_dir.glob("source.*")
                           if path.is_file() and path != cookie_file]
            if len(audio_files) != 1:
                raise RuntimeError("B 站音频下载未产生唯一的音频文件")
            source = audio_files[0]
            if source.stat().st_size > self.max_audio_bytes:
                raise ValueError(f"下载音频超过大小限制 {self.max_audio_bytes // (1024 * 1024)} MB")
            wav = work_dir / "audio-16k-mono.wav"
            self._to_wav(source, wav)
            yield wav

    @staticmethod
    def _validate_download_info(info: Mapping | None, *, bvid: str,
                                page: Mapping) -> None:
        """Reject an audio result that yt-dlp resolved to another video/P."""
        if not isinstance(info, Mapping):
            raise RuntimeError("B 站音频下载未返回视频归属信息")
        expected_page = int(page.get("page") or 1)
        resolved_id = str(info.get("id") or "")
        if resolved_id:
            normalized = resolved_id.lower()
            expected = bvid.lower()
            if not (normalized == expected or normalized.startswith(expected + "_p")):
                raise RuntimeError(f"音频归属校验失败: yt-dlp 返回 {resolved_id}")
            if "_p" in normalized:
                suffix = normalized.rsplit("_p", 1)[1]
                if suffix.isdigit() and int(suffix) != expected_page:
                    raise RuntimeError(
                        f"音频分P校验失败: 目标 P{expected_page}, 实际 P{int(suffix)}")
        webpage_url = str(info.get("webpage_url") or "")
        if webpage_url:
            url = urlparse(webpage_url)
            if bvid.lower() not in url.path.lower():
                raise RuntimeError("音频页面归属校验失败: URL 不属于目标视频")
            values = parse_qs(url.query).get("p", [])
            if values and values[0].isdigit() and int(values[0]) != expected_page:
                raise RuntimeError(
                    f"音频 URL 分P校验失败: 目标 P{expected_page}, 实际 P{int(values[0])}")
        resolved_duration = float(info.get("duration") or 0)
        expected_duration = float(page.get("duration") or 0)
        if resolved_duration and expected_duration:
            tolerance = max(8.0, expected_duration * 0.03)
            if abs(resolved_duration - expected_duration) > tolerance:
                raise RuntimeError(
                    f"音频时长校验失败: 目标约 {expected_duration:.1f}s, "
                    f"实际 {resolved_duration:.1f}s")

    @staticmethod
    def _default_ydl_factory(options):
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise RuntimeError(
                "缺少可选 B 站音频下载依赖，请按 requirements-audio.txt 安装 yt-dlp"
            ) from exc
        return YoutubeDL(options)

    def _progress_hook(self, progress: dict) -> None:
        if progress.get("status") != "downloading":
            return
        size = progress.get("downloaded_bytes") or 0
        if size > self.max_audio_bytes:
            raise ValueError(
                f"下载音频超过大小限制 {self.max_audio_bytes // (1024 * 1024)} MB"
            )

    def _netscape_cookies(self) -> str:
        lines = ["# Netscape HTTP Cookie File"]
        for name, value in self.cookies.items():
            safe_value = str(value).replace("\r", "").replace("\n", "")
            lines.append(f".bilibili.com\tTRUE\t/\tTRUE\t0\t{name}\t{safe_value}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _to_wav(source: Path, target: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg，请安装并将其加入 PATH")
        try:
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                 "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
                check=True, capture_output=True, text=True,
                timeout=7500,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            raise RuntimeError(f"ffmpeg 音频转码失败: {detail or exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffmpeg 音频转码超时") from exc
