"""Recovery guard - run at startup (and from the UI) after a hard power cut.

Steps:
  1. detect an unclean previous exit via the run marker
  2. verify every model against models_manifest.json (size check)
  3. repair torn JSONL session logs
  4. mark jobs stuck in running/queued as interrupted and requeue them
  5. clear cache/temp
"""

import json
import time
from pathlib import Path

import logger as logmod
import state
import storage
from config import MODELS_MANIFEST, RECOVER_REQUEUE, SESSION_DIR

REPORT = {}


def verify_models():
    missing, ok = [], []
    try:
        manifest = json.loads(MODELS_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"manifest": False, "missing": [], "ok": []}
    if isinstance(manifest, dict):
        manifest = [manifest]
    for entry in manifest:
        path = Path(entry.get("path", ""))
        want = int(entry.get("size") or 0)
        have = path.stat().st_size if path.exists() else 0
        if path.exists() and (want == 0 or have == want):
            ok.append(path.name)
        else:
            missing.append({"file": path.name, "path": str(path), "want": want, "have": have})
    return {"manifest": True, "missing": missing, "ok": ok}


def repair_logs():
    return logmod.recover_logs()


def requeue_jobs(executor=None):
    interrupted = state.interrupted_jobs()
    executed = []
    if executor and interrupted:
        for job in interrupted:
            try:
                result = executor(job["kind"], job["args"])
                state.update_job(job["id"], "done", result=str(result))
                executed.append({"id": job["id"], "kind": job["kind"], "result": str(result)[:200]})
            except Exception as exc:  # noqa: BLE001
                state.update_job(job["id"], "failed", error=str(exc))
                executed.append({"id": job["id"], "kind": job["kind"], "error": str(exc)})
    return {"interrupted": interrupted, "executed": executed}


def run(executor=None, min_free_gb=None):
    state.ensure_dirs()
    state.init_db()
    marker = state.get_marker()
    unclean = marker is not None

    models = verify_models()
    logs = repair_logs()
    interrupted = state.mark_interrupted()
    # Never auto-run heavy media jobs unless explicitly enabled - an interrupted
    # video would otherwise restart for an hour at boot.
    jobs = requeue_jobs(executor if RECOVER_REQUEUE else None)
    temp = storage.clean_cache()
    spaces = storage.retention(min_free_gb=min_free_gb)
    try:
        import chats
        stale_chats = chats.reset_stale_generating()
    except Exception:  # noqa: BLE001
        stale_chats = []

    state.clear_marker()

    report = {
        "ts": time.time(),
        "unclean_exit": unclean,
        "previous_token": (marker or {}).get("token"),
        "models_missing": models.get("missing", []),
        "models_ok": len(models.get("ok", [])),
        "logs_repaired": logs,
        "jobs_interrupted": interrupted,
        "jobs_requeued": jobs.get("executed", []),
        "stale_chats_reset": stale_chats,
        "temp_removed": len(temp),
        "free_gb": storage.free_gb(),
    }
    REPORT.clear()
    REPORT.update(report)
    return report


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
