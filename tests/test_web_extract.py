# -*- coding: utf-8 -*-
"""Web 端到端: 提交真实提取作业 -> 轮询 -> 校验文件 -> 下载验证"""
import time
import httpx

BASE = "http://127.0.0.1:8765"
c = httpx.Client(base_url=BASE, timeout=60)

r = c.post("/api/extract", json={
    "urls": "https://www.bilibili.com/video/BV11gYd6UEPv/",
    "fmts": ["srt", "vtt", "txt", "json", "md"],
    "pages": "all", "langs": ["all"]})
assert r.status_code == 200, r.text
job_id = r.json()["job_id"]
print("作业已提交:", job_id)

for _ in range(60):
    j = c.get(f"/api/jobs/{job_id}").json()
    if j["status"] in ("done", "error", "need_login"):
        break
    time.sleep(2)

print("\n".join(j["logs"]))
print("状态:", j["status"], "进度:", j["percent"], "%")
assert j["status"] == "done", "作业未成功完成"

res = j["results"][0]
print("\n结果: status=%s, tracks=%s, files=%d" % (res["status"], res.get("tracks"), len(res["files"])))
assert res["status"] == "ok", "该视频应能提取到字幕"
assert len(res["files"]) == 10, f"2 语言 x 5 格式应为 10 个文件, 实际 {len(res['files'])}"

# 下载验证
f0 = res["files"][0]
r = c.get("/api/download", params={"path": f0})
assert r.status_code == 200 and len(r.content) > 100, "文件下载失败"
print(f"[PASS] 文件下载验证: {f0.split(chr(92))[-1]} {len(r.content)} 字节")
print("\n端到端测试通过, 文件清单:")
for f in res["files"]:
    print("  -", f.split("\\")[-1] if "\\" in f else f.split("/")[-1])
