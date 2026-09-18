# -*- coding: utf-8 -*-
"""字幕 JSON -> SRT / VTT / TXT / JSON / Markdown 转换(纯函数, 零依赖)。"""
from __future__ import annotations

import json
import re

ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_filename(name: str, max_len: int = 80) -> str:
    name = ILLEGAL.sub("_", name).strip().strip(".")
    return (name or "untitled")[:max_len]


def _hms(seconds: float, srt: bool = True) -> str:
    """秒 -> HH:MM:SS,mmm(SRT) 或 HH:MM:SS.mmm(VTT)。

    总毫秒数先取整再逐级进位, 避免四舍五入到 1000ms 时产生
    非法时间戳(如 00:00:59,1000)。"""
    total_ms = max(0, int(round(float(seconds or 0) * 1000)))
    h, total_ms = divmod(total_ms, 3_600_000)
    m, total_ms = divmod(total_ms, 60_000)
    s, ms = divmod(total_ms, 1000)
    sep = "," if srt else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _seg(seg: dict) -> tuple[float, float, str]:
    """容错读取单条字幕; 缺字段时跳过会得到空文本。"""
    return (float(seg.get("from", 0) or 0),
            float(seg.get("to", 0) or 0),
            str(seg.get("content", "")).strip())


def to_srt(body: list[dict]) -> str:
    blocks = []
    n = 0
    for seg in body:
        start, end, text = _seg(seg)
        if not text:
            continue
        n += 1
        blocks.append(f"{n}\n{_hms(start)} --> {_hms(end)}\n{text}\n")
    return "\n".join(blocks)


def to_vtt(body: list[dict]) -> str:
    blocks = []
    for seg in body:
        start, end, text = _seg(seg)
        if not text:
            continue
        blocks.append(f"{_hms(start, False)} --> {_hms(end, False)}\n{text}\n")
    return "WEBVTT\n\n" + "\n".join(blocks)


def to_txt(body: list[dict]) -> str:
    return "\n".join(text for seg in body
                     if (text := str(seg.get("content", "")).strip()))


def to_json(body: list[dict], meta: dict | None = None) -> str:
    return json.dumps({"meta": meta or {}, "body": body},
                      ensure_ascii=False, indent=2)


def to_md(body: list[dict], meta: dict | None = None) -> str:
    meta = meta or {}
    lines = [f"# {meta.get('title', '字幕')}", ""]
    if meta.get("owner"):
        lines.append(f"- UP主: {meta['owner']}")
    if meta.get("bvid"):
        lines.append(f"- 链接: https://www.bilibili.com/video/{meta['bvid']}")
    if meta.get("lan_doc"):
        lines.append(f"- 字幕语言: {meta['lan_doc']}")
    lines.append("")
    for seg in body:
        start, _, text = _seg(seg)
        if not text:
            continue
        total = int(start)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        ts = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        lines.append(f"**[{ts}]** {text}")
        lines.append("")
    return "\n".join(lines)


CONVERTERS = {
    "srt": (to_srt, "SRT 字幕(带时间轴)"),
    "vtt": (to_vtt, "WebVTT 字幕"),
    "txt": (to_txt, "纯文本"),
    "json": (to_json, "原始 JSON"),
    "md": (to_md, "Markdown 笔记"),
}


def render(fmt: str, body: list[dict], meta: dict | None = None) -> str:
    fn = CONVERTERS[fmt][0]
    return fn(body, meta) if fmt in ("json", "md") else fn(body)
