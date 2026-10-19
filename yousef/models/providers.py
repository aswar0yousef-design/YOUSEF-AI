"""Model providers: Ollama (local), Anthropic, OpenAI, Gemini.

Each provider is a thin adapter over the official HTTP API, so there are no
third-party SDKs to install or keep in sync.
"""

from __future__ import annotations

import logging
import time
import urllib.parse

from .base import NO_PROXY_OPENER, Provider, ProviderError, http_json, split_system

log = logging.getLogger("yousef.models")

_EMBED_HINTS = ("embed", "bge-", "bge:", "all-minilm", "snowflake-arctic-embed")
JSON_ONLY = "Respond with a single JSON object only, no other text."
# models that reason in a separate channel (Ollama returns it as message.thinking)
_REASONING = ("gpt-oss",)
# Ollama's model runner died (usually the graphics card ran out of memory); Ollama restarts it on the next request
_RUNNER_CRASH = ("process has terminated", "cuda error", "out of memory", "unable to allocate", "cudamalloc",
                 "exit status 0xc0000409", "unexpected eof", "connection reset")


def explain_crash(model: str, error: Exception) -> str:
    return (f"توقّف Ollama عند تشغيل {model} على كرت الشاشة وعلى المعالج أيضًا، فالمشكلة في Ollama نفسه أو في تعريف "
            "كرت الشاشة، لا في يوسف. جرّب بالترتيب: أغلق Ollama من أيقونته بجانب الساعة وافتحه من جديد؛ حدّث Ollama "
            f"من ollama.com وتعريف NVIDIA؛ ثم جرّب في الطرفية: ollama run {model} \"مرحبا\". وإن لم يعمل هناك أيضًا "
            f"فاختر نموذجًا آخر مثل qwen2.5:7b. (الخطأ: {str(error)[:160]})")


def _crashed(error: Exception) -> bool:
    text = str(error).lower()
    return any(c in text for c in _RUNNER_CRASH)


def _is_reasoning(model: str) -> bool:
    return any(h in model.lower() for h in _REASONING)


def _with_instruction(messages: list[dict], text: str) -> list[dict]:
    out = [dict(m) for m in messages]
    if out and out[0]["role"] == "system":
        out[0]["content"] = out[0]["content"].rstrip() + "\n\n" + text
    else:
        out.insert(0, {"role": "system", "content": text})
    return out


class OllamaProvider(Provider):
    name = "ollama"
    label = "Ollama (محلي)"
    is_local = True

    retry_pause = 3.0

    def __init__(self, base_url: str, num_ctx: int = 8192):
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        # set by the router, shared across requests: models the graphics card could not run this session
        self.cpu_only: set[str] = set()
        self.free_gpu = None  # callable -> bool: moves other work (speech) off the graphics card

    def list_models(self) -> list[str]:
        data = http_json("GET", f"{self.base_url}/api/tags", timeout=5, opener=NO_PROXY_OPENER) or {}
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def chat_models(self) -> list[str]:
        return [m for m in self.list_models() if not any(h in m.lower() for h in _EMBED_HINTS)]

    def _post(self, body: dict) -> dict:
        # First call can be slow while the model loads into GPU memory.
        return http_json("POST", f"{self.base_url}/api/chat", body=body, timeout=900, opener=NO_PROXY_OPENER) or {}

    def _call(self, model: str, messages: list[dict], json_format: bool) -> tuple[str, dict]:
        # the context size stays the user's: a larger one makes Ollama reload the model and can overflow a small GPU
        body = {"model": model, "messages": messages, "stream": False,
                "options": {"temperature": 0.3, "num_ctx": self.num_ctx}}
        if model in self.cpu_only:
            body["options"]["num_gpu"] = 0
        if json_format:
            body["format"] = "json"
        if _is_reasoning(model):
            body["think"] = "low"  # gpt-oss cannot switch reasoning off; "low" keeps answers fast
        try:
            data = self._post(body)
        except ProviderError as e:
            if "think" in body and "think" in str(e).lower():
                log.warning("%s: this Ollama does not accept a reasoning level (%s); asking without it", model, e)
                body.pop("think")
                data = self._post(body)
            elif _crashed(e):
                data = self._recover(model, body, e)
            else:
                raise
        message = data.get("message") or {}
        if "content" not in message:
            raise ProviderError(f"Ollama returned no message: {str(data)[:200]}")
        return message["content"] or "", data

    def _recover(self, model: str, body: dict, error: Exception) -> dict:
        """Ollama's runner died, usually because the model did not fit on the graphics card.
        1. free the card (speech moves to the processor) or just try again: Ollama restarts its runner;
        2. run this model on the processor for the rest of the session (slower, but it answers);
        3. give up with what to check."""
        log.warning("%s: Ollama's runner stopped (%s)", model, str(error)[:200])
        freed = bool(self.free_gpu and self.free_gpu())
        if freed:
            log.warning("%s: moved speech recognition off the graphics card; trying again", model)
        time.sleep(self.retry_pause)
        if "num_gpu" not in body["options"]:
            try:
                return self._post(body)
            except ProviderError as again:
                if not _crashed(again):
                    raise
                log.warning("%s: stopped again on the graphics card; running it on the processor", model)
            body["options"]["num_gpu"] = 0
            self.cpu_only.add(model)
        try:
            return self._post(body)
        except ProviderError as last:
            if _crashed(last):
                self.cpu_only.discard(model)
                raise ProviderError(explain_crash(model, last)) from None
            raise

    def chat(self, model, messages, *, json_mode=False):
        if not json_mode:
            return self._call(model, messages, False)[0]
        reasoning = _is_reasoning(model)
        # gpt-oss writes its answer after a reasoning channel; a JSON grammar from the first token can leave the
        # answer empty, so it gets the instruction in words instead, like the cloud models
        content, data = self._call(model, _with_instruction(messages, JSON_ONLY) if reasoning else messages,
                                   json_format=not reasoning)
        if content.strip():
            return content
        thinking = (data.get("message") or {}).get("thinking") or ""
        log.warning("%s gave an empty JSON answer (done_reason=%s, reasoning %d chars); asking again in words",
                    model, data.get("done_reason"), len(thinking))
        content, data = self._call(model, _with_instruction(messages, JSON_ONLY), json_format=False)
        if not content.strip() and data.get("done_reason") == "length":
            raise ProviderError(f"{model} ran out of context (num_ctx {self.num_ctx}); raise it in settings")
        return content


class AnthropicProvider(Provider):
    name = "anthropic"
    label = "Claude (Anthropic)"
    base_url = "https://api.anthropic.com/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}

    def list_models(self) -> list[str]:
        data = http_json("GET", f"{self.base_url}/models?limit=100", headers=self._headers(), timeout=20) or {}
        return [m["id"] for m in data.get("data", []) if m.get("id")]

    def chat(self, model, messages, *, json_mode=False):
        system, turns = split_system(messages)
        if json_mode:
            system += "\n\nRespond with a single JSON object only, no other text."
        body = {"model": model, "max_tokens": 8192, "messages": turns}
        if system.strip():
            body["system"] = system.strip()
        data = http_json("POST", f"{self.base_url}/messages", headers=self._headers(), body=body, timeout=300) or {}
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        if not parts:
            raise ProviderError(f"Claude returned no text (stop_reason={data.get('stop_reason')})")
        return "".join(parts)


class OpenAIProvider(Provider):
    name = "openai"
    label = "OpenAI"
    base_url = "https://api.openai.com/v1"
    _NOT_CHAT = ("embedding", "tts", "whisper", "dall-e", "image", "audio", "realtime",
                 "moderation", "transcribe", "search", "davinci", "babbage", "sora")

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def list_models(self) -> list[str]:
        data = http_json("GET", f"{self.base_url}/models", headers=self._headers(), timeout=20) or {}
        ids = [m["id"] for m in data.get("data", []) if m.get("id")]
        return sorted(i for i in ids if not any(x in i.lower() for x in self._NOT_CHAT))

    def chat(self, model, messages, *, json_mode=False):
        body = {"model": model, "messages": messages}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = http_json("POST", f"{self.base_url}/chat/completions", headers=self._headers(), body=body, timeout=300) or {}
        choices = data.get("choices") or []
        content = (choices[0].get("message") or {}).get("content") if choices else None
        if not content:
            raise ProviderError("OpenAI returned no text")
        return content


class GeminiProvider(Provider):
    name = "gemini"
    label = "Gemini (Google)"
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key}

    def list_models(self) -> list[str]:
        data = http_json("GET", f"{self.base_url}/models?pageSize=200", headers=self._headers(), timeout=20) or {}
        out = []
        for m in data.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                out.append(m.get("name", "").removeprefix("models/"))
        return [m for m in out if m]

    def chat(self, model, messages, *, json_mode=False):
        system, turns = split_system(messages)
        model = model.removeprefix("models/")
        body: dict = {
            "contents": [
                {"role": "model" if t["role"] == "assistant" else "user", "parts": [{"text": t["content"]}]}
                for t in turns
            ]
        }
        if system.strip():
            body["systemInstruction"] = {"parts": [{"text": system.strip()}]}
        if json_mode:
            body["generationConfig"] = {"responseMimeType": "application/json"}
        url = f"{self.base_url}/models/{urllib.parse.quote(model, safe='-._')}:generateContent"
        data = http_json("POST", url, headers=self._headers(), body=body, timeout=300) or {}
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise ProviderError(f"Gemini returned no answer ({reason})")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text:
            raise ProviderError(f"Gemini returned no text ({candidates[0].get('finishReason')})")
        return text


CLOUD_CLASSES = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}
