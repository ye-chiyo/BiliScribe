# -*- coding: utf-8 -*-
"""Web 服务接口冒烟测试(服务需已启动于 127.0.0.1:8765)"""
import base64
import time

import httpx

BASE = "http://127.0.0.1:8765"
c = httpx.Client(base_url=BASE, timeout=30)

# 1. 首页
r = c.get("/")
assert r.status_code == 200 and "BiliScribe" in r.text, "首页异常"
print("[PASS] GET / 首页正常")

# 2. 登录状态
r = c.get("/api/status")
st = r.json()
assert st["logged_in"] is True, f"应已登录, 实际 {st}"
print(f"[PASS] /api/status 已登录: {st['uname']}, 到期 {time.strftime('%Y-%m-%d', time.localtime(st['expires'])) if st.get('expires') else '?'}")

# 3. 二维码生成
r = c.post("/api/login/start")
d = r.json()
assert d["qr"].startswith("data:image/png;base64,"), "二维码未返回"
raw = base64.b64decode(d["qr"].split(",", 1)[1])
assert raw[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
print(f"[PASS] /api/login/start 二维码 PNG {len(raw)//1024} KB")

# 4. 轮询接口(刚生成, 应为 waiting/scanned)
r = c.get("/api/login/poll")
assert r.json()["state"] in ("waiting", "scanned", "success", "expired")
print("[PASS] /api/login/poll 状态:", r.json()["state"])

# 5. 无效链接 -> 400
r = c.post("/api/extract", json={"urls": "这是一段没有链接的文本", "fmts": ["srt"]})
assert r.status_code == 400, f"无效链接应 400, 实际 {r.status_code}"
print("[PASS] 无效链接正确返回 400:", r.json()["error"])

# 6. 路径穿越防护
r = c.get("/api/download", params={"path": r"C:\Windows\win.ini"})
assert r.status_code == 403, f"越权下载应 403, 实际 {r.status_code}"
print("[PASS] 下载接口路径穿越防护 403")

print("\nWeb 冒烟测试全部通过")
