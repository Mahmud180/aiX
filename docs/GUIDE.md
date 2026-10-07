# aiX build guide

This document explains what aiX is, how the pieces fit together, and how to
install it from scratch. It was written while setting the project up on a
12 GB RTX 3060 running Windows.

## What it is

aiX is a local chat agent with a web interface. The chat model runs in Ollama.
Image, video and image-to-video generation run in ComfyUI. A small Python layer
connects a language model with a set of tools and exposes everything through a
browser UI.

The design goal was a fully local, unfiltered assistant that can also make
media, survive power cuts (common where this was built), and keep a durable log
of everything for later work.

## Architecture

```
aix.bat
  -> procman.py        start/stop Ollama and ComfyUI
  -> webui.py          Flask server, serves web/index.html
       -> agent.py     chat loop: streaming, tool calls, cancellation
            -> ollama_client.py   talks to Ollama's HTTP API
            -> tools.py           the tool set and JSON schemas
                 -> comfyui_backend.py   image/video generation
                 -> vision.py            describe images with a vision model
                 -> media.py/storage.py  outputs, sidecars, retention
       -> chats.py     persistent chat history
       -> state.py     job ledger (SQLite), process registry, atomic writes
       -> logger.py    session logs (JSONL + console transcript)
       -> recover.py   startup recovery after an unclean shutdown
```

Each Python file is small and single-purpose. `config.py` holds every setting as
an environment variable.

## Prerequisites

- Windows 10/11
- NVIDIA GPU. A 12 GB card (RTX 3060) was the target; more VRAM is easier.
- Python 3.10 or newer
- Ollama (the portable Windows build is fine)
- ffmpeg, either on PATH or placed at `bin/ffmpeg.exe` in this folder
- Roughly 50 GB of free disk for models

Only one heavy model runs on the GPU at a time. The code unloads the chat model
before ComfyUI work and frees ComfyUI before chatting again, because a 14B model
and a diffusion model do not fit in 12 GB together.

## Step 1: Ollama and the chat models

Install Ollama and make sure the server is reachable at `127.0.0.1:11434`.

Pull the models:

```bat
ollama pull huihui_ai/qwen2.5-abliterate:14b
ollama pull qwen2.5vl:7b
```

The first is an abliterated build of Qwen2.5 14B (refusal behavior removed from
the weights). The second is a vision model used by `describe_image` and
`describe_input`.

Build the agent model from the Modelfile in this folder:

```bat
ollama create aiX-agent -f Modelfile
```

`Modelfile` sets the base model, sampling parameters, and the system prompt.

If your Ollama uses a custom model directory, set `OLLAMA_EXE` and
`OLLAMA_MODELS` before starting (see the README table).

## Step 2: Python dependencies

```bat
python -m pip install -r requirements.txt
```

This installs Flask, requests, BeautifulSoup, ddgs, Pillow, numpy and OpenCV.
Everything else is the standard library.

## Step 3: ComfyUI and the diffusion models

Run the setup script. It downloads the ComfyUI portable build, extracts it,
configures the model paths, installs the GGUF custom node, then downloads the
diffusion models:

```powershell
powershell -ExecutionPolicy Bypass -File setup\setup_ai.ps1
```

The install root defaults to `D:\AI`. Override it with the `AIX_AI_ROOT`
environment variable, and set the same variable when running the app.

The models it fetches:

- FLUX.1-dev (GGUF Q6_K) plus its text encoders and VAE, for text-to-image
- Wan 2.2 TI2V-5B plus its text encoder and VAE, for text-to-video and
  image-to-video
- FLUX.1-Kontext-dev (GGUF), for instruction-based image editing

`setup\fetch_models.ps1` is separate so downloads can be re-run or resumed on
their own. It writes `models_manifest.json`, which the recovery step uses to
check that model files are complete.

`setup\pull_models.ps1` pulls the two Ollama models if you prefer doing both
from one place.

Note on model licenses: FLUX.1-dev is non-commercial, Wan 2.2 is Apache-2.0, and
the Ollama models have their own terms. Check them before commercial use.

## Step 4: Configuration

The defaults match a common layout (`D:\AI` for the AI stack, `D:\ollama` for
Ollama). If your paths differ, set environment variables such as `AIX_AI_ROOT`,
`OLLAMA_EXE`, `OLLAMA_MODELS`, `AIX_OUTPUT_DIR` and `AIX_EXPORT_DIR`. The full
list is in the README.

ffmpeg is resolved in this order: `AIX_FFMPEG`, then `ffmpeg` on PATH, then
`bin/ffmpeg.exe`. If none is found, video transcoding and the ffmpeg tools are
disabled but the rest still works.

## Step 5: Run it

```bat
aix.bat
```

This starts Ollama and ComfyUI in parallel, launches the web UI, and opens an
Edge app window at `http://127.0.0.1:8765/`.

Other commands:

| Command | Action |
|---|---|
| `aix.bat stop` | stop everything, including Ollama |
| `aix.bat status` | show what is running |
| `aix.bat recover` | run the recovery check by hand |
| `aix.bat clean` | run storage retention now |
| `aix.bat cli` | chat in the terminal instead |

## How a request flows

1. You type a message in the web UI.
2. `webui.py` records the message in the chat file right away, so it survives a
   reload or a crash, then starts a worker thread.
3. `agent.py` streams the model's reply token by token and watches for tool
   calls.
4. When the model calls a tool, the tool runs and its result goes back to the
   model, which continues. This repeats until the model answers without a tool.
5. Everything is written to a session log. Generated media is added to the
   output library.

Media generation is heavier. `comfyui_backend.py` uploads any input image to
ComfyUI, fills in a workflow template from `workflows/`, waits for the job,
pulls the result, and registers it. Video is generated at 24 fps; anything
longer than 5 seconds is produced as several segments and stitched with ffmpeg.

## Tools

The model can call these. Schemas live in `tools.py`.

- Files: `get_time`, `list_dir`, `read_file`, `write_file`
- Code: `run_shell`, `run_python` (confirmation in the CLI; disabled in the web
  UI unless `AIX_WEB_ALLOW_DANGEROUS=1`)
- Web: `web_search`, `fetch_url`
- Images: `generate_image`, `edit_image`
- Video: `generate_video`, `animate_image`
- Vision: `describe_image`, `describe_input`
- Inputs: `list_inputs`
- Viewer: `open_media`
- ffmpeg: `trim_video`, `extract_frames`, `video_to_gif`, `add_audio`,
  `resize_image`

## Storage and retention

Generated files go to `AIX_OUTPUT_DIR`. Each one gets a JSON sidecar and a row
in `library.db`. The UI can download, export, pin or delete any output.

Retention keeps the newest few outputs plus anything pinned, and only deletes
when free space drops below a threshold. Cache and temporary files are cleared
first. See `AIX_KEEP_LAST` and `AIX_MIN_FREE_GB` in the README.

## Recovery after a power cut

The machine this runs on loses power without warning. A few measures make that
survivable:

- Writes to state and media are atomic (write a temp file, flush, rename).
- Session logs are append-only. A half-written last line is dropped when read.
- Generation jobs go through a SQLite ledger. A job caught mid-run is marked
  `interrupted` at the next start and listed in the UI. It is not re-run
  automatically unless `AIX_RECOVER_REQUEUE=1`, since re-running a video can tie
  up the GPU for an hour.
- Model files are checked against `models_manifest.json`; downloads resume.
- A run marker and `run/state.db` let `recover.py` detect and report an unclean
  exit.

No software can stop a hard power cut from corrupting a file mid-write. A UPS
that shuts the machine down cleanly is the only real protection.

## Troubleshooting

- Ollama unreachable: check it is running and listening on `127.0.0.1:11434`.
- ComfyUI not answering: check `logs/comfyui.log`. The first job after a restart
  is slow because the models load from disk.
- "out of memory" or a ComfyUI crash: only one heavy model should use the GPU at
  a time. Lower the resolution or steps, and close other GPU apps.
- A generation seems stuck: use Stop in the UI, which interrupts the ComfyUI job
  and clears its queue.
- Image or video looks wrong: the workflow templates in `workflows/` are plain
  JSON and can be edited; each corresponds to one pipeline.

## Layout

```
aix.bat              launcher
Modelfile            Ollama model definition
requirements.txt     Python dependencies
config.py            all settings
agent.py             chat loop, streaming, tool calls
webui.py             Flask server
web/index.html       web UI
tools.py             tool set and schemas
comfyui_backend.py   image/video generation
vision.py            local vision captions
media.py             media facade
chats.py             chat history
state.py             job ledger, process registry, atomic writes
storage.py           output library, retention, export
procman.py           start/stop services
logger.py            session logs
recover.py           startup recovery
workflows/           ComfyUI workflow templates
setup/               install scripts
docs/                this guide
```
