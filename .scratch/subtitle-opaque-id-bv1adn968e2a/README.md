# BV1aDn968E2A 无前缀 AI 字幕被归属校验误拒

Labels: `ready-for-agent`  
类型：修复规范，待实现  
日期：2026-10-27（Asia/Shanghai）

## 问题与证据

目标视频存在现代接口可读取的 ai-zh，但本地公开客户端方法拒绝了它。实测 aid=117505221724658、cid=43061546452、视频时长 824 秒；现代接口两次均返回一条资源 ID 为 e8edb3c0dc6f558b5b433ba24ec806be 的轨道，正文 252 段，结束于 822.77 秒。

直接原因：本地和 Worker 都要求中文字幕资源 ID 带目标 aid+cid 前缀，因此无法接收本案例的不透明 ID；现代候选过滤后进入旧接口，本地最终抛出 ForeignSubtitleError。正文内容和时长仅作核对，不单独证明归属。旧接口观测不稳定，不能把所有拒绝都断言为已证实串轨。

## 解决方案与实施契约

完整规范：[无归属前缀 AI 字幕兼容与归属验证规范](../../docs/spec-subtitle-ownership.md)。此链接文档是本 issue 的唯一完整规格，后续更新以它为准。

采用资源身份验证与现代请求来源绑定两种明示依据；无明确冲突的受支持现代不透明资源可接收，旧接口不明资源继续拒绝。同步本地/云端、缓存读写和正文刷新语义，区分空列表、无法验证、明确冲突和接口错误。现代来源信任不能保证发现所有服务端误配，详见规范的信任假设。

用户已确认测试边界为公开提取入口、mock 外部请求、检查导出或可见错误；协议/解码保留少量底层测试。需先用真实客户端网络 mock 场景重现失败，再实现。不得 mock 掉字幕发现和归属判断。

## 验证命令与交付

从仓库根运行；这些命令是后续实施要求，本次文档工作未执行实现验收：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m unittest tests.test_offline_maintenance
.\.venv\Scripts\python.exe tests\test_subtitle_guard.py
npm --prefix site run build
npm --prefix site test
```

另需关闭 ASR 的目标视频真实提取检查，并分别说明本地和云端的已验证范围。自动测试不得访问 B 站或使用真实凭证。本 issue 为 ready-for-agent，不表示实现完成；当前只发布规范，没有修改产品代码或部署。
