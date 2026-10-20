"""The designer agent: makes pictures into the library with the user's OpenAI or Gemini key."""

from __future__ import annotations

from ..integrations.image_tools import ImageToolbox
from ..integrations.social_tools import StoreReader
from ..models import Router
from .multitool import MultiToolbox
from .toolloop import LoopResult, run_tool_loop

DESIGN_PROMPT = """You are making pictures for the user with tools.

Reply with exactly ONE JSON object per turn, using one of these forms:
{tools}

Rules:
- Make exactly what was asked: the number of pictures, the shape for where they will be used (Instagram: square or portrait; banners: landscape).
- For a product, read it first (get_product) and build on its picture ("from" with its https link) so the product stays recognisable.
- Never claim a picture was published; you only make them. They appear in the user's picture library.
- When you are done, reply with "done": list the pictures you made (media:<name>) and describe each in a line, in the user's language.
- You have at most {limit} tool calls."""


def build_toolbox(images, media, stores=None, default_store: str | None = None) -> MultiToolbox:
    reader = StoreReader(stores, default_store) if stores is not None and stores.entries() else None
    return MultiToolbox(ImageToolbox(images, media), reader)


def run_designer(router: Router, toolbox: MultiToolbox, system: str, task_text: str, max_calls: int = 10) -> LoopResult:
    system = system + "\n\n" + DESIGN_PROMPT.format(tools=toolbox.help(), limit=max_calls)
    return run_tool_loop(router, toolbox, system, task_text, task_type="writing", max_calls=max_calls, name="designer")
