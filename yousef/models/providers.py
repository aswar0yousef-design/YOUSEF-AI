"""Model providers: Ollama (local), Anthropic, OpenAI, Gemini.

Each provider is a thin adapter over the official HTTP API, so there are no
third-party SDKs to install or keep in sync.
"""

from __future__ import annotations

import urllib.parse

from .base import NO_PROXY_OPENER, Provider, ProviderError, http_json, split_system

_EMBED_HINTS = ("embed", "bge-", "bge:", "all-minilm", "snowflake-arctic-embed")


class OllamaProvider(Provider):
    name = "ollama"
    label = "Ollama (محلي)"
    is_local = True

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def list_models(self) -> list[str]:
        data = http_json("GET", f"{self.base_url}/api/tags", timeout=5, opener=NO_PROXY_OPENER) or {}
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]

    def chat_models(self) -> list[str]:
        return [m for m in self.list_models() if not any(h in m.lower() for h in _EMBED_HINTS)]

    def chat(self, model, messages, *, json_mode=False):
        body = {"model": model, "messages": messages, "stream": False, "options": {"temperature": 0.3}}
        if json_mode:
            body["format"] = "json"
        # First call can be slow while the model loads into GPU memory.
        data = http_json("POST", f"{self.base_url}/api/chat", body=body, timeout=900, opener=NO_PROXY_OPENER) or {}
        message = data.get("message") or {}
        if "content" not in message:
            raise ProviderError(f"Ollama returned no message: {str(data)[:200]}")
        return message["content"]


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
