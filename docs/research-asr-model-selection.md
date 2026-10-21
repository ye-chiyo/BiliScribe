# BiliScribe 中文 ASR 模型选型研究

研究日期：2026-10-20
研究目标：为 BiliScribe 的“无 B 站 CC/AI 字幕时才进行 ASR”流程选择普通话准确率高、可部署到云 GPU、可导出时间轴的 ASR。当前阶段暂不把方言覆盖作为选型加分项。

## 结论

### 推荐分层

1. **准确率优先候选：Qwen3-ASR-1.7B + Qwen3-ForcedAligner-0.6B**
   - 新增评估价值最高。官方公开基准包含普通话、网络音频和会议音频；支持长音频识别，并提供字/词级强制对齐时间戳。
   - 识别与对齐是两个模型；云 GPU 可以通过常驻加载减少初始化开销。强制对齐每次最多处理 5 分钟音频，长视频仍需分块。
   - Apache-2.0。官方提供 Transformers 和 vLLM 推理路径，适合部署为独立 GPU 服务。

2. **准确率和原生时间轴候选：FireRedASR2-AED**
   - 官方报告在 4 个普通话公开测试集上平均 CER 为 **3.05%**，其自有对比表中优于 Whisper large-v3、Paraformer-Large、Fun-ASR-Nano；支持中文普通话、20 多种方言/口音、英语和中英混说。
   - Python API 可直接返回句级/字级时间戳和置信度，减少独立对齐模型依赖。
   - 约 1.1B 参数；官方代码只在 Ubuntu 22.04 测试，单次输入建议不超过 60 秒，长视频必须由 VAD 切段后合并时间轴。云端 Ubuntu GPU 环境更贴近其官方验证环境。

3. **BiliScribe 稳定基线、部署复杂度优先：FunASR Paraformer-zh**
   - 中文/中英混说专用的成熟离线路线，220M 参数，官方模型卡提供 VAD、标点和时间戳组合；FunASR 文档把它定位为普通话生产 ASR。
   - 可使用 CPU 或 CUDA，模型小于 FireRedASR2，适合作为云服务的低成本、低延迟基线，也便于后续保留本地降级方案。
   - 公开基准和官方材料显示其准确率不及 FireRedASR2；需要在真实 B 站音频（口音、音乐、游戏音效、专有名词）上做 CER/人工抽检后再固化参数。

4. **跨语言兜底：faster-whisper + Whisper large-v3**
   - Whisper large-v3 覆盖中文及大量语言，MIT 许可；faster-whisper 使用 CTranslate2，官方称相同精度下最高约 4 倍速度，并支持 GPU/CPU INT8、词级时间戳和 VAD。
   - 普通话准确率不是首选，但当视频混用多语言、模型兼容性优先或 FireRed/Paraformer 不可用时有价值。

### 不建议作为首个默认模型

- **SenseVoiceSmall**：中文、粤语、英语、日语、韩语，234M 参数，CPU 友好且速度很快；但 Hugging Face 卡标注为 `license: other`，需遵守 FunASR 模型自定义协议（署名、保留模型名等），商业分发前要单独完成法务确认。它更适合快速 CPU 方案或多语种场景，不应仅凭“速度快”宣称比中文专用模型更准确。
- **Fun-ASR-Nano-2512**：约 800M 参数，普通话公开测试表现值得作为第二轮对照；官方模型卡虽标有 timestamps，不同 checkpoint/pipeline 的时间戳粒度和精度仍需核实。
- **GLM-ASR-Nano-2512**：1.5B 参数、官方报告的中文基准有竞争力，可作为第二轮普通话对照；官方 Transformers 示例主要输出文本，字幕时间轴需要额外对齐，当前不优先于 Qwen3/FireRed。
- **FireRedASR2-LLM**：8B 级、官方普通话平均 CER 2.89%，但单次输入约 40 秒且资源需求更高；官方 LLM 示例没有像 AED 路线那样直接给出词级时间戳。可留作云端高成本对照，不列入首轮 shortlist。

## 与 BiliScribe 的字幕优先策略

用户要求的行为应实现为确定性的两阶段决策：

```text
获取视频信息和分 P 列表
        |
查询并验证 B 站 CC / AI 字幕
        |
存在至少一个有效字幕轨道且正文非空？
       / \
     是   否（确认“无字幕”，不是网络故障）
     |          |
直接使用字幕   下载音频 -> VAD 切段 -> ASR -> 合并时间轴
```

具体规则：

- **有效字幕命中后立即结束**：不下载音频、不启动 `ffmpeg`、不加载 ASR 模型。输出元数据标记 `source=cc` 或 `source=ai_subtitle`。
- **只有确认没有字幕时才回退**：字幕接口超时、鉴权失败、被限流或解析异常不能直接当作“无字幕”；应返回可重试错误，避免无意触发昂贵的音频下载和转写。
- **按视频分 P 处理**：某些分 P 有字幕、某些分 P 没有时，只对缺失字幕的分 P 运行 ASR，已有字幕的分 P 保留原轨道。
- **字幕正文为空或无有效时间段**应视为“无有效字幕”，然后进入 ASR；只要存在可用正文，就不因质量主观判断而重复转写。
- ASR 结果必须明确标记 `source=asr`、模型 ID、模型版本、语言、运行设备和时间轴来源，避免用户把机器转写误认为 UP 主提供的 CC。

## 模型对比

| 模型 | 中文覆盖 | 官方时间轴能力 | 长音频处理 | 参数/资源 | 许可与风险 | BiliScribe 适配 |
|---|---|---|---|---|---|---|
| FireRedASR2-AED | 普通话、英语、混说 | 句/字级 timestamp、confidence | 单段 <=60s；用 FireRedVAD/VAD 切段 | 约 1.1B；GPU 推荐 | Apache-2.0 权重/代码；官方仅测试 Ubuntu 22.04 | **普通话准确率与时间轴候选** |
| Qwen3-ASR-1.7B + ForcedAligner-0.6B | 普通话及多语 | ASR 输出文本；独立 aligner 给字/词级 timestamps | ASR 支持长音频；aligner 每次 <=5 分钟 | 1.7B + 0.6B；云 GPU 常驻更合适 | Apache-2.0；Transformers/vLLM | **普通话高准确率候选，必须实测** |
| Paraformer-zh + FSMN-VAD + ct-punc | 中文、英语混说 | FunASR `sentence_info` 和时间戳 | VAD 切短段；适合批处理 | 约 220M；CPU/CUDA | 模型卡 Apache-2.0；FunASR 工具包 MIT | **首版默认候选** |
| SenseVoiceSmall + FSMN-VAD | 普通话及多语 | 管线可返回分段时间戳 | 官方示例用 VAD，单段上限可配 30s | 234M；CPU 友好 | 模型采用 FunASR 自定义 MODEL_LICENSE，需署名并保留模型名 | 速度对照组 |
| Fun-ASR-Nano-2512 | 普通话及多语 | 依 checkpoint/pipeline；必要时需额外对齐 | 支持 VAD 切段；长音频需自行编排 | 约 800M；GPU 或 GGUF CPU/边缘 | 模型卡 Apache-2.0；须检查所选 checkpoint | 第二轮普通话对照 |
| GLM-ASR-Nano-2512 | 普通话及多语 | 官方示例为文本输出；需外部对齐 | 可分段处理 | 约 1.5B；云 GPU | Apache-2.0 | 第二轮普通话对照 |
| faster-whisper + Whisper large-v3 | 中文及多语种 | segment/word timestamps、VAD | 30 秒窗口滑动；库支持长文件 | 1.55B；GPU FP16/INT8 或 CPU INT8 | Whisper 代码/权重 MIT；faster-whisper MIT | 多语兜底 |
| FireRedASR2-LLM | 中文、方言、英语、混说 | LLM API示例不保证字级时间戳 | 单段 <=40s | 约 8B；高显存/GPU | Apache-2.0 | 后续服务器高精度档 |

## 一手资料核对

### FireRedASR2S / FireRedASR2-AED

- 官方仓库 README：模型覆盖、CER 表、时间戳示例、VAD 组合、输入限制和“只测试 Ubuntu 22.04”。
- 官方 README 的普通话平均 CER：FireRedASR2-LLM 2.89%，FireRedASR2-AED 3.05%。这些数字是项目方自报的公开基准结果，不能替代 B 站真实音频评测。
- AED Python 示例设置 `return_timestamp=True`，返回 `(字/词, start, end)`；系统示例还可返回句级置信度、VAD 段和语言识别。
- 单段限制：AED 60 秒，超过 60 秒可能出现幻觉，超过 200 秒会触发位置编码错误；LLM 40 秒。因此 BiliScribe 必须把长视频拆成短片段后再合并。
- 模型和代码仓库使用 Apache-2.0；部署时仍需保留第三方依赖的许可证和模型声明。

来源：

- [FireRedASR2S 官方仓库](https://github.com/FireRedTeam/FireRedASR2S)
- [FireRedASR2S 官方 README（模型、基准、API、限制）](https://raw.githubusercontent.com/FireRedTeam/FireRedASR2S/main/README.md)
- [FireRedASR2-AED Hugging Face 模型卡](https://huggingface.co/FireRedTeam/FireRedASR2-AED)
- [FireRedASR2 技术报告](https://arxiv.org/abs/2603.10420)
- [FireRedASR2S LICENSE](https://raw.githubusercontent.com/FireRedTeam/FireRedASR2S/main/LICENSE)

### Qwen3-ASR

- 官方仓库发布 Qwen3-ASR-1.7B/0.6B 和 Qwen3-ForcedAligner-0.6B，支持普通话和多语；ASR 支持离线/流式和长音频。
- 官方公开表包含 WenetSpeech、AISHELL-2、Fleurs-zh 等普通话/中文数据集，指标主要为 WER。数据集、切分、文本归一化和指标口径与 FireRed 的 CER 对比表不同，不能直接比较百分数来宣布胜负。
- 时间戳由独立 ForcedAligner 生成，支持字/词等任意单位、最多 5 分钟音频。BiliScribe 若采用它，应按 <=5 分钟块对齐，并在块边界检查时间偏移和文本重复。
- 官方 2026-06 起提供 Transformers 原生支持；仓库还提供 vLLM 后端。当前没有找到官方针对 RTX 4060 8 GB 的峰值显存或统一速度数据，因此模型可运行性及 ASR+aligner 的内存峰值需本机 smoke test。
- Hugging Face 模型卡标注 Apache-2.0。

来源：

- [Qwen3-ASR 官方仓库与 README](https://github.com/QwenLM/Qwen3-ASR)
- [Qwen3-ASR 官方 benchmark 表](https://github.com/QwenLM/Qwen3-ASR#evaluation)
- [Qwen3-ASR-1.7B 模型卡](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
- [Qwen3-ForcedAligner-0.6B 模型卡](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)

### Fun-ASR-Nano、GLM-ASR 与其他候选

- Fun-ASR-Nano-2512 的官方模型卡标注 Apache-2.0，覆盖普通话、日语、英语及其他语言，并发布若干中文测试集结果。字幕用途需要区分模型识别和 pipeline 对齐能力；若拿不到稳定时间戳，应组合外部 aligner。
- GLM-ASR-Nano-2512 官方仓库称其为 1.5B 模型，强调普通话和低音量鲁棒性；项目给出的平均错误率与 benchmark 是厂商自报。仓库 Transformers 示例返回识别文本，未体现可直接用于 SRT 的字级时间轴。
- Step-Audio2 是通用音频理解/对话模型，部署与时间轴路径对本地字幕用途成本偏高，不进入首轮 shortlist。

来源：

- [Fun-ASR-Nano-2512 模型卡](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512)
- [FunASR 官方模型指南](https://www.funasr.com/en/docs/model-zoo.html)
- [GLM-ASR 官方仓库](https://github.com/zai-org/GLM-ASR)
- [Step-Audio2 官方仓库](https://github.com/stepfun-ai/Step-Audio2)

### FunASR、Paraformer、SenseVoice

- FunASR 官方仓库把 Paraformer-zh 定位为中文生产路线，并给出 VAD、标点、说话人和 timestamp 的组合调用。
- Paraformer-zh 官方 Hugging Face 卡：220M 参数、中文/英语、16 kHz、非自回归、支持离线/流式路线，并示例输出 `sentence_info`。
- SenseVoice 官方仓库说明发布的 SenseVoiceSmall checkpoint 支持普通话、粤语、英语、日语、韩语；论文和 README 报告其在中文/粤语 benchmark 上相对 Whisper 的优势，并称相近参数下速度显著更快。长音频示例用 FSMN-VAD 和 `max_single_segment_time`。
- Fun-ASR-Nano 官方卡虽标有 timestamps 能力，但需要按具体 checkpoint 和 pipeline 验证其输出粒度与精度；不能把模型标签等同于可直接导出可靠字级字幕时间轴。
- FunASR 工具包为 MIT，但模型许可独立。SenseVoiceSmall 的模型卡使用 `license=other`，链接到 FunASR `MODEL_LICENSE`；该协议要求注明来源作者并保留模型名称，且包含自动终止和“仅供参考/学习”的责任条款，应在商业化前核对。

来源：

- [FunASR 官方仓库](https://github.com/modelscope/FunASR)
- [FunASR 模型选择指南](https://github.com/modelscope/FunASR/blob/main/docs/model_selection.md)
- [Paraformer-zh 模型卡](https://huggingface.co/funasr/paraformer-zh)
- [SenseVoice 官方仓库](https://github.com/FunAudioLLM/SenseVoice)
- [SenseVoiceSmall 模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)
- [FunASR MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)
- [SenseVoice 论文](https://arxiv.org/abs/2407.04051)

### Whisper / faster-whisper

- OpenAI Whisper 官方 README：large-v3 属于 1.55B 多语种模型；官方模型表给出大模型约 10 GB 显存量级，实际依实现和精度而变。
- OpenAI 官方 README 声明代码和模型权重 MIT；官方模型卡同时提醒弱监督可能产生幻觉、不同语言/口音表现不均，应在目标场景评测。
- faster-whisper 官方 README：基于 CTranslate2；相同精度下最高约 4 倍速度、更低内存；GPU 支持 FP16/INT8，CPU 支持 INT8；提供 word-level timestamps 和 VAD；其 PyAV 解码不要求系统安装 ffmpeg。

来源：

- [OpenAI Whisper 官方仓库](https://github.com/openai/whisper)
- [Whisper 官方模型卡](https://github.com/openai/whisper/blob/main/model-card.md)
- [Whisper large-v3 模型卡](https://huggingface.co/openai/whisper-large-v3)
- [faster-whisper 官方仓库](https://github.com/SYSTRAN/faster-whisper)
- [faster-whisper large-v3 转换模型卡](https://huggingface.co/Systran/faster-whisper-large-v3)

## 推荐的实现顺序

### 第一阶段：先确定产品行为

在现有字幕提取流程结束前增加一个明确的结果分类：

```text
subtitle_found -> 直接返回 CC/AI 字幕，不下载音频
subtitle_absent -> 创建 ASR 任务
subtitle_error -> 返回可重试错误，不自动当作 absent
```

这条规则比“字幕失败就尝试 ASR”更重要，因为它控制隐私、下载流量、模型成本和用户预期。

### 第二阶段：先做一个模型适配器

建议接口保持模型无关：

```python
class AsrEngine:
    model_id: str
    def transcribe(self, wav_path, *, language="zh") -> list[Segment]: ...
```

先以 Paraformer-zh 建立稳定基线，再对 Qwen3-ASR-1.7B + ForcedAligner-0.6B 和 FireRedASR2-AED 做同音频集对比。比较普通话 CER、RTF、云 GPU 成本、峰值显存、失败率、时间轴偏差和专有名词准确率；云端可优先采用常驻模型，只有在并发或显存压力下再评估顺序加载/卸载或量化。

### 第三阶段：长视频和资源边界

- 下载音频仅在 `subtitle_absent` 后发生，并写入每任务隔离的临时目录。
- 统一转为 16 kHz、单声道 PCM WAV；FireRedASR2 和 Paraformer 的官方示例都采用该格式。
- 用 VAD 切成 30~60 秒片段，保留约 0.5~1 秒重叠；按原始音频偏移修正 timestamps，再去重相邻片段重复文本。
- 设置最大视频时长、最大音频大小、并发数和模型显存/内存预算；取消、失败和成功都在 `finally` 清理音频临时文件。
- 模型文件可在本地缓存，但默认不缓存 B 站音频或最终 ASR 文稿，除非用户显式保存。

### 第四阶段：必须做的真实评测

建立 20~50 条有代表性的 B 站普通话音频集，至少覆盖背景音乐、游戏/现场噪声、多人重叠、快速语速、口语化表达和专有名词。记录：

- 字符错误率（CER）和人工可读性抽检；
- 端到端耗时和实时率（RTF）；
- 峰值内存/显存；
- 时间戳漂移和切段重复率；
- 下载、解码、模型加载和转写失败率；
- 不同模型版本、运行时和量化设置。

在评测结果出来前，不应对用户承诺“接近 CC 的准确率”；ASR 应在界面和导出元数据中明确标注为机器转写。

## 最终建议

对 BiliScribe 的产品路线，建议采用：

```text
有效 CC/AI 字幕存在 -> 永远使用字幕，不运行 ASR
无有效字幕 -> 默认 Paraformer-zh（易部署）
普通话高准确率 shortlist -> Qwen3-ASR-1.7B + ForcedAligner-0.6B、FireRedASR2-AED
多语言或中文模型不可用 -> faster-whisper large-v3 兜底
```

当前证据不足以断言新候选在 B 站普通话视频上一定胜过 FireRed。建议把 Paraformer-zh 当作稳定基线，在云端 Ubuntu GPU 环境中对 Qwen3-ASR-1.7B（含 ForcedAligner）和 FireRedASR2-AED 做小规模 smoke test，再用同一批有人工校对文本的 B 站普通话音频比较准确率、耗时、云 GPU 成本与时间轴。Qwen 是目前最值得新增测试的模型，但在这些实测完成前不应直接定为默认模型。
