# -*- coding: utf-8 -*-
"""BiliScribe 命令行入口。"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

from . import __version__
from .api import BiliError, QRLogin, extract_urls
from .auth import (DATA_DIR, OUTPUT_DIR, clear_credentials, get_client,
                   load_credentials, login_status, save_credentials)
from .service import JobManager


def cmd_status(args):
    st = login_status()
    if st["logged_in"]:
        exp = ""
        if st.get("expires"):
            exp = ", 凭证到期 " + time.strftime("%Y-%m-%d", time.localtime(st["expires"]))
        print(f"已登录: {st['uname']}{exp}")
    elif st.get("offline"):
        print(f"登录状态校验失败(网络异常), 本地保存的账号: {st.get('saved_uname', '?')}")
        print("请检查网络连接; 凭证仍保存在本机, 网络恢复后可正常使用")
    else:
        print("未登录, 请运行: python -m biliscribe login")
    print(f"字幕输出目录: {OUTPUT_DIR}")


def cmd_login(args):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    qr = QRLogin()
    _, data_url = qr.generate()
    # data URL 还原成 PNG 文件并打开
    import base64
    png = DATA_DIR / "qr_login.png"
    png.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
    print(f"登录二维码已保存: {png}")
    print("正在打开二维码图片, 请用手机 B站 App 扫码并确认(180秒内)...")
    try:
        os.startfile(str(png))  # type: ignore[attr-defined]
    except Exception:
        webbrowser.open(png.as_uri())

    deadline = time.time() + 180
    last = None
    try:
        while time.time() < deadline:
            r = qr.poll()
            if r["state"] == "success":
                # 用新 cookie 建客户端校验账号
                from .api import BiliClient
                with BiliClient(r["cookies"]) as c:
                    nav = c.nav()
                save_credentials(r["cookies"], nav.get("uname", ""),
                                 nav.get("mid", 0), r.get("expires"))
                print(f"登录成功: {nav.get('uname')}")
                if r.get("expires"):
                    print("凭证到期:", time.strftime("%Y-%m-%d %H:%M",
                                                    time.localtime(r["expires"])))
                return 0
            if r["state"] == "expired":
                print("二维码已失效, 请重新运行 login")
                return 2
            msg = "已扫码, 请在手机上确认" if r["state"] == "scanned" else "等待扫码..."
            if msg != last:
                print(msg)
                last = msg
            time.sleep(2)
        print("超时, 请重新运行 login")
        return 3
    finally:
        qr.close()


def cmd_logout(args):
    clear_credentials()
    print("已退出登录, 本地凭证已删除")


def cmd_extract(args):
    if args.urls:
        urls_text = " ".join(args.urls)
    elif not sys.stdin.isatty():
        urls_text = sys.stdin.read()  # 支持管道/重定向输入
    else:
        print("请提供视频链接, 例如: python -m biliscribe extract "
              "https://www.bilibili.com/video/BVxxxx/")
        print("或使用 python -m biliscribe extract -h 查看帮助")
        return 1
    urls = extract_urls(urls_text)
    if not urls:
        print("未识别到有效链接")
        return 1
    if not load_credentials():
        print("未登录, 请先运行: python -m biliscribe login")
        return 2
    mgr = JobManager()
    job_id = mgr.create(urls_text, fmts=args.fmts.split(","),
                        pages_spec=args.pages, langs=args.langs.split(","),
                        out_dir=args.out)
    job = mgr.get(job_id)
    seen = 0
    while job["status"] in ("queued", "running"):
        while seen < len(job["logs"]):
            print(job["logs"][seen]); seen += 1
        time.sleep(1)
        job = mgr.get(job_id)
    while seen < len(job["logs"]):
        print(job["logs"][seen]); seen += 1
    total = len(job["results"])
    ok = sum(1 for r in job["results"] if r.get("status") == "ok")
    if total:
        print(f"\n完成: {ok}/{total} 个视频成功, 输出目录 {job['out_dir']}")
    else:
        print("\n作业失败:", job["logs"][-1] if job["logs"] else "未知错误")
    return 0 if ok else 4


def _find_free_port(start: int, host: str = "127.0.0.1", tries: int = 20) -> int:
    import socket
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"端口 {start}~{start + tries - 1} 均被占用")


def cmd_web(args):
    import uvicorn
    from .web.server import create_app

    app = create_app()
    port = _find_free_port(args.port)
    if port != args.port:
        print(f"提示: 默认端口 {args.port} 已被占用, 自动改用 {port}")
    url = f"http://127.0.0.1:{port}"
    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"BiliScribe 本地控制台启动中: {url}  (Ctrl+C 停止)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def build_parser():
    p = argparse.ArgumentParser(prog="biliscribe",
                                description="B站视频字幕本地提取工具 v" + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="查看登录状态").set_defaults(func=cmd_status)
    sub.add_parser("login", help="扫码登录(凭证本地保存约180天)").set_defaults(func=cmd_login)
    sub.add_parser("logout", help="删除本地登录凭证").set_defaults(func=cmd_logout)

    pe = sub.add_parser("extract", help="提取字幕")
    pe.add_argument("urls", nargs="*", help="视频链接(可多个, 也可粘贴文本/管道输入)")
    pe.add_argument("--fmts", default="md", help="格式: srt,vtt,txt,json,md (默认 md)")
    pe.add_argument("--pages", default="all", help="分P: all 或 1,3,5-8")
    pe.add_argument("--langs", default="zh", help="语言代码, 默认 zh")
    pe.add_argument("--out", default=None, help="输出目录")
    pe.set_defaults(func=cmd_extract)

    pw = sub.add_parser("web", help="启动本地网页控制台")
    pw.add_argument("--port", type=int, default=8765)
    pw.add_argument("--no-open", action="store_true")
    pw.set_defaults(func=cmd_web)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BiliError as e:
        print(f"接口错误: {e}")
        return 5


if __name__ == "__main__":
    sys.exit(main() or 0)
