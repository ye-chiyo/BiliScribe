# 双轴审查修复验证

受审集成版本：7e731d1；原始固定点：1cf952e。Standards 轴发现 1 项 P2，Spec 轴发现 2 项 P2，均为本地与 Worker 的失败路径行为差异。本次集中修复这三项，不新增产品能力或外部依赖。

## 修复结果

1. 本地旧接口改为与 Worker 相同的有界回退：首轮无法验证时仍可在后续轮取得合格轨道；现代接口已经确认的冲突不被后续网络失败覆盖。最终没有合格结果时保留失败，仍禁止正文下载、导出和 ASR 掩盖。
2. 本地正文 HTTP 401、429 分别保留 auth_required、rate_limited，不再统一变成 network_error；403 签名刷新保持既有独立处理。
3. 本地 JSON 传输层先检查 HTTP 状态再解码；损坏 JSON 为 subtitle_parse_error，缺少 API code 的响应为 subtitle_api_error，不再误报确认无字幕。

## 公开路径的红绿证据

- 旧接口先返回不透明资源、后返回匹配资源：原本抛 UnverifiedSubtitleError；修复后成功导出独立固定文本。Worker 同场景成功。
- 正文 401、429：原本得到 network_error，与固定预期的 auth_required、rate_limited 不符；修复后通过。
- 旧接口 HTTP 401、429 配空 JSON：原本未抛出错误，成为假无字幕；修复后分类正确。
- 旧接口损坏 JSON：原本 network_error，修复后 subtitle_parse_error；200 且缺少 API code 的空对象从假无字幕变为 subtitle_api_error。
- 现代冲突之后旧接口 HTTP 503：两端均保留 ownership_conflict，且没有正文请求。

所有新增场景均通过已确认的公开提取边界：本地真实 BiliClient 和提取编排只替换外部 HTTP，Worker 通过公开 HTTP 提取入口、mock 外部 fetch。预期状态和文本为独立常量，测试无真实 Cookie、无真实 B 站访问。旧保护测试中要求“首轮立即结束”的断言已改成三轮有界重试，继续断言冲突异常和不落盘。

## 最终检查

- Python 完整 pytest：94 passed。
- 离线维护 unittest：5 passed。
- 独立字幕保护脚本：通过。
- Worker 重新构建后的完整测试：39 passed。
- git diff --check：通过。

真实目标视频、最终体验服务以及部署边界由集成阶段另外记录；本修复代理没有访问真实 B 站、推送或部署。
