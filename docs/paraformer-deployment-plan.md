# BiliScribe 本地 Paraformer-zh 部署准备方案

研究日期：2026-10-20
范围：Windows 本地版；普通话；暂不准备云端部署和 Cloudflare Worker 接入。

## 结论

首版 Paraformer 应作为本地 Python 模块按需加载，而不是单独的 HTTP 服务：

```text
本地 FastAPI / CLI
        |
        | 查询并验证 B站 CC / AI 字幕
        |
        +-- 有任意有效字幕轨道 --> 现有字幕导出，不加载 ASR
        |
        +-- 确认没有有效字幕 --> 下载音频
                                      |
                                      +-- ffmpeg 转 16 kHz mono WAV
                                      +-- FSMN-VAD
                                      +-- Paraformer-zh
                                      +-- ct-punc（可选）
                                      +-- 转为现有 body 并导出
```

当前只准备本地运行环境和模块接口，不安装模型、不改启动脚本、不改变现有无字幕行为。

## 为什么不采用 Gateway

- 当前项目的主程序已经是本地 Python + FastAPI，直接调用本地模型没有跨进程和上传开销。
- Cloudflare Worker 不在本次范围内；它不能运行 Python/PyTorch，也不应影响本地方案。
- 本机 RTX 4060 Laptop 8GB 足以作为 Paraformer 的首轮 CUDA 验证设备；如果 CUDA 环境不通，Paraformer 仍可 CPU 运行。
- 现有 `JobManager` 是串行队列，正好可以避免多个任务同时占用模型显存。

## 当前项目的真实缺口

现有代码已经有字幕列表查询、归属校验、分 P 编排和导出，但还没有：

- B 站音频下载模块；
- `ffmpeg` 检测和 16 kHz mono 转码；
- FunASR / Paraformer 依赖；
- ASR 结果到现有 `from` / `to` / `content` body 的适配；
- ASR 来源、模型版本和设备元数据；
- 无字幕分 P 的自动回退逻辑。

因此不能把“没有字幕”直接等同于“已经可以转写”。本地部署准备应先完成环境 smoke test，再实现音频来源和流程接入。

## 字幕决策规则

每个分 P 必须先判断有没有任何有效 CC / AI 字幕，再应用用户的语言筛选：

```text
subtitle_found  -> 使用已有字幕；不下载音频、不加载 ASR
subtitle_absent -> 下载音频并运行 ASR
subtitle_error  -> 返回可重试错误；不运行 ASR
```

这意味着：视频只有英文字幕、用户只选择中文时，虽然可能没有中文导出文件，但仍不能自动触发 ASR。若以后需要“中文轨缺失时补转写”，应增加单独的显式开关。

`subtitle_absent` 只能来自 B 站字幕接口完成现有重试和归属校验后的空列表。网络错误、登录失效、限流和串轨校验失败都必须保持为错误。

## 本地运行环境

### 必需组件

- Windows x64；当前项目要求 Python 3.10+；
- `ffmpeg` 和 `ffprobe`，加入 PATH；
- FunASR、PyTorch、torchaudio；
- Paraformer-zh 模型及其依赖模型：`fsmn-vad`，可选 `ct-punc`；
- 首次运行时可访问模型下载地址，之后使用本地模型缓存。

FunASR 官方模型卡将 Paraformer-zh 标为 220M、16 kHz、中文/英文、非自回归，并提供 `sentence_info` 时间戳。FunASR 工具包是 MIT，模型权重许可证需按模型卡保留 Apache-2.0 声明。

### CUDA 与 CPU

优先验证 CUDA：

```powershell
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

若输出 `False`，先不要强行使用 `device="cuda"`，应改为 CPU smoke test 或重新安装与驱动匹配的 PyTorch CUDA wheel。PyTorch CUDA wheel 的具体安装命令应按当前驱动和 PyTorch 官方安装页确定，不能把某个 CUDA 版本硬编码进通用 `requirements.txt`。

### 依赖隔离

现有 `requirements.txt` 保持轻量，不把 PyTorch/FunASR 强行加入启动时安装。建议新增可选文件：

```text
requirements-asr.txt
```

其中固定经过 smoke test 的 `funasr` 版本；PyTorch 和 torchaudio 在 Windows 上按 CUDA/CPU 选择单独安装。这样现有用户仍可只使用 CC/AI 字幕，不必下载数百 MB 到数 GB 的 ASR 依赖和模型。

建议配置：

```text
BILISCRIBE_ASR_ENABLED=false
BILISCRIBE_ASR_DEVICE=auto
BILISCRIBE_ASR_MODEL=paraformer-zh
BILISCRIBE_ASR_MAX_AUDIO_MB=512
BILISCRIBE_ASR_MAX_DURATION_SECONDS=7200
BILISCRIBE_ASR_MODEL_DIR=<可选的本地模型缓存目录>
```

本地 Web 启动脚本在完成本地音频 smoke test 后设置 `BILISCRIBE_ASR_ENABLED=true`，网页默认勾选 ASR 回退；CLI 和 MCP 仍要求显式启用。`auto` 优先 CUDA，CUDA 不可用时回退 CPU；若用户显式设置 `cuda` 而 CUDA 不可用，应给出明确错误，不静默切换。

## 模块接口

新增的本地 ASR 实现应藏在一个窄接口后：

```python
class AsrEngine(Protocol):
    model_id: str

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str = "zh",
        offset_seconds: float = 0.0,
    ) -> list[Segment]: ...
```

`Segment` 至少包含：

```text
start: float
end: float
text: str
source: "asr"
model_id: str
timestamp_source: "paraformer_sentence_info" | "derived"
```

首版新增 `ParaformerEngine`：

- 第一次真正进入 ASR 路径时才导入 FunASR 并创建 `AutoModel`；
- 模型实例在当前进程中复用，避免每个分 P 重复加载；
- `device="auto"` 根据 PyTorch CUDA 状态选择设备；
- 对输入统一要求 16 kHz、单声道；
- 将 `sentence_info` 的毫秒时间戳转换为现有字幕 body 的秒数；
- 只返回非空、`end > start` 的片段。

建议的模型构造形态：

```python
AutoModel(
    model="paraformer-zh",
    vad_model="fsmn-vad",
    punc_model="ct-punc",
    device=device,
)
```

第一轮可以不启用说话人识别，避免无关的 `cam++` 权重和额外资源。字幕用途只需要文本、分段时间和标点。

## 音频处理链路

本地音频链路应分成两个可测试的模块：

1. `AudioSource`：接收本地文件，未来再接入 B 站音频下载；
2. `AudioPreprocessor`：调用 `ffmpeg` 转成临时 WAV。

```text
输入音频
  -> 校验时长和大小
  -> ffmpeg -ac 1 -ar 16000 -f wav
  -> Paraformer + FSMN-VAD
  -> sentence_info
  -> Segment
  -> from/to/content
```

每个任务使用独立临时目录，音频和中间文件在成功、失败和异常退出路径中清理。模型缓存目录与任务临时目录必须分开。

长视频不应依赖一次性整段推理。优先让 FSMN-VAD 产生语音区间；如果输入超过模型/内存策略，再按 30~60 秒分块，保留少量重叠，合并时按偏移修正时间戳并去除边界重复文本。

## 接入现有代码的位置

### `biliscribe/service.py`

`extract_one` 当前先按语言过滤 `tracks`。实现 ASR 前应调整为：

1. 保存接口返回的全部有效轨道；
2. 全部轨道为空时才标记该分 P 为 `subtitle_absent`；
3. 有轨道时按 `langs` 过滤并导出现有字幕；
4. 只有 `subtitle_absent` 才调用 `AudioSource` 和 `ParaformerEngine`。

这样可以保持“已有任意字幕时跳过 ASR”的产品规则。

### `biliscribe/subtitles.py`

不新增另一套导出器。ASR 片段转换成：

```python
{"from": segment.start, "to": segment.end, "content": segment.text}
```

继续复用 SRT、VTT、TXT、JSON 和 Markdown 转换器。ASR 元数据应扩展到 JSON/Markdown，例如：

```text
subtitle_source=asr
asr_model=paraformer-zh
asr_device=cuda
asr_timestamp_source=paraformer_sentence_info
```

### `biliscribe/web/server.py` 与网页

现有网页默认请求 `langs=["zh"]`。本地 ASR 首版不必增加复杂模型选择器，增加一个明确的“无字幕时使用本地普通话 ASR”开关，并在结果中显示“机器转写”。网页默认开启；使用前需安装可选 ASR/音频依赖，CLI/MCP 仍默认关闭。

命令行可后续增加：

```text
--asr off|local
```

默认 `off`，开启时还要检查 `BILISCRIBE_ASR_ENABLED` 和本地模型可用性。

## 准备阶段与实现阶段

### P0：环境检查，不改业务代码

- 确认 Python 版本、`ffmpeg -version`、`ffprobe -version`；
- 安装与设备匹配的 PyTorch/torchaudio；
- 安装经过固定版本的 FunASR；
- 验证 `torch.cuda.is_available()`；
- 指定模型缓存目录和临时目录；
- 记录 FunASR、PyTorch、torchaudio、驱动和模型版本。

### P1：本地音频 smoke test

- 使用一段本地普通话 WAV/MP3，不连接 B 站；
- 首次加载 Paraformer-zh、FSMN-VAD、ct-punc；
- 验证输出文本、`sentence_info`、单调时间轴和 SRT 导出；
- 验证 CPU/CUDA 两种设备错误信息；
- 验证临时音频清理和模型缓存保留。

### P2：BiliScribe 模块接入

- 增加 `biliscribe/asr.py` 和本地 `AudioSource` 接口；
- 先用 mock 音频源测试无字幕分 P 的调用；
- 有效字幕分 P 不应导入 FunASR 或创建模型；
- ASR 失败不应覆盖其他分 P 的已有结果；
- 给结果增加 `source=cc|ai_subtitle|asr` 和 ASR 元数据。

### P3：B 站音频下载

仓库目前没有音频下载实现。需要单独决定：

- 使用 `yt-dlp` 获取可访问的音频流，还是直接调用 B 站播放接口；
- 如何复用当前登录 Cookie 和 Referer；
- 是否把 `yt-dlp`/ffmpeg 作为可选安装项；
- 下载失败、权限限制、会员视频和多 P 的错误语义；
- 音频大小、时长和清理策略。

在 P3 完成前，不能把 B 站“无字幕”自动标记为“ASR 已完成”。

## 验证清单

- 有任意有效 CC/AI 字幕：没有音频下载、没有 ffmpeg 调用、没有 FunASR 导入或模型加载；
- 只有非中文字幕且用户选择中文：不调用 ASR；
- 完成重试后的空字幕列表：只对对应分 P 调用 ASR；
- 多 P：有字幕的 P 保持原轨道，无字幕的 P 单独转写；
- 字幕接口异常：不误触发 ASR；
- ASR 结果：时间戳非负、`end > start`、按时间排序、文本非空；
- SRT/VTT/TXT/JSON/Markdown 仍通过现有转换器；
- 取消、失败和成功都会清理任务临时目录；
- 现有不安装 ASR 依赖的用户仍能启动并使用 CC/AI 字幕流程。

## 本次检查后的范围

本次只准备 Windows 本地部署：

- 不创建云 GPU 服务；
- 不修改 Cloudflare Worker；
- 不增加 HTTP Gateway、服务令牌或跨机上传；
- 不在现有基础 `requirements.txt` 中强制加入 PyTorch/FunASR；
- 不把 ASR 模型在应用启动时自动加载；
- B 站音频下载使用可选 `yt-dlp` 适配器，Cookie 仅写入任务临时文件，并由上下文退出清理。
- Windows Web 启动脚本和网页默认启用自动回退；CLI `--asr local` 与 MCP 仍需显式开启，并且必须设置 `BILISCRIBE_ASR_ENABLED=true`。

P3 已实现 B 站音频下载、ffmpeg 转码、大小/时长限制、按分 P 的显式 ASR 回退和失败清理；真实 B 站无字幕视频的端到端验证仍需单独执行。

执行状态：P0、CPU/CUDA 路径 P1、P2 本地接口、stdio MCP 和 P3 代码接入已完成；详见[本地验证记录](paraformer-local-verification.md)及[MCP 配置说明](mcp-local-ai.md)。Windows Web 自动回退已默认开启，MCP/CLI 仍由独立开关控制。
