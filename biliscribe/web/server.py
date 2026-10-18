# -*- coding: utf-8 -*-
"""BiliScribe 本地 Web 控制台(仅监听 127.0.0.1)。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..api import BiliClient, QRLogin, cleanup_cache
from ..auth import (OUTPUT_DIR, load_credentials, login_status,
                    save_credentials)
from ..service import JobManager

STATIC_DIR = Path(__file__).parent / "static"


class ExtractReq(BaseModel):
    urls: str
    fmts: list[str] = ["md"]
    pages: str = "all"
    langs: list[str] = ["zh"]
    out_dir: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="BiliScribe")
    jobs = JobManager()
    login_session: dict = {"qr": None}
    try:
        cleanup_cache()
    except Exception:
        pass

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/status")
    def status():
        st = login_status()
        st["output_dir"] = str(OUTPUT_DIR)
        st["version"] = _version()
        return st

    @app.post("/api/login/start")
    def login_start():
        old = login_session.get("qr")
        if old is not None:
            try:
                old.close()  # 重复发起扫码先释放上一个会话, 避免 client 泄漏
            except Exception:
                pass
        qr = QRLogin()
        try:
            _, data_url = qr.generate()
        except Exception:
            qr.close()
            raise
        login_session["qr"] = qr
        return {"qr": data_url}

    @app.get("/api/login/poll")
    def login_poll():
        qr: QRLogin | None = login_session.get("qr")
        if qr is None or qr.key is None:
            return {"state": "none"}
        r = qr.poll()
        if r["state"] == "success":
            with BiliClient(r["cookies"]) as c:
                nav = c.nav()
            save_credentials(r["cookies"], nav.get("uname", ""),
                             nav.get("mid", 0), r.get("expires"))
            qr.close()
            login_session["qr"] = None
            return {"state": "success", "uname": nav.get("uname", "")}
        return r

    @app.post("/api/extract")
    def extract(req: ExtractReq):
        if not load_credentials():
            return JSONResponse({"error": "not_logged_in"}, status_code=401)
        try:
            job_id = jobs.create(req.urls, fmts=req.fmts, pages_spec=req.pages,
                                 langs=req.langs, out_dir=req.out_dir)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return {k: job[k] for k in
                ("id", "status", "logs", "results", "percent", "out_dir")}

    @app.get("/api/download")
    def download(path: str):
        root = OUTPUT_DIR.resolve()
        target = Path(path).resolve()
        if root not in target.parents and target != root:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not target.is_file():
            return JSONResponse({"error": "not_found"}, status_code=404)
        return FileResponse(target, filename=target.name)

    @app.post("/api/open-output")
    def open_output():
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(OUTPUT_DIR))  # type: ignore[attr-defined]
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


def _version() -> str:
    try:
        from .. import __version__
        return __version__
    except Exception:
        return "unknown"
