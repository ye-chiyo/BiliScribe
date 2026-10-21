# BiliScribe Local MCP

BiliScribe exposes an optional MCP server over stdio for local AI clients. It
does not change the web app or base dependency set. The server must be started
with the project's Python environment and runs only while the MCP client keeps
the subprocess open.

## Install

The MCP server uses the project's virtual environment. Install the MCP package
and, when local transcription is needed, the optional ASR stack separately.

From the project directory in PowerShell, install the MCP dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

### Optional local ASR

Install `ffmpeg` including `ffprobe`, and make both commands available on
`PATH`. For example, on Windows with `winget`:

```powershell
winget install --id Gyan.FFmpeg.Shared --exact
ffmpeg -version
ffprobe -version
```

Open a new terminal after installation if `ffmpeg` is not yet on `PATH`.
Install exactly one matching PyTorch route before FunASR. The CUDA 12.8 command
below is the route verified for this project's RTX 4060 Laptop GPU; choose the
CPU index on machines without a supported NVIDIA CUDA setup.

CUDA 12.8:

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.11.0+cu128 torchaudio==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements-asr.txt
```

CPU:

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.11.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements-asr.txt
```

Verify the selected runtime before enabling transcription:

```powershell
.\.venv\Scripts\python.exe -c "import torch, torchaudio, funasr; print(torch.__version__, torchaudio.__version__, funasr.__version__); print('CUDA:', torch.cuda.is_available())"
```

The first transcription downloads Paraformer and its dependent models if they
are not cached. To keep model files outside the repository, set `MODELSCOPE_CACHE`
and `HF_HOME` in the MCP client's `env` as shown below. Keep
`BILISCRIBE_ASR_ENABLED` unset until the ASR dependencies, ffmpeg tools and
model cache are ready; ASR remains disabled by default.

The MCP server offers three tools:

- `get_capabilities`: reports whether local credentials are present, ASR/CUDA
  availability, audio limits and allowed local audio directories. It does not
  validate the Bilibili login session.
- `extract_subtitles`: exports existing Bilibili CC/AI subtitle tracks. It does
  not download video audio or invoke ASR when tracks are absent.
- `transcribe_local_audio`: transcribes a local audio file with Paraformer and
  optionally writes `srt`, `vtt`, `txt`, `json` or `md` output.

## MCP client configuration

Add a stdio server entry to the AI client's MCP configuration. Use an absolute
path for `command`; the project directory is the subprocess working directory.

```json
{
  "mcpServers": {
    "biliscribe": {
      "command": "D:\\AI\\BiliScribe\\.venv\\Scripts\\python.exe",
      "args": ["-m", "biliscribe.mcp_server"],
      "cwd": "D:\\AI\\BiliScribe",
      "env": {
        "MODELSCOPE_CACHE": "D:\\AI\\BiliScribe-model-cache",
        "HF_HOME": "D:\\AI\\BiliScribe-model-cache\\huggingface",
        "BILISCRIBE_MCP_AUDIO_DIRS": "C:\\Users\\614\\Downloads",
        "BILISCRIBE_ASR_ENABLED": "true",
        "BILISCRIBE_ASR_DEVICE": "auto"
      }
    }
  }
}
```

`BILISCRIBE_ASR_ENABLED` defaults to `false`; leave it unset to expose subtitle
extraction without enabling local transcription. `BILISCRIBE_MCP_AUDIO_DIRS`
is a path-separated allowlist (semicolon-separated on Windows). If unset, only
the current user's `Downloads` directory is allowed. The server validates the
resolved file path, file size and duration before invoking ffmpeg/FunASR.

The output directory defaults to the project's `output` folder. It can be
changed with `BILISCRIBE_MCP_OUTPUT_DIR`. Model cache paths remain separate from
temporary audio, which is removed when each transcription call finishes.
Exports never replace an existing file. Repeated runs use names such as
`recording.asr (2).srt` and `recording.asr (3).srt`.

`get_capabilities` reports `credentials_present`, which only means a local
credential file exists. It reports `login_status: "not_checked"` because the
MCP capability query does not contact Bilibili to validate that credential.

This MCP server does not download Bilibili audio or automatically fall back to
ASR during `extract_subtitles`. Use `transcribe_local_audio` for a local file;
the local Web and CLI extraction workflows have their own optional Bilibili
audio download and ASR fallback.
