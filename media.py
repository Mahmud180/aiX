"""Media generation facade over the ComfyUI backend."""

import comfyui_backend as backend


def generate_image(prompt, width=1024, height=1024, steps=20, seed=None, style=None):
    return backend.generate_image(prompt, width=width, height=height, steps=steps, seed=seed)


def generate_video(prompt, seconds=4, fps=24, width=832, height=480, steps=30, seed=None):
    return backend.generate_video(
        prompt, seconds=seconds, fps=fps, width=width, height=height, steps=steps, seed=seed
    )


def animate_image(image, prompt, seconds=2, fps=24, steps=30, seed=None):
    return backend.animate_image(
        image, prompt, seconds=seconds, fps=fps, steps=steps, seed=seed
    )


def edit_image(image, prompt, steps=20, seed=None):
    return backend.edit_image(image, prompt, steps=steps, seed=seed)


def open_media(path):
    import os
    from pathlib import Path

    from config import OUTPUT_DIR

    target = Path(path)
    if not target.is_absolute():
        for base in (OUTPUT_DIR, Path.cwd()):
            cand = (base / target).resolve()
            if cand.exists():
                target = cand
                break
    if not target.exists():
        return f"not found: {target}"
    if not hasattr(os, "startfile"):
        return f"cannot open automatically: {target}"
    os.startfile(str(target))
    return f"opened {target}"
