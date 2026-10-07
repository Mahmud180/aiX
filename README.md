# aiX

A fully local, uncensored, multimodal AI agent with a web UI. Chat, search the
web, read/write files, run code, and **generate images, videos, and image-to-video**
imagery with your own GPU. Everything runs on your machine.

- **Chat model:** `aiX-agent` = `huihui_ai/qwen2.5-abliterate:14b` (refusal-removed) + a no-refusal system prompt.
- **Image:** FLUX.1-dev (GGUF) via ComfyUI.
- **Video / image-to-video:** Wan 2.2 TI2V-5B.
- **Image editing:** FLUX.1-Kontext-dev (instruction editing).
- **Vision:** `qwen2.5vl:7b` (describes images / videos for the agent).
- **UI:** local Flask web app, opened in an Edge app-window.

No content filters, no moderation, no policy layer - the web tier only moves bytes.

## Quick start

```bat
aix.bat
```

This starts Ollama, ComfyUI, the web UI, and opens the window at
`http://127.0.0.1:8765/`.

| Command | Action |
|---|---|
| `aix.bat` | start everything + open the UI |
| `aix.bat stop` | stop all processes (incl. Ollama) |
| `aix.bat restart` | stop then start |
| `aix.bat status` | show process status |
| `aix.bat recover` | run the recovery guard manually |
| `aix.bat clean` | run storage retention now |
| `aix.bat cli` | terminal chat instead of the web UI |

## Using inputs (images/videos)

1. Click **+** or **drag-and-drop** a file onto the window (saved to `inputs/`).
2. Ask in plain language, e.g.:
   - "make a video of me flying through the sky" -> `animate_image`
   - "put this car on a snowy mountain road" -> `edit_image`
   - "what's in this photo?" -> `describe_input`

The agent inspects inputs with the local vision model and drives ComfyUI.

## Tools

| Tool | Description |
|---|---|
| `get_time`, `list_dir`, `read_file`, `write_file` | workspace files |
| `run_shell`, `run_python` | code execution (confirmation in CLI; off by default in web) |
| `web_search`, `fetch_url` | DuckDuckGo search + page text |
| `generate_image` | FLUX text-to-image |
| `generate_video` | Wan 2.2 text-to-video |
| `animate_image` | image -> video |
| `edit_image` | FLUX Kontext instruction editing |
| `describe_image`, `describe_input` | local vision captions |
| `list_inputs` | list uploaded files |
| `open_media` | open a result in the desktop viewer |
| `trim_video`, `extract_frames`, `video_to_gif`, `add_audio`, `resize_image` | ffmpeg utilities |

## Storage & retention

- Generated outputs live in `D:\Programming\AI\outputs` with a `.json` sidecar and a
  `library.db` index. Every output is downloadable from the UI.
- **Export** copies a file to `G:\aiX-exports`; **Export + free D:** moves it.
- **Retention** keeps the newest `AIX_KEEP_LAST` (default 3) outputs plus anything
  **pinned**, and only trims (cache first, then oldest unprotected outputs) when
  D: drops below `AIX_MIN_FREE_GB` (default 15).
- Nothing large is written to C:. The `reel_llm-system` project is never modified
  (its ffmpeg is executed read-only).

## Power-loss safety (load-shedding)

Hard power cuts are expected. The system is built to survive and recover:

- Atomic writes (temp -> fsync -> rename) for state/config/media.
- Append-only JSONL logs; a torn final line is discarded on read.
- Job ledger (`run/state.db`): jobs left `running` are marked `interrupted` and
  **requeued** on next start.
- Model files verified against `models_manifest.json`; downloads are resumable.
- `run/.running` marker + `recover.py` detect an unclean exit on startup.

Software can recover from a cut but can't prevent mid-write corruption of OS files;
a small UPS that triggers a clean shutdown is the only true prevention.

## Install / rebuild the media backend

```powershell
powershell -ExecutionPolicy Bypass -File D:\Programming\AI\setup\setup_ai.ps1
```

## Configuration (env)

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `127.0.0.1:11434` | Ollama server |
| `OLLAMA_EXE` / `OLLAMA_MODELS` | `D:\ollama\...` | portable Ollama |
| `AIX_MODEL` | `aiX-agent` | chat model |
| `AIX_VISION_MODEL` | `qwen2.5vl:7b` | vision model |
| `AIX_MEDIA_BACKEND` | `comfyui` | media engine |
| `AIX_AI_ROOT` | `D:\Programming\AI` | ComfyUI + models + outputs |
| `AIX_OUTPUT_DIR` | `<AI_ROOT>\outputs` | generated media |
| `AIX_EXPORT_DIR` | `G:\aiX-exports` | export target |
| `AIX_KEEP_LAST` | `3` | outputs protected from cleanup |
| `AIX_MIN_FREE_GB` | `15` | cleanup trigger |
| `AIX_KEEP_SESSIONS` | `20` | log sessions retained |
| `AIX_STOP_OLLAMA` | `1` | Stop/Close also stops Ollama |
| `AIX_WEB_PORT` | `8765` | web UI port |
| `AIX_FFMPEG` | reel-project ffmpeg | encoder (read-only reuse) |

## The "uncensored" layer

`Modelfile` builds `aiX-agent` from the abliterated Qwen2.5 base with a
no-refusal system prompt. Rebuild after editing:

```bat
"%OLLAMA_EXE%" pull huihui_ai/qwen2.5-abliterate:14b
"%OLLAMA_EXE%" create aiX-agent -f Modelfile
```

## Logs

See `logs/README.md`. Sessions are JSONL (`logs/sessions/<id>.jsonl`) plus a full
console transcript, suitable for replay and future tooling.
