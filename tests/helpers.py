"""Test doubles: scripted model providers and a fake Ollama HTTP server."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from yousef.models.base import Provider, ProviderError


class FakeProvider(Provider):
    def __init__(self, name, responder=None, models=("fake-model",), is_local=False, fail=False):
        self.name = name
        self.label = name
        self.is_local = is_local
        self.responder = responder or (lambda messages, json_mode: f"answer from {name}")
        self.models = list(models)
        self.fail = fail
        self.calls = []

    def chat(self, model, messages, *, json_mode=False):
        self.calls.append({"model": model, "messages": messages, "json_mode": json_mode})
        if self.fail:
            raise ProviderError(f"{self.name} is down")
        return self.responder(messages, json_mode)

    def list_models(self):
        if self.fail:
            raise ProviderError("unreachable")
        return list(self.models)

    def chat_models(self):
        return self.list_models()


def factory_from(providers: dict):
    def factory(name, settings, keystore):
        return providers.get(name)
    return factory


def plan_json(*steps):
    return json.dumps({"steps": [
        {"agent": a, "instruction": i, "needs_confirmation": c} for a, i, c in steps
    ]})


class XorCipher:
    """Reversible stand-in for DPAPI so tests can check keys are not stored in clear text."""
    name = "xor-test"
    secure = True

    def encrypt(self, data):
        return bytes(b ^ 0x5A for b in data)

    def decrypt(self, data):
        return bytes(b ^ 0x5A for b in data)


def wait_for(predicate, timeout=10.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


class FakeOllama:
    """Minimal Ollama API: /api/tags and /api/chat."""

    def __init__(self, models=("qwen2.5:7b", "nomic-embed-text:latest"), planner_reply=None):
        self.models = list(models)
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send({"models": [{"name": m} for m in outer.models]})
                else:
                    self._send({"error": "not found"}, 404)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length))
                outer.requests.append(body)
                if body.get("model") not in outer.models:
                    return self._send({"error": f"model '{body.get('model')}' not found"}, 404)
                if body.get("format") == "json":
                    content = planner_reply or plan_json(("general", body["messages"][-1]["content"][:40], False))
                else:
                    content = "<think>hidden</think>مرحبًا، هذه إجابة من النموذج المحلي."
                self._send({"message": {"role": "assistant", "content": content}, "done": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
