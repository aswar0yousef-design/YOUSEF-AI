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
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .config import CLOUD_PROVIDERS, SettingsError
from .core import AGENTS
from .core.agents import NEEDS
from .keystore import KeyStoreError
from .media import MAX_IMAGE_BYTES, MediaError
from .models import ProviderError
from .voice import AudioError, VoiceNotInstalled, VoiceNotReady
from .integrations import StoreError
from .integrations.stores import sales_summary
from .workspace import WorkspaceError, registry

log = logging.getLogger("yousef.server")

WEB_DIR = Path(__file__).parent / "web"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/voice.js": ("voice.js", "text/javascript; charset=utf-8"),
    "/voice-worklet.js": ("voice-worklet.js", "text/javascript; charset=utf-8"),
}
CSP = ("default-src 'self'; img-src 'self' data: https:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
MAX_BODY = 1_000_000
MAX_AUDIO = 4_000_000  # about two minutes of 16 kHz mono audio
AUDIO_ROUTES = {"/api/voice/transcribe"}
IMAGE_ROUTE = "/api/media"
MEDIA_FILE = re.compile(r"/api/media/(img-[0-9a-f]{16}\.(?:jpg|png))")


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
                if method == "GET" and MEDIA_FILE.fullmatch(parsed.path):
                    return self._media(MEDIA_FILE.fullmatch(parsed.path).group(1))
                if method == "POST" and parsed.path in AUDIO_ROUTES:
                    body = {"audio": self._read_audio()}
                elif method == "POST" and parsed.path == IMAGE_ROUTE:
                    body = {"image": self._read_image(), "name": unquote(self.headers.get("X-File-Name") or "")}
                else:
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

        def _read_audio(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_AUDIO:
                raise ApiError(413, "المقطع الصوتي أطول من المسموح")
            if not (self.headers.get("Content-Type") or "").startswith("audio/wav"):
                raise ApiError(415, "expected audio/wav")
            if length == 0:
                raise ApiError(400, "empty audio")
            return self.rfile.read(length)

        def _read_image(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_IMAGE_BYTES:
                raise ApiError(413, "الصورة أكبر من 8MB")
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() not in ("image/jpeg", "image/png"):
                raise ApiError(415, "الصورة يجب أن تكون JPEG أو PNG")
            if length == 0:
                raise ApiError(400, "empty image")
            return self.rfile.read(length)

        def _media(self, name: str):
            try:
                data, ctype = api.app.media.read(name)
            except MediaError:
                raise ApiError(404, "not found") from None
            self._send(200, data, ctype, {"Cache-Control": "private, max-age=86400",
                                          "Content-Security-Policy": "default-src 'none'"})

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

    server = Server((host, port), Handler)

    def stop():
        server.shutdown()
        server.server_close()  # free the port for the copy that asked

    api.on_stop = stop
    return server


def _clean_context(raw) -> dict:
    """Editor context sent by the VS Code extension (or nothing)."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ApiError(400, "context must be an object")
    out = {}
    for key, limit in (("file", 500), ("language", 40), ("selection", 20000)):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ApiError(400, f"context.{key} must be text")
            if value.strip():
                out[key] = value[:limit]
    for key in ("start_line", "end_line"):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, int) or value < 0:
                raise ApiError(400, f"context.{key} must be a line number")
            out[key] = value
    return out


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ApiError(400, "invalid id") from None


class Api:
    def __init__(self, app):
        self.app = app
        self.on_stop = None  # set by make_server: shuts this server down
        self.routes = [
            ("GET", r"/api/status", self.status),
            ("POST", r"/api/system/stop", self.stop),
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
            ("GET", r"/api/workspaces", self.list_workspaces),
            ("POST", r"/api/workspaces", self.add_workspace),
            ("DELETE", r"/api/workspaces/(.+)", self.remove_workspace),
            ("PUT", r"/api/workspaces/(.+)", self.update_workspace),
            ("GET", r"/api/changes/(\d+)", self.get_change),
            ("GET", r"/api/stores", self.list_stores),
            ("POST", r"/api/stores", self.add_store),
            ("POST", r"/api/stores/([0-9a-f]{8})/test", self.test_store),
            ("GET", r"/api/stores/([0-9a-f]{8})/summary", self.store_summary),
            ("DELETE", r"/api/stores/([0-9a-f]{8})", self.remove_store),
            ("GET", r"/api/social/posts", self.list_posts),
            ("POST", r"/api/social/posts/(\d+)/cancel", self.cancel_post),
            ("POST", r"/api/social/posts/(\d+)/publish", self.publish_post),
            ("GET", r"/api/media", self.list_media),
            ("POST", r"/api/media", self.upload_media),
            ("DELETE", r"/api/media/(img-[0-9a-f]{16}\.(?:jpg|png))", self.delete_media),
            ("GET", r"/api/ads", self.list_ads),
            ("POST", r"/api/ads", self.connect_ads),
            ("DELETE", r"/api/ads/(act_\d{1,30})", self.remove_ads),
            ("GET", r"/api/social", self.list_social),
            ("POST", r"/api/social", self.connect_social),
            ("POST", r"/api/social/(\d{1,30})/test", self.test_social),
            ("GET", r"/api/social/(\d{1,30})/posts", self.social_posts),
            ("DELETE", r"/api/social/(\d{1,30})", self.remove_social),
            ("GET", r"/api/voice/status", self.voice_status),
            ("POST", r"/api/voice/load", self.voice_load),
            ("POST", r"/api/voice/transcribe", self.voice_transcribe),
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

    def stop(self, query, body):
        """A newer copy asks this one to step aside (start.bat after an update)."""
        if self.on_stop is None:
            raise ApiError(404, "not found")
        threading.Timer(0.3, self.on_stop).start()
        return 200, {"stopping": True, "version": __version__}

    def agents(self, query, body):
        return 200, {"agents": [dict(a.public(), needs=list(NEEDS.get(a.key, ()))) for a in AGENTS.values()]}

    # ---- tasks -----------------------------------------------------------------
    def list_tasks(self, query, body):
        limit = _int(query.get("limit", ["30"])[0])
        return 200, {"tasks": self.app.memory.list_tasks(limit=limit)}

    def create_task(self, query, body):
        source = body.get("source", "text")
        if source not in ("text", "voice", "vscode"):
            raise ApiError(400, "source must be text, voice or vscode")
        context = _clean_context(body.get("context"))
        workspace = body.get("workspace") or None
        if workspace is not None:
            entry = registry.find(self.app.settings, str(workspace))
            if entry is None:
                raise ApiError(403, "workspace_not_allowed")
            workspace = entry["name"]
        store = body.get("store") or None
        if store is not None:
            entry = self.app.stores.find(str(store))
            if entry is None:
                raise ApiError(404, "المتجر غير موجود")
            store = entry["id"]
        try:
            task_id = self.app.orchestrator.submit(str(body.get("request", "")), source, workspace, context, store)
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
        scope = body.get("scope", "all")
        if scope not in ("all", "files"):
            raise ApiError(400, "scope must be all or files")
        exclude = body.get("exclude") or []
        if not isinstance(exclude, list) or len(exclude) > 100 or not all(isinstance(i, int) for i in exclude):
            raise ApiError(400, "exclude must be a list of action ids")
        if not self.app.orchestrator.decide(_int(step_id), approve, scope, exclude):
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

    # ---- voice -----------------------------------------------------------------
    def voice_status(self, query, body):
        return 200, self.app.voice.status()

    def voice_load(self, query, body):
        try:
            return 200, self.app.voice.load()
        except VoiceNotInstalled:
            raise ApiError(501, "الصوت غير مثبّت. شغّل install-voice.bat مرة واحدة ثم أعد تشغيل يوسف.") from None

    def voice_transcribe(self, query, body):
        try:
            return 200, self.app.voice.transcribe(body["audio"])
        except AudioError as e:
            raise ApiError(400, f"ملف صوتي غير صالح: {e}") from None
        except VoiceNotInstalled:
            raise ApiError(501, "الصوت غير مثبّت. شغّل install-voice.bat مرة واحدة ثم أعد تشغيل يوسف.") from None
        except VoiceNotReady as e:
            raise ApiError(503, str(e)) from None

    # ---- project folders -------------------------------------------------------
    def list_workspaces(self, query, body):
        return 200, {"workspaces": registry.list_workspaces(self.app.settings)}

    def add_workspace(self, query, body):
        try:
            entry = registry.add(self.app.settings, str(body.get("path", "")), body.get("name") or None)
        except WorkspaceError as e:
            raise ApiError(400, str(e)) from None
        return 201, {"workspace": entry, "workspaces": registry.list_workspaces(self.app.settings)}

    def update_workspace(self, name, query, body):
        try:
            entry = registry.update(self.app.settings, unquote(name), body)
        except WorkspaceError as e:
            raise ApiError(404, str(e)) from None
        except SettingsError as e:
            raise ApiError(400, str(e)) from None
        return 200, {"workspace": entry, "workspaces": registry.list_workspaces(self.app.settings)}

    def remove_workspace(self, name, query, body):
        if not registry.remove(self.app.settings, unquote(name)):
            raise ApiError(404, "workspace not found")
        return 200, {"workspaces": registry.list_workspaces(self.app.settings)}

    def get_change(self, change_id, query, body):
        change = self.app.memory.get_change(_int(change_id))
        if not change:
            raise ApiError(404, "change not found")
        task = self.app.memory.get_task(change["task_id"])
        entry = registry.find(self.app.settings, task.get("workspace")) if task else None
        change["workspace"] = entry
        return 200, {"change": change}

    # ---- stores ------------------------------------------------------------------
    def list_stores(self, query, body):
        return 200, {"stores": self.app.stores.public()}

    def add_store(self, query, body):
        platform = body.get("platform")
        if platform == "woocommerce":
            key, sec = str(body.get("consumer_key", "")).strip(), str(body.get("consumer_secret", "")).strip()
            if not key or not sec:
                raise ApiError(400, "أدخل consumer key وconsumer secret")
            secret = f"{key}:{sec}"
        else:
            secret = str(body.get("token", "")).strip()
            if not secret:
                raise ApiError(400, "أدخل Admin API access token")
        try:
            entry, info = self.app.stores.add(str(body.get("name", "")), platform, str(body.get("url", "")), secret,
                                              str(body.get("api_version", "")))
        except (StoreError, KeyStoreError, SettingsError) as e:
            raise ApiError(400, str(e)) from None
        return 201, {"store": entry, "info": info, "stores": self.app.stores.public()}

    def test_store(self, store_id, query, body):
        try:
            return 200, {"info": self.app.stores.test(store_id)}
        except StoreError as e:
            raise ApiError(502, str(e)) from None

    def store_summary(self, store_id, query, body):
        entry = self.app.stores.find(store_id)
        if not entry:
            raise ApiError(404, "store not found")
        days = max(1, min(_int(query.get("days", ["30"])[0]), 60))
        try:
            orders = self.app.stores.client(entry).orders(days=days)
        except StoreError as e:
            raise ApiError(502, str(e)) from None
        return 200, {"store": entry["name"], "days": days, "summary": sales_summary(orders)}

    def remove_store(self, store_id, query, body):
        if not self.app.stores.remove(store_id):
            raise ApiError(404, "store not found")
        return 200, {"stores": self.app.stores.public()}

    # ---- social accounts ---------------------------------------------------------
    def list_social(self, query, body):
        return 200, {"accounts": self.app.social.public()}

    def connect_social(self, query, body):
        token = str(body.get("token", "")).strip()
        if not token:
            raise ApiError(400, "الصق رمز Meta أولًا")
        try:
            added = self.app.social.connect(token, ads=self.app.ads)
        except (ProviderError, KeyStoreError, SettingsError) as e:
            raise ApiError(400, str(e)) from None
        return 201, {"added": added, "accounts": self.app.social.public(), "ads": self.app.ads.public()}

    def list_ads(self, query, body):
        return 200, {"ads": self.app.ads.public()}

    def connect_ads(self, query, body):
        token = str(body.get("token", "")).strip()
        if not token:
            raise ApiError(400, "الصق رمز Meta أولًا")
        try:
            self.app.ads.connect(token)
        except (ProviderError, KeyStoreError, SettingsError) as e:
            raise ApiError(400, str(e)) from None
        return 201, {"ads": self.app.ads.public()}

    def remove_ads(self, account_id, query, body):
        if not self.app.ads.remove(account_id):
            raise ApiError(404, "ad account not found")
        return 200, {"ads": self.app.ads.public()}

    def test_social(self, page_id, query, body):
        try:
            return 200, {"info": self.app.social.test(page_id)}
        except ProviderError as e:
            raise ApiError(502, str(e)) from None

    def social_posts(self, page_id, query, body):
        entry = self.app.social.find(page_id)
        if not entry:
            raise ApiError(404, "account not found")
        try:
            page = self.app.social.page(entry)
            return 200, {"facebook": page.facebook_posts(5), "instagram": page.instagram_posts(5),
                         "scheduled": page.facebook_scheduled()}
        except ProviderError as e:
            raise ApiError(502, str(e)) from None

    def list_posts(self, query, body):
        wanted = tuple(s for s in (query.get("status", [""])[0] or "").split(",") if s) or None
        limit = max(1, min(_int(query.get("limit", ["50"])[0]), 200))
        return 200, {"posts": self.app.memory.list_social_posts(wanted, limit)}

    def cancel_post(self, post_id, query, body):
        try:
            note = self.app.publisher.cancel(_int(post_id))
        except ProviderError as e:
            raise ApiError(400, str(e)) from None
        return 200, {"note": note, "post": self.app.memory.get_social_post(_int(post_id))}

    def publish_post(self, post_id, query, body):
        try:
            post = self.app.publisher.publish_now(_int(post_id))
        except ProviderError as e:
            raise ApiError(400, str(e)) from None
        return 200, {"post": post}

    def list_media(self, query, body):
        return 200, {"files": self.app.media.list()}

    def upload_media(self, query, body):
        try:
            item = self.app.media.save(body["image"], body.get("name") or "")
        except MediaError as e:
            raise ApiError(400, str(e)) from None
        return 201, {"media": item, "files": self.app.media.list()}

    def delete_media(self, name, query, body):
        if not self.app.media.delete(name):
            raise ApiError(404, "not found")
        return 200, {"files": self.app.media.list()}

    def remove_social(self, page_id, query, body):
        if not self.app.social.remove(page_id):
            raise ApiError(404, "account not found")
        return 200, {"accounts": self.app.social.public()}
