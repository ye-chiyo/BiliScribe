# 01: 准确区分字幕为空、无法验证、归属冲突与接口失败

**What to build:** 本地与云端公开提取结果准确表达字幕失败原因，不把证据不足宣称为串轨，也不把归属失败转换为无字幕或 ASR 成功。来源为父 issue《BV1aDn968E2A 无前缀 AI 字幕被归属校验误拒》及规范《无归属前缀 AI 字幕兼容与归属验证规范》。

**Blocked by:** None (can start immediately).

**Status:** ready-for-human

- [x] 旧接口不透明资源报告无法验证，支持的身份资源明确冲突时报告归属冲突；两端 reason 与 message 一致。
- [x] 空列表、语言不匹配、正文空、网络、登录、限流及解析失败的结果保持可辨识。
- [x] 拒绝资源无正文下载、导出或音频转写；本地异常编排不吞掉归属失败。
- [x] 公开本地提取与 Worker HTTP 入口完成 T03、T04、T11、T16 的离线验证；真实客户端只 mock 外部 HTTP，不 mock 发现与归属判断。
- [x] 单场景先红后绿，既有成功路径不回归；测试仅使用独立临时数据与合成凭证，不访问 B 站。


## Implementation evidence

- Base: integration branch at 37c0d5b; implementation isolated on codex/subtitle-ticket01.
- Public Python regressions use the real BiliClient and extraction interface with mocked HTTP transport; cloud regressions use the Worker HTTP endpoint with mocked external fetch and time.
- Red: the unverified legacy case initially failed with “DID NOT RAISE BiliError”, demonstrating that the foreign error was swallowed into the no-subtitle/ASR branch. Cloud initially returned no machine-readable reason. Both now pass with ownership_unverified and no export.
- Additional red/green checks cover retaining modern identity conflicts across empty legacy fallback, preserving rate-limit and HTTP auth/rate failures, and distinguishing empty lists, unmatched languages, empty bodies, and body network failure.
- Validation: Python pytest 61 passed; offline-maintenance unittest 5 passed; standalone subtitle guard passed; cloud build and 14 Node tests passed; git diff --check passed.
- Modern opaque acceptance and versioned source/cache binding intentionally remain for tickets 02 and 03. No real Bilibili access, credentials, deployment, or parent-issue change in this ticket.
