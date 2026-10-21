# -*- coding: utf-8 -*-
"""Optional local ASR boundary.

The module deliberately does not import FunASR at module import time.  Users
who only export existing Bilibili subtitles keep the lightweight dependency
path and never construct an ASR model.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Protocol


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    source: str = "asr"
    model_id: str = "paraformer-zh"
    timestamp_source: str = "paraformer_sentence_info"


class AsrEngine(Protocol):
    model_id: str

    def transcribe(self, audio_path: Path, *, language: str = "zh",
                   offset_seconds: float = 0.0) -> list[Segment]: ...


class AudioSource(Protocol):
    def open_audio(self, *, bvid: str, aid: int,
                   page: Mapping) -> Iterator[Path]: ...


class LocalAudioSource:
    """A local-file source used by P2 integration and tests.

    A mapping can provide one file per page number.  A single path is useful
    for a one-page mock job and is intentionally not a Bilibili downloader.
    """

    def __init__(self, audio: Path | str | Mapping[int, Path | str]):
        self._audio = audio

    @contextmanager
    def open_audio(self, *, bvid: str, aid: int,
                   page: Mapping) -> Iterator[Path]:
        del bvid, aid
        audio = self._audio
        if isinstance(audio, Mapping):
            try:
                audio = audio[int(page["page"])]
            except (KeyError, TypeError, ValueError) as exc:
                raise FileNotFoundError(f"未提供 P{page.get('page')} 的本地音频") from exc
        path = Path(audio).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"本地音频不存在: {path}")
        yield path


class ParaformerEngine:
    """Lazy FunASR Paraformer engine with explicit CUDA error semantics."""

    def __init__(self, *, device: str = "auto", model_id: str = "paraformer-zh"):
        self.requested_device = device
        self.model_id = model_id
        self.device: str | None = None
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        # Optional dependencies are loaded only when the ASR path is used.
        try:
            import torch
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError(
                "本地 ASR 依赖未安装，请按 requirements-asr.txt 安装 funasr/torch"
            ) from exc

        requested = self.requested_device.lower().strip()
        if requested not in {"auto", "cpu", "cuda"}:
            raise ValueError(f"不支持的 ASR 设备: {self.requested_device}")
        if requested == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("已指定 device='cuda'，但当前 PyTorch CUDA 不可用")
        self.device = ("cuda" if requested == "auto" and torch.cuda.is_available()
                       else requested if requested != "auto" else "cpu")
        self._model = AutoModel(
            model=self.model_id,
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            device=self.device,
        )
        return self._model

    def transcribe(self, audio_path: Path, *, language: str = "zh",
                   offset_seconds: float = 0.0) -> list[Segment]:
        if language != "zh":
            raise ValueError("Paraformer-zh 本地接口只接受 language='zh'")
        result = self._ensure_model().generate(
            input=str(audio_path), batch_size_s=300, sentence_timestamp=True,
        ) or []
        segments: list[Segment] = []
        for item in result:
            for sentence in item.get("sentence_info", []) or []:
                text = str(sentence.get("text", "")).strip()
                start = float(sentence.get("start", 0) or 0) / 1000 + offset_seconds
                end = float(sentence.get("end", 0) or 0) / 1000 + offset_seconds
                if text and end > start and start >= 0:
                    segments.append(Segment(start, end, text,
                                            model_id=self.model_id,
                                            timestamp_source="paraformer_sentence_info"))
        return sorted(segments, key=lambda segment: (segment.start, segment.end))


def segments_to_body(segments: list[Segment]) -> list[dict]:
    return [{"from": segment.start, "to": segment.end, "content": segment.text}
            for segment in segments
            if segment.text.strip() and segment.end > segment.start and segment.start >= 0]
