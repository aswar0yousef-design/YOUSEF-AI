"""Shared pieces for model providers: HTTP helper, errors, message cleanup."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# Local servers (Ollama) must never go through a system proxy.
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
DEFAULT_OPENER = urllib.request.build_opener()

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class ProviderError(RuntimeError):
    """A provider call failed (network, auth, quota, bad model...)."""


def http_json(method: str, url: str, *, headers: dict | None = None, body=None,
              timeout: float = 120, opener=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with (opener or DEFAULT_OPENER).open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
            parsed = json.loads(detail)
            err = parsed.get("error", parsed)
            detail = err.get("message", detail) if isinstance(err, dict) else str(err)
        except Exception:
            pass
        raise ProviderError(f"HTTP {e.code}: {str(detail)[:300]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise ProviderError(f"connection failed: {reason}") from None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        raise ProviderError("provider returned a non-JSON response") from None


def clean_output(text: str) -> str:
    """Drop hidden reasoning blocks some local models emit."""
    return _THINK.sub("", text or "").strip()


def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Separate system text and make turns alternate user/assistant, starting with user.

    Needed by APIs (Anthropic, Gemini) that take the system prompt separately and
    reject two consecutive messages from the same role.
    """
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    turns: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            continue
        role = "assistant" if m["role"] == "assistant" else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1] = {"role": role, "content": turns[-1]["content"] + "\n\n" + m["content"]}
        else:
            turns.append({"role": role, "content": m["content"]})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return "\n\n".join(system_parts), turns


class Provider:
    name = "base"
    label = "base"
    is_local = False

    def chat(self, model: str, messages: list[dict], *, json_mode: bool = False) -> str:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        raise NotImplementedError
