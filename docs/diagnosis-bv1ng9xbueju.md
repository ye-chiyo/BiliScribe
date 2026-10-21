# BV1NG9xBUEju 字幕提取问题协作排查说明

更新时间：2026-10-20
目标视频：<https://www.bilibili.com/video/BV1NG9xBUEju/>
项目：BiliScribe

## 1. 一句话结论

当前最可能的问题发生在 B 站字幕列表响应，而不是 BiliScribe 的 Protobuf 解码或字幕正文下载：现代字幕接口没有返回目标轨道，旧接口偶尔返回一条可下载但无法证明属于目标视频的 `ai-zh` 轨道。播放器页面目前也已人工确认没有“中文 AI”选项。

因此，BiliScribe 当前返回 0 条字幕、0 个文件是有意的安全结果：它拒绝导出无法验证归属的字幕，避免再次产生与视频内容不符的结果。

## 2. 目标视频身份

视频信息接口返回：

| 字段 | 值 |
| --- | --- |
| `bvid` | `BV1NG9xBUEju` |
| `aid` | `116488438549326` |
| `cid` | `37950522219` |
| 分 P | 1 |
| 时长 | 约 1225 秒 |

这些身份信息可以正常解析，故问题不在链接识别、视频信息接口或分 P 选择。

## 3. 已验证事实

### 3.1 字幕接口响应

在同一登录态和同一 Python/httpx 客户端中，目标视频的结果如下：

| 接口 | 响应 | 解析结果 |
| --- | --- | --- |
| `/x/player/wbi/v2` | HTTP 200，JSON 正常 | 0 条字幕轨 |
| `/x/v2/subtitle/web/view` | HTTP 200，2 字节 Protobuf `0a00` | 0 条字幕轨 |
| `/x/player/v2` | HTTP 200，结果不稳定 | 多数为 0 条，偶尔 1 条 `ai-zh` |

现代 Protobuf 接口在多组参数下都保持空响应，包括语言偏好、`web_location`、`playlist_switch`、`context_ext`、设备头和 WBI 签名变体。仅修改这些常见参数没有恢复目标轨道。

### 3.2 旧接口返回的轨道不能安全使用

旧 `/x/player/v2` 偶尔返回一条 `ai-zh` 字幕 URL。对其中一次返回的 URL 做了脱敏探测：

- 字幕正文请求 HTTP 200；
- 正文包含 869 个字幕段；
- 最大结束时间约 1660.28 秒；
- 目标视频时长约 1225 秒；
- 资源 ID 不含目标 `aid+cid` 前缀；
- BiliScribe 的归属校验判定不属于当前视频。

这说明“接口有返回”和“返回的是目标视频字幕”是两件不同的事。HTTP 200、`ai-zh` 标签或正文非空都不足以证明归属。

### 3.3 成功视频对照

使用相同登录态和相同 BiliScribe 流程：

- `BV16hZFB5ERM`：现代 Protobuf 接口返回目标归属的 `ai-zh` 轨，可以生成字幕文件；
- `BV11gYd6UEPv`：现代 Protobuf 接口返回多条目标归属轨，可以生成字幕文件；
- `BV1NG9xBUEju`：现代接口为空，旧接口只有不匹配或不可验证的轨道。

这组对照排除了“所有视频都无法解析”“Protobuf 解码整体错误”“字幕正文下载逻辑整体失效”。

### 3.4 播放器状态

用户已在已登录 B 站播放器中确认目标视频没有“中文 AI”字幕选项。因此当前没有证据表明播放器手里存在一条可供客户端复用的目标 AI 字幕轨。

浏览器控制层未能稳定绑定该标签读取 Network；已有抓包记录也没有目标视频的字幕元数据和正文请求。这个限制只影响播放器侧证据，不改变上述接口实测结果。

## 4. 失败链路

1. BiliScribe 从视频信息接口取得正确的 `aid/cid`。
2. 现代 Protobuf 字幕接口返回空轨道。
3. 旧接口重试时偶尔返回其他视频或无法验证归属的轨道。
4. `track_belongs_to_video()` 拒绝这些轨道。
5. 轨道未进入缓存，正文下载和导出不会执行。
6. 作业结果为 0 条字幕、0 个文件。

相关代码：

- Python 字幕列表与归属校验：[biliscribe/api.py](/D:/AI/BiliScribe/biliscribe/api.py:224)
- Python 提取编排：[biliscribe/service.py](/D:/AI/BiliScribe/biliscribe/service.py:95)
- Worker 对应逻辑：[site/worker.mjs](/D:/AI/BiliScribe/site/worker.mjs:355)

## 5. 根因假设，按当前证据排序

### 假设 A：B 站旧接口发生服务端/边缘缓存串轨或陈旧数据

支持证据：

- 同一视频的旧接口结果在 0 条和 1 条之间变化；
- 返回的资源归属 ID 与目标 `aid/cid` 不符；
- 返回正文时间轴超过目标视频时长；
- 随机缓存参数不能稳定修复结果。

反证或未知：

- 不能仅凭客户端结果区分 B 站源站数据映射、边缘缓存和请求上下文差异；
- 尚未获得播放器实际字幕请求作为独立对照。

### 假设 B：目标视频当前确实没有可用的 B 站 AI 字幕

支持证据：

- 现代 Protobuf 接口为空；
- WBI 播放器接口为空；
- 用户确认播放器没有“中文 AI”选项；
- 第三方 `yt-dlp` 字幕路径也报告没有可请求字幕。

限制：

- “没有公开接口轨道”不等于能证明 B 站后台永远没有字幕数据；只能说明当前登录态和当前播放器状态下没有可安全取得的轨道。

### 假设 C：播放器使用了当前客户端没有复现的额外上下文

可能包括设备指纹、会话关联、内部实验开关或尚未识别的接口。

当前支持度较低：用户已确认播放器没有“中文 AI”，且常见设备头、WBI 签名和参数变体均未恢复现代轨道。但由于浏览器 Network 未捕获，不能完全排除。

### 假设 D：资源 ID 归属规则变化，导致客户端误拒绝

当前支持度低。目标视频旧轨资源 ID 不仅无法匹配 `aid/cid`，正文时间轴也超过视频时长；因此不能仅用“资源 ID 格式变化”解释全部异常。若将来拿到播放器真实资源，必须先建立新的可验证归属规则，再修改代码。

## 6. 建议的下一步验证

### 6.1 需要一名协作者在浏览器侧做的验证

在已登录目标视频页面：

1. 打开开发者工具 Network，清空记录；
2. 重新加载视频页面；
3. 搜索 `subtitle`、`player/v2`、`player/wbi/v2`；
4. 打开播放器设置或字幕菜单；
5. 记录脱敏后的请求路径、状态码、响应 `Content-Type`、响应字节数、轨道语言和字幕 URL 的主机/路径结构；
6. 不要提供 Cookie、完整 `auth_key`、完整签名 URL 或字幕正文。

重点是确认是否存在不同于 `/x/v2/subtitle/web/view` 的接口，以及播放器是否真的返回目标 `aid/cid` 对应的轨道。

### 6.2 可重复的本地对照

使用项目虚拟环境运行：

```powershell
.\.venv\Scripts\python.exe -m biliscribe extract `
  "https://www.bilibili.com/video/BV1NG9xBUEju/" `
  --fmts txt --out .tmp\bv1ng9x-recheck
```

该命令的预期安全结果是：目标视频 0 个文件，并提示没有属于当前视频的可用字幕轨。对照视频可使用 `BV16hZFB5ERM`，应能取得目标归属字幕。

## 7. 可行解决措施

### 可以立即实施

- 保留 `aid/cid` 归属校验，禁止把旧接口的错轨导出；
- 增加脱敏诊断模式，记录每个接口的状态码、字节数、轨道数、是否出现 foreign track、缓存命中情况；
- 空 Protobuf、旧接口串轨、正文空响应使用不同错误类型，便于后续比较；
- 继续保持本地 Python/httpx/FastAPI 与 Worker 行为一致；
- 将新的播放器真实响应做成 mock fixture 后，再实现最小接口适配。

### 暂不应实施

- 删除中文轨道的 `aid/cid` 校验；
- 看到 HTTP 200 或正文非空就放行；
- 将不透明 `/bfs/subtitle/` 资源永久缓存；
- 直接复制第三方 ASR 工作流到现有 CC/AI 提取链路。

### 如果必须得到文字内容

可把 ASR 作为独立、明确标记的功能分支，而不是字幕提取失败后的隐式回退：

- 下载音频并使用 Whisper 等 ASR；
- 明确输出是机器转录，不是 B 站 CC/AI 字幕；
- 增加 `ffmpeg`、模型、运行时间和磁盘空间依赖；
- 设计临时音频清理、隐私和缓存策略；
- 与现有字幕归属校验和导出格式分开测试。

## 8. 与协作者讨论时的结论边界

可以确定：

- 目标视频身份正确；
- 现代接口没有返回目标字幕轨；
- 旧接口偶发返回了不可验证或明显不匹配的轨道；
- BiliScribe 拒绝导出是为了防止错误字幕；
- 同一客户端对其他视频可以正常工作。

不能确定：

- B 站后台是否永久保存过该视频的 AI 字幕；
- B 站内部具体是哪一层发生串轨或数据映射错误；
- 播放器是否曾在其他账号、地区、时间或实验配置下展示过该轨；
- 是否存在尚未捕获的私有接口。

## 9. 现有回归测试与记录

- 字幕归属、Protobuf 解析、轮换 URL 解码和空正文缓存行为：[tests/test_subtitle_guard.py](/D:/AI/BiliScribe/tests/test_subtitle_guard.py:246)
- Worker mock 测试：[site/tests/worker.test.mjs](/D:/AI/BiliScribe/site/tests/worker.test.mjs:40)
- 详细研究记录：[docs/research-bilibili-ai-subtitle-bv1ng9xbueju.md](/D:/AI/BiliScribe/docs/research-bilibili-ai-subtitle-bv1ng9xbueju.md:1)
- 问题追踪记录：[.scratch/subtitle-track-mismatch-bv1ng9xbueju/README.md](/D:/AI/BiliScribe/.scratch/subtitle-track-mismatch-bv1ng9xbueju/README.md:1)

所有自动化测试中的 B 站请求均使用 mock；真实探测只保留脱敏的状态、数量和范围信息，没有把 Cookie、签名 URL 或字幕正文写入项目。
