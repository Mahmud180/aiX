"""Output library: registration, retention and export."""

import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from config import (
    CACHE_DIR,
    EXPORT_DIR,
    KEEP_LAST,
    KEEP_SESSIONS,
    LIBRARY_DB,
    LOGS_DIR,
    MEDIA_DIR,
    MIN_FREE_GB,
    OUTPUT_DIR,
    SESSION_DIR,
)
import state

VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _db():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LIBRARY_DB), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS outputs (
            id TEXT PRIMARY KEY, path TEXT, kind TEXT, prompt TEXT, model TEXT,
            params TEXT, created REAL, size INTEGER, pinned INTEGER DEFAULT 0,
            exported INTEGER DEFAULT 0
        )"""
    )
    conn.commit()
    return conn


def kind_for(path):
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return "file"


def register(path, prompt="", model="", params=None):
    path = Path(path).resolve()
    if not path.exists():
        return None
    oid = uuid.uuid4().hex[:10]
    meta = {
        "id": oid,
        "path": str(path),
        "kind": kind_for(path),
        "prompt": prompt,
        "model": model,
        "params": params or {},
        "created": time.time(),
        "size": path.stat().st_size,
    }
    state.atomic_write(path.with_suffix(path.suffix + ".json"), json.dumps(meta, indent=2))
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO outputs (id, path, kind, prompt, model, params, created, size) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (oid, str(path), meta["kind"], prompt, model, json.dumps(params or {}),
             meta["created"], meta["size"]),
        )
        conn.commit()
    finally:
        conn.close()
    return meta


def list_outputs():
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, path, kind, prompt, model, params, created, size, pinned, exported "
            "FROM outputs ORDER BY created DESC"
        ).fetchall()
    finally:
        conn.close()
    items = []
    for r in rows:
        path = Path(r[1])
        items.append({
            "id": r[0], "path": r[1], "name": path.name, "kind": r[2],
            "prompt": r[3], "model": r[4], "params": state.read_json_text(r[5]),
            "created": r[6], "size": r[7], "pinned": bool(r[8]), "exported": bool(r[9]),
            "exists": path.exists(),
        })
    return items


def get(oid):
    for item in list_outputs():
        if item["id"] == oid:
            return item
    return None


def pin(oid, value=True):
    conn = _db()
    try:
        conn.execute("UPDATE outputs SET pinned=? WHERE id=?", (1 if value else 0, oid))
        conn.commit()
    finally:
        conn.close()


def delete(oid):
    item = get(oid)
    if not item:
        return False
    path = Path(item["path"])
    for p in (path, path.with_suffix(path.suffix + ".json")):
        try:
            p.unlink()
        except OSError:
            pass
    conn = _db()
    try:
        conn.execute("DELETE FROM outputs WHERE id=?", (oid,))
        conn.commit()
    finally:
        conn.close()
    return True


def export(oid, move=False, dest_dir=None):
    item = get(oid)
    if not item or not Path(item["path"]).exists():
        return None
    dest_dir = Path(dest_dir or EXPORT_DIR)
    dest_dir.mkdir(parents=True, exist_ok=True)
    src = Path(item["path"])
    dest = dest_dir / src.name
    if move:
        shutil.move(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))
    conn = _db()
    try:
        conn.execute("UPDATE outputs SET exported=1, path=? WHERE id=?", (str(dest), oid))
        conn.commit()
    finally:
        conn.close()
    return str(dest)


def free_gb(path=None):
    import shutil as _shutil

    from config import AI_ROOT
    for target in (path, OUTPUT_DIR, AI_ROOT):
        if not target:
            continue
        try:
            return _shutil.disk_usage(str(target)).free / (1024 ** 3)
        except OSError:
            continue
    return float("inf")


def clean_cache():
    removed = []
    if CACHE_DIR.exists():
        for p in CACHE_DIR.rglob("*"):
            if p.is_file():
                try:
                    p.unlink()
                    removed.append(str(p))
                except OSError:
                    pass
    return removed


def clean_sessions(keep=KEEP_SESSIONS):
    removed = []
    if not SESSION_DIR.exists():
        return removed
    sessions = sorted(SESSION_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    keep_ids = {p.stem for p in sessions[:keep]}
    for p in SESSION_DIR.glob("*"):
        if p.stem not in keep_ids:
            try:
                p.unlink()
                removed.append(str(p))
            except OSError:
                pass
    return removed


def retention(dry_run=False, min_free_gb=None):
    """Trim cache first, then the oldest unprotected outputs, keeping last N."""
    min_free = MIN_FREE_GB if min_free_gb is None else min_free_gb
    report = {"cache_removed": [], "outputs_removed": [], "sessions_removed": [],
              "free_before": free_gb(), "actions": dry_run}
    if dry_run:
        report["cache_removed"] = [str(p) for p in CACHE_DIR.rglob("*") if p.is_file()] if CACHE_DIR.exists() else []
    else:
        report["cache_removed"] = clean_cache()

    if free_gb() >= min_free:
        return report

    items = list_outputs()
    protected = {i["id"] for i in items[:KEEP_LAST]}      # newest N
    protected |= {i["id"] for i in items if i["pinned"]}  # pinned
    candidates = [i for i in items if i["id"] not in protected]
    candidates.sort(key=lambda i: i["created"])            # oldest first
    for item in candidates:
        if free_gb() >= min_free:
            break
        if dry_run:
            report["outputs_removed"].append(item["path"])
        elif delete(item["id"]):
            report["outputs_removed"].append(item["path"])

    report["sessions_removed"] = clean_sessions()
    report["free_after"] = free_gb()
    return report
