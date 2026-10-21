# -*- coding: utf-8 -*-
"""Optional stdio MCP server for local BiliScribe workflows."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import subtitles as subs_mod
from .api import BiliError
from .asr import ParaformerEngine, segments_to_body
from .auth import OUTPUT_DIR, get_client, load_credentials
from .service import extract_one

MAX_AUDIO_MB = int(os.environ.get("BILISCRIBE_ASR_MAX_AUDIO_MB", "512"))
MAX_AUDIO_SECONDS = int(os.environ.get("BILISCRIBE_ASR_MAX_DURATION_SECONDS", "7200"))
ASR_ENABLED = os.environ.get("BILISCRIBE_ASR_ENABLED", "false").lower() in {
    "1", "true", "yes", "on"
}
ASR_DEVICE = os.environ.get("BILISCRIBE_ASR_DEVICE", "auto")
ASR_MODEL = os.environ.get("BILISCRIBE_ASR_MODEL", "paraformer-zh")

mcp = FastMCP(
    "BiliScribe",
    instructions=("本地 B 站字幕提取和本地音频转写。仅调用 extract_subtitles "
                  "读取 B 站已有字幕；transcribe_local_audio 只处理允许目录内的本地音频。"),
    log_level="ERROR",
)
_engine: ParaformerEngine | None = None


def _get_engine() -> ParaformerEngine:
    global _engine
    if _engine is None:
        _engine = ParaformerEngine(device=ASR_DEVICE, model_id=ASR_MODEL)
    return _engine


def _allowed_audio_roots() -> list[Path]:
    configured = os.environ.get("BILISCRIBE_MCP_AUDIO_DIRS")
    roots = ([Path(part).expanduser() for part in configured.split(os.pathsep) if part.strip()]
             if configured else [Path.home() / "Downloads"])
    return [path.resolve() for path in roots if path.exists()]


def _audio_path(value: str) -> Path:
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("音频路径必须指向文件")
    roots = _allowed_audio_roots()
    if not any(path == root or root in path.parents for root in roots):
        raise ValueError("音频文件不在允许目录中；通过 BILISCRIBE_MCP_AUDIO_DIRS 配置目录")
    if path.stat().st_size > MAX_AUDIO_MB * 1024 * 1024:
        raise ValueError(f"音频文件超过大小限制 {MAX_AUDIO_MB} MB")
    return path


def _probe_duration(path: Path) -> float:
    probe = shutil.which("ffprobe")
    if not probe:
        raise RuntimeError("未找到 ffprobe，请先安装 ffmpeg 并将其加入 PATH")
    result = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    duration = float(json.loads(result.stdout)["format"]["duration"])
    if duration <= 0 or duration > MAX_AUDIO_SECONDS:
        raise ValueError(f"音频时长须在 0 到 {MAX_AUDIO_SECONDS} 秒之间")
    return duration


def _to_wav(source: Path, target: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，请先安装并将其加入 PATH")
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
        check=True, capture_output=True, text=True, timeout=min(MAX_AUDIO_SECONDS + 60, 7500),
    )


@mcp.tool()
def get_capabilities() -> dict:
    """Return local login, optional ASR, CUDA, and audio policy status."""
    try:
        import torch
        cuda_available = bool(torch.cuda.is_available())
        device_name = torch.cuda.get_device_name(0) if cuda_available else None
    except ImportError:
        cuda_available, device_name = False, None
    return {
        "credentials_present": bool(load_credentials()),
        "login_status": "not_checked",
        "asr_enabled": ASR_ENABLED,
        "asr_dependencies_available": _engine is not None or _asr_importable(),
        "asr_model": ASR_MODEL,
        "asr_device_requested": ASR_DEVICE,
        "cuda_available": cuda_available,
        "cuda_device": device_name,
        "audio_allowed_dirs": [str(path) for path in _allowed_audio_roots()],
        "max_audio_mb": MAX_AUDIO_MB,
        "max_audio_seconds": MAX_AUDIO_SECONDS,
        "bilibili_audio_download": False,
    }


def _asr_importable() -> bool:
    try:
        import funasr  # noqa: F401
        return True
    except ImportError:
        return False


def _write_export(output_dir: Path, source_stem: str, fmt: str,
                  content: str, encoding: str) -> Path:
    """Create an export without replacing a previous transcription."""
    safe_stem = subs_mod.safe_filename(source_stem)
    index = 1
    while True:
        suffix = "" if index == 1 else f" ({index})"
        target = output_dir / f"{safe_stem}.asr{suffix}.{fmt}"
        try:
            with target.open("x", encoding=encoding, newline="") as stream:
                stream.write(content)
            return target
        except FileExistsError:
            index += 1


@mcp.tool()
def extract_subtitles(url: str, formats: list[str] | None = None,
                      pages: str = "all", languages: list[str] | None = None,
                      output_dir: str | None = None) -> dict:
    """Extract existing CC/AI subtitle tracks from a Bilibili video URL.

    This tool does not download video audio or invoke ASR when subtitles are absent.
    """
    if not load_credentials():
        raise RuntimeError("未登录；请先运行 python -m biliscribe login")
    out = Path(output_dir).expanduser().resolve() if output_dir else OUTPUT_DIR.resolve()
    out.mkdir(parents=True, exist_ok=True)
    logs: list[str] = []
    with get_client() as client:
        status = client.nav()
        if status.get("isLogin") is False:
            raise RuntimeError("B 站登录凭证已失效")
        result = extract_one(client, url, formats or ["md"], pages,
                             languages or ["zh"], out, logs.append)
    return {"result": result, "logs": logs}


@mcp.tool()
def transcribe_local_audio(audio_path: str, output_format: str | None = None) -> dict:
    """Transcribe a local audio file; returns sentence segments and optional export path."""
    if not ASR_ENABLED:
        raise RuntimeError("本地 ASR 未启用；设置 BILISCRIBE_ASR_ENABLED=true 后重启 MCP")
    source = _audio_path(audio_path)
    duration = _probe_duration(source)
    with tempfile.TemporaryDirectory(prefix="biliscribe-mcp-") as temp:
        wav_path = Path(temp) / "audio-16k-mono.wav"
        _to_wav(source, wav_path)
        # Keep model progress output off stdout, which carries MCP JSON-RPC.
        with redirect_stdout(sys.stderr):
            engine = _get_engine()
            segments = engine.transcribe(wav_path, language="zh")
    body = segments_to_body(segments)
    export_path = None
    if output_format:
        fmt = output_format.lower().strip()
        if fmt not in subs_mod.CONVERTERS:
            raise ValueError("output_format 须为 srt、vtt、txt、json 或 md")
        output_dir = Path(os.environ.get("BILISCRIBE_MCP_OUTPUT_DIR", OUTPUT_DIR)).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        output_dir = output_dir.resolve()
        meta = {"title": source.stem, "subtitle_source": "asr",
                "asr_model": engine.model_id, "asr_device": engine.device or ASR_DEVICE,
                "asr_timestamp_source": "paraformer_sentence_info"}
        encoding = "utf-8-sig" if fmt == "srt" else "utf-8"
        target = _write_export(output_dir, source.stem, fmt,
                               subs_mod.render(fmt, body, meta), encoding)
        export_path = str(target)
    return {
        "audio_path": str(source), "duration_seconds": duration,
        "source": "asr", "model_id": engine.model_id,
        "device": engine.device or ASR_DEVICE,
        "segments": body, "output_path": export_path,
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
