# aiX logs

Everything the system does is logged here so sessions can be replayed and mined
for future development. All files are append-only; a hard power cut can only
ever lose the final partial line, which `recover.py` / `logger.read_session_tolerant`
drops.

## logs/sessions/<id>.jsonl
One JSON object per line. Common fields: `ts` (epoch), `iso`, `kind`.

| kind | extra fields |
|---|---|
| `session_start` | `model`, `meta` |
| `user` | `text` |
| `delta` | `text` (streamed token chunk) |
| `assistant` | `text` (final message) |
| `tool_call` | `name`, `args` |
| `tool_result` | `name`, `result` (truncated to 2000 chars) |
| `error` | `where`, `message` |
| `session_end` | - |

## logs/sessions/<id>.log
Full console transcript (stdout/stderr tee) for the session.

## Other logs
- `logs/ollama.log` - reserved for the Ollama server.
- `logs/download.log` - model fetch / verify output (from D:\Programming\AI\logs).

## Durable state
- `run/state.db` (SQLite, WAL): the job ledger (`jobs`: id, kind, args, status,
  result, error, created, updated). Jobs left `running` after a power cut are
  marked `interrupted` and requeued on recovery.
- `run/processes.json`: PIDs we started (for Stop/Close).
- `run/.running`: run marker; presence from a previous boot means an unclean exit.
- `D:\Programming\AI\models_manifest.json`: model file sizes/hashes for verification.

## Reading a session (tolerant)
```python
import logger
for ev in logger.read_session_tolerant("logs/sessions/20261006-123456-abcd.jsonl"):
    print(ev["kind"], ev.get("text") or ev.get("name") or "")
```
