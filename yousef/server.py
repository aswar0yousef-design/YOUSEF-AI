"""Local HTTP server for the dashboard and its JSON API.

Listens on 127.0.0.1 only. Protections against other websites open in the same
browser talking to it:
- Host header must be this machine (blocks DNS-rebinding),
- every state-changing request must carry X-Yousef-Client and JSON content,
  which a foreign page cannot send without a CORS preflight we never approve,
- a foreign Origin header is rejected.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import socketserver
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import CLOUD_PROVIDERS, SettingsError
from .core import AGENTS
from .keystore import KeyStoreError
from .models import ProviderError

log = logging.getLogger("yousef.server")

WEB_DIR = Path(__file__).parent / "web"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
MAX_BODY = 1_000_000


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR would let a second copy bind the same port.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # Skip HTTPServer.server_bind's getfqdn() lookup, which can stall on Windows.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def make_server(app, port: int, host: str = "127.0.0.1") -> Server:
    api = Api(app)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"YousefAI/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet console; errors are logged elsewhere
            log.debug("%s - %s", self.address_string(), fmt % args)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

        # ---------------------------------------------------------------
        def _handle(self, method: str):
            try:
                self._check_host()
                parsed = urlparse(self.path)
                if method == "GET" and parsed.path in STATIC_FILES:
                    return self._static(parsed.path)
                if not parsed.path.startswith("/api/"):
                    raise ApiError(404, "not found")
                if method != "GET":
                    self._check_write_request()
                body = self._read_json() if method in ("POST", "PUT") else {}
                status, payload = api.dispatch(method, parsed.path, parse_qs(parsed.query), body)
                self._json(status, payload)
            except ApiError as e:
                self._fail(e.status, e.message)
            except Exception:
                log.exception("unhandled error on %s %s", method, self.path)
                self._fail(500, "خطأ داخلي في الخادم، راجع ملف السجل.")

        def _fail(self, status: int, message: str):
            # The request body may be unread; close the connection so it is
            # never parsed as the next request.
            self.close_connection = True
            body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8",
                       {"Cache-Control": "no-store", "Connection": "close"})

        def _check_host(self):
            host = (self.headers.get("Host") or "").lower()
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed:
                raise ApiError(403, "forbidden host")

        def _check_write_request(self):
            if self.headers.get("X-Yousef-Client") != "1":
                raise ApiError(403, "missing client header")
            origin = self.headers.get("Origin")
            if origin:
                port = self.server.server_port
                if origin.lower() not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                    raise ApiError(403, "forbidden origin")

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, "request too large")
            if length == 0:
                return {}
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise ApiError(415, "expected application/json")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ApiError(400, "invalid JSON") from None
            if not isinstance(data, dict):
                raise ApiError(400, "expected a JSON object")
            return data

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

        def _static(self, path: str):
            name, ctype = STATIC_FILES[path]
            body = (WEB_DIR / name).read_bytes()
            self._send(200, body, ctype, {"Content-Security-Policy": CSP, "Cache-Control": "no-cache"})

    return Server((host, port), Handler)


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ApiError(400, "invalid id") from None


class Api:
    def __init__(self, app):
        self.app = app
        self.routes = [
            ("GET", r"/api/status", self.status),
            ("GET", r"/api/agents", self.agents),
            ("GET", r"/api/tasks", self.list_tasks),
            ("POST", r"/api/tasks", self.create_task),
            ("GET", r"/api/tasks/(\d+)", self.get_task),
            ("POST", r"/api/tasks/(\d+)/cancel", self.cancel_task),
            ("POST", r"/api/tasks/(\d+)/feedback", self.feedback),
            ("POST", r"/api/steps/(\d+)/decision", self.decide),
            ("POST", r"/api/conversation/new", self.new_conversation),
            ("GET", r"/api/settings", self.get_settings),
            ("PUT", r"/api/settings", self.put_settings),
            ("PUT", r"/api/keys/(\w+)", self.put_key),
            ("DELETE", r"/api/keys/(\w+)", self.delete_key),
            ("GET", r"/api/models/(\w+)", self.models),
            ("GET", r"/api/lessons", self.list_lessons),
            ("POST", r"/api/lessons", self.add_lesson),
            ("DELETE", r"/api/lessons/(\d+)", self.delete_lesson),
        ]

    def dispatch(self, method: str, path: str, query: dict, body: dict):
        path_matched = False
        for m, pattern, func in self.routes:
            match = re.fullmatch(pattern, path)
            if not match:
                continue
            path_matched = True
            if m == method:
                return func(*match.groups(), query=query, body=body)
        raise ApiError(405 if path_matched else 404, "method not allowed" if path_matched else "not found")

    # ---- status ----------------------------------------------------------------
    def status(self, query, body):
        s = self.app.settings.get()
        refresh = query.get("refresh", ["0"])[0] == "1"
        keys = self.app.keystore.status()
        cloud = {p: {"has_key": keys[p], "model": s["cloud_models"][p]} for p in CLOUD_PROVIDERS}
        return 200, {
            "version": __version__,
            "mode": s["mode"],
            "ollama": self.app.router.ollama_status(refresh=refresh),
            "cloud": cloud,
            "keys_encrypted": self.app.keystore.secure,
            "data_dir": str(self.app.data_dir),
        }

    def agents(self, query, body):
        return 200, {"agents": [a.public() for a in AGENTS.values()]}

    # ---- tasks -----------------------------------------------------------------
    def list_tasks(self, query, body):
        limit = _int(query.get("limit", ["30"])[0])
        return 200, {"tasks": self.app.memory.list_tasks(limit=limit)}

    def create_task(self, query, body):
        try:
            task_id = self.app.orchestrator.submit(str(body.get("request", "")))
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        return 201, {"task": self.app.memory.get_task(task_id)}

    def get_task(self, task_id, query, body):
        task = self.app.memory.get_task(_int(task_id))
        if not task:
            raise ApiError(404, "task not found")
        return 200, {"task": task}

    def cancel_task(self, task_id, query, body):
        if not self.app.orchestrator.cancel(_int(task_id)):
            raise ApiError(409, "المهمة ليست قيد التنفيذ")
        return 200, {"ok": True}

    def feedback(self, task_id, query, body):
        good = body.get("good")
        if not isinstance(good, bool):
            raise ApiError(400, "good must be true or false")
        if not self.app.orchestrator.feedback(_int(task_id), good, str(body.get("note", ""))):
            raise ApiError(409, "لا يمكن تقييم هذه المهمة الآن")
        return 200, {"ok": True}

    def decide(self, step_id, query, body):
        approve = body.get("approve")
        if not isinstance(approve, bool):
            raise ApiError(400, "approve must be true or false")
        if not self.app.orchestrator.decide(_int(step_id), approve):
            raise ApiError(409, "هذه الخطوة لا تنتظر موافقة")
        return 200, {"ok": True}

    def new_conversation(self, query, body):
        self.app.memory.new_conversation()
        return 200, {"ok": True}

    # ---- settings and keys -----------------------------------------------------
    def get_settings(self, query, body):
        return 200, {"settings": self.app.settings.get(), "keys": self.app.keystore.status()}

    def put_settings(self, query, body):
        try:
            settings = self.app.settings.update(body.get("settings", body))
        except SettingsError as e:
            raise ApiError(400, str(e)) from None
        return 200, {"settings": settings, "keys": self.app.keystore.status()}

    def put_key(self, provider, query, body):
        try:
            self.app.keystore.set(provider, str(body.get("key", "")))
        except KeyStoreError as e:
            raise ApiError(400, str(e)) from None
        return 200, {"keys": self.app.keystore.status()}

    def delete_key(self, provider, query, body):
        try:
            self.app.keystore.delete(provider)
        except KeyStoreError as e:
            raise ApiError(400, str(e)) from None
        return 200, {"keys": self.app.keystore.status()}

    def models(self, provider_name, query, body):
        if provider_name == "ollama":
            status = self.app.router.ollama_status(refresh=True)
            if not status["reachable"]:
                raise ApiError(502, f"تعذّر الوصول إلى Ollama: {status['error']}")
            return 200, {"models": status["models"]}
        if provider_name not in CLOUD_PROVIDERS:
            raise ApiError(404, "unknown provider")
        provider = self.app.router.provider(provider_name)
        if provider is None:
            raise ApiError(400, "أضف المفتاح أولًا")
        try:
            return 200, {"models": provider.list_models()}
        except ProviderError as e:
            raise ApiError(502, str(e)) from None

    # ---- lessons ---------------------------------------------------------------
    def list_lessons(self, query, body):
        return 200, {"lessons": self.app.memory.list_lessons()}

    def add_lesson(self, query, body):
        try:
            lesson_id = self.app.memory.add_lesson("rule", str(body.get("text", "")))
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        return 201, {"id": lesson_id, "lessons": self.app.memory.list_lessons()}

    def delete_lesson(self, lesson_id, query, body):
        if not self.app.memory.delete_lesson(_int(lesson_id)):
            raise ApiError(404, "lesson not found")
        return 200, {"lessons": self.app.memory.list_lessons()}
