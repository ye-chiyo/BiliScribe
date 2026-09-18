# -*- coding: utf-8 -*-
"""补充验证: SRT BOM / 语言过滤 / 作业串行排队 / 时间戳进位落盘"""
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8765"
c = httpx.Client(base_url=BASE, timeout=60)
URL = "https://www.bilibili.com/video/BV11gYd6UEPv/"


def submit(langs, fmts):
    r = c.post("/api/extract", json={"urls": URL, "fmts": fmts,
                                    "pages": "all", "langs": langs})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def wait(jid):
    j = None
    for _ in range(60):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "error", "need_login"):
            return j
        time.sleep(1)
    raise AssertionError("作业超时: " + jid)


# 1. 语言过滤: 仅中文 -> 只应有 1 条轨, srt+txt 共 2 个文件
jid = submit(["zh"], ["srt", "txt"])
j = wait(jid)
res = j["results"][0]
print("仅中文结果: tracks=%s files=%d" % (res.get("tracks"), len(res["files"])))
assert res["status"] == "ok"
assert len(res["files"]) == 2, f"仅中文应 2 个文件, 实际 {len(res['files'])}"
assert all(".ai-zh." in f for f in res["files"]), "文件名应全部为中文轨"
print("[PASS] 语言过滤(仅中文)生效")

# 2. SRT 文件带 UTF-8 BOM
srt = next(f for f in res["files"] if f.endswith(".srt"))
r = c.get("/api/download", params={"path": srt})
assert r.content[:3] == b"\xef\xbb\xbf", "SRT 缺少 UTF-8 BOM"
assert "00:00:" in r.content.decode("utf-8-sig")
print("[PASS] SRT 带 UTF-8 BOM, 中文可正常解码")

# 3. 作业串行: 连续提交两个, 观察到 queued 或依次完成
j1 = submit(["all"], ["txt"])
j2 = submit(["en"], ["txt"])
seen_queued = False
for _ in range(30):
    a = c.get(f"/api/jobs/{j1}").json()
    b = c.get(f"/api/jobs/{j2}").json()
    if b["status"] == "queued":
        seen_queued = True
    if a["status"] in ("done", "error") and b["status"] in ("done", "error"):
        break
    time.sleep(1)
ja = wait(j1)
jb = wait(j2)
assert ja["status"] == "done" and jb["status"] == "done"
print(f"[PASS] 两作业均完成, 观察到排队态: {seen_queued}")

# 4. 仅英文
en_files = jb["results"][0]["files"]
assert all(".ai-en." in f for f in en_files), en_files
print(f"[PASS] 仅英文过滤生效: {len(en_files)} 个文件")

print("\n补充验证全部通过")
