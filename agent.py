"""aiX agent core - UI agnostic.

The Agent streams tokens and tool events through an optional ``emit`` callback
so the CLI, the web UI and tests can all share the same loop. Every event is
also written to the durable session log.
"""

import argparse
import datetime as dt
import json
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import logger as logmod
import state
import tools as toolkit
from config import BASE_MODEL, CHAT_DIR, INPUTS_DIR, MODEL
from ollama_client import OllamaClient, OllamaError

BANNER = "aiX  -  local agent  (model: {model})"

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)

HELP = """commands:
  /help                    show this help
  /reset                   clear the conversation
  /attach <path>           copy a file into inputs/ for the agent to use
  /inputs                  list attached input files
  /system [text]           show or set an extra system instruction
  /model [name]            show or switch the model
  /tools                   list tools and their state
  /tools <name> on|off     toggle a tool
  /save [name]             save the conversation to chats/
  /exit                    quit"""


def _normalize(name):
    return name[:-7] if name.endswith(":latest") else name


class Agent:
    def __init__(self, client, model, native_tools=True, stream=True,
                 emit=None, session_logger=None, confirm=None, cancel_check=None):
        self.client = client
        self.model = model
        self.stream = stream
        self.native_tools = native_tools
        self.enabled = set(toolkit.REGISTRY)
        self.system_extra = ""
        self.messages = []
        self.emit = emit or (lambda *a, **k: None)
        self.log = session_logger
        self.confirm = confirm or (lambda name, args: False)
        self.cancel_check = cancel_check

    # --- logging helpers ---------------------------------------------------
    def _event(self, kind, **data):
        if self.log:
            self.log.event(kind, **data)
        self.emit(kind, **data)

    def reset(self):
        self.messages = []

    def active_specs(self):
        return [s for s in toolkit.TOOL_SPECS if s["function"]["name"] in self.enabled]

    def _inputs_note(self):
        try:
            INPUTS_DIR.mkdir(parents=True, exist_ok=True)
            files = [p for p in INPUTS_DIR.iterdir() if p.is_file()]
        except OSError:
            files = []
        if not files:
            return ""
        listing = ", ".join(f"{p.name}" for p in files)
        return (f"User input files available in inputs/: {listing}. "
                "Use describe_input to inspect them, animate_image to turn a photo "
                "into a video, or edit_image to modify an image.")

    def _system_prompt(self):
        parts = []
        if not self.native_tools and self.enabled:
            parts.append(toolkit.text_protocol_prompt())
        note = self._inputs_note()
        if note:
            parts.append(note)
        if self.system_extra:
            parts.append(self.system_extra)
        return "\n\n".join(parts)

    def _build_messages(self):
        msgs = []
        system = self._system_prompt()
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(self.messages)
        return msgs

    # --- turn --------------------------------------------------------------
    def run_turn(self, user_text):
        self._event("user", text=user_text)
        self.messages.append({"role": "user", "content": user_text})
        while True:
            if self.cancel_check and self.cancel_check():
                self._event("cancelled")
                return "[cancelled]"
            try:
                content, tool_calls = self._complete()
            except OllamaError as exc:
                self._event("error", where="chat", message=str(exc))
                self.messages.pop()
                return f"[error] {exc}"

            if self.cancel_check and self.cancel_check():
                self.messages.append({"role": "assistant", "content": content})
                self._event("cancelled")
                return content or "[cancelled]"

            if tool_calls:
                self._apply_native_tool_calls(tool_calls, content)
                continue

            if not self.native_tools and self.enabled:
                parsed, cleaned = self._extract_text_tool_calls(content)
                if parsed:
                    self.messages.append({"role": "assistant", "content": content})
                    for name, args in parsed:
                        result = self._invoke(name, args)
                        self.messages.append({
                            "role": "user",
                            "content": f"<tool_result name={name!r}>\n{result}\n</tool_result>",
                        })
                    continue

            self.messages.append({"role": "assistant", "content": content})
            self._event("assistant", text=content)
            return content

    def _complete(self):
        specs = self.active_specs() if self.native_tools else None
        content_parts = []
        tool_calls = []
        result = self.client.chat(self.model, self._build_messages(), tools=specs, stream=self.stream)

        if self.stream:
            for chunk in result:
                if self.cancel_check and self.cancel_check():
                    break
                msg = chunk.get("message", {}) or {}
                piece = msg.get("content") or ""
                if piece:
                    sys.stdout.write(piece)
                    sys.stdout.flush()
                    content_parts.append(piece)
                    self.emit("delta", text=piece)
                if msg.get("tool_calls"):
                    tool_calls.extend(msg["tool_calls"])
                if chunk.get("done"):
                    break
            sys.stdout.write("\n")
        else:
            msg = result.get("message", {}) or {}
            content = msg.get("content") or ""
            content_parts.append(content)
            tool_calls.extend(msg.get("tool_calls") or [])
            sys.stdout.write(content + "\n")

        return "".join(content_parts).strip(), tool_calls

    def _apply_native_tool_calls(self, tool_calls, content):
        self.messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call.get("function", {}) or {}
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            result = self._invoke(name, args)
            self.messages.append({"role": "tool", "content": result, "tool_name": name})

    def _extract_text_tool_calls(self, content):
        parsed = []
        for match in TOOL_CALL_RE.finditer(content):
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            name = data.get("name")
            if name:
                parsed.append((name, data.get("arguments", {})))
        return parsed, TOOL_CALL_RE.sub("", content).strip()

    def _invoke(self, name, args):
        self._event("tool_call", name=name, args=args)
        if name not in self.enabled:
            result = f"tool {name!r} is disabled"
            self._event("tool_result", name=name, result=result)
            return result
        if name in toolkit.DANGEROUS and not self.confirm(name, args):
            result = f"user denied permission to run {name}"
            self._event("tool_result", name=name, result=result)
            return result
        try:
            result = toolkit.execute(name, args)
        except Exception as exc:  # noqa: BLE001
            result = f"{name} error: {exc}"
        self._event("tool_result", name=name, result=str(result)[:2000])
        return result


def _short_json(obj, limit=300):
    try:
        text = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(obj)
    return text if len(text) <= limit else text[:limit] + "..."


def make_confirm():
    def confirm(name, args):
        try:
            answer = input(f"\n[!] allow {name} {_short_json(args)}? [y/N] ").strip().lower()
        except EOFError:
            return False
        return answer in ("y", "yes")

    return confirm


def _attach(path):
    import shutil
    from pathlib import Path

    src = Path(path)
    if not src.exists():
        return f"(not found: {path})"
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = INPUTS_DIR / src.name
    try:
        shutil.copy2(str(src), str(dest))
    except OSError as exc:
        return f"(copy failed: {exc})"
    return f"(attached {src.name} -> {dest})"


def handle_command(agent, line):
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit"):
        return True
    if cmd == "/help":
        print(HELP)
    elif cmd == "/reset":
        agent.reset()
        print("(history cleared)")
    elif cmd == "/attach":
        print(_attach(arg) if arg else "usage: /attach <path>")
    elif cmd == "/inputs":
        print(toolkit.list_inputs())
    elif cmd == "/system":
        if arg:
            agent.system_extra = arg
            print("(system instruction updated)")
        else:
            print(agent.system_extra or "(none)")
    elif cmd == "/model":
        if arg:
            agent.model = arg
            print(f"(model -> {arg})")
        else:
            print(agent.model)
    elif cmd == "/tools":
        sub = arg.split()
        if not sub:
            for spec in toolkit.TOOL_SPECS:
                name = spec["function"]["name"]
                state_ = "on" if name in agent.enabled else "off"
                print(f"  {name:<14} {state_:<3}  {spec['function']['description']}")
        elif len(sub) == 2 and sub[1] in ("on", "off"):
            if sub[0] in toolkit.REGISTRY:
                agent.enabled.add(sub[0]) if sub[1] == "on" else agent.enabled.discard(sub[0])
                print(f"({sub[0]} -> {sub[1]})")
            else:
                print(f"(unknown tool: {sub[0]})")
        else:
            print("usage: /tools [name on|off]")
    elif cmd == "/save":
        CHAT_DIR.mkdir(parents=True, exist_ok=True)
        name = arg or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path = CHAT_DIR / f"{name}.json"
        path.write_text(json.dumps(agent.messages, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"(saved {len(agent.messages)} messages to {path})")
    else:
        print(f"(unknown command: {cmd})")
    return False


def build_agent(args):
    client = OllamaClient()
    if not client.is_up():
        print(f"[error] Ollama not reachable at {client.base_url}. Run aix.bat first.")
        return None, None, 2

    model = args.model
    try:
        available = client.list_models()
    except OllamaError as exc:
        print(f"[error] {exc}")
        return None, None, 1

    known = {_normalize(n) for n in available}
    if _normalize(model) not in known:
        if _normalize(args.base_model) in known:
            print(f"[warn] model {model!r} not found; using {args.base_model!r}.")
            model = args.base_model
        else:
            print(f"[error] model {model!r} not found. Available: {', '.join(available) or 'none'}")
            return None, None, 1

    if args.no_tools or args.text_tools:
        native = False
    else:
        native = client.supports_tools(model)
        if not native:
            print("[info] native tool calling unavailable; using text tool protocol")

    agent = Agent(client, model, native_tools=native, stream=not args.no_stream)
    if args.no_tools:
        agent.enabled = set()
    return agent, client, 0


def main():
    parser = argparse.ArgumentParser(description="aiX local chat agent")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-model", default=BASE_MODEL)
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--text-tools", action="store_true")
    parser.add_argument("--once", metavar="PROMPT")
    parser.add_argument("--session")
    args = parser.parse_args()

    session = logmod.SessionLogger(session_id=args.session, model=args.model)
    logmod.install(session)

    agent, client, code = build_agent(args)
    if agent is None:
        session.close()
        return code
    agent.log = session
    agent.confirm = make_confirm()

    if args.once:
        agent.run_turn(args.once)
        session.close()
        return 0

    print(BANNER.format(model=agent.model))
    print("type /help for commands\n")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            if handle_command(agent, line):
                break
            continue
        agent.run_turn(line)
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
