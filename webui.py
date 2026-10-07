"""aiX web UI - Flask server + SSE streaming chat.

Single-user local app. Provides the chat, uploads (+/drag-drop), the output
library with download/export/pin/delete, retention, recovery and process
control.
"""

import json
import os
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

import logger as logmod
import chats
import procman
import recover
import state
import storage
import tools as toolkit
from agent import Agent
from config import (
    COMFYUI_URL,
    EDGE,
    INPUTS_DIR,
    MODEL,
    OLLAMA_BASE,
    OUTPUT_DIR,
    SESSION_DIR,
    WEB_DIR,
    WEB_HOST,
    WEB_PORT,
)
from ollama_client import OllamaClient

app = Flask(__name__, static_folder=None)

# One generation at a time (12 GB GPU). The RUN dict is the shared, resumable
# state of the current turn so any client can (re)attach to the stream.
_RUN = {
    "chat_id": None,
    "running": False,
    "done": True,
    "events": [],
    "cancel": threading.Event(),
    "updated": threading.Event(),
}
_STATE = {"agent": None, "session": None, "recovery": None}


def _confirm(name, args):
    if name not in toolkit.DANGEROUS:
        return True
    return os.environ.get("AIX_WEB_ALLOW_DANGEROUS", "0").lower() in ("1", "true", "yes")


def _build():
    client = OllamaClient()
    model = MODEL
    native = client.supports_tools(model) if client.is_up() else False
    session = logmod.SessionLogger(model=model, meta={"interface": "web"})
    logmod.install(session)
    agent = Agent(client, model, native_tools=native, stream=True,
                  session_logger=session, confirm=_confirm,
                  cancel_check=_RUN["cancel"].is_set)
    _STATE["session"] = session
    _STATE["agent"] = agent
    _STATE["client"] = client
    return agent, session


def _requeue_executor(kind, args):
    return toolkit.execute(kind, args)


def _background_recovery():
    # Runs first; the marker is (re)set afterwards so this startup isn't
    # itself mistaken for an unclean exit.
    report = None
    try:
        report = recover.run(executor=_requeue_executor)
        _STATE["recovery"] = report
    except Exception as exc:  # noqa: BLE001
        _STATE["recovery"] = {"error": str(exc)}
    finally:
        if _STATE["session"]:
            state.set_marker(_STATE["session"].id)


# --- static / page ---------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(str(WEB_DIR), "index.html")


@app.route("/web/<path:path>")
def web_assets(path):
    return send_from_directory(str(WEB_DIR), path)


# --- status / control ------------------------------------------------------
def _model_loaded():
    try:
        import requests
        r = requests.get(OLLAMA_BASE.rstrip("/") + "/api/ps", timeout=3)
        base = MODEL.split(":")[0]
        return any(m.get("name", "").split(":")[0] == base for m in r.json().get("models", []))
    except Exception:  # noqa: BLE001
        return False


@app.route("/api/status")
def api_status():
    st = procman.status()
    agent = _STATE["agent"]
    st["model"] = agent.model if agent else MODEL
    st["busy"] = _RUN["running"]
    st["running_chat"] = _RUN["chat_id"] if _RUN["running"] else None
    st["model_loaded"] = _model_loaded()
    st["outputs"] = len(storage.list_outputs())
    st["attachments"] = len([p for p in INPUTS_DIR.iterdir() if p.is_file()]) if INPUTS_DIR.exists() else 0
    st["recovery"] = _STATE["recovery"]
    return jsonify(st)


@app.route("/api/recovery")
def api_recovery():
    return jsonify(_STATE["recovery"] or {})


@app.route("/api/control/<action>", methods=["POST"])
def api_control(action):
    if action == "start":
        return jsonify(procman.start())
    if action == "stop":
        return jsonify(procman.stop())
    if action == "restart":
        procman.stop()
        time.sleep(2)
        return jsonify(procman.start())
    if action == "recover":
        report = recover.run(executor=_requeue_executor)
        _STATE["recovery"] = report
        return jsonify(report)
    return jsonify({"error": f"unknown action {action}"}), 400


@app.route("/api/clean", methods=["POST"])
def api_clean():
    return jsonify(storage.retention())


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    result = procman.stop()
    time.sleep(0.5)
    threading.Thread(target=lambda: (time.sleep(1), os._exit(0)), daemon=True).start()
    return jsonify({"ok": True, **result})


# --- chat (start turn + resumable stream) ----------------------------------
@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("message") or "").strip()
    if not text:
        return jsonify({"error": "empty message"}), 400
    if _RUN["running"]:
        return jsonify({"error": "busy", "chat_id": _RUN["chat_id"]}), 409

    chat_id = data.get("chat_id")
    chat = chats.get_chat(chat_id) if chat_id else None
    if chat is None:
        chat = chats.create_chat()
        chat_id = chat["id"]

    messages = list(chat.get("messages", []))
    # Persist the user message immediately so switching chats / reloading never
    # loses it while the turn runs.
    chats.save_chat(chat_id, messages + [{"role": "user", "content": text}], status="generating")

    agent = _STATE["agent"]
    agent.reset()
    agent.messages = messages
    # Startup is async now, so re-detect tool support at turn time.
    try:
        client = _STATE.get("client")
        if client and client.is_up():
            agent.native_tools = client.supports_tools(agent.model)
    except Exception:  # noqa: BLE001
        pass
    _RUN.update(chat_id=chat_id, running=True, done=False, events=[])
    _RUN["cancel"].clear()
    _RUN["updated"].clear()
    events = _RUN["events"]

    def emit(kind, **payload):
        events.append({"kind": kind, **payload})
        _RUN["updated"].set()

    emit("chat", chat_id=chat_id)

    def worker():
        agent.emit = emit
        try:
            agent.run_turn(text)
        except Exception as exc:  # noqa: BLE001
            emit("error", message=str(exc))
        finally:
            try:
                chats.save_chat(chat_id, agent.messages, status="idle")
            except Exception:  # noqa: BLE001
                pass
            _RUN["running"] = False
            _RUN["done"] = True
            _RUN["updated"].set()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"chat_id": chat_id, "running": True})


def _sse(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.route("/api/chat/stream/<cid>")
def api_chat_stream(cid):
    """Replay the current turn's buffered events for <cid>, then tail it."""
    def gen():
        idx = 0
        while True:
            if _RUN["chat_id"] != cid:
                break
            events = _RUN["events"]
            while idx < len(events):
                yield _sse(events[idx])
                idx += 1
            if _RUN["done"] and idx >= len(events):
                break
            _RUN["updated"].clear()
            _RUN["updated"].wait(timeout=1.0)
        yield _sse({"kind": "done", "chat_id": cid})

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/chat/state/<cid>")
def api_chat_state(cid):
    running = _RUN["running"] and _RUN["chat_id"] == cid
    return jsonify({
        "chat_id": cid,
        "running": running,
        "events": len(_RUN["events"]) if running else 0,
    })


# --- chats -----------------------------------------------------------------
@app.route("/api/chats")
def api_chats():
    return jsonify({"chats": chats.list_chats()})


@app.route("/api/chats", methods=["POST"])
def api_chats_new():
    body = request.get_json(silent=True) or {}
    return jsonify(chats.create_chat(body.get("title") or "New chat"))


@app.route("/api/chats/<cid>")
def api_chat_get(cid):
    chat = chats.get_chat(cid)
    if not chat:
        return jsonify({"error": "not found"}), 404
    return jsonify(chat)


@app.route("/api/chats/<cid>/rename", methods=["POST"])
def api_chat_rename(cid):
    title = (request.get_json(silent=True) or {}).get("title", "").strip()
    return jsonify({"ok": chats.rename_chat(cid, title) if title else False})


@app.route("/api/chats/<cid>", methods=["DELETE"])
def api_chat_delete(cid):
    return jsonify({"ok": chats.delete_chat(cid)})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    _RUN["cancel"].set()
    try:
        import requests
        import comfyui_backend
        comfyui_backend.request_cancel()
        base = COMFYUI_URL.rstrip("/")
        requests.post(base + "/interrupt", timeout=5)
        requests.post(base + "/queue", json={"clear": True}, timeout=5)
    except Exception:  # noqa: BLE001
        pass
    return jsonify({"ok": True})


# --- uploads / inputs ------------------------------------------------------
@app.route("/api/upload", methods=["POST"])
def api_upload():
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in request.files.getlist("file"):
        name = Path(f.filename).name
        if not name:
            continue
        dest = INPUTS_DIR / name
        f.save(str(dest))
        saved.append(name)
    return jsonify({"saved": saved, "attachments": _attachments()})


@app.route("/api/attachments")
def api_attachments():
    return jsonify({"attachments": _attachments()})


@app.route("/api/attachments/<path:name>", methods=["DELETE"])
def api_attachment_delete(name):
    target = INPUTS_DIR / Path(name).name
    try:
        target.unlink()
    except OSError:
        pass
    return jsonify({"attachments": _attachments()})


def _attachments():
    if not INPUTS_DIR.exists():
        return []
    out = []
    for p in sorted(INPUTS_DIR.iterdir()):
        if p.is_file():
            out.append({"name": p.name, "size": p.stat().st_size, "kind": storage.kind_for(p)})
    return out


# --- outputs / library -----------------------------------------------------
@app.route("/api/outputs")
def api_outputs():
    return jsonify({"outputs": storage.list_outputs()})


@app.route("/api/file/<oid>")
def api_file(oid):
    item = storage.get(oid)
    if not item or not Path(item["path"]).exists():
        return "not found", 404
    return send_file(item["path"])


@app.route("/api/download/<oid>")
def api_download(oid):
    item = storage.get(oid)
    if not item or not Path(item["path"]).exists():
        return "not found", 404
    return send_file(item["path"], as_attachment=True, download_name=item["name"])


@app.route("/api/pin/<oid>", methods=["POST"])
def api_pin(oid):
    value = (request.get_json(silent=True) or {}).get("value", True)
    storage.pin(oid, value)
    return jsonify({"ok": True})


@app.route("/api/delete/<oid>", methods=["POST"])
def api_delete(oid):
    return jsonify({"ok": storage.delete(oid)})


@app.route("/api/export/<oid>", methods=["POST"])
def api_export(oid):
    move = bool((request.get_json(silent=True) or {}).get("move", False))
    dest = storage.export(oid, move=move)
    return jsonify({"ok": bool(dest), "dest": dest})


def _open_window(url):
    try:
        if Path(EDGE).exists():
            import subprocess
            subprocess.Popen([EDGE, f"--app={url}"], creationflags=0x00000008)
            return
    except Exception:
        pass
    webbrowser.open(url)


def main():
    for d in (INPUTS_DIR, OUTPUT_DIR, SESSION_DIR):
        d.mkdir(parents=True, exist_ok=True)
    _build()
    threading.Thread(target=_background_recovery, daemon=True).start()
    url = f"http://{WEB_HOST}:{WEB_PORT}/"
    if "--no-window" not in os.sys.argv:
        threading.Timer(1.5, lambda: _open_window(url)).start()
    print(f"aiX web UI -> {url}")
    app.run(host=WEB_HOST, port=WEB_PORT, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
