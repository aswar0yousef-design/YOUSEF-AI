"""Paths and non-secret settings.

Every Windows user gets their own data folder under %LOCALAPPDATA%\\YousefAI,
so two people using the same PC never see each other's tasks, memory or keys.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from pathlib import Path

APP_DIR_NAME = "YousefAI"
DEFAULT_PORT = 8765

TASK_TYPES = ("chat", "plan", "code", "analysis", "writing")
ROUTE_CHOICES = ("local", "cloud", "local_only")
MODES = ("auto", "local_only", "cloud_first")
CLOUD_PROVIDERS = ("anthropic", "openai", "gemini")
SERVICE_KEYS = ("github", "vercel")  # tokens for connectors, stored like API keys
KEY_NAMES = CLOUD_PROVIDERS + SERVICE_KEYS

DEFAULT_SETTINGS: dict = {
    "port": DEFAULT_PORT,
    # auto: follow per-task routing | local_only: never use cloud | cloud_first: cloud before local
    "mode": "auto",
    "ollama_url": "http://127.0.0.1:11434",
    # empty = pick the first installed chat model automatically
    "local_model": "",
    # context window for Ollama models (tokens); the coder's tool loop needs room
    "ollama_ctx": 8192,
    "cloud_models": {"anthropic": "", "openai": "", "gemini": ""},
    "cloud_order": ["anthropic", "openai", "gemini"],
    # local = local first, cloud as fallback; cloud = cloud first, local as fallback
    "routing": {
        "chat": "local",
        "plan": "local",
        "code": "cloud",
        "analysis": "cloud",
        "writing": "cloud",
    },
    # how many previous user/assistant turns are given to the agents as context
    "history_turns": 4,
    # project folders the coder may read and propose changes in: [{"name", "path"}]
    "workspaces": [],
    # connected online stores: [{"id", "name", "platform", "url", "api_version"}]; secrets live in keys.dat
    "stores": [],
    # Facebook Pages (with their Instagram accounts): [{"id", "name", "ig_id", "ig_username"}]; tokens live in keys.dat
    "social": [],
    # local speech-to-text (faster-whisper); spoken replies are set in the browser
    "voice": {
        "model": "large-v3-turbo",  # small and below misunderstand Arabic too often
        "device": "cpu",
        "language": "auto",
        "wake_words": ["يوسف", "يا يوسف", "Yousef", "Youssef", "Yusuf"],
        "preload": True,
    },
}

VOICE_MODELS = ("tiny", "base", "small", "medium", "large-v3-turbo")
VOICE_DEVICES = ("cpu", "cuda")
VOICE_LANGUAGES = ("auto", "ar", "en")


def data_dir() -> Path:
    override = os.environ.get("YOUSEF_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        path = Path(base) / APP_DIR_NAME
    else:
        path = Path.home() / ".yousef-ai"
    path.mkdir(parents=True, exist_ok=True)
    return path


class SettingsError(ValueError):
    pass


def _validate(merged: dict) -> dict:
    s = merged
    if not isinstance(s.get("port"), int) or not 1024 <= s["port"] <= 65535:
        raise SettingsError("port must be a number between 1024 and 65535")
    if s.get("mode") not in MODES:
        raise SettingsError(f"mode must be one of {MODES}")
    url = s.get("ollama_url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise SettingsError("ollama_url must start with http:// or https://")
    s["ollama_url"] = url.rstrip("/")
    if not isinstance(s.get("local_model"), str):
        raise SettingsError("local_model must be text")
    s["local_model"] = s["local_model"].strip()
    models = s.get("cloud_models")
    if not isinstance(models, dict):
        raise SettingsError("cloud_models must be an object")
    s["cloud_models"] = {p: str(models.get(p, "") or "").strip() for p in CLOUD_PROVIDERS}
    order = s.get("cloud_order")
    if (
        not isinstance(order, list)
        or not all(isinstance(p, str) for p in order)
        or len(order) != len(set(order))
        or not set(order) <= set(CLOUD_PROVIDERS)
    ):
        raise SettingsError(f"cloud_order must list providers from {CLOUD_PROVIDERS} without repeats")
    s["cloud_order"] = order + [p for p in CLOUD_PROVIDERS if p not in order]
    routing = s.get("routing")
    if not isinstance(routing, dict):
        raise SettingsError("routing must be an object")
    for t in TASK_TYPES:
        value = routing.get(t, DEFAULT_SETTINGS["routing"][t])
        if value not in ROUTE_CHOICES:
            raise SettingsError(f"routing.{t} must be one of {ROUTE_CHOICES}")
        routing[t] = value
    s["routing"] = {t: routing[t] for t in TASK_TYPES}
    turns = s.get("history_turns")
    if not isinstance(turns, int) or not 0 <= turns <= 20:
        raise SettingsError("history_turns must be between 0 and 20")
    s["voice"] = _validate_voice(s.get("voice"))
    ctx = s.get("ollama_ctx")
    if not isinstance(ctx, int) or not 2048 <= ctx <= 131072:
        raise SettingsError("ollama_ctx must be between 2048 and 131072")
    s["workspaces"] = _validate_workspaces(s.get("workspaces"))
    s["stores"] = _validate_stores(s.get("stores"))
    s["social"] = _validate_social(s.get("social"))
    return {k: s[k] for k in DEFAULT_SETTINGS}


STORE_PLATFORMS = ("shopify", "woocommerce")


def _validate_stores(items) -> list:
    if not isinstance(items, list) or len(items) > 20:
        raise SettingsError("stores must be a list of at most 20 stores")
    out, ids = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise SettingsError("each store needs an id, name, platform and url")
        sid = str(item.get("id") or "")
        name = str(item.get("name") or "").strip()
        platform = item.get("platform")
        url = str(item.get("url") or "").strip()
        version = str(item.get("api_version") or "").strip()
        if (len(sid) != 8 or any(c not in "0123456789abcdef" for c in sid) or sid in ids
                or not 1 <= len(name) <= 60 or platform not in STORE_PLATFORMS or not url or len(url) > 300
                or len(version) > 20):
            raise SettingsError("invalid store entry")
        ids.add(sid)
        out.append({"id": sid, "name": name, "platform": platform, "url": url, "api_version": version})
    return out


def _validate_social(items) -> list:
    if not isinstance(items, list) or len(items) > 20:
        raise SettingsError("social must be a list of at most 20 pages")
    out, ids = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise SettingsError("each social account needs an id and a name")
        pid = str(item.get("id") or "")
        name = str(item.get("name") or "").strip()
        ig_id = str(item.get("ig_id") or "")
        ig_user = str(item.get("ig_username") or "").strip()
        if (not pid.isdigit() or len(pid) > 30 or pid in ids or not 1 <= len(name) <= 100
                or (ig_id and (not ig_id.isdigit() or len(ig_id) > 30)) or len(ig_user) > 60):
            raise SettingsError("invalid social account entry")
        ids.add(pid)
        out.append({"id": pid, "name": name, "ig_id": ig_id, "ig_username": ig_user})
    return out


def _validate_workspaces(items) -> list:
    if not isinstance(items, list) or len(items) > 30:
        raise SettingsError("workspaces must be a list of at most 30 folders")
    out, names = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise SettingsError("each workspace needs a name and a path")
        name = str(item.get("name") or "").strip()
        path = str(item.get("path") or "").strip()
        if not 1 <= len(name) <= 60 or not path or not (os.path.isabs(path) or path.startswith("\\\\")):
            raise SettingsError("each workspace needs a name (1-60 letters) and an absolute path")
        if name.lower() in names:
            raise SettingsError(f"workspace name used twice: {name}")
        names.add(name.lower())
        entry = {"name": name, "path": path}
        commands = item.get("commands", [])
        if isinstance(commands, str):
            commands = [c for c in commands.splitlines()]
        commands = [" ".join(str(c).split()) for c in commands if str(c).strip()]
        if len(commands) > 15 or any(len(c) > 300 for c in commands):
            raise SettingsError("allowed commands: at most 15, each under 300 characters")
        entry["commands"] = list(dict.fromkeys(commands))
        for key in ("vercel_project", "vercel_team"):
            value = str(item.get(key) or "").strip()
            if len(value) > 100:
                raise SettingsError(f"{key} is too long")
            entry[key] = value
        out.append(entry)
    return out


def _validate_voice(voice) -> dict:
    if not isinstance(voice, dict):
        raise SettingsError("voice must be an object")
    v = {k: voice.get(k, DEFAULT_SETTINGS["voice"][k]) for k in DEFAULT_SETTINGS["voice"]}
    if v["model"] not in VOICE_MODELS:
        raise SettingsError(f"voice.model must be one of {VOICE_MODELS}")
    if v["device"] not in VOICE_DEVICES:
        raise SettingsError(f"voice.device must be one of {VOICE_DEVICES}")
    if v["language"] not in VOICE_LANGUAGES:
        raise SettingsError(f"voice.language must be one of {VOICE_LANGUAGES}")
    words = v["wake_words"]
    if isinstance(words, str):
        words = [w for w in (p.strip() for p in words.replace("،", ",").split(",")) if w]
    if (not isinstance(words, list) or not 1 <= len(words) <= 10
            or not all(isinstance(w, str) and 2 <= len(w.strip()) <= 30 for w in words)):
        raise SettingsError("voice.wake_words must list 1-10 words of 2-30 letters")
    v["wake_words"] = list(dict.fromkeys(w.strip() for w in words))
    if not isinstance(v["preload"], bool):
        raise SettingsError("voice.preload must be true or false")
    return v


class Settings:
    """JSON settings file with validation. Never holds API keys."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data = copy.deepcopy(DEFAULT_SETTINGS)
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                self._data = _validate(self._merge(self._data, stored))
            except (ValueError, OSError):
                # A broken file must never stop the assistant from starting.
                backup = path.with_suffix(".broken.json")
                try:
                    path.replace(backup)
                except OSError:
                    pass
                self._data = copy.deepcopy(DEFAULT_SETTINGS)
        self._save()

    @staticmethod
    def _merge(base: dict, patch: dict) -> dict:
        out = copy.deepcopy(base)
        for key, value in (patch or {}).items():
            if key not in DEFAULT_SETTINGS:
                continue
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                merged = dict(out[key])
                merged.update(value)
                out[key] = merged
            else:
                out[key] = value
        return out

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise SettingsError("settings update must be an object")
        with self._lock:
            new = _validate(self._merge(self._data, patch))
            self._data = new
            self._save()
            return copy.deepcopy(new)

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
