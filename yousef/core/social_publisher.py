"""The social publisher agent: the shared tool loop with the social (and read-only store) tools."""

from __future__ import annotations

from ..integrations.social_tools import SocialToolbox, StoreReader
from ..models import Router
from .multitool import MultiToolbox
from .toolloop import LoopResult, run_tool_loop

SOCIAL_MAX_CALLS = 16

SOCIAL_PROMPT = """You are publishing for the user on their social accounts, with tools.
Connected accounts:
{accounts}

Reply with exactly ONE JSON object per turn, using one of these forms:
{tools}

Rules:
- Look before you write: read the account's recent posts (recent_posts) to match its voice and avoid repeating them, and read the products you talk about (get_product) for real names, prices and pictures.
- Every post is a proposal: the user sees it and approves before anything is published. Never claim a post is already published.
- Use the user's pictures (list_media, "media:<name>") when they gave some; otherwise product pictures from the store. Instagram cannot post without a picture.
- Write each post ready to publish, in the user's language. When Facebook and Instagram need different texts, give "text" for Facebook and "text_instagram" for Instagram.
- Schedule ("when") only when the user asks for a time or a later day; otherwise publish after approval.
- When you are done, reply with "done": summarise what you propose (accounts, platforms, timing) in the user's language, and include the post text so the user can read it.
- You have at most {limit} tool calls."""


def build_toolbox(accounts, memory, media, stores=None, default_store: str | None = None) -> MultiToolbox:
    social = SocialToolbox(accounts, memory, media)
    reader = StoreReader(stores, default_store) if stores is not None and stores.entries() else None
    return MultiToolbox(social, reader)


def run_social_publisher(router: Router, toolbox: MultiToolbox, system: str, task_text: str,
                         max_calls: int = SOCIAL_MAX_CALLS) -> LoopResult:
    social = toolbox.get(SocialToolbox)
    system = system + "\n\n" + SOCIAL_PROMPT.format(accounts=social.describe_accounts(), tools=toolbox.help(),
                                                    limit=max_calls)
    return run_tool_loop(router, toolbox, system, task_text, task_type="writing", max_calls=max_calls,
                         name="social_publisher")
