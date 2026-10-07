"""Environment-driven configuration for aiX."""

import os
import shutil
from pathlib import Path


def _base_url(host: str) -> str:
    host = (host or "").strip()
    if host.startswith(("http://", "https://")):
        return host.rstrip("/")
    return "http://" + host.rstrip("/")


def _env_bool(name, default=True):
    return os.environ.get(name, "1" if default else "0").lower() not in ("0", "false", "no")


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
OLLAMA_BASE = _base_url(OLLAMA_HOST)
OLLAMA_EXE = os.environ.get("OLLAMA_EXE", r"D:\ollama\ollama.exe")
OLLAMA_MODELS = os.environ.get("OLLAMA_MODELS", r"D:\ollama\models")

MODEL = os.environ.get("AIX_MODEL", "aiX-agent")
BASE_MODEL = os.environ.get("AIX_BASE_MODEL", "qwen2.5:14b-instruct")
VISION_MODEL = os.environ.get("AIX_VISION_MODEL", "qwen2.5vl:7b")

PROJECT_DIR = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("AIX_WORKSPACE") or PROJECT_DIR).resolve()

AI_ROOT = Path(os.environ.get("AIX_AI_ROOT") or r"D:\Programming\AI")
COMFY_DIR = Path(os.environ.get("AIX_COMFY_DIR") or (AI_ROOT / "ComfyUI_windows_portable"))
COMFYUI_URL = os.environ.get("AIX_COMFYUI_URL", "http://127.0.0.1:8188")
WORKFLOW_DIR = Path(os.environ.get("AIX_WORKFLOW_DIR") or (WORKSPACE / "workflows"))

INPUTS_DIR = Path(os.environ.get("AIX_INPUTS_DIR") or (WORKSPACE / "inputs")).resolve()
OUTPUT_DIR = Path(os.environ.get("AIX_OUTPUT_DIR") or (AI_ROOT / "outputs")).resolve()
MEDIA_DIR = OUTPUT_DIR
CACHE_DIR = Path(os.environ.get("AIX_CACHE_DIR") or (AI_ROOT / "cache")).resolve()
CHAT_DIR = WORKSPACE / "chats"
LOGS_DIR = Path(os.environ.get("AIX_LOGS_DIR") or (WORKSPACE / "logs")).resolve()
SESSION_DIR = LOGS_DIR / "sessions"
RUN_DIR = Path(os.environ.get("AIX_RUN_DIR") or (WORKSPACE / "run")).resolve()
WEB_DIR = Path(os.environ.get("AIX_WEB_DIR") or (WORKSPACE / "web")).resolve()

STATE_DB = RUN_DIR / "state.db"
LIBRARY_DB = OUTPUT_DIR / "library.db"
MODELS_MANIFEST = AI_ROOT / "models_manifest.json"
RUN_MARKER = RUN_DIR / ".running"

EXPORT_DIR = Path(os.environ.get("AIX_EXPORT_DIR") or r"G:\aiX-exports")

# Resolve ffmpeg: AIX_FFMPEG -> ffmpeg on PATH -> ./bin/ffmpeg. Empty string
# disables ffmpeg-backed features.
def _resolve_ffmpeg():
    configured = os.environ.get("AIX_FFMPEG", "").strip()
    if configured:
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = PROJECT_DIR / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    return str(local) if local.exists() else ""


FFMPEG = _resolve_ffmpeg()
EDGE = os.environ.get(
    "AIX_EDGE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)

WEB_HOST = os.environ.get("AIX_WEB_HOST", "127.0.0.1")
WEB_PORT = _env_int("AIX_WEB_PORT", 8765)

MEDIA_BACKEND = os.environ.get("AIX_MEDIA_BACKEND", "comfyui")
MEDIA_FREE_VRAM = _env_bool("AIX_MEDIA_FREE_VRAM", True)
MEDIA_AUTOSTART = _env_bool("AIX_MEDIA_AUTOSTART", True)
SHELL_ENABLED = _env_bool("AIX_SHELL", True)
REQUEST_TIMEOUT = _env_int("AIX_TIMEOUT", 600)

KEEP_LAST = _env_int("AIX_KEEP_LAST", 3)
MIN_FREE_GB = _env_int("AIX_MIN_FREE_GB", 15)
KEEP_SESSIONS = _env_int("AIX_KEEP_SESSIONS", 20)
STOP_OLLAMA_ON_CLOSE = _env_bool("AIX_STOP_OLLAMA", True)
RECOVER_REQUEUE = _env_bool("AIX_RECOVER_REQUEUE", False)
