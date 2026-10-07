"""Minimal Ollama HTTP client: streaming chat + optional native tool calling."""

import json

import requests

from config import OLLAMA_BASE, REQUEST_TIMEOUT


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, base_url=None, timeout=None):
        self.base_url = (base_url or OLLAMA_BASE).rstrip("/")
        self.timeout = timeout or REQUEST_TIMEOUT

    def _url(self, path):
        return f"{self.base_url}{path}"

    def is_up(self, timeout=5):
        try:
            return requests.get(self._url("/api/tags"), timeout=timeout).ok
        except requests.RequestException:
            return False

    def list_models(self):
        try:
            r = requests.get(self._url("/api/tags"), timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaError(f"cannot list models: {exc}") from exc
        return [m.get("name", "?") for m in r.json().get("models", [])]

    def show(self, model):
        try:
            r = requests.post(self._url("/api/show"), json={"model": model}, timeout=60)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise OllamaError(f"cannot show model {model!r}: {exc}") from exc
        return r.json()

    def supports_tools(self, model):
        try:
            info = self.show(model)
        except OllamaError:
            return False
        caps = info.get("capabilities")
        return bool(caps) and "tools" in caps

    def chat(self, model, messages, tools=None, stream=True, options=None):
        """Return either a dict (stream=False) or a generator of NDJSON dicts."""
        payload = {"model": model, "messages": messages, "stream": stream}
        if options:
            payload["options"] = options
        if tools:
            payload["tools"] = tools

        if not stream:
            try:
                r = requests.post(
                    self._url("/api/chat"), json=payload, timeout=self.timeout
                )
                r.raise_for_status()
            except requests.RequestException as exc:
                raise OllamaError(str(exc)) from exc
            return r.json()

        def _gen():
            try:
                with requests.post(
                    self._url("/api/chat"),
                    json=payload,
                    stream=True,
                    timeout=self.timeout,
                ) as r:
                    if r.status_code >= 400:
                        raise OllamaError(f"HTTP {r.status_code}: {r.text[:300]}")
                    for raw in r.iter_lines():
                        if not raw:
                            continue
                        try:
                            yield json.loads(raw)
                        except json.JSONDecodeError:
                            continue
            except requests.RequestException as exc:
                raise OllamaError(str(exc)) from exc

        return _gen()
