"""Append-only session logging.

Writes logs/sessions/<id>.jsonl (events) and <id>.log (console transcript).
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

from config import LOGS_DIR, SESSION_DIR

_STATE = {"logger": None}


class SessionLogger:
    def __init__(self, session_id=None, model=None, meta=None):
        self.id = session_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.model = model
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.jsonl = SESSION_DIR / f"{self.id}.jsonl"
        self.textlog = SESSION_DIR / f"{self.id}.log"
        self._fh = open(self.jsonl, "a", encoding="utf-8")
        self._text = open(self.textlog, "a", encoding="utf-8", errors="replace")
        self.event("session_start", model=model, meta=meta or {})

    def event(self, kind, **data):
        record = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind}
        record.update(data)
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except (OSError, ValueError):
            pass
        return record

    def write(self, text):
        try:
            self._text.write(text)
            self._text.flush()
        except (OSError, ValueError):
            pass

    def close(self):
        self.event("session_end")
        for fh in (self._fh, self._text):
            try:
                fh.close()
            except OSError:
                pass


class Tee:
    """Duplicate writes to the real stream and a logger."""

    def __init__(self, stream, logger):
        self.stream = stream
        self.logger = logger

    def write(self, data):
        try:
            self.stream.write(data)
        except (OSError, UnicodeError):
            pass
        self.logger.write(data)
        return len(data)

    def flush(self):
        try:
            self.stream.flush()
        except (OSError, UnicodeError):
            pass

    def __getattr__(self, item):
        return getattr(self.stream, item)


def install(logger):
    """Route stdout/stderr through the logger (idempotent)."""
    _STATE["logger"] = logger
    if not isinstance(sys.stdout, Tee):
        sys.stdout = Tee(sys.stdout, logger)
    if not isinstance(sys.stderr, Tee):
        sys.stderr = Tee(sys.stderr, logger)
    return logger


def get():
    return _STATE["logger"]


def current_session_id():
    logger = _STATE["logger"]
    return logger.id if logger else None


def read_session_tolerant(path):
    """Yield events; silently drop a torn trailing line (power-cut safety)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def recover_logs():
    """Quarantine torn trailing lines in every session jsonl."""
    repaired = []
    if not SESSION_DIR.exists():
        return repaired
    for path in SESSION_DIR.glob("*.jsonl"):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if not raw or raw.endswith(b"\n"):
            continue
        # final line was cut off by a power loss - drop it
        cut = raw.rfind(b"\n") + 1
        try:
            path.write_bytes(raw[:cut])
            repaired.append(path.name)
        except OSError:
            pass
    return repaired
