# Logs

Session logs live here so past runs can be replayed later. Files are
append-only; a power cut can only lose the final partial line, which the reader
drops.

## logs/sessions/<id>.jsonl

One JSON object per line. Common fields: `ts` (epoch seconds), `iso`, `kind`.

| kind | extra fields |
|---|---|
| `session_start` | `model`, `meta` |
| `user` | `text` |
| `delta` | `text` (streamed token chunk) |
| `assistant` | `text` (final message) |
| `tool_call` | `name`, `args` |
| `tool_result` | `name`, `result` (truncated to 2000 chars) |
| `error` | `where`, `message` |
| `cancelled` | - |
| `session_end` | - |

## logs/sessions/<id>.log

Full console transcript (stdout/stderr) for the session.

## Durable state

- `run/state.db` (SQLite, WAL): the job ledger. A job left `running` after a
  power cut is marked `interrupted` on the next start.
- `run/processes.json`: PIDs started by `procman`, used by Stop/Close.
- `run/.running`: run marker; its presence from a previous boot means an unclean
  exit.
- `models_manifest.json`: model file sizes used to verify downloads.

## Reading a session

```python
import logger
for ev in logger.read_session_tolerant("logs/sessions/20260101-000000-abcd.jsonl"):
    print(ev["kind"], ev.get("text") or ev.get("name") or "")
```
