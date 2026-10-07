"""Local vision: describe an image (or a video frame) with an Ollama vision model."""

import base64
from pathlib import Path

import requests

from config import MEDIA_DIR, OLLAMA_BASE, REQUEST_TIMEOUT, VISION_MODEL, WORKSPACE

VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif"}


class VisionError(RuntimeError):
    pass


def _extract_frame(path, position=0.1):
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise VisionError("cannot open video")
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * position))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise VisionError("cannot read a frame from the video")
    out = MEDIA_DIR / f"{path.stem}_frame.jpg"
    cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return out


def _resolve(path):
    target = Path(path)
    candidates = []
    if target.is_absolute():
        candidates.append(target)
    else:
        parts = target.parts
        if parts and parts[0].lower() == "media":
            candidates.append((MEDIA_DIR / Path(*parts[1:])).resolve())
        candidates.append((MEDIA_DIR / target).resolve())
        candidates.append((MEDIA_DIR / target.name).resolve())
        candidates.append((WORKSPACE / target).resolve())
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # fall back to the newest media file with a matching name/suffix
    recent = sorted(MEDIA_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate in recent:
        if candidate.name == target.name:
            return candidate
    # last resort: the most recently generated media file
    for candidate in recent:
        if candidate.is_file() and candidate.suffix.lower() != ".jpg":
            return candidate
    return None


def describe_image(path, question="Describe this image in detail.", model=None):
    target = _resolve(path)
    if target is None:
        recent = sorted(
            (p.name for p in MEDIA_DIR.glob("*.*") if p.is_file()),
            key=lambda n: (MEDIA_DIR / n).stat().st_mtime,
            reverse=True,
        )[:10]
        return f"file not found: {path}. Available media files: {', '.join(recent) or '(none)'}"

    if target.suffix.lower() in VIDEO_EXT:
        target = _extract_frame(target)

    b64 = base64.b64encode(target.read_bytes()).decode("ascii")
    payload = {
        "model": model or VISION_MODEL,
        "messages": [{"role": "user", "content": question, "images": [b64]}],
        "stream": False,
    }
    try:
        r = requests.post(
            OLLAMA_BASE.rstrip("/") + "/api/chat", json=payload, timeout=REQUEST_TIMEOUT
        )
    except requests.RequestException as exc:
        return f"vision request failed: {exc}"
    if r.status_code >= 400:
        return f"vision model error HTTP {r.status_code}: {r.text[:200]}"
    return (r.json().get("message", {}) or {}).get("content", "").strip() or "(no description)"
