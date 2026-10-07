"""Process manager: start/stop/status for the aiX stack (Ollama + ComfyUI).

PIDs are tracked in run/processes.json so Stop/Close can terminate exactly the
processes we started (and not unrelated ones).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import state
from config import (
    COMFY_DIR,
    OLLAMA_EXE,
    OLLAMA_MODELS,
    STOP_OLLAMA_ON_CLOSE,
)

CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
FLAGS = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS


def _detached(args, env=None, cwd=None):
    proc = subprocess.Popen(
        args, env=env, cwd=cwd,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=FLAGS, close_fds=True,
    )
    return proc.pid


def ollama_up(timeout=3):
    import requests
    from config import OLLAMA_BASE
    try:
        return requests.get(OLLAMA_BASE.rstrip("/") + "/api/tags", timeout=timeout).ok
    except Exception:
        return False


def comfy_up(timeout=3):
    import comfyui_backend
    return comfyui_backend.is_up(timeout=timeout)


def start_ollama(wait=True):
    if ollama_up():
        return "ollama: already running"
    exe = Path(OLLAMA_EXE)
    if not exe.exists():
        return f"ollama: binary not found ({exe})"
    env = os.environ.copy()
    env["OLLAMA_MODELS"] = OLLAMA_MODELS
    env.setdefault("OLLAMA_HOST", "127.0.0.1:11434")
    pid = _detached([str(exe), "serve"], env=env, cwd=str(exe.parent))
    state.register_process("ollama", pid)
    if not wait:
        return f"ollama: starting (pid {pid})"
    for _ in range(40):
        time.sleep(1)
        if ollama_up():
            return f"ollama: started (pid {pid})"
    return "ollama: started but not answering yet"


def start_comfyui(wait=True):
    if comfy_up():
        return "comfyui: already running"
    python = COMFY_DIR / "python_embeded" / "python.exe"
    main = COMFY_DIR / "ComfyUI" / "main.py"
    if not python.exists() or not main.exists():
        return f"comfyui: not found under {COMFY_DIR}"
    log = COMFY_DIR.parent / "logs" / "comfyui.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    pid = subprocess.Popen(
        [str(python), "-s", str(main), "--windows-standalone-build",
         "--port", "8188", "--listen", "127.0.0.1", "--lowvram"],
        cwd=str(COMFY_DIR), stdin=subprocess.DEVNULL,
        stdout=open(log, "a", encoding="utf-8", errors="replace"),
        stderr=subprocess.STDOUT, creationflags=FLAGS, close_fds=True,
    ).pid
    state.register_process("comfyui", pid)
    if not wait:
        return f"comfyui: starting (pid {pid})"
    for _ in range(90):
        time.sleep(2)
        if comfy_up():
            return f"comfyui: started (pid {pid})"
    return "comfyui: started but not answering yet"


def start(wait=False):
    """Spawn Ollama + ComfyUI. Default is non-blocking (return immediately)."""
    return {"ollama": start_ollama(wait), "comfyui": start_comfyui(wait)}


def _kill(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=30)
        return True
    except Exception:
        return False


def stop(stop_ollama=None):
    if stop_ollama is None:
        stop_ollama = STOP_OLLAMA_ON_CLOSE
    procs = state.list_processes()
    stopped = []
    for name, info in procs.items():
        if name == "ollama" and not stop_ollama:
            continue
        pid = info.get("pid")
        if pid and _kill(pid):
            stopped.append(f"{name}({pid})")
    state.atomic_write(state.RUN_DIR / "processes.json", "{}")
    state.clear_marker()
    return {"stopped": stopped, "ollama_preserved": not stop_ollama}


def status():
    import storage
    from config import OLLAMA_BASE
    return {
        "ollama": ollama_up(),
        "comfyui": comfy_up(),
        "ollama_url": OLLAMA_BASE,
        "free_gb": round(storage.free_gb(), 1),
        "processes": state.list_processes(),
    }


if __name__ == "__main__":
    state.ensure_dirs()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "start":
        print(start(wait="--sync" in sys.argv))
    elif cmd == "stop":
        print(stop(stop_ollama="--keep-ollama" not in sys.argv))
    elif cmd == "status":
        print(status())
    else:
        print(f"unknown command: {cmd}")
