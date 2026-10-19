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

DEFAULT_SETTINGS: dict = {
    "port": DEFAULT_PORT,
    # auto: follow per-task routing | local_only: never use cloud | cloud_first: cloud before local
    "mode": "auto",
    "ollama_url": "http://127.0.0.1:11434",
    # empty = pick the first installed chat model automatically
    "local_model": "",
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
}


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
    return {k: s[k] for k in DEFAULT_SETTINGS}


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
