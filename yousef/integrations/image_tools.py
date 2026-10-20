"""The picture tool agents use: make a picture (or a new version of one of the user's) into the library."""

from __future__ import annotations

import urllib.parse
import urllib.request

from ..media import MAX_IMAGE_BYTES, MediaError, kind_of
from ..models.base import ProviderError
from .images import MAX_PER_TASK, SHAPES, ImageError

TOOL_HELP = """{"tool": "list_media"}                                                 pictures the user gave Yousef or Yousef made ("media:<name>")
{"tool": "generate_image", "prompt": "detailed description of the picture", "shape": "square", "from": "media:img-....jpg"}   make a picture with the user's OpenAI or Gemini key (shape: square, portrait 4:5 or landscape; "from" is optional: a picture to build on, such as "media:<name>" or a product picture's https link). It is saved as media:<name>"""
NOTES = """Pictures cost money on the user's account: make at most {limit} per task, only when the user wants new pictures or has none that fit. Describe the picture in detail (subject, setting, light, style, colours); keep any text in the picture short, and say in which language."""


class ImageToolbox:
    def __init__(self, maker, media, limit: int = MAX_PER_TASK):
        self.maker = maker    # images.ImageMaker
        self.media = media    # media.MediaLibrary
        self.limit = limit
        self.made: list[str] = []
        self.trace: list[dict] = []

    def help(self) -> str:
        lines = TOOL_HELP.splitlines()
        if getattr(self, "tool_list_media", None) is None:  # another toolbox lists the pictures
            lines = lines[1:]
        return "\n".join(lines) + "\n\n" + NOTES.format(limit=self.limit)

    def tool_list_media(self, call: dict) -> str:
        files = self.media.list()[:40]
        return "\n".join(f"media:{f['name']} | {f['label'] or '-'} | {f['size'] // 1024} KB" for f in files) \
            or "No pictures yet."

    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        try:
            if tool == "list_media":
                out = self.tool_list_media(call)
                self.trace.append({"tool": tool, "target": "", "ok": True})
                return out
            if tool != "generate_image":
                raise ImageError(f'unknown tool "{tool}"')
            out, name = self.tool_generate_image(call)
            self.trace.append({"tool": tool, "target": str(call.get("prompt") or "")[:120], "ok": True,
                               "note": f"media:{name}"})
            return out
        except (ProviderError, MediaError, ValueError) as e:
            self.trace.append({"tool": tool, "target": str(call.get("prompt") or "")[:120], "ok": False,
                               "note": str(e)[:200]})
            return f"Error: {e}"

    def tool_generate_image(self, call: dict) -> tuple[str, str]:
        if len(self.made) >= self.limit:
            raise ImageError(f"picture limit reached for this task ({self.limit}); use the ones already made")
        source = None
        ref = str(call.get("from") or "").strip()
        if ref.startswith("https://"):
            source = download(ref)
        elif ref:
            source = self.media.read(ref)
        shape = str(call.get("shape") or "square").lower()
        result = self.maker.make(str(call.get("prompt") or ""), shape if shape in SHAPES else "square", source)
        label = f"صورة من {result['provider']} ({result['model']})"
        item = self.media.save(result["data"], label)
        self.made.append(item["name"])
        return (f"Created media:{item['name']} with {result['provider']}/{result['model']}. Use it as an image in a post, "
                "or describe it to the user."), item["name"]


def download(url: str) -> tuple[bytes, str]:
    """A picture from the web (a product photo on the store's CDN) to build on."""
    if urllib.parse.urlparse(url).scheme != "https":
        raise ImageError("picture links must be https")
    req = urllib.request.Request(url, headers={"User-Agent": "YOUSEF-AI", "Accept": "image/jpeg,image/png"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(MAX_IMAGE_BYTES + 1)
    except OSError as e:
        raise ImageError(f"could not download the picture: {getattr(e, 'reason', e)}") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError("the picture is larger than 8MB")
    kind = kind_of(data)  # JPEG or PNG only
    return data, "image/jpeg" if kind == "jpg" else "image/png"
