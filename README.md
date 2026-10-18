# BiliScribe · B站视频字幕本地提取工具

粘贴一个 B 站视频链接，提取其中文/英文 CC 或 AI 字幕，导出为 **SRT / VTT / TXT / JSON / Markdown**。
本地版在电脑上运行，登录凭证保存在项目 `data/cookies.json`；工具提取 B 站已经提供的 CC/AI 字幕，**不包含 ASR 语音识别**。

当前版本：**v0.3.1**（深色影院风网页控制台 · 字幕归属校验）

## 云端版本

私有 Sites 站点：[BiliScribe Cloud](https://biliscribe-cloud.shatancool1981.chatgpt.site)。云端版源代码在 [`site/`](site/)；它是原生 JavaScript Worker/Sites，不读取本机 `data/cookies.json`，首次使用需单独扫码登录。凭证加密后保存在浏览器的 HttpOnly、Secure、SameSite 会话 Cookie 中；字幕最多在 Worker 实例内存缓存 24 小时，实例回收即清除，导出由浏览器下载且不会永久保存。云端每次最多处理 3 个链接、单视频 8 个分 P；本地版适合更大的批量任务。

## 功能

- 扫码登录 B 站（一次登录，凭证约 **180 天**有效，期间无需再扫）
- 支持 `bilibili.com/video/BV...`、`b23.tv` 短链（自动跟随跳转）、带 `?p=` 的分 P 链接（未另选分 P 时只提取链接所指分 P）
- 单个 / 多个链接批量、多 P 视频批量
- 字幕语言可选：**仅中文（默认）/ 全部语言 / 仅英文**（网页分段切换，命令行 `--langs`）
- 默认导出 Markdown 笔记；也可选择 SRT（带时间轴，可挂字幕播放）、VTT、纯文本 TXT、原始 JSON
- 本地网页控制台（浏览器操作）+ 命令行两种用法
- 作业**串行队列**：多个提取任务自动排队，避免并发触发 B 站限流
- 自动限速、设备指纹预热、有限退避重试与 24 小时本地缓存；缓存按视频/资源隔离，并在缓存和导出前验证字幕归属
- 8765 端口被占用时自动向后寻找可用端口

## 快速开始（Windows）

1. 安装 **Python 3.10+**（勾选 *Add Python to PATH*）：https://www.python.org/downloads/
   （`启动.bat` 会自动检测版本，低于 3.10 会给出明确提示）
2. 双击 **`启动.bat`**（首次会自动创建虚拟环境并安装依赖，需联网，约 1–3 分钟）
3. 浏览器自动打开 `http://127.0.0.1:8765`（若端口被占，命令行窗口会显示实际端口）
4. 首次使用点「**扫码登录**」，用手机 B 站 App 扫码确认（建议小号）
5. 粘贴视频链接 → 选择格式与语言 →「开始提取」，完成后可直接下载或点「打开文件夹」

关闭黑色命令行窗口即停止服务；下次使用再双击 `启动.bat` 即可。

## 开发与测试

本地版使用 Python 3.10+、FastAPI、Uvicorn、httpx 和 qrcode；云端版使用原生 JavaScript Worker/Sites 与 Node.js 内置测试运行器。当前保留同步 `httpx.Client`，连接在单个串行作业中复用；队列和请求间隔用于控制 B 站访问频率，批量任务的实际速度受网络、字幕轨数和服务端限流影响。

```powershell
# Python 离线回归，不需要 pytest，也不访问 B站
.\.venv\Scripts\python.exe -m unittest tests.test_offline_maintenance
.\.venv\Scripts\python.exe tests\test_subtitle_guard.py
.\.venv\Scripts\python.exe -m compileall -q biliscribe
.\.venv\Scripts\python.exe tests\benchmark_mock.py

# 云端构建与 mock 测试
cd site
npm test
npm run build
```

`tests/test_web.py`、`tests/test_web_extract.py` 和 `tests/test_web_extras.py` 是面向已启动本地网页服务的手工端到端脚本；前两者需要真实登录或 B 站访问，不属于自动化测试。自动化回归和基准使用 mock。

本地缓存位于 `data/cache/`，默认有效 24 小时；失效文件在本地服务启动时清理。登录凭证位于 `data/cookies.json`，通过临时文件原子替换保存，登出时删除。`data/` 与 `output/` 均为本地运行数据，不应提交或分享；日志不会记录 Cookie 值。

## 命令行用法

```bash
# 在项目目录激活虚拟环境后：
python -m biliscribe login                 # 扫码登录
python -m biliscribe status                # 查看登录状态与凭证到期时间
python -m biliscribe extract "视频链接"      # 默认导出 md（仅中文）
python -m biliscribe extract "链接" --fmts srt,md --pages 1,3-5
python -m biliscribe extract "链接" --langs zh      # 仅中文轨
python -m biliscribe extract "链接" --langs en      # 仅英文轨
python -m biliscribe extract "链接1" "链接2"
echo "链接文本" | python -m biliscribe extract      # 支持管道输入
python -m biliscribe web --port 8765       # 启动网页控制台
python -m biliscribe logout                # 删除本地凭证
```

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
│  ├─ cli.py          # 命令行入口
│  └─ web/            # 本地网页控制台（FastAPI + 原生 HTML/CSS/JS，零构建）
├─ data/              # 登录凭证与缓存（请勿外传、勿截图分享）
├─ output/            # 导出的字幕文件
├─ tests/             # 单元测试与 Web 冒烟/端到端测试
├─ requirements.txt
├─ 启动.bat
└─ README.md
```

## v0.3.1 更新要点

- **降低「提取到其他视频字幕」的风险**：当字幕资源 id 含有 aid+cid 归属段时，校验其是否属于当前视频；校验失败会退避重试。部分纯十六进制资源 id 不含可核验的归属段，当前只能放行，无法保证所有字幕轨都可验证归属。
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
- **视频提示「无所选语言的 CC/AI 字幕」？** 可能是视频没有该语言字幕，也可能是接口临时限流返回空列表；可稍后重试。本项目当前不包含 ASR 语音转写。
- **网页上明明有字幕，软件却偶尔提示无字幕？** 短时间高频请求会触发 B 站临时限流（字幕列表概率性返回空）。软件已内置设备指纹、视频页预热、退避重试与 24 小时本地缓存；遇到时等几分钟再试即可，成功提取过的视频再次导出走缓存、不再请求接口。
- **提示「字幕接口多次返回其他视频的数据」？** B 站接口偶发把别的视频字幕轨返回给当前视频（v0.3.1 起会自动识别并重试）。若多次重试仍失败，等几分钟再提取即可；该提示意味着本次没有写入任何字幕文件，不会产生内容错误的文件。
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
