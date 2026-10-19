"""Model router: picks which model answers each kind of task.

Order of preference comes from settings:
- mode "local_only": only Ollama, never the cloud (privacy mode)
- mode "cloud_first": cloud providers first, Ollama as fallback
- mode "auto": per task type, "local" (local first) or "cloud" (cloud first)
If a candidate fails, the next one is tried automatically.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from ..config import Settings
from ..keystore import KeyStore
from .base import Provider, ProviderError, clean_output
from .providers import CLOUD_CLASSES, OllamaProvider

log = logging.getLogger("yousef.router")


class NoModelError(RuntimeError):
    """No model could answer: none configured, or all of them failed."""


@dataclass
class Completion:
    text: str
    provider: str
    model: str


def default_factory(name: str, settings: dict, keystore: KeyStore) -> Provider | None:
    if name == "ollama":
        return OllamaProvider(settings["ollama_url"])
    cls = CLOUD_CLASSES.get(name)
    key = keystore.get(name) if cls else None
    return cls(key) if cls and key else None


class Router:
    LOCAL_CACHE_SECONDS = 20

    def __init__(self, settings: Settings, keystore: KeyStore, factory=default_factory):
        self.settings = settings
        self.keystore = keystore
        self.factory = factory
        self._lock = threading.Lock()
        self._local_cache: tuple[float, str, dict] | None = None

    def provider(self, name: str) -> Provider | None:
        return self.factory(name, self.settings.get(), self.keystore)

    # ---- local --------------------------------------------------------------
    def ollama_status(self, refresh: bool = False) -> dict:
        s = self.settings.get()
        with self._lock:
            cached = self._local_cache
            if (not refresh and cached and cached[1] == s["ollama_url"]
                    and time.time() - cached[0] < self.LOCAL_CACHE_SECONDS):
                status = dict(cached[2])
            else:
                status = self._probe_ollama(s)
                self._local_cache = (time.time(), s["ollama_url"], status)
        chosen = s["local_model"]
        models = status.get("models", [])
        status["active_model"] = chosen if chosen else (models[0] if models else "")
        status["url"] = s["ollama_url"]
        return status

    def _probe_ollama(self, s: dict) -> dict:
        provider = self.factory("ollama", s, self.keystore)
        if provider is None:
            return {"reachable": False, "models": [], "error": "Ollama provider disabled"}
        try:
            lister = getattr(provider, "chat_models", provider.list_models)
            return {"reachable": True, "models": lister(), "error": ""}
        except ProviderError as e:
            return {"reachable": False, "models": [], "error": str(e)}

    # ---- routing ------------------------------------------------------------
    def candidates(self, task_type: str) -> list[tuple[Provider, str]]:
        s = self.settings.get()
        route = s["routing"].get(task_type, "local")
        local: list[tuple[Provider, str]] = []
        status = self.ollama_status()
        if status["reachable"] and status["active_model"]:
            provider = self.factory("ollama", s, self.keystore)
            if provider:
                local.append((provider, status["active_model"]))

        cloud: list[tuple[Provider, str]] = []
        if s["mode"] != "local_only" and route != "local_only":
            for name in s["cloud_order"]:
                model = s["cloud_models"].get(name, "")
                if not model:
                    continue
                provider = self.factory(name, s, self.keystore)
                if provider:
                    cloud.append((provider, model))

        if s["mode"] == "cloud_first" or route == "cloud":
            return cloud + local
        return local + cloud

    def complete(self, task_type: str, messages: list[dict], *, json_mode: bool = False) -> Completion:
        candidates = self.candidates(task_type)
        if not candidates:
            raise NoModelError(
                "لا يوجد نموذج جاهز. شغّل Ollama وثبّت نموذجًا (مثل: ollama pull qwen2.5:7b)، "
                "أو أضف مفتاح API واختر نموذجًا من الإعدادات."
            )
        errors = []
        for provider, model in candidates:
            try:
                started = time.time()
                text = clean_output(provider.chat(model, messages, json_mode=json_mode))
                log.info("%s/%s answered %s in %.1fs", provider.name, model, task_type, time.time() - started)
                return Completion(text=text, provider=provider.name, model=model)
            except ProviderError as e:
                log.warning("%s/%s failed: %s", provider.name, model, e)
                errors.append(f"{provider.label} ({model}): {e}")
        raise NoModelError("فشلت كل النماذج المتاحة — " + " | ".join(errors))
