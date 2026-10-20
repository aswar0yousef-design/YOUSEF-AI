"""Pictures made by OpenAI or Gemini image models, saved in Yousef's picture library.

Uses the API key the user already saved for that provider. Each picture costs
money on the user's account, so an agent may make only a few per task.
A picture can also be made from one of the user's pictures (a product photo
on a new background, a banner with the offer...).

OpenAI: POST /v1/images/generations and /v1/images/edits (GPT Image models,
base64 JPEG back). Gemini: the Interactions API (image response format), with
generateContent as a fallback for older setups.
"""

from __future__ import annotations

import base64
import logging

from ..models.base import ProviderError, http_json

log = logging.getLogger("yousef.images")

OPENAI_URL = "https://api.openai.com/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_MODELS = ("gpt-image-2.5-flare", "gpt-image-2", "gpt-image-1.5", "gpt-image-1")
GEMINI_MODELS = ("gemini-nano-banana-2.1", "gemini-3.1-flash-image", "gemini-2.5-flash-image")
SHAPES = ("square", "portrait", "landscape")
# Instagram accepts 4:5 up to 1.91:1, so "portrait" stays at 4:5 where the model allows it
GEMINI_ASPECT = {"square": "1:1", "portrait": "4:5", "landscape": "16:9"}
MAX_PER_TASK = 4


class ImageError(ProviderError):
    pass


def _openai_size(model: str, shape: str) -> str:
    if shape == "landscape":
        return "1536x1024"
    if shape == "portrait":
        # the gpt-image-2 family takes any size divisible by 16; older models only 2:3, which Instagram refuses
        return "1024x1280" if model.startswith(("gpt-image-2", "chatgpt-image")) else "1024x1024"
    return "1024x1024"


def find_image(data) -> tuple[bytes, str] | None:
    """The first base64 picture anywhere in a response (Interactions or generateContent shapes)."""
    stack = [data]
    while stack:
        item = stack.pop(0)
        if isinstance(item, dict):
            inline = item.get("inlineData") or item.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                return base64.b64decode(inline["data"]), inline.get("mimeType") or inline.get("mime_type") or "image/png"
            if item.get("data") and (item.get("type") in ("image", "output_image") or item.get("mime_type")
                                     or item.get("mimeType")) and isinstance(item["data"], str):
                return base64.b64decode(item["data"]), item.get("mime_type") or item.get("mimeType") or "image/png"
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


class ImageMaker:
    def __init__(self, settings, keystore, openai_url: str = OPENAI_URL, gemini_url: str = GEMINI_URL):
        self.settings = settings
        self.keystore = keystore
        self.openai_url = openai_url
        self.gemini_url = gemini_url

    def providers(self) -> list[str]:
        """Providers that can make pictures now, in the user's order of preference."""
        pref = self.settings.get()["images"]["provider"]
        have = [p for p in ("openai", "gemini") if self.keystore.get(p)]
        if pref in have:
            return [pref] + [p for p in have if p != pref]
        return [] if pref not in ("auto", "openai", "gemini") else have

    @property
    def available(self) -> bool:
        return bool(self.providers())

    def make(self, prompt: str, shape: str = "square", source: tuple[bytes, str] | None = None) -> dict:
        """-> {"data": bytes, "mime": "image/jpeg", "provider", "model"}; source = (bytes, mime) to edit from."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ImageError("وصف الصورة فارغ")
        if shape not in SHAPES:
            shape = "square"
        errors = []
        for provider in self.providers():
            for model in self._models(provider):
                try:
                    if provider == "openai":
                        data, mime = self._openai(model, prompt, shape, source)
                    else:
                        data, mime = self._gemini(model, prompt, shape, source)
                    return {"data": data, "mime": mime, "provider": provider, "model": model}
                except ProviderError as e:
                    errors.append(f"{provider}/{model}: {str(e)[:160]}")
                    log.warning("image with %s/%s failed: %s", provider, model, e)
                    if not _model_problem(e):
                        break  # a key or quota problem: other models of this provider will fail the same way
        if not errors:
            raise ImageError("لصنع الصور أضف مفتاح OpenAI أو Gemini من الإعدادات")
        raise ImageError("تعذّر صنع الصورة — " + " | ".join(errors[:3]))

    def _models(self, provider: str) -> list[str]:
        chosen = self.settings.get()["images"].get(f"{provider}_model") or ""
        defaults = OPENAI_MODELS if provider == "openai" else GEMINI_MODELS
        return [chosen] + [m for m in defaults if m != chosen] if chosen else list(defaults)

    # ---- OpenAI -------------------------------------------------------------------------
    def _openai(self, model: str, prompt: str, shape: str, source) -> tuple[bytes, str]:
        headers = {"Authorization": f"Bearer {self.keystore.get('openai')}"}
        body = {"model": model, "prompt": prompt, "size": _openai_size(model, shape), "quality": "medium",
                "output_format": "jpeg", "n": 1}
        if source:
            data, mime = source
            body["images"] = [{"image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}]
            out = http_json("POST", f"{self.openai_url}/images/edits", headers=headers, body=body, timeout=300)
        else:
            out = http_json("POST", f"{self.openai_url}/images/generations", headers=headers, body=body, timeout=300)
        items = (out or {}).get("data") or []
        if not items or not items[0].get("b64_json"):
            raise ImageError("OpenAI لم يُرجع صورة")
        return base64.b64decode(items[0]["b64_json"]), "image/jpeg"

    # ---- Gemini -------------------------------------------------------------------------
    def _gemini(self, model: str, prompt: str, shape: str, source) -> tuple[bytes, str]:
        headers = {"x-goog-api-key": self.keystore.get("gemini")}
        inputs = [{"type": "text", "text": prompt}]
        if source:
            inputs.append({"type": "image", "mime_type": source[1], "data": base64.b64encode(source[0]).decode()})
        body = {"model": model, "input": inputs,
                "response_format": {"type": "image", "mime_type": "image/jpeg", "aspect_ratio": GEMINI_ASPECT[shape]}}
        try:
            out = http_json("POST", f"{self.gemini_url}/interactions", headers=headers, body=body, timeout=300)
        except ProviderError as e:
            if "404" not in str(e):
                raise
            parts = [{"text": prompt}]
            if source:
                parts.append({"inlineData": {"mimeType": source[1], "data": base64.b64encode(source[0]).decode()}})
            out = http_json("POST", f"{self.gemini_url}/models/{model}:generateContent", headers=headers, timeout=300,
                            body={"contents": [{"parts": parts}],
                                  "generationConfig": {"responseModalities": ["TEXT", "IMAGE"],
                                                       "imageConfig": {"aspectRatio": GEMINI_ASPECT[shape]}}})
        found = find_image(out)
        if not found:
            raise ImageError("Gemini لم يُرجع صورة")
        return found


def _model_problem(error: Exception) -> bool:
    text = str(error).lower()
    return any(k in text for k in ("model", "404", "not found", "does not exist", "unsupported", "verify", "access"))
