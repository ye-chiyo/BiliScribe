# -*- coding: utf-8 -*-
"""高层编排: 链接 -> 视频信息 -> 各分P字幕 -> 多格式落盘; 含批量作业管理。"""
from __future__ import annotations

import queue
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import subtitles as subs_mod
from .asr import AsrEngine, AudioSource, ParaformerEngine, segments_to_body
from .api import (BiliClient, BiliError, ForeignSubtitleError, extract_urls,
                  parse_video_url)
from .auth import OUTPUT_DIR, get_client


def _write_asr_export(path: Path, content: str, encoding: str) -> Path:
    index = 1
    while True:
        target = path if index == 1 else path.with_name(
            f"{path.stem} ({index}){path.suffix}")
        try:
            with target.open("x", encoding=encoding, newline="") as stream:
                stream.write(content)
            return target
        except FileExistsError:
            index += 1


def parse_pages_spec(spec: str, total: int) -> list[int]:
    """'all' 或 '1,3,5-8' -> 页码列表。"""
    spec = (spec or "all").strip().lower()
    if spec in ("all", "*", ""):
        return list(range(1, total + 1))
    pages: set[int] = set()
    for chunk in spec.replace("，", ",").split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            if a.strip().isdigit() and b.strip().isdigit():
                pages.update(range(int(a), int(b) + 1))
        elif chunk.isdigit():
            pages.add(int(chunk))
    return sorted(p for p in pages if 1 <= p <= total)


def _lang_match(lan: str, wanted: set[str]) -> bool:
    """语言族匹配: 统一 B 站别名后匹配 ai-zh / zh-CN / cmn 等轨道。"""
    def canonical(value: str) -> str:
        value = value.strip().lower().replace("_", "-")
        return {"cmn": "zh", "zh-cn": "zh", "zh-hans": "zh",
                "zh-hant": "zh"}.get(value, value)

    lan = (lan or "").lower().replace("_", "-")
    if canonical(lan) in {canonical(item) for item in wanted}:
        return True
    parts = re.split(r"-", lan)
    return any(canonical(part) in {canonical(item) for item in wanted} for part in parts)


def _pick_tracks(tracks: list[dict], langs: list[str]) -> list[dict]:
    normalized = [str(lang).strip().lower() for lang in langs if str(lang).strip()]
    if not normalized or "all" in normalized:
        return tracks
    wanted = set(normalized)
    picked = [t for t in tracks if _lang_match(t.get("lan", ""), wanted)]
    return picked


def extract_one(client: BiliClient, raw_url: str, fmts: list[str],
                pages_spec: str, langs: list[str], out_dir: Path,
                log, *, asr_engine: AsrEngine | None = None,
                audio_source: AudioSource | None = None,
                force_asr: bool = False) -> dict:
    url = client.resolve(raw_url.strip())
    bvid, aid, linked_page = parse_video_url(url)
    if not bvid and not aid:
        raise BiliError(-1, "链接中未找到 BV/av 号")
    info = client.get_video_info(bvid=bvid, aid=aid)
    bvid = info["bvid"]
    total = len(info["pages"])
    page_nos = parse_pages_spec(pages_spec, total)
    # 没有另行指定分 P 时，遵从链接中的 ?p=；普通链接仍处理全部分 P。
    if pages_spec.strip().lower() in ("all", "*", "") and re.search(r"[?&]p=\d+", url):
        page_nos = [linked_page] if 1 <= linked_page <= total else []
    if not page_nos:
        raise ValueError("没有有效的分 P 页码，请检查链接或分 P 选项")
    log(f"视频《{info['title']}》UP主:{info['owner']} | 共 {total} P, 本次处理 {len(page_nos)} P")

    result = {"url": raw_url, "bvid": bvid, "title": info["title"],
              "owner": info["owner"], "pic": info.get("pic", ""),
              "duration": info.get("duration", 0),
              "pages_total": total, "pages_done": 0, "tracks": 0,
              "files": [], "no_subtitle_pages": [], "subtitle_absent_pages": [],
              "subtitle_mismatch_pages": [],
              "empty_body_pages": [], "asr_errors": [], "sources": [], "status": "ok"}
    multi = total > 1
    # 目录/文件名带 bvid, 避免同名视频互相覆盖
    title_part = subs_mod.safe_filename(info["title"], 60)
    if multi:
        video_dir = out_dir / f"{title_part}_{bvid}"
    else:
        video_dir = out_dir
    video_dir.mkdir(parents=True, exist_ok=True)

    valid_fmts = [f for f in fmts if f in subs_mod.CONVERTERS]
    for idx, pno in enumerate(page_nos):
        page = next(p for p in info["pages"] if p["page"] == pno)
        label = f"P{pno}" + (f"《{page['part']}》" if multi else "")
        if force_asr:
            all_tracks = []
        else:
            try:
                subtitle_kwargs = {"on_log": log}
                # 保持兼容旧版客户端/mock，同时生产路径带 UP 主归属校验。
                if info.get("owner_mid") is not None:
                    subtitle_kwargs["owner_mid"] = info["owner_mid"]
                all_tracks = client.get_subtitle_list(
                    bvid, info["aid"], page["cid"], **subtitle_kwargs)
            except ForeignSubtitleError:
                all_tracks = []
                result["subtitle_mismatch_pages"].append(pno)
                log(f"  {label}: 字幕归属不匹配, 立即使用本地 ASR")
        tracks = _pick_tracks(all_tracks, langs)
        if force_asr or not all_tracks:
            result["subtitle_absent_pages"].append(pno)
            page_written = False
            if asr_engine is not None and audio_source is not None:
                try:
                    with audio_source.open_audio(
                            bvid=bvid, aid=info["aid"], page=page) as audio_path:
                        segments = asr_engine.transcribe(audio_path, language="zh")
                    body = segments_to_body(segments)
                    if body:
                        meta = {"title": info["title"], "owner": info["owner"],
                                "bvid": bvid, "lan_doc": "机器转写（普通话）",
                                "page": pno, "part": page["part"],
                                "subtitle_source": "asr",
                                "asr_model": getattr(asr_engine, "model_id", "unknown"),
                                "asr_device": getattr(asr_engine, "device", None) or
                                              getattr(asr_engine, "requested_device", "unknown"),
                                "asr_timestamp_source": segments[0].timestamp_source}
                        for fmt in valid_fmts:
                            stem = (f"P{pno:02d}-{subs_mod.safe_filename(page['part'], 40)}.asr"
                                    if multi else f"{title_part}_{bvid}.asr")
                            fpath = video_dir / f"{stem}.{fmt}"
                            enc = "utf-8-sig" if fmt == "srt" else "utf-8"
                            fpath = _write_asr_export(
                                fpath, subs_mod.render(fmt, body, meta), enc)
                            result["files"].append(str(fpath))
                        result["tracks"] += 1
                        result["sources"].append("asr")
                        page_written = True
                        log(f"  {label} [机器转写] {len(body)} 条 -> {', '.join(valid_fmts)}")
                    else:
                        log(f"  {label}: ASR 未检测到语音")
                except Exception as exc:
                    message = f"P{pno}: {exc}"
                    result["asr_errors"].append(message)
                    log(f"  {label}: 本地 ASR 失败: {exc}")
            else:
                log(f"  {label}: 没有字幕轨道")
            if not page_written:
                result["no_subtitle_pages"].append(pno)
        elif not tracks:
            log(f"  {label}: 有字幕轨道，但没有所选语言")
            result["no_subtitle_pages"].append(pno)
        page_had_empty_body = False
        refreshed_tracks = None
        for t in tracks:
            body = client.fetch_subtitle(t["subtitle_url"], bvid)
            if not body and refreshed_tracks is None:
                invalidate = getattr(client, "invalidate_subtitle_cache", None)
                if not callable(invalidate):
                    refreshed_tracks = []
                    fresh = []
                else:
                    invalidate(bvid, info["aid"], page["cid"])
                    fresh = client.get_subtitle_list(
                        bvid, info["aid"], page["cid"], on_log=log,
                        force_refresh=True)
                refreshed_tracks = [candidate for candidate in
                                    _pick_tracks(fresh, langs)
                                    if candidate.get("lan") == t.get("lan")]
                for candidate in refreshed_tracks:
                    body = client.fetch_subtitle(candidate["subtitle_url"], bvid)
                    if body:
                        t = candidate
                        break
            if not body:
                page_had_empty_body = True
                continue
            lan = t.get("lan", "unk")
            meta = {"title": info["title"], "owner": info["owner"], "bvid": bvid,
                    "lan_doc": t.get("lan_doc", lan), "page": pno, "part": page["part"]}
            for fmt in valid_fmts:
                if multi:
                    stem = (f"P{pno:02d}-{subs_mod.safe_filename(page['part'], 40)}"
                            f".{lan}")
                else:
                    stem = f"{title_part}_{bvid}.{lan}"
                fpath = video_dir / f"{stem}.{fmt}"
                # SRT 带 UTF-8 BOM, 避免 Premiere/剪映等导入中文乱码
                enc = "utf-8-sig" if fmt == "srt" else "utf-8"
                fpath.write_text(subs_mod.render(fmt, body, meta), encoding=enc)
                result["files"].append(str(fpath))
            result["tracks"] += 1
            source = "ai_subtitle" if str(lan).lower().startswith("ai-") else "cc"
            if source not in result["sources"]:
                result["sources"].append(source)
            log(f"  {label} [{t.get('lan_doc', lan)}] {len(body)} 条 -> {', '.join(valid_fmts)}")
            client.polite_sleep()
        if tracks and page_had_empty_body and pno not in result["empty_body_pages"]:
            result["empty_body_pages"].append(pno)
        result["pages_done"] += 1
        if idx < len(page_nos) - 1:
            client.polite_sleep()

    if result["asr_errors"]:
        result["status"] = "partial" if result["files"] else "asr_error"
    elif not result["files"]:
        result["status"] = "no_subtitle"
        if result["empty_body_pages"] and not result["no_subtitle_pages"]:
            result["message"] = "已找到所选语言字幕轨, 但字幕正文接口暂时返回空内容; 请稍后重试。"
    return result


class JobManager:
    """作业串行执行(同时只跑一个, 避免并发请求互相加剧 B 站限流),
    多余作业排队; 作业记录保留最近 MAX_JOBS 条。"""

    MAX_JOBS = 50

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._worker_started = False

    def create(self, urls_text: str, fmts=None, pages_spec: str = "all",
               langs=None, out_dir: str | None = None, *,
               asr_enabled: bool = False, asr_engine: AsrEngine | None = None,
               audio_source: AudioSource | None = None,
               force_asr: bool = False) -> str:
        urls = extract_urls(urls_text)
        if not urls:
            raise ValueError("未识别到有效的 B 站视频链接")
        if (asr_enabled or force_asr) and os.environ.get("BILISCRIBE_ASR_ENABLED", "false").lower() not in {
                "1", "true", "yes", "on"}:
            raise ValueError("本地 ASR 未启用；设置 BILISCRIBE_ASR_ENABLED=true")
        job_id = uuid.uuid4().hex[:12]
        out = Path(out_dir) if out_dir else OUTPUT_DIR
        out.mkdir(parents=True, exist_ok=True)
        job = {"id": job_id, "urls": urls, "status": "queued", "logs": [],
               "results": [], "percent": 0, "created_at": time.time(),
               "out_dir": str(out)}
        with self.lock:
            self.jobs[job_id] = job
            self._prune_locked()
        if (asr_enabled or force_asr) and asr_engine is None:
            asr_engine = ParaformerEngine()
        self._q.put((job, urls, fmts or ["md"], pages_spec,
                     langs or ["zh"], out, asr_enabled or force_asr,
                     asr_engine, audio_source, force_asr))
        self._ensure_worker()
        return job_id

    def _prune_locked(self):
        overflow = len(self.jobs) - self.MAX_JOBS
        if overflow <= 0:
            return
        finished = sorted((j["created_at"], jid)
                          for jid, j in self.jobs.items()
                          if j["status"] not in ("queued", "running"))
        for _, jid in finished[:overflow]:
            self.jobs.pop(jid, None)

    def _ensure_worker(self):
        if not self._worker_started:
            self._worker_started = True
            threading.Thread(target=self._worker_loop, daemon=True).start()

    def _worker_loop(self):
        while True:
            item = self._q.get()
            try:
                self._run(*item)
            except Exception as e:  # 兜底, 绝不让工作线程死掉
                job = item[0]
                job["status"] = "error"
                self._log(job, f"作业失败: {e}")
                traceback.print_exc()

    def _log(self, job, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self.lock:
            job["logs"].append(line)

    def _run(self, job, urls, fmts, pages_spec, langs, out,
             asr_enabled=False, asr_engine=None, audio_source=None,
             force_asr=False):
        job["status"] = "running"
        try:
            with get_client() as client:
                nav = client.nav()
                if nav.get("isLogin") is False:
                    reason = "登录凭证已过期" if nav.get("expired") else "未登录"
                    self._log(job, f"{reason}: 游客无法获取字幕, 请先扫码登录")
                    job["status"] = "need_login"
                    return
                if nav.get("isLogin") is None:
                    self._log(job,
                              f"登录状态校验失败({nav.get('error', '网络异常')}),"
                              "仍尝试提取, 若失败请检查网络后重试")
                else:
                    self._log(job, f"登录账号: {nav['uname']} | 共 {len(urls)} 个链接")
                if asr_enabled and audio_source is None:
                    from .audio import BilibiliAudioSource
                    audio_source = BilibiliAudioSource(
                        cookies=client.cookies_dict(),
                        max_audio_mb=float(os.environ.get("BILISCRIBE_ASR_MAX_AUDIO_MB", "512")),
                        max_duration_seconds=int(os.environ.get(
                            "BILISCRIBE_ASR_MAX_DURATION_SECONDS", "7200")),
                    )
                if asr_enabled:
                    self._log(job, "本地 ASR 已启用；若需转写，首次模型加载或下载期间进度可能暂不变化")
                for i, u in enumerate(urls):
                    self._log(job, f"({i + 1}/{len(urls)}) 处理: {u}")
                    try:
                        r = extract_one(client, u, fmts, pages_spec, langs, out,
                                        lambda m: self._log(job, m),
                                        asr_engine=asr_engine if asr_enabled else None,
                                        audio_source=audio_source if asr_enabled else None,
                                        force_asr=force_asr)
                        job["results"].append(r)
                        if r["status"] == "no_subtitle":
                            self._log(job, "  -> 该视频没有可用字幕")
                        else:
                            self._log(job, f"  -> 已导出 {len(r['files'])} 个文件")
                    except BiliError as e:
                        self._log(job, f"  !! 接口错误: {e}")
                        job["results"].append({"url": u, "status": "error", "message": str(e),
                                               "files": []})
                    except Exception as e:
                        self._log(job, f"  !! 异常: {e}")
                        traceback.print_exc()
                        job["results"].append({"url": u, "status": "error", "message": str(e),
                                               "files": []})
                    job["percent"] = round((i + 1) / len(urls) * 100)
            job["percent"] = 100
            job["status"] = "done"
            self._log(job, "全部完成, 文件保存在: " + job["out_dir"])
        except Exception as e:
            job["status"] = "error"
            self._log(job, f"作业失败: {e}")

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            return self.jobs.get(job_id)
