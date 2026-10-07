"""ComfyUI backend: real diffusion image/video via ComfyUI's HTTP API.

Supports text-to-image (FLUX), text-to-video and image-to-video (Wan 2.2
TI2V-5B), and instruction image editing (FLUX Kontext). Workflows are API-format
JSON in workflows/. Results are written to OUTPUT_DIR and registered in the
library. Nothing large is written to C:.
"""

import json
import math
import random
import subprocess
import threading
import time
import uuid
from pathlib import Path

import requests

import storage
from config import (
    COMFY_DIR,
    COMFYUI_URL,
    CACHE_DIR,
    FFMPEG,
    INPUTS_DIR,
    MEDIA_AUTOSTART,
    MEDIA_FREE_VRAM,
    MODEL,
    OLLAMA_BASE,
    OUTPUT_DIR,
    VISION_MODEL,
    WORKFLOW_DIR,
)

DEFAULT_NEGATIVE = (
    "low quality, worst quality, blurry, distorted, watermark, text, "
    "jpeg artifacts, overexposed, static"
)

# Video limits for a 12 GB card: Wan 2.2 TI2V is native 24 fps and a single
# shot beyond ~121 frames (5 s) thrashes. Longer requests are segmented.
VIDEO_FPS = 24
MAX_VIDEO_FRAMES = 121

_CANCEL = threading.Event()


def request_cancel():
    _CANCEL.set()


def clear_cancel():
    _CANCEL.clear()


def is_cancelled():
    return _CANCEL.is_set()


class ComfyError(RuntimeError):
    pass


def _base():
    return COMFYUI_URL.rstrip("/")


def _mkdir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def is_up(timeout=4):
    try:
        return requests.get(_base() + "/system_stats", timeout=timeout).ok
    except requests.RequestException:
        return False


def _free_vram():
    """Ask Ollama to unload the chat/vision models so ComfyUI can use the GPU."""
    if not MEDIA_FREE_VRAM:
        return
    for name in {MODEL, VISION_MODEL}:
        try:
            requests.post(
                OLLAMA_BASE.rstrip("/") + "/api/generate",
                json={"model": name, "keep_alive": 0, "prompt": ""},
                timeout=30,
            )
        except requests.RequestException:
            pass
    time.sleep(2)


def release():
    """Ask ComfyUI to unload its models so Ollama can reclaim the GPU."""
    try:
        requests.post(
            _base() + "/free",
            json={"unload_models": True, "free_memory": True},
            timeout=30,
        )
    except requests.RequestException:
        pass


def ensure_up(timeout=180):
    if is_up():
        return True
    if not MEDIA_AUTOSTART:
        raise ComfyError("ComfyUI is not running - start it with aix.bat")
    python = COMFY_DIR / "python_embeded" / "python.exe"
    main = COMFY_DIR / "ComfyUI" / "main.py"
    if not python.exists() or not main.exists():
        raise ComfyError(f"ComfyUI not found under {COMFY_DIR}")
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(python), "-s", str(main), "--windows-standalone-build",
         "--port", "8188", "--listen", "127.0.0.1", "--lowvram"],
        cwd=str(COMFY_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    start = time.time()
    while time.time() - start < timeout:
        if is_up():
            return True
        time.sleep(2)
    raise ComfyError("ComfyUI did not come up in time")


def _load(name):
    with open(WORKFLOW_DIR / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _seed(seed):
    return int(seed) if seed is not None else random.randint(0, 2**31 - 1)


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def _queue(workflow):
    payload = {"prompt": workflow, "client_id": str(uuid.uuid4())}
    r = requests.post(_base() + "/prompt", json=payload, timeout=60)
    if r.status_code >= 400:
        raise ComfyError(f"queue failed HTTP {r.status_code}: {r.text[:400]}")
    return r.json()["prompt_id"]


def _interrupt():
    try:
        requests.post(_base() + "/interrupt", timeout=5)
        requests.post(_base() + "/queue", json={"clear": True}, timeout=5)
    except requests.RequestException:
        pass


def _await(prompt_id, timeout=1800, poll=1.5):
    start = time.time()
    while time.time() - start < timeout:
        if _CANCEL.is_set():
            _interrupt()
            raise ComfyError("cancelled")
        try:
            r = requests.get(f"{_base()}/history/{prompt_id}", timeout=30)
        except requests.RequestException:
            time.sleep(poll)
            continue
        if r.ok:
            data = r.json()
            entry = data.get(prompt_id)
            if entry:
                if entry.get("outputs"):
                    return entry["outputs"]
                status = entry.get("status", {}) or {}
                if status.get("status_str") == "error":
                    raise ComfyError(f"workflow error: {json.dumps(status)[:600]}")
        time.sleep(poll)
    raise ComfyError(f"timed out after {timeout}s waiting for ComfyUI")


def _collect(outputs):
    files = []
    for node in outputs.values():
        if not isinstance(node, dict):
            continue
        for key in ("images", "gifs", "videos", "video"):
            for item in node.get(key) or []:
                if isinstance(item, dict) and item.get("filename"):
                    files.append(item)
    return files


def _download(item, dest):
    params = {
        "filename": item["filename"],
        "subfolder": item.get("subfolder", ""),
        "type": item.get("type", "output"),
    }
    r = requests.get(_base() + "/view", params=params, timeout=600)
    r.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return dest


def _transcode(src, dest, fps=None):
    if not FFMPEG or not Path(FFMPEG).exists():
        return src
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", str(src)]
    if fps:
        cmd += ["-r", str(int(fps))]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except Exception:
        return src
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
        try:
            src.unlink()
        except OSError:
            pass
        return dest
    return src


def _save_output(item, prefix, prompt, model, params, transcode_fps=None):
    suffix = Path(item["filename"]).suffix.lower()
    dest = OUTPUT_DIR / f"{prefix}_{_stamp()}_{uuid.uuid4().hex[:4]}{suffix}"
    _download(item, dest)
    if transcode_fps and suffix in (".webm", ".mkv", ".avi"):
        final = _transcode(dest, dest.with_suffix(".mp4"), fps=transcode_fps)
        if final != dest:
            dest = Path(final)
    storage.register(dest, prompt=prompt, model=model, params=params)
    return str(dest)


def _upload_image(path):
    path = Path(path)
    with open(path, "rb") as fh:
        r = requests.post(
            _base() + "/upload/image",
            files={"image": (path.name, fh, "application/octet-stream")},
            data={"overwrite": "true", "type": "input"},
            timeout=120,
        )
    r.raise_for_status()
    info = r.json()
    name = info.get("name")
    if info.get("subfolder"):
        name = f"{info['subfolder']}/{name}"
    return name


def _resolve_input(image):
    p = Path(image)
    candidates = [p] if p.is_absolute() else []
    for base in (INPUTS_DIR, OUTPUT_DIR, WORKFLOW_DIR.parent):
        candidates.append((base / p).resolve())
        candidates.append((base / p.name).resolve())
    for cand in candidates:
        if cand.exists():
            return cand
    raise ComfyError(f"input image not found: {image}")


def _scale_size(path, max_side=832, mult=32):
    from PIL import Image

    with Image.open(path) as im:
        w, h = im.size
    scale = min(1.0, float(max_side) / max(w, h))
    w2 = max(mult, int(round(w * scale / mult)) * mult)
    h2 = max(mult, int(round(h * scale / mult)) * mult)
    return w2, h2


def _length_from(seconds, fps):
    length = max(5, int(round(float(seconds) * int(fps))))
    return ((length - 1) // 4) * 4 + 1


# --- generation ------------------------------------------------------------
def generate_image(prompt, width=1024, height=1024, steps=20, seed=None, guidance=3.5):
    _mkdir()
    ensure_up()
    _free_vram()
    clear_cancel()
    wf = _load("flux_txt2img.json")
    wf["positive"]["inputs"]["text"] = prompt
    wf["latent"]["inputs"]["width"] = int(width)
    wf["latent"]["inputs"]["height"] = int(height)
    wf["guidance"]["inputs"]["guidance"] = float(guidance)
    wf["sampler"]["inputs"]["steps"] = int(steps)
    wf["sampler"]["inputs"]["seed"] = _seed(seed)
    wf["save"]["inputs"]["filename_prefix"] = f"aix/img_{_stamp()}"
    try:
        outputs = _await(_queue(wf))
        files = [f for f in _collect(outputs)
                 if Path(f["filename"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        if not files:
            files = _collect(outputs)
        if not files:
            raise ComfyError("no image produced")
        path = _save_output(files[0], "img", prompt, "flux1-dev",
                            {"width": width, "height": height, "steps": steps, "seed": seed})
        return f"{path}  ({width}x{height}, flux1-dev)"
    finally:
        release()


def _last_frame(mp4):
    out = CACHE_DIR / f"last_{_stamp()}_{uuid.uuid4().hex[:4]}.png"
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-sseof", "-0.1", "-i", str(mp4),
           "-frames:v", "1", str(out)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001
        return None
    return out if out.exists() else None


def _concat(paths, prefix):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    listfile = CACHE_DIR / f"concat_{_stamp()}_{uuid.uuid4().hex[:4]}.txt"
    listfile.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in paths), encoding="utf-8")
    out = OUTPUT_DIR / f"{prefix}_{_stamp()}_{uuid.uuid4().hex[:4]}.mp4"
    base = [FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile)]
    ok = False
    if FFMPEG and Path(FFMPEG).exists():
        try:
            proc = subprocess.run(base + ["-c", "copy", str(out)],
                                  capture_output=True, text=True, timeout=600)
            ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            try:
                subprocess.run(base + ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                                       "-movflags", "+faststart", str(out)],
                               capture_output=True, text=True, timeout=900)
            except Exception:  # noqa: BLE001
                pass
    try:
        listfile.unlink()
    except OSError:
        pass
    for p in paths:
        try:
            Path(p).unlink()
        except OSError:
            pass
    return out


def _run_video_segment(prompt, negative, width, height, frames, steps, seed,
                       image_path, prefix, fps):
    wf = _load("wan22_i2v.json" if image_path else "wan22_t2v.json")
    if image_path:
        wf["loadimage"]["inputs"]["image"] = _upload_image(image_path)
    wf["positive"]["inputs"]["text"] = prompt
    wf["negative"]["inputs"]["text"] = negative
    wf["latent"]["inputs"]["width"] = int(width)
    wf["latent"]["inputs"]["height"] = int(height)
    wf["latent"]["inputs"]["length"] = int(frames)
    wf["sampler"]["inputs"]["steps"] = int(steps)
    wf["sampler"]["inputs"]["seed"] = _seed(seed)
    wf["save"]["inputs"]["fps"] = float(fps)
    wf["save"]["inputs"]["filename_prefix"] = f"aix/{prefix}_{_stamp()}"
    outputs = _await(_queue(wf), timeout=3600)
    files = _collect(outputs)
    if not files:
        raise ComfyError("no video produced")
    item = files[0]
    suffix = Path(item["filename"]).suffix.lower()
    raw = CACHE_DIR / f"raw_{_stamp()}_{uuid.uuid4().hex[:4]}{suffix}"
    _download(item, raw)
    mp4 = CACHE_DIR / f"{prefix}_{_stamp()}_{uuid.uuid4().hex[:4]}.mp4"
    return Path(_transcode(raw, mp4, fps=None))


def _video_core(prompt, seconds, fps, width, height, steps, seed, negative, start_image, prefix):
    _mkdir()
    ensure_up()
    _free_vram()
    clear_cancel()
    fps = VIDEO_FPS
    total = max(1, int(round(float(seconds) * fps)))
    nseg = max(1, math.ceil(total / MAX_VIDEO_FRAMES))
    remaining = total
    current = _resolve_input(start_image) if start_image else None
    segments = []
    try:
        for i in range(nseg):
            frames = min(MAX_VIDEO_FRAMES, remaining)
            frames = ((frames - 1) // 4) * 4 + 1
            remaining -= frames
            seg_seed = (int(seed) + i) if seed is not None else None
            print(f"[video] segment {i + 1}/{nseg}: {width}x{height} {frames} frames, steps={steps}")
            seg = _run_video_segment(prompt, negative, width, height, frames, steps,
                                     seg_seed, current, prefix, fps)
            segments.append(seg)
            if i < nseg - 1:
                current = _last_frame(seg)
        if len(segments) == 1:
            final = OUTPUT_DIR / segments[0].name
            segments[0].replace(final)
        else:
            final = _concat(segments, prefix)
        storage.register(final, prompt=prompt, model="wan2.2-ti2v-5b",
                         params={"seconds": seconds, "fps": fps, "segments": nseg,
                                 "steps": steps, "seed": seed,
                                 "source": str(start_image) if start_image else None})
        note = " i2v" if start_image else ""
        return (f"{final}  ({total} frames, ~{round(total / float(fps), 2)}s @{fps}fps, "
                f"{nseg} segment(s){note}, wan2.2-ti2v-5b)")
    finally:
        release()


def generate_video(prompt, seconds=4, fps=24, width=832, height=480, steps=30, seed=None,
                   negative=DEFAULT_NEGATIVE):
    return _video_core(prompt, seconds, fps, width, height, steps, seed, negative, None, "vid")


def animate_image(image, prompt, seconds=2, fps=24, steps=30, seed=None,
                  negative=DEFAULT_NEGATIVE, max_side=832):
    src = _resolve_input(image)
    width, height = _scale_size(src, max_side=max_side, mult=32)
    return _video_core(prompt, seconds, fps, width, height, steps, seed, negative, src, "i2v")


def edit_image(image, prompt, steps=20, seed=None, guidance=2.5, max_side=1024):
    _mkdir()
    ensure_up()
    _free_vram()
    clear_cancel()
    src = _resolve_input(image)
    name = _upload_image(src)
    width, height = _scale_size(src, max_side=max_side, mult=16)
    wf = _load("flux_kontext_edit.json")
    wf["loadimage"]["inputs"]["image"] = name
    wf["positive"]["inputs"]["text"] = prompt
    wf["guidance"]["inputs"]["guidance"] = float(guidance)
    wf["latent"]["inputs"]["width"] = width
    wf["latent"]["inputs"]["height"] = height
    wf["sampler"]["inputs"]["steps"] = int(steps)
    wf["sampler"]["inputs"]["seed"] = _seed(seed)
    wf["save"]["inputs"]["filename_prefix"] = f"aix/edit_{_stamp()}"
    try:
        outputs = _await(_queue(wf))
        files = [f for f in _collect(outputs)
                 if Path(f["filename"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        if not files:
            files = _collect(outputs)
        if not files:
            raise ComfyError("no image produced")
        path = _save_output(files[0], "edit", prompt, "flux1-kontext-dev",
                            {"source": str(src), "steps": steps, "guidance": guidance, "seed": seed})
        return f"{path}  ({width}x{height}, flux kontext edit)"
    finally:
        release()
