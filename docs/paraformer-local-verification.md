# Paraformer 本地部署验证记录

验证日期：2026-10-21
范围：P0 环境检查、P1 本地音频 smoke test（CPU 与 CUDA）、P2/P3 离线与真实视频回退验证

## 已完成

- 项目 `.venv` 使用 Python 3.13.13（Windows x64）。
- `ffmpeg`/`ffprobe` 已在 PATH，版本 9.0.2。
- 已安装可选 ASR 依赖：`funasr==1.4.16`、`torch==2.11.0+cu128`、`torchaudio==2.11.0+cu128`、`setuptools==81.0.0`。
- 已验证 `funasr`、`torch`、`torchaudio` 可以导入。
- 已验证输入音频可由 ffmpeg 转为 16 kHz、单声道、PCM WAV。
- 已成功加载 Paraformer、FSMN-VAD 和 ct-punc，并完成本地推理。
- 官方样本推理返回 `sentence_info`：1070 ms 到 4365 ms，转换为现有 SRT 输出成功。
- 2 秒纯音频样本没有语音，推理返回空片段，未产生伪字幕。
- CUDA 推理已完成：`torch.cuda.is_available()=True`，设备为 RTX 4060 Laptop GPU；Paraformer、FSMN-VAD、ct-punc 均在 GPU 加载，峰值显存约 2.1 GB，样本推理 RTF 约 0.158。
- CUDA 路径默认结果包含字级 `timestamp`；传入 `sentence_timestamp=True` 后返回 `sentence_info`，句级范围为 1070--4365 ms。

## 当前设备状态

CUDA 验证前 `.venv` 中安装的是 CPU wheel；现已切换为 CUDA 12.8 wheel：

```text
torch=2.11.0+cu128
torchaudio=2.11.0+cu128
torch.cuda.is_available()=True
torch.version.cuda=12.8
device=NVIDIA GeForce RTX 4060 Laptop GPU
```

机器检测到 RTX 4060 Laptop GPU，驱动版本 572.83，`nvidia-smi` 报告 CUDA 12.8。CUDA 版 PyTorch wheel 与官方索引 SHA256 一致后安装成功；后续环境可参考 `requirements-asr.txt` 中的可选 CUDA 安装命令，不应把 CUDA 版本硬编码到通用依赖中。

## 模型缓存

模型缓存已放在项目外：`D:\AI\BiliScribe-model-cache`。已缓存：

- `paraformer-zh` 实际解析到 FunASR 1.4.16 的 `iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`；
- `fsmn-vad`；
- `ct-punc`。

运行时可通过以下环境变量复用缓存：

```powershell
$env:MODELSCOPE_CACHE = 'D:\AI\BiliScribe-model-cache'
$env:HF_HOME = 'D:\AI\BiliScribe-model-cache\huggingface'
```

## 集成验证

- MCP 提供可选 stdio 接口和独立依赖清单；ASR 默认关闭，音频路径受 allowlist 限制。MCP 的 B 站字幕工具不下载音频或自动回退，独立本地音频转写工具可选启用。
- P2/P3 离线测试覆盖 `ParaformerEngine` 延迟加载、`LocalAudioSource`、按分 P 回退、ASR 错误隔离，以及 yt-dlp Cookie 临时文件、归属/大小/时长校验、ffmpeg 转码和异常清理。
- 已使用当前本地 B 站登录会话对 `BV1NG9xBUEju` 验证串轨字幕拒绝后进入本地音频下载、ffmpeg 转码、CUDA ASR 和 Markdown 导出；结果标记为“机器转写（普通话）”。此项验证回退链路，不构成对识别准确率的人工评测。
- 本地语音样本 smoke test 使用 FunASR 模型仓库随附的 `asr_example.wav`；仓库未包含可重复使用的真实普通话音频样本。

## 限制

- ASR 质量尚未通过人工校对语料计算 CER/WER；不同视频的识别准确率仍需按实际内容评估。
- 首次模型下载和加载时间取决于网络与缓存状态；模型缓存应与每个任务的临时音频分开。
