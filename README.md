# BiliScribe · B站视频字幕本地提取工具

粘贴一个 B 站视频链接，提取其中文/英文 CC 或 AI 字幕，导出为 **SRT / VTT / TXT / JSON / Markdown**。
纯本地运行，登录凭证只保存在你自己的电脑上（无数据库、无云端）。

当前版本：**v0.3.1**（深色影院风网页控制台 · 字幕归属校验）

## 云端版本

私有 Sites 站点：[BiliScribe Cloud](https://biliscribe-cloud.shatancool1981.chatgpt.site)。云端版源代码在 [`site/`](site/)；首次使用需要在云端页面重新扫码登录，不会上传本机 `data/cookies.json`。导出文件直接在浏览器下载，云端不永久保存字幕。每次最多处理 3 个链接、单视频 8 个分 P；本地版仍适合更大的批量任务。

## 功能

- 扫码登录 B 站（一次登录，凭证约 **180 天**有效，期间无需再扫）
- 支持 `bilibili.com/video/BV...`、`b23.tv` 短链（自动跟随跳转）、带 `?p=` 的分 P 链接（未另选分 P 时只提取链接所指分 P）
- 单个 / 多个链接批量、多 P 视频批量
- 字幕语言可选：**仅中文（默认）/ 全部语言 / 仅英文**（网页分段切换，命令行 `--langs`）
- 默认导出 Markdown 笔记；也可选择 SRT（带时间轴，可挂字幕播放）、VTT、纯文本 TXT、原始 JSON
- 本地网页控制台（浏览器操作）+ 命令行两种用法
- 作业**串行队列**：多个提取任务自动排队，避免并发触发 B 站限流
- 自动限速、设备指纹预热、退避重试与 24 小时本地缓存，礼貌访问
- 8765 端口被占用时自动向后寻找可用端口

## 快速开始（Windows）

1. 安装 **Python 3.10+**（勾选 *Add Python to PATH*）：https://www.python.org/downloads/
   （`启动.bat` 会自动检测版本，低于 3.10 会给出明确提示）
2. 双击 **`启动.bat`**（首次会自动创建虚拟环境并安装依赖，需联网，约 1–3 分钟）
3. 浏览器自动打开 `http://127.0.0.1:8765`（若端口被占，命令行窗口会显示实际端口）
4. 首次使用点「**扫码登录**」，用手机 B 站 App 扫码确认（建议小号）
5. 粘贴视频链接 → 选择格式与语言 →「开始提取」，完成后可直接下载或点「打开文件夹」

关闭黑色命令行窗口即停止服务；下次使用再双击 `启动.bat` 即可。

## 命令行用法

```bash
# 在项目目录激活虚拟环境后：
python -m biliscribe login                 # 扫码登录
python -m biliscribe status                # 查看登录状态与凭证到期时间
python -m biliscribe extract "视频链接"      # 默认导出 md（仅中文）
python -m biliscribe extract "链接" --fmts srt,md --pages 1,3-5
python -m biliscribe extract "链接" --langs zh      # 仅中文轨
python -m biliscribe extract "链接" --langs en      # 仅英文轨
python -m biliscribe extract "链接" --asr local    # 无字幕分P时启用本地 ASR
python -m biliscribe extract "链接1" "链接2"
echo "链接文本" | python -m biliscribe extract      # 支持管道输入
python -m biliscribe web --port 8765       # 启动网页控制台
python -m biliscribe logout                # 删除本地凭证
```

## AI 客户端（MCP）

可选安装 `requirements-mcp.txt` 后，通过 stdio MCP 提供现有字幕提取、能力查询和本地音频转写工具。ASR 默认关闭；Windows 客户端配置示例与允许目录说明见 [`docs/mcp-local-ai.md`](docs/mcp-local-ai.md)。

网页默认勾选“无字幕时使用本地 ASR”，Windows `启动.bat` 会为本地 Web 服务启用该能力；
仅当接口确认无有效字幕轨（包括确认返回其他视频的字幕轨）时才会下载音频并转写；
字幕接口网络、登录或限流错误仍按错误返回，不会误当成无字幕。首次配置需按本机硬件安装
`requirements-asr.txt` 中的 FunASR/PyTorch（CUDA 版请先选择匹配的 PyTorch wheel）、
安装 `requirements-audio.txt` 中的 yt-dlp，并确保 ffmpeg/ffprobe 在 PATH 中。
CLI 仍需显式传入 `--asr local` 且设置 `BILISCRIBE_ASR_ENABLED=true`；MCP 的 ASR 开关
也继续独立控制，默认关闭。未安装可选依赖时，取消网页勾选仍可正常提取 CC/AI 字幕。

## 输出文件与命名

- 默认输出到项目下的 `output/` 目录
- 单 P 视频：`{视频标题}_{BV号}.{语言}.{格式}`，例如 `堵桥来_BV11gYd6UEPv.ai-zh.srt`
  （文件名带 BV 号，不同视频同名标题也不会互相覆盖）
- 多 P 视频：自动建立 `{标题}_{BV号}/` 子文件夹，文件名为 `P{序号}-{分P标题}.{语言}.{格式}`
- **SRT 文件带 UTF-8 BOM**，Windows 记事本 / Pr / 剪映可直接正确识别中文，不会乱码
- VTT / TXT / JSON / Markdown 为 UTF-8 编码；时间轴已做毫秒进位处理（不会出现 `,1000` 毫秒）

## 目录结构

```
BiliScribe/
├─ biliscribe/
│  ├─ api.py          # B站接口客户端（链接解析/视频信息/字幕/扫码登录）
│  ├─ auth.py         # 登录凭证本地持久化（原子写入，防损坏）
│  ├─ subtitles.py    # SRT/VTT/TXT/JSON/MD 转换（时间轴进位/缺字段容错）
│  ├─ service.py      # 提取编排、语言筛选、串行作业队列、文件命名
│  ├─ asr.py          # 可选 Paraformer 引擎与字幕片段接口
│  ├─ audio.py        # 可选 B 站音频下载、ffmpeg 转码、临时文件清理
│  ├─ mcp_server.py   # 可选 stdio MCP 服务
│  ├─ cli.py          # 命令行入口
│  └─ web/            # 本地网页控制台（FastAPI + 原生 HTML/CSS/JS，零构建）
├─ data/              # 登录凭证与缓存（请勿外传、勿截图分享）
├─ output/            # 导出的字幕文件
├─ tests/             # 单元测试与 Web 冒烟/端到端测试
├─ requirements.txt
├─ 启动.bat
└─ README.md
```

## 测试

在项目虚拟环境中运行离线单元测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

仓库根目录的 `pytest.ini` 将默认收集范围固定为 `tests/` 中的离线测试，
避免扫描 `github-checkout/` 等嵌套仓库。Web 冒烟和真实 B 站提取测试需要
先启动本地服务及其依赖，默认不由 pytest 收集；准备好环境后可显式运行：

```powershell
.\.venv\Scripts\python.exe -m biliscribe web --port 8765
# 在另一个终端中：
.\.venv\Scripts\python.exe tests/test_web.py
.\.venv\Scripts\python.exe tests/test_web_extras.py
.\.venv\Scripts\python.exe tests/test_web_extract.py
```

## v0.3.1 更新要点

- **降低「提取到其他视频字幕」的风险**：当字幕资源 id 含有 aid+cid 归属段时，校验其是否属于当前视频；确认串轨时拒绝该响应，并在本地 ASR 已启用时立即回退。部分纯十六进制资源 id 不含可核验的归属段；生产请求会同时使用字幕作者归属信息进行校验。
- 字幕列表查询统一走 bvid 形式（av 号链接经视频信息接口换算为 bvid），不再使用 aid 形式
- 正文缓存键改为稳定的字幕资源 id（不再含时效签名 auth_key），同一字幕轨刷新签名后仍命中缓存，且缓存天然按视频隔离
- 本地缓存的字幕列表同样经过归属校验，历史脏缓存会自动丢弃重拉

## v0.3.0 更新要点

- 网页控制台重新设计：深色影院风单栏布局、账号状态胶囊、扫码登录居中弹层、Toast 提示、移动端自适应
- 修复「字幕语言」选择在网页端不生效的问题（此前始终下载全部语言）
- 修复 SRT/VTT 毫秒四舍五入到 1000 时不进位、Markdown 时间戳超过 1 小时不显示小时的问题
- 修复字幕条目缺字段时可能报错中断的问题
- 文件名加入 BV 号，避免同名视频互相覆盖；SRT 改为带 BOM 的 UTF-8
- 提取作业改为串行队列，网页端可见「排队中」状态，任务列表自动限流清理
- 端口被占用时自动寻找可用端口，不再直接崩溃
- 断网时区分「网络异常」与「未登录」，不再误报登录失效
- 凭证文件改为原子写入，避免写入中途异常损坏
- 短链尾随中文标点（句号、括号等）自动清洗
- `启动.bat` 增加 Python 版本检测；命令行无参数运行时给出提示而非挂起

## 常见问题

- **提示未登录 / 字幕为空？** B 站字幕接口必须登录，游客拿不到字幕列表；请先扫码登录。
- **顶栏显示「网络异常，登录态未校验」？** 断网时无法联网校验凭证，本地凭证仍在；网络恢复后刷新页面即可。
- **二维码失效？** 二维码 180 秒有效，失效后点弹层中的「重新获取二维码」即可；重复点扫码会自动释放上一个二维码会话。
- **视频提示「无所选语言的 CC/AI 字幕」？** 视频可能没有所选语言的字幕；仅当确认没有任何有效字幕轨时，网页才会按设置回退到本地 ASR。字幕接口网络、登录或限流错误会保留为错误，不会启动 ASR。
- **网页上明明有字幕，软件却偶尔提示无字幕？** 短时间高频请求会触发 B 站临时限流（字幕列表概率性返回空）。软件已内置设备指纹、视频页预热、退避重试与 24 小时本地缓存；遇到时等几分钟再试即可，成功提取过的视频再次导出走缓存、不再请求接口。
- **字幕接口返回其他视频的轨道？** 程序会拒绝串轨数据，并立即对该分 P 回退到本地 ASR；不会导出疑似其他视频的字幕。
- **支持哪些字幕语言？** 视频提供几种语言就可导出几种（实测可同时拿到中、英、西、葡等 AI 翻译轨）；语言代码按词缀匹配，如 `zh` 同时命中 `ai-zh` 与 `zh-CN`。
- **换电脑 / 重新安装？** 复制整个项目目录即可；`data/` 里是登录凭证，不要分享给他人。
- **安全吗？** 程序只与 `bilibili.com` 通信，凭证保存在本地 `data/`（原子写入），日志不会打印 Cookie。

## 合规声明

本工具仅供**个人学习与研究**：请使用本人账号访问你本有权限观看的内容，不得用于大规模爬取、
公开再发布/转售字幕、绕过付费或权限控制。字幕版权归 UP 主与哔哩哔哩所有，因使用产生的
后果由使用者自行承担。

## 致谢

接口行为参考社区文档 [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)
与 [BBDown](https://github.com/nilaoda/BBDown)、[yt-dlp](https://github.com/yt-dlp/yt-dlp) 等开源项目。
