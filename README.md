# aiX

A local AI agent with a web UI. It chats, searches the web, reads and writes
files, runs code, and generates images, videos and image-to-video on your own
GPU. Everything runs on your machine.

- Chat model: `aiX-agent` (an abliterated Qwen2.5 14B plus a no-refusal system prompt)
- Image: FLUX.1-dev (GGUF) through ComfyUI
- Video and image-to-video: Wan 2.2 TI2V-5B
- Image editing: FLUX.1-Kontext-dev
- Vision: `qwen2.5vl:7b` (used to describe images for the agent)
- UI: small Flask app opened in an Edge app window

The application layer adds no content filtering of its own; the model and the
image/video pipeline run locally and unfiltered.

## Requirements

- Windows 10/11, an NVIDIA GPU (tested on a 12 GB RTX 3060)
- Python 3.10+
- Ollama (portable build supported)
- ffmpeg: either on your PATH or dropped in `bin/ffmpeg.exe`
- ComfyUI portable plus the model files (see the setup guide)

## Quick start

```bat
aix.bat
```

This starts Ollama and ComfyUI, launches the web UI, and opens it at
`http://127.0.0.1:8765/`.

| Command | Action |
|---|---|
| `aix.bat` | start everything and open the UI |
| `aix.bat stop` | stop all processes (including Ollama) |
| `aix.bat restart` | stop then start |
| `aix.bat status` | show process status |
| `aix.bat recover` | run the recovery check |
| `aix.bat clean` | run storage retention now |
| `aix.bat cli` | terminal chat instead of the web UI |

## Using images and videos

1. Click **+** or drag a file onto the window. It is copied into `inputs/`.
2. Ask normally. Examples:
   - "make a video of me flying through the sky" calls `animate_image`
   - "put this car on a snowy mountain road" calls `edit_image`
   - "what's in this photo?" calls `describe_input`

The agent looks at inputs with the local vision model and drives ComfyUI.

## Tools

| Tool | Description |
|---|---|
| `get_time`, `list_dir`, `read_file`, `write_file` | workspace files |
| `run_shell`, `run_python` | code execution (confirmation in the CLI, off by default in the web UI) |
| `web_search`, `fetch_url` | DuckDuckGo search and page text |
| `generate_image` | FLUX text-to-image |
| `generate_video` | Wan 2.2 text-to-video |
| `animate_image` | image to video |
| `edit_image` | FLUX Kontext instruction editing |
| `describe_image`, `describe_input` | local vision captions |
| `list_inputs` | list uploaded files |
| `open_media` | open a result in the desktop viewer |
| `trim_video`, `extract_frames`, `video_to_gif`, `add_audio`, `resize_image` | ffmpeg helpers |

## Storage and retention

- Outputs are written under `AIX_OUTPUT_DIR` with a `.json` sidecar and a
  `library.db` index. Every output is downloadable from the UI.
- Export copies a file to `AIX_EXPORT_DIR`; the UI also offers "export and free
  the source drive" which moves it.
- Retention keeps the newest `AIX_KEEP_LAST` outputs (default 3) plus anything
  pinned, and only trims when free space is below `AIX_MIN_FREE_GB` (default 15).
  Cache is cleared first.

## Power-loss recovery

The system is built for machines that lose power without warning:

- State, config and media are written atomically (temp file, fsync, rename).
- Session logs are append-only; a torn final line is discarded when read.
- Jobs run through a SQLite ledger. A job interrupted by a power cut is marked
  `interrupted` and listed in the UI. It is not re-run automatically unless
  `AIX_RECOVER_REQUEUE=1`.
- Model files are verified against `models_manifest.json`; downloads resume.
- A run marker plus `recover.py` detect an unclean exit at startup.

Software can recover from a power cut but cannot prevent a half-written OS file.
A small UPS that triggers a clean shutdown is the only real protection.

## Setup guide

See `docs/GUIDE.md` for the full install (Ollama, the chat model, ComfyUI and
the model downloads). Setup scripts are in `setup/`.

## Configuration

All settings are environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `127.0.0.1:11434` | Ollama server |
| `OLLAMA_EXE` | `D:\ollama\ollama.exe` | portable Ollama binary |
| `OLLAMA_MODELS` | `D:\ollama\models` | Ollama model store |
| `AIX_MODEL` | `aiX-agent` | chat model |
| `AIX_VISION_MODEL` | `qwen2.5vl:7b` | vision model |
| `AIX_AI_ROOT` | `D:\Programming\AI` | ComfyUI, models, outputs, cache |
| `AIX_COMFY_DIR` | `<AI_ROOT>\ComfyUI_windows_portable` | ComfyUI install |
| `AIX_COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI server |
| `AIX_MEDIA_BACKEND` | `comfyui` | media engine |
| `AIX_OUTPUT_DIR` | `<AI_ROOT>\outputs` | generated media |
| `AIX_EXPORT_DIR` | `G:\aiX-exports` | export target |
| `AIX_FFMPEG` | auto | ffmpeg path (else PATH, else `bin/ffmpeg.exe`) |
| `AIX_KEEP_LAST` | `3` | outputs protected from cleanup |
| `AIX_MIN_FREE_GB` | `15` | cleanup trigger |
| `AIX_KEEP_SESSIONS` | `20` | log sessions retained |
| `AIX_STOP_OLLAMA` | `1` | whether Stop/Close also stops Ollama |
| `AIX_WEB_PORT` | `8765` | web UI port |
| `AIX_RECOVER_REQUEUE` | `0` | auto re-run interrupted jobs on recovery |

## The uncensored model

`Modelfile` builds `aiX-agent` from an abliterated Qwen2.5 base with a
no-refusal system prompt. Rebuild it with:

```bat
ollama pull huihui_ai/qwen2.5-abliterate:14b
ollama create aiX-agent -f Modelfile
```

## Logs

See `logs/README.md`. Each session writes a JSONL event log plus a full console
transcript.

## License

MIT. See `LICENSE`.

Third-party components keep their own licenses: FLUX.1-dev is released under a
non-commercial license, Wan 2.2 is Apache-2.0, and the Ollama models carry their
respective licenses. Check each before commercial use.
