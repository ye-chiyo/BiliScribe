# BV16hZFB5ERM 字幕串轨问题

## 现象

旧版本 BiliScribe 对该视频多次提取到相同的两条错误字幕。B 站播放器实际显示有“中文 AI”字幕，视频信息本身没有串错。

## 根因

1. 旧实现主要依赖 `/x/player/v2` 的 JSON 字幕列表。该接口在风控/限流状态下可能返回其他视频的字幕轨，且重复请求相同查询键时可能持续命中相同的错误响应。
2. 旧的 CID-only 字幕列表缓存无法区分不同视频，可能把错误轨继续保留到缓存 TTL 内。
3. 播放器使用的现代 `/x/v2/subtitle/web/view` 返回 Protobuf；其中 `subtitle_url` 可能是 `//subtitle.bilibili.com/<XOR 编码路径>`，不能直接按旧的 `/prod/<aid><cid>` URL 逻辑下载。

## 修复

- 优先请求并解析 `/x/v2/subtitle/web/view` 的 Protobuf 字幕元数据。
- 按播放器的两组轮换 XOR 密钥解码 `subtitle.bilibili.com` 路径，并改写到 `aisubtitle.hdslb.com`。
- 解码后先用资源 ID 中的 `aid/cid` 校验字幕归属，校验通过后才缓存或导出。
- 字幕列表缓存改为按 `bvid/aid/cid` 隔离；正文缓存按稳定资源 ID 隔离；空正文不写入缓存。
- Python 本地版和 JavaScript Worker 保持相同的 URL 安全、语言匹配、归属校验和缓存规则。

## 真实复测

2026-10-19 在无账号 Cookie 注入的公开请求中确认：

- 视频信息接口返回 `aid=116470822476749`、`cid=37850188927`。
- Protobuf 接口返回 1 条 `ai-zh` 字幕轨。
- 解码后的资源 ID 含目标 `aid+cid`，归属校验通过。
- 字幕正文接口返回 HTTP 200，解析出 373 条时间轴字幕。
- 本地网页控制台实际生成 `Hermes+Obsidian+LLM wkii，构建AI知识库_BV16hZFB5ERM.ai-zh.md`，页面显示“成功 · 1 条字幕轨 · 1 个文件”。

## 验证命令

- `tests/test_subtitle_guard.py`：16 passed
- `site`: `npm run build`：通过
- `site`: `npm test`：2 passed
- `git diff --check`：通过

不记录 Cookie、签名 URL、完整字幕正文或其他临时抓包数据。
