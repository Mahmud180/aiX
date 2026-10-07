"""Agent tools and their JSON specs."""

import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

import requests

import media
from config import SHELL_ENABLED, WORKSPACE

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


DANGEROUS = {"run_shell", "write_file"}


def _resolve(path):
    p = Path(path)
    target = p.resolve() if p.is_absolute() else (WORKSPACE / p).resolve()
    if target != WORKSPACE and WORKSPACE not in target.parents:
        raise ValueError(f"refusing path outside workspace: {path}")
    return target


def get_time():
    return _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def list_dir(path="."):
    target = _resolve(path)
    if not target.is_dir():
        return f"not a directory: {target}"
    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    lines = []
    for e in entries:
        if e.is_dir():
            lines.append(f"DIR   {e.name}")
        else:
            lines.append(f"FILE  {e.name}  ({e.stat().st_size} bytes)")
    return "\n".join(lines) or "(empty)"


def read_file(path, max_chars=20000):
    target = _resolve(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n... [truncated, {len(text)} chars total]"
    return text


def write_file(path, content):
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {target}"


def run_shell(command, timeout=120):
    if not SHELL_ENABLED:
        return "shell tool is disabled (set AIX_SHELL=1 to enable)"
    for shell in (["pwsh", "-NoProfile", "-Command"], ["powershell", "-NoProfile", "-Command"]):
        try:
            proc = subprocess.run(
                shell + [command],
                capture_output=True,
                text=True,
                cwd=str(WORKSPACE),
                timeout=timeout,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return out.strip() or f"(no output, exit code {proc.returncode})"
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return f"command timed out after {timeout}s"
    return "no PowerShell interpreter found"


def run_python(code, timeout=120):
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(WORKSPACE),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"python timed out after {timeout}s"
    out = (proc.stdout or "") + (proc.stderr or "")
    return out.strip() or f"(no output, exit code {proc.returncode})"


def web_search(query, max_results=5):
    if DDGS is None:
        return "web search unavailable: install 'ddgs' (pip install ddgs)"
    try:
        results = list(DDGS().text(query, max_results=int(max_results)))
    except Exception as exc:  # noqa: BLE001 - surface any backend error to the model
        return f"search failed: {exc}"
    if not results:
        return "no results"
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '')}\n   {r.get('href', '')}\n   {r.get('body', '')}")
    return "\n".join(lines)


def fetch_url(url, max_chars=8000):
    if BeautifulSoup is None:
        return "fetch unavailable: install 'beautifulsoup4'"
    try:
        r = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (aiX local agent)"},
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        return f"fetch failed: {exc}"
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    if len(text) > max_chars:
        text = text[:max_chars] + "... [truncated]"
    return f"URL: {r.url}\n\n{text}"


def _tracked(name, args, fn):
    try:
        import state

        job_id = state.add_job(name, args, status="running")
    except Exception:
        state = None
        job_id = None
    try:
        result = fn()
        if job_id:
            state.update_job(job_id, "done", result=str(result)[:500])
        return result
    except Exception as exc:
        if job_id:
            state.update_job(job_id, "failed", error=str(exc))
        raise


def generate_image(prompt, width=1024, height=1024, steps=20, seed=None, style=None):
    return _tracked(
        "generate_image", {"prompt": prompt, "width": width, "height": height},
        lambda: media.generate_image(prompt, width=width, height=height, steps=steps, seed=seed),
    )


def generate_video(prompt, seconds=4, fps=24, width=832, height=480, steps=30, seed=None):
    return _tracked(
        "generate_video", {"prompt": prompt, "seconds": seconds, "fps": fps},
        lambda: media.generate_video(prompt, seconds=seconds, fps=fps, width=width,
                                     height=height, steps=steps, seed=seed),
    )


def animate_image(image, prompt, seconds=2, fps=24, steps=30, seed=None):
    return _tracked(
        "animate_image", {"image": image, "prompt": prompt, "seconds": seconds, "fps": fps},
        lambda: media.animate_image(image, prompt, seconds=seconds, fps=fps,
                                    steps=steps, seed=seed),
    )


def edit_image(image, prompt, steps=20, seed=None):
    return _tracked(
        "edit_image", {"image": image, "prompt": prompt},
        lambda: media.edit_image(image, prompt, steps=steps, seed=seed),
    )


def open_media(path):
    return media.open_media(path)


def list_inputs():
    from config import INPUTS_DIR

    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in INPUTS_DIR.iterdir() if p.is_file())
    if not files:
        return "(no input files) - drop a file in inputs/ or use the web upload button"
    return "\n".join(f"{p.name}  ({p.stat().st_size} bytes)" for p in files)


def describe_input(path, question="Describe this media in detail."):
    import vision
    from config import INPUTS_DIR

    target = Path(path)
    if not target.is_absolute() and not target.exists():
        target = INPUTS_DIR / target.name
    return vision.describe_image(str(target), question=question)


def describe_image(path, question="Describe this image in detail."):
    import vision

    return vision.describe_image(path, question=question)


# ffmpeg utilities
def _resolve_media(path):
    from config import INPUTS_DIR, OUTPUT_DIR

    p = Path(path)
    if p.is_absolute() and p.exists():
        return p
    for base in (INPUTS_DIR, OUTPUT_DIR, WORKSPACE):
        for cand in ((base / p).resolve(), (base / p.name).resolve()):
            if cand.exists():
                return cand
    raise ValueError(f"file not found: {path}")


def _ffmpeg(args, timeout=600):
    from config import FFMPEG

    if not FFMPEG or not Path(FFMPEG).exists():
        return None
    return subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args],
                          capture_output=True, text=True, timeout=timeout)


def _out_path(prefix, ext):
    import time
    import uuid

    from config import OUTPUT_DIR

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:4]}{ext}"


def _register(path, prompt, source):
    import storage

    storage.register(path, prompt=prompt, model="ffmpeg", params={"source": str(source)})


def trim_video(path, start=0, duration=5):
    src = _resolve_media(path)
    out = _out_path("trim", ".mp4")
    proc = _ffmpeg(["-ss", str(start), "-i", str(src), "-t", str(duration),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out)])
    if proc is None:
        return "ffmpeg unavailable"
    if proc.returncode != 0:
        return f"trim failed: {proc.stderr[:300]}"
    _register(out, f"trim {src.name}", src)
    return f"{out}  (trimmed {duration}s)"


def extract_frames(path, fps=1):
    src = _resolve_media(path)
    from config import OUTPUT_DIR

    folder = OUTPUT_DIR / f"frames_{src.stem}"
    folder.mkdir(parents=True, exist_ok=True)
    proc = _ffmpeg(["-i", str(src), "-vf", f"fps={fps}", str(folder / "frame_%04d.png")])
    if proc is None:
        return "ffmpeg unavailable"
    if proc.returncode != 0:
        return f"extract failed: {proc.stderr[:300]}"
    return f"{folder}  ({len(list(folder.glob('*.png')))} frames)"


def video_to_gif(path, fps=12, width=480):
    src = _resolve_media(path)
    out = _out_path("gif", ".gif")
    proc = _ffmpeg(["-i", str(src), "-vf", f"fps={fps},scale={width}:-1:flags=lanczos", str(out)])
    if proc is None:
        return "ffmpeg unavailable"
    if proc.returncode != 0:
        return f"gif failed: {proc.stderr[:300]}"
    _register(out, f"gif from {src.name}", src)
    return str(out)


def add_audio(video, audio):
    src = _resolve_media(video)
    aud = _resolve_media(audio)
    out = _out_path("audio", ".mp4")
    proc = _ffmpeg(["-i", str(src), "-i", str(aud), "-c:v", "copy", "-c:a", "aac",
                    "-shortest", str(out)])
    if proc is None:
        return "ffmpeg unavailable"
    if proc.returncode != 0:
        return f"add_audio failed: {proc.stderr[:300]}"
    _register(out, f"{src.name} + audio", src)
    return str(out)


def resize_image(path, width=1024, height=None):
    from PIL import Image

    from config import OUTPUT_DIR

    src = _resolve_media(path)
    im = Image.open(src)
    if height is None:
        height = int(im.height * (width / im.width))
    out = OUTPUT_DIR / f"resize_{src.stem}_{width}.png"
    im.resize((int(width), int(height))).save(out)
    _register(out, f"resize {src.name}", src)
    return f"{out}  ({width}x{int(height)})"


REGISTRY = {
    "get_time": get_time,
    "list_dir": list_dir,
    "read_file": read_file,
    "write_file": write_file,
    "run_shell": run_shell,
    "run_python": run_python,
    "web_search": web_search,
    "fetch_url": fetch_url,
    "generate_image": generate_image,
    "generate_video": generate_video,
    "animate_image": animate_image,
    "edit_image": edit_image,
    "open_media": open_media,
    "describe_image": describe_image,
    "list_inputs": list_inputs,
    "describe_input": describe_input,
    "trim_video": trim_video,
    "extract_frames": extract_frames,
    "video_to_gif": video_to_gif,
    "add_audio": add_audio,
    "resize_image": resize_image,
}


def _schema(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SPECS = [
    _schema("get_time", "Get the current local date and time.", {}, []),
    _schema(
        "list_dir",
        "List files in a directory inside the workspace.",
        {"path": {"type": "string", "description": "Relative path (default '.')"}},
        [],
    ),
    _schema(
        "read_file",
        "Read a text file inside the workspace.",
        {"path": {"type": "string", "description": "Relative file path"}},
        ["path"],
    ),
    _schema(
        "write_file",
        "Create or overwrite a text file inside the workspace.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
    ),
    _schema(
        "run_shell",
        "Run a PowerShell command in the workspace and return its output.",
        {"command": {"type": "string"}},
        ["command"],
    ),
    _schema(
        "run_python",
        "Execute a short Python snippet and return its output.",
        {"code": {"type": "string"}},
        ["code"],
    ),
    _schema(
        "web_search",
        "Search the web and return the top results.",
        {"query": {"type": "string"}, "max_results": {"type": "integer"}},
        ["query"],
    ),
    _schema(
        "fetch_url",
        "Download a URL and return its readable text content.",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _schema(
        "generate_image",
        "Generate an image from a text prompt and save it to the media folder. "
        "Returns the file path.",
        {
            "prompt": {"type": "string"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "style": {"type": "string"},
        },
        ["prompt"],
    ),
    _schema(
        "generate_video",
        "Generate a video from a text prompt (Wan 2.2). Runs at 24 fps; requests "
        "longer than 5 seconds are generated as multiple stitched segments. Returns "
        "the file path.",
        {
            "prompt": {"type": "string"},
            "seconds": {"type": "number"},
            "steps": {"type": "integer"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
        },
        ["prompt"],
    ),
    _schema(
        "open_media",
        "Open a previously generated image or video in the default desktop viewer.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _schema(
        "describe_image",
        "Describe a generated image or video (a frame is sampled) using a local vision "
        "model. Returns a text caption.",
        {"path": {"type": "string"}, "question": {"type": "string"}},
        ["path"],
    ),
    _schema(
        "list_inputs",
        "List the user-uploaded input files (inputs/ folder).",
        {},
        [],
    ),
    _schema(
        "describe_input",
        "Describe an uploaded input image or video using the local vision model.",
        {"path": {"type": "string"}, "question": {"type": "string"}},
        ["path"],
    ),
    _schema(
        "animate_image",
        "Animate an input image into a video (image-to-video) at 24 fps. Use when the "
        "user gives a photo and asks for a video of it moving, e.g. 'make me fly'. "
        "Requests longer than 5 seconds become multiple stitched segments. Returns the path.",
        {
            "image": {"type": "string", "description": "input image path or name"},
            "prompt": {"type": "string", "description": "how the scene should move/change"},
            "seconds": {"type": "number"},
            "steps": {"type": "integer"},
        },
        ["image", "prompt"],
    ),
    _schema(
        "edit_image",
        "Edit an existing image with a text instruction (e.g. 'put me on a beach', "
        "'change my outfit to a suit'). Returns the new image path.",
        {"image": {"type": "string"}, "prompt": {"type": "string"}},
        ["image", "prompt"],
    ),
    _schema(
        "trim_video",
        "Trim a video to a start time and duration (seconds).",
        {"path": {"type": "string"}, "start": {"type": "number"}, "duration": {"type": "number"}},
        ["path"],
    ),
    _schema(
        "extract_frames",
        "Extract frames from a video at a given fps into a folder.",
        {"path": {"type": "string"}, "fps": {"type": "number"}},
        ["path"],
    ),
    _schema(
        "video_to_gif",
        "Convert a video to an animated GIF.",
        {"path": {"type": "string"}, "fps": {"type": "integer"}, "width": {"type": "integer"}},
        ["path"],
    ),
    _schema(
        "add_audio",
        "Attach an audio file to a video.",
        {"video": {"type": "string"}, "audio": {"type": "string"}},
        ["video", "audio"],
    ),
    _schema(
        "resize_image",
        "Resize an image to a target width (height optional).",
        {"path": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}},
        ["path"],
    ),
]


def execute(name, arguments):
    fn = REGISTRY.get(name)
    if fn is None:
        return f"unknown tool: {name}"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return f"invalid arguments for {name}: {arguments!r}"
    arguments = arguments or {}
    try:
        return str(fn(**arguments))
    except Exception as exc:  # noqa: BLE001 - report tool failure back to the model
        return f"{name} error: {exc}"


def text_protocol_prompt():
    spec = json.dumps(TOOL_SPECS, indent=2)
    return (
        "You can use tools by emitting a JSON block wrapped in <tool_call> tags, "
        "exactly like this:\n"
        '<tool_call>{"name": "web_search", "arguments": {"query": "example"}}</tool_call>\n'
        "Emit only one tool call per block. After the tool result is provided, "
        "continue and answer. Available tools:\n" + spec
    )
