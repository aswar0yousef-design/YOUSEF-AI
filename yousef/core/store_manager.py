"""The store manager agent: the shared tool loop with the store toolbox."""

from __future__ import annotations

from ..integrations.store_tools import StoreToolbox
from ..integrations.stores import Stores
from ..models import Router
from .toolloop import MAX_TOOL_CALLS, LoopResult, run_tool_loop

STORE_PROMPT = """You are managing the user's online stores with tools.
Connected stores:
{stores}

Reply with exactly ONE JSON object per turn, using one of these forms:
{tools}

Rules:
- Read before you change: look the product up (list_products, get_product) and use its real id.
- Product creations and updates are proposals: the user sees each one with before/after values and approves before anything changes in the store. Never claim a change is already made.
- New products are drafts unless the user clearly asked to publish them now ("status": "active").
- Prices are plain numbers in the store's currency, without the currency symbol.
- You cannot delete products, refund, cancel or edit orders, and orders carry no customer personal data.
- Never invent sales figures; use list_orders or sales_summary.
- When you can answer, reply with "done": summarise what you found and what you propose, in the user's language.
- You have at most {limit} tool calls."""


def run_store_manager(router: Router, stores: Stores, system: str, task_text: str, default_store: str | None = None,
                      max_calls: int = MAX_TOOL_CALLS, toolbox: StoreToolbox | None = None) -> LoopResult:
    toolbox = toolbox or StoreToolbox(stores, default_store)
    system = system + "\n\n" + STORE_PROMPT.format(stores=toolbox.describe_stores(), tools=toolbox.help(),
                                                   limit=max_calls)
    return run_tool_loop(router, toolbox, system, task_text, task_type="analysis", max_calls=max_calls,
                         name="store_manager")
