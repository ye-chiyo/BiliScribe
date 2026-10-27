# 额度恢复后续作交接

当前状态：额度恢复后的实现与验收已完成，待用户体验。以下暂停内容作为历史记录保留。

暂停日期：2026-10-27，Asia/Shanghai。用户明确要求保存交接、等待额度恢复后继续；暂停不代表完成。票 02 实现代理已中断，所有工作区保留，未清理、未 reset、未 push、未部署。

## 目标与已确认约束

继续完整实现《无归属前缀 AI 字幕兼容与归属验证规范》，任务顺序 01 → 02 → 03。用户已调用 implement-spec，要求独立实现 worktree、TDD、merger 集成和最后 Standards/Spec 双轴审查。

用户确认的测试边界：公开本地提取入口 + 真实 BiliClient，仅 mock 外部 HTTP；Worker HTTP 入口 + fetch mock；检查实际导出或可见错误，不 mock 掉字幕发现与归属判断。全部自动测试禁止访问 B 站或使用真实凭证。新主回归必须先红后绿。原始主工作区有本会话未提交文档，保留不动。

## 工作区与 Git 状态

- 原始主工作区：D:\AI\BiliScribe，main，基线 1cf952eaf955c2f6316f7f9f6eaa5c8e42e279a6。没有应用产品修复；已有本会话规范/父 issue/子票未提交副本。
- 集成工作区：C:\Users\614\.codex\worktrees\biliscribe-subtitle-ownership\BiliScribe，分支 codex/subtitle-ownership-integration，HEAD 8e858a0a5da90855252650ae2532f618b95d70fc。暂停前工作区干净；本交接文档为暂停时新增的未提交文件。
- 票 01 工作区：C:\Users\614\.codex\worktrees\biliscribe-ticket01\BiliScribe，分支 codex/subtitle-ticket01，提交 d2b060392f9acd12ec47252c7825722835b6b37c，干净，已经合并到集成分支。
- 票 02 工作区：C:\Users\614\.codex\worktrees\biliscribe-ticket02\BiliScribe，分支 codex/subtitle-ticket02，HEAD 仍为 8e858a0。实现尚未提交，切勿重建、reset 或删除。
- 票 03 尚未启动。

集成提交历史：37c0d5b 发布规范和三票；d2b0603 实现票 01；8e858a0 合并票 01。没有 PR，没有远端推送。

## 规范和任务入口

- 完整规范：docs/spec-subtitle-ownership.md。
- 父 issue：.scratch/subtitle-opaque-id-bv1adn968e2a/README.md。遵守 to-tickets，不修改或关闭父 issue。
- 子票：同目录 issues/01-failure-reasons.md、02-modern-opaque-tracks.md、03-cache-refresh-delivery.md。
- 只读探索笔记（仓库外）：C:\Users\614\.codex\tmp\biliscribe-ownership-exploration.md。
- 技能：C:\Users\614\.agents\skills 下 implement-spec、tdd、code-review 的 SKILL.md。技能已读，恢复后按需刷新。

## 已完成的票 01

本地/云端统一 ownership_conflict、ownership_unverified、auth_required、rate_limited、network_error、subtitle_parse_error、subtitle_api_error；无文件原因包括 subtitle_absent、language_unavailable、subtitle_body_empty。本地不再把归属失败吞成无字幕或 ASR；现代失败不能被旧接口空列表覆盖；网络正文失败不再伪装成空正文。

已验证：Python 61 passed、离线维护 unittest 5 passed、独立字幕保护脚本通过、云端构建和 14 tests passed、diff check 通过。票 01 已记录证据并标为 ready-for-human。

## 未提交的票 02：恢复的第一落点

暂停时修改的六个文件：biliscribe/api.py、biliscribe/service.py、site/worker.mjs、site/tests/ownership.test.mjs、tests/test_ownership_extraction.py、tests/test_subtitle_guard.py。票据证据/完成状态尚未写入。

实现内容：本地客户端持有 SubtitleApproval 和授权 registry；Worker 使用 WeakMap。请求层为实际现代响应创建视频/aid/cid/轨道 fingerprint/时间/策略版本绑定；生产正文下载使用批准轨道接口，下载前后校验。现代 32 位十六进制不透明资源限定接收；先安全验证、解码，再分类；明确作者冲突优先拒绝；旧接口来源字段不能自行授权。新不透明轨道列表/正文缓存暂不复用，留给票 03。混合列表逐轨处理，不再因一个坏轨整体拒绝。

代理报告的证据：

- Python 与 Worker 的主回归均已实际 RED → GREEN，通过真实 Protobuf、编码地址、公开导出，生成固定预期文本，不请求旧字幕接口。
- 两组地址编码密钥、32 位全数字资源、混合轨、未知地址/编码及旧接口伪造来源均有公开路径样例。
- Python 全套最近一次 73 passed；离线维护 5 passed；独立保护脚本通过。
- Worker focused 曾 19 passed，完整 cloud 曾 22 passed；之后增加若干用例，最近新增四个 focused 通过。最后一次全部云端测试尚待确认/重跑。
- 一个全数字 fixture 曾误写为 34 位，已修为真正 32 位；未因该样本修改产品规则。

恢复后先看 diff，运行最终验证；检查有无遗留测试进程。补写票 02 证据与状态后提交，再用 merger 子代理合并到集成分支。不要把代理报告当作父级最终验收。

## 票 03 与最终交付待办

1. 票 02 合并后，从最新集成 tip 创建票 03 独立 worktree/分支，TDD 实现版本化列表来源缓存及重新验证、旧缓存未命中、完整视频/分 P/资源隔离正文缓存、签名及空正文刷新。复用既有归属与来源记录，不从调用方字典补造证明。
2. 当前低层 fetch_subtitle 仍可直接下载，生产导出必须走批准轨道路径；最终所有缓存读取/刷新/导出要保持同一验证语义。只按 resource id 的旧正文缓存不能串用。
3. 完成全部 T01–T16，语言/分 P/链接与五种格式固定预期；更新票 03 证据。
4. 对整个集成分支，以原始基线 1cf952e 为固定点，运行 code-review 的 Standards 和 Spec 两个独立子代理；用一个实现子代理修复有效发现，再做必要复测。
5. 真实本地用户路径关闭 ASR 验证目标视频，保留脱敏结果和导出；云端未部署，须分别说明自动 mock 覆盖与实际运行范围。
6. 启动集成工作区独立本地体验服务（建议先查 8771 是否空闲），复制凭证时只复制到该隔离工作区 data，不能修改原始主工作区凭证。保留原始 8765/8766 服务。
7. 完成后清理实现 worktree，保留集成 worktree供体验；未合并/未提交工作区绝不清理。无 push 或部署授权。

## 运行工具与验证命令

Python 使用 D:\AI\BiliScribe\.venv\Scripts\python.exe，并明确 workdir 为待测 checkout，不能在原始主工作区运行新分支验收。Node/npm 位于 D:\node。新的 checkout 需 npm install --ignore-scripts --no-package-lock（site 目录）；云端始终先 build 再 test。Python 新公开测试已加入 pytest.ini 的显式收集名单。

从相应 checkout 执行 Python pytest、unittest tests.test_offline_maintenance、tests/test_subtitle_guard.py；云端 npm --prefix site run build 与 npm --prefix site test；最后 git diff --check。

手工真实验证脚本（不属于自动测试）：C:\Users\614\.codex\tmp\verify-biliscribe-subtitle.py。参数 --repo 指定 checkout，--credentials 指向本地既有凭证文件，--out 指向仓库外独立输出。脚本调用真实公开 extract_one、中文、五种格式、ASR 关闭，只输出脱敏摘要，不记录原始异常或签名 URL。

修复前完整提取对照已保存在 C:\Users\614\.codex\tmp\biliscribe-live-before\summary.json：2026-10-27 23:03:11 +08:00，status=no_subtitle，tracks=0，files=[]。修复后的真实检查尚未运行。可在最终集成版用同脚本输出到新目录做对照。

## 不得夸大的结论

现代来源绑定依赖服务端按 pid/oid 返回正确资源，不能识别无冲突字段的所有服务端误配。字幕内容相符、252 段、末尾 822.77 秒接近 824 秒仅为人工佐证，不单独授权归属。票 02 未提交、票 03 未实现、最终双轴审查和用户路径验收尚未完成；整个修复不能登记完成。

## 额度与恢复

暂停时应用报告短窗口已用 98%，周窗口已用 92%。短窗口显示重置时间为 2026-10-28 01:58:51 +08:00；周窗口仍有独立限制。恢复时重新查询额度，不把旧快照当作当前余额。用户要求额度恢复后继续原任务，不能另起无关任务。

已创建当前聊天的一次性续作心跳：automationId=biliscribe，ACTIVE，计划当地时间 02:05；规则 COUNT=1。续作先重新查询额度。票02改动另有仓库外补丁备份 C:\Users\614\.codex\tmp\biliscribe-ticket02-paused.patch。

## 2026-10-28 续作记录
02:05 额度检查：短窗口已重置，周窗口已用93%。票02最终检查通过并提交718a9c6，由merger合并为30ee037。票03已从该tip在独立工作区启动。02:07已用票02真实提取目标视频，得到1条ai-zh、5个文件、252段、末尾822.77秒；摘要 C:\Users\614\.codex\tmp\biliscribe-live-ticket02\summary.json。最终验收尚待票03与审查。

## 续作最终状态

票03提交1110bc6已集成；审查修正8cebe7c已集成至a5ee1f9。两个独立审查轴均确认未解决项0。2026-10-28 02:27:08，最终服务8771两次真实HTTP提取成功，各1轨5文件，10下载与文件一致。集成分支codex/subtitle-ownership-integration保留用于体验，工单ready-for-human，未修改父issue，未推送或部署。真实云端尚未验证。最终验收和审查见docs/verification-subtitle-ownership.md及docs/review-subtitle-ownership.md。
