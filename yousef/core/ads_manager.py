"""The ads manager agent: Meta campaigns and their results, with read-only store sales to compare."""

from __future__ import annotations

from ..integrations.ads_tools import AdsToolbox
from ..integrations.social_tools import StoreReader
from ..models import Router
from .multitool import MultiToolbox
from .toolloop import LoopResult, run_tool_loop

ADS_MAX_CALLS = 16

ADS_PROMPT = """You are managing the user's Meta (Facebook and Instagram) ad accounts with tools.
Connected ad accounts:
{accounts}

Reply with exactly ONE JSON object per turn, using one of these forms:
{tools}

Rules:
- Read before you judge: look at campaigns (and ad sets or daily results when a decision depends on them). Base every number on what the tools return; never invent results.
- When a store is connected, compare Meta's reported revenue with the store's real sales (sales_summary) and say how far they agree.
- Judge with the right measure for the objective: purchases, cost per purchase and ROAS for sales; clicks and CPC for traffic. Say when there is too little data to judge (few days or little spend).
- Pausing, resuming and budget changes are proposals: the user approves each one before anything changes. Never claim a change is made. Explain why for each proposal.
- Prefer gradual budget increases (about 20%) for campaigns that sell profitably; pause what spends without results after enough spend.
- You cannot create campaigns, ads or audiences, or edit creatives; when they would help, describe them for the user to make in Ads Manager.
- When you are done, reply with "done": a short report in the user's language (what works, what does not, what you propose and why).
- You have at most {limit} tool calls."""


def build_toolbox(ads, stores=None, default_store: str | None = None) -> MultiToolbox:
    reader = StoreReader(stores, default_store) if stores is not None and stores.entries() else None
    return MultiToolbox(AdsToolbox(ads), reader)


def run_ads_manager(router: Router, toolbox: MultiToolbox, system: str, task_text: str,
                    max_calls: int = ADS_MAX_CALLS) -> LoopResult:
    ads = toolbox.get(AdsToolbox)
    system = system + "\n\n" + ADS_PROMPT.format(accounts=ads.describe_accounts(), tools=toolbox.help(), limit=max_calls)
    return run_tool_loop(router, toolbox, system, task_text, task_type="analysis", max_calls=max_calls,
                         name="ads_manager")
