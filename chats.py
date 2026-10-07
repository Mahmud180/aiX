"""Persistent chat storage.

Each chat is a JSON file in chats/<id>.json written atomically, so history
survives reloads and power cuts. Multiple chats can coexist.
"""

import json
import time
import uuid
from pathlib import Path

import state
from config import CHAT_DIR


def _path(cid):
    return CHAT_DIR / f"{cid}.json"


def list_chats():
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in CHAT_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append({
            "id": data.get("id", p.stem),
            "title": data.get("title", "New chat"),
            "updated": data.get("updated", 0),
            "count": len(data.get("messages", [])),
        })
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def get_chat(cid):
    p = _path(cid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def create_chat(title="New chat"):
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    data = {"id": cid, "title": title, "created": time.time(),
            "updated": time.time(), "status": "idle", "messages": []}
    state.atomic_write(_path(cid), json.dumps(data, ensure_ascii=False, indent=2))
    return data


def _autotitle(messages):
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str) and m["content"].strip():
            text = m["content"].strip().replace("\n", " ")
            return text[:48] + ("…" if len(text) > 48 else "")
    return "New chat"


def save_chat(cid, messages, title=None, status=None):
    data = get_chat(cid) or {"id": cid, "created": time.time(), "title": "New chat"}
    data["messages"] = messages
    data["updated"] = time.time()
    if status is not None:
        data["status"] = status
    if title:
        data["title"] = title
    elif data.get("title") in (None, "", "New chat"):
        data["title"] = _autotitle(messages)
    state.atomic_write(_path(cid), json.dumps(data, ensure_ascii=False, indent=2))
    return data


def reset_stale_generating():
    """Any chat left 'generating' after a crash is no longer running."""
    fixed = []
    for entry in list_chats():
        data = get_chat(entry["id"]) or {}
        if data.get("status") == "generating":
            data["status"] = "idle"
            state.atomic_write(_path(entry["id"]), json.dumps(data, ensure_ascii=False, indent=2))
            fixed.append(entry["id"])
    return fixed


def delete_chat(cid):
    try:
        _path(cid).unlink()
        return True
    except OSError:
        return False


def rename_chat(cid, title):
    data = get_chat(cid)
    if not data:
        return False
    data["title"] = title
    data["updated"] = time.time()
    state.atomic_write(_path(cid), json.dumps(data, ensure_ascii=False, indent=2))
    return True
