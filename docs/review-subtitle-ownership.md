# 字幕归属修复双轴审查

固定点：1cf952e；初审：7e731d1；修复复审：a5ee1f9。两轴独立审查，以下保留原始发现和复审结论。

## Standards


Baseline: `1cf952e`; reviewed integration: `7e731d1`.

1. **[P2] Classify local legacy HTTP and JSON failures before interpreting an empty subtitle response** — `biliscribe/api.py:432` (request path at lines 413–423). The local JSON transport never checks HTTP status, and its new final error always uses `network_error`. For a modern empty response followed by legacy HTTP 401/429 with HTML, local extraction reports `network_error`; with a JSON body lacking an API code it can return an empty successful result and report `subtitle_absent`. A malformed successful JSON response also becomes `network_error`. Worker explicitly maps HTTP 401/403 to `auth_required`, 429 to `rate_limited` (`site/worker.mjs:104`), and malformed JSON to `subtitle_parse_error` (line 122). Normalize HTTP status and JSON decoding failures in the local adapter before accepting `data`, then cover those legacy cases at the existing public extraction seam. This is a concrete adapter-consistency defect under the repository's single-context/two-adapter guidance (`docs/agents/domain.md`) and the stated parity review requirement.

No other actionable documented-standard violations found in the inspected change. New tests mock external requests, source approval precedes production cache use/export, no new ASR path or dependency stack was introduced, and local issue statuses use the configured vocabulary. No additional smell-only findings: repeated context arguments are related to the necessary boundary checks, and changing them now is not justified by a concrete defect.

Summary: one concrete parity finding (P2); no additional smell findings. Read-only review, no tests run.

## Recheck at a5ee1f9

The Standards P2 finding is resolved by fix `8cebe7c`. The local JSON transport now checks HTTP status before decoding, rejects invalid response envelopes, maps 401/403 and 429 to the same reasons as Worker, and classifies malformed JSON as `subtitle_parse_error`. Public extraction regressions cover HTTP 401/429, malformed JSON and a missing API code, assert the expected reason and verify no export/body request. The associated bounded-fallback changes preserve prior modern ownership conflicts through later legacy failures; the shared failure classifier is also used for local body errors.

Inspected `git diff 7e731d1..HEAD` and `docs/verification-review-fixes.md`; no new actionable Standards defects were introduced by this fix. Reported final checks are 94 Python and 39 Worker tests; this read-only recheck did not rerun them. Current Standards outstanding findings: **0**.

## Spec


固定点：1cf952eaf955c2f6316f7f9f6eaa5c8e42e279a6；受审 HEAD：7e731d141b09123f1326b705fd14a11976392fa3。静态审查，未运行测试，未修改仓库。

1. **[P2] 旧接口回退在两端不是同一个状态机。** 规范第 51 行要求“按既有限次策略回退旧接口，保留前序失败原因”，第 57 行要求“两端……同一决策表”。Python `biliscribe/api.py:623` 未捕获旧接口异常，且 `:636–641` 遇到首个拒绝列表立即抛出；Worker `site/worker.mjs:493–505` 保留 failure 并继续其余轮次。因此现代为空、旧接口第一轮不透明、第二轮返回合格轨时，本地失败而云端成功；现代明确冲突后旧接口网络失败时，本地覆盖成网络错误，云端保留冲突。建议统一有界回退与最终错误优先级，并用相同公开场景锁定两端结果。

2. **[P2] 正文 HTTP 登录/限流错误在 Python 被统一降为网络错误。** 规范第 52 行要求错误类型可辨识，第 53 行要求“reason 的具体枚举在两端实现中一致”，第 57 行要求同一场景一致。Python `biliscribe/api.py:720–725` 将所有 HTTPError 最终映射到 network_error；正文 401、429 因此丢失登录/限流含义。Worker `site/worker.mjs:105–107` 分别产生 auth_required、rate_limited，并在 `:537–540` 原样传播（403 的签名刷新另行处理）。建议正文错误复用状态分类，增加两端 401/429 公开提取对照。

除上述两项之外，未发现需要报告的新增范围或来源/缓存授权缺陷。现代源绑定的服务端信任限制符合规范，不应描述成密码学归属证明。

合计：Spec 2 项，均为 P2；主要问题为回退路径的本地/云端结果不一致。

## 修复复查
受审版本 a5ee1f92422d08a3216f10cd0e949f0a7a77767f（修复提交 8cebe7c）。只检查相对 7e731d1 的修复 diff 与 verification-review-fixes 记录，未重跑测试。
两项原 Spec P2 均已解决：本地旧接口现保持三轮有界回退，并保留前序失败、让明确冲突优先；其控制流与 Worker 对齐。正文 HTTP 401/429 通过共用状态分类保留 auth_required/rate_limited，403 仍走签名刷新。新增公开入口测试分别覆盖后续轮恢复、现代冲突后旧接口失败以及正文401/429，且两端有固定预期。
此次修复范围内未发现新的可操作问题。当前未解决 Spec 发现：0。

汇总：Standards 原 1 项 P2、现 0 项；Spec 原 2 项 P2、现 0 项。
