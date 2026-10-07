"""Durable state: atomic writes, job ledger and process registry."""

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from config import RUN_DIR, RUN_MARKER, STATE_DB


def ensure_dirs():
    RUN_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write(path, data, encoding="utf-8"):
    """Write via temp + fsync + rename so a power cut can't leave a half file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{uuid.uuid4().hex[:6]}")
    with open(tmp, "w", encoding=encoding) as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def atomic_write_bytes(path, blob):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{uuid.uuid4().hex[:6]}")
    with open(tmp, "wb") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# run marker (detect a previous unclean exit)
def set_marker(token):
    ensure_dirs()
    atomic_write(RUN_MARKER, json.dumps({"token": token, "started": time.time()}))


def get_marker():
    return read_json(RUN_MARKER)


def clear_marker():
    try:
        RUN_MARKER.unlink()
    except OSError:
        pass


# job ledger
def _db():
    ensure_dirs()
    conn = sqlite3.connect(str(STATE_DB), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    conn = _db()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                args TEXT,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                created REAL,
                updated REAL
            )"""
        )
        conn.commit()
    finally:
        conn.close()


def add_job(kind, args, status="queued"):
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO jobs (id, kind, args, status, created, updated) VALUES (?,?,?,?,?,?)",
            (job_id, kind, json.dumps(args or {}), status, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def update_job(job_id, status, result=None, error=None):
    conn = _db()
    try:
        conn.execute(
            "UPDATE jobs SET status=?, result=?, error=?, updated=? WHERE id=?",
            (status, result, error, time.time(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_jobs(limit=50):
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, kind, args, status, result, error, created, updated "
            "FROM jobs ORDER BY created DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0], "kind": r[1], "args": read_json_text(r[2]),
            "status": r[3], "result": r[4], "error": r[5],
            "created": r[6], "updated": r[7],
        }
        for r in rows
    ]


def read_json_text(text):
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {}


def mark_interrupted():
    """Any job still 'running' at startup was killed by a power cut."""
    conn = _db()
    try:
        rows = conn.execute("SELECT id FROM jobs WHERE status IN ('running','queued')").fetchall()
        conn.execute(
            "UPDATE jobs SET status='interrupted', updated=? WHERE status IN ('running','queued')",
            (time.time(),),
        )
        conn.commit()
    finally:
        conn.close()
    return [r[0] for r in rows]


def interrupted_jobs():
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, kind, args FROM jobs WHERE status='interrupted' ORDER BY created"
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "kind": r[1], "args": read_json_text(r[2])} for r in rows]


# process registry
def register_process(name, pid):
    ensure_dirs()
    reg = read_json(RUN_DIR / "processes.json", {}) or {}
    reg[name] = {"pid": pid, "started": time.time()}
    atomic_write(RUN_DIR / "processes.json", json.dumps(reg, indent=2))


def unregister_process(name):
    reg = read_json(RUN_DIR / "processes.json", {}) or {}
    reg.pop(name, None)
    atomic_write(RUN_DIR / "processes.json", json.dumps(reg, indent=2))


def list_processes():
    return read_json(RUN_DIR / "processes.json", {}) or {}
