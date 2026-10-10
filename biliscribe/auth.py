# -*- coding: utf-8 -*-
"""登录凭证的本地持久化(自包含 data 目录)。"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .api import BiliClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CRED_FILE = DATA_DIR / "cookies.json"
OUTPUT_DIR = PROJECT_ROOT / "output"


def save_credentials(cookies: dict, uname: str = "", mid: int = 0,
                     expires: float | None = None) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"cookies": cookies, "uname": uname, "mid": mid,
               "saved_at": time.time(), "expires": expires}
    # 原子写: 先写临时文件再 replace, 避免写入中途崩溃损坏凭证
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CRED_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return payload


def load_credentials() -> dict | None:
    if not CRED_FILE.exists():
        return None
    try:
        return json.loads(CRED_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def clear_credentials():
    if CRED_FILE.exists():
        CRED_FILE.unlink()


def get_client() -> BiliClient:
    cred = load_credentials()
    return BiliClient(cred["cookies"] if cred else None)


def login_status() -> dict:
    """返回当前登录态(联网校验)。断网/限流时返回 offline=True,
    不误报为未登录。"""
    cred = load_credentials()
    if not cred:
        return {"logged_in": False, "uname": "", "expires": None}
    if cred.get("expires") and float(cred["expires"]) <= time.time():
        return {"logged_in": False, "expired": True, "uname": "",
                "saved_uname": cred.get("uname", ""),
                "expires": cred.get("expires")}
    with get_client() as c:
        nav = c.nav()
    if nav.get("isLogin") is None:
        return {"logged_in": False, "offline": True,
                "uname": cred.get("uname", ""),
                "saved_uname": cred.get("uname", ""),
                "expires": cred.get("expires"),
                "error": nav.get("error", "")}
    if not nav.get("isLogin"):
        return {"logged_in": False, "uname": "", "expires": cred.get("expires"),
                "saved_uname": cred.get("uname", "")}
    if cred.get("uname") != nav["uname"]:
        save_credentials(cred["cookies"], nav["uname"], nav["mid"],
                         cred.get("expires"))
    return {"logged_in": True, "uname": nav["uname"], "mid": nav["mid"],
            "expires": cred.get("expires")}
