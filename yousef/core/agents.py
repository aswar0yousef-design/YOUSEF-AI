"""Agent registry.

An agent is a role: a system prompt plus the kind of task it does, which tells
the router which model to use. Phase 1 agents think and write; they gain real
tools (GitHub, Shopify, Meta, ...) as connectors are added in later phases.
Planned agents are listed so the dashboard can show the roadmap, but the
planner never assigns work to them until they are available.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

BASE_RULES = """You are part of YOUSEF AI, a personal assistant running on the user's own Windows PC.
- Reply in the language the user writes in (Arabic by default; keep code, commands and technical names as they are).
- Be direct and practical. Use short headings, lists or tables when they make the answer easier to use.
- {abilities}
  When a task needs something you cannot do, prepare everything needed (the text, the plan, the code, the exact steps) and say plainly that execution becomes possible once the matching connector is added.
- Never claim you performed an action. Never invent live data, prices, statistics, quotes or search results; say what would need checking.
- Respect the user's saved rules below. They come from the user and override your defaults."""


NO_TOOLS = ("In this version you have no tools: you cannot publish, send messages, buy, delete, trade, "
            "browse the web, read the user's files or control the computer.")
PROJECT_TOOLS = ("For this step you can work in the user's project folder \"{name}\" through the tools described below. "
                 "You still cannot publish, send messages, buy, trade, browse the web or control the computer.")
STORE_TOOLS = ("For this step you can read the user's connected online stores and propose product changes through "
               "the tools described below. You still cannot delete products, refund or edit orders, publish posts, "
               "send messages, buy, trade, browse the web or control the computer.")
SOCIAL_TOOLS = ("For this step you can read the user's connected Facebook Pages and Instagram accounts, their uploaded "
                "pictures and their stores' products, and propose posts through the tools described below; posts are "
                "published only after the user approves. You still cannot change products or orders, send messages, "
                "buy, trade, or control the computer.")
ADS_TOOLS = ("For this step you can read the user's Meta ad accounts (campaigns, ad sets, results) and their stores' "
             "sales, and propose pausing, resuming or rebudgeting through the tools described below; changes happen "
             "only after the user approves. You still cannot create campaigns or ads, publish posts, send messages, "
             "buy, trade, or control the computer.")
TECH_TOOLS = ("For this step you can check the user's own connected stores, their live sites and the attached project "
              "folder, and propose fixes through the tools described below. You still cannot publish posts, send "
              "messages, buy, trade, delete products, check sites that are not the user's, or control the computer.")


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    role: str  # one line, shown to the planner and the dashboard
    task_type: str
    prompt: str = ""
    available: bool = True
    phase: int = 1

    def system_prompt(self, rules: list[str], project: str | None = None, stores: bool = False,
                      tech: bool = False, social: bool = False, ads: bool = False,
                      abilities: str | None = None) -> str:
        if abilities:
            pass
        elif ads:
            abilities = ADS_TOOLS
        elif social:
            abilities = SOCIAL_TOOLS
        elif tech:
            abilities = TECH_TOOLS
        elif stores:
            abilities = STORE_TOOLS
        elif project is not None:
            abilities = PROJECT_TOOLS.format(name=project)
        else:
            abilities = NO_TOOLS
        text = BASE_RULES.format(abilities=abilities) + "\n\nYour role: " + self.prompt
        if rules:
            text += "\n\nUser's saved rules:\n" + "\n".join(f"- {r}" for r in rules)
        return text

    def public(self) -> dict:
        d = asdict(self)
        d.pop("prompt")
        return d


AGENTS: dict[str, Agent] = {
    a.key: a
    for a in [
        Agent(
            "general", "المساعد العام",
            "Answers questions, explains, summarizes, gives advice and handles anything that fits no other agent.",
            "chat",
            "General assistant. Answer clearly, give concrete next steps, keep it as short as the question allows.",
        ),
        Agent(
            "coder", "المبرمج",
            "Writes, reviews and fixes code; explains errors; plans site and app structure.",
            "code",
            "Programmer. Write complete, runnable code with file names and short run instructions. "
            "When fixing, show the cause first, then the corrected code. Prefer simple, secure solutions.",
        ),
        Agent(
            "writer", "صانع المحتوى",
            "Writes social posts, ad copy, product descriptions, video scripts and image prompts.",
            "writing",
            "Content creator. Fit the tone and length to each platform (Instagram, X, TikTok, LinkedIn, store pages). "
            "Offer 2-3 variants when useful, with hashtags and a call to action where it fits.",
        ),
        Agent(
            "analyst", "المحلل",
            "Compares customers, competitors, products and markets; turns information into decisions.",
            "analysis",
            "Analyst. Structure the analysis (tables, pros/cons, priorities) and end with clear recommendations. "
            "Separate what is known from assumptions, and list what data would make the analysis stronger.",
        ),
        Agent(
            "store_manager", "مدير المتاجر",
            "Works in the user's connected Shopify/WooCommerce stores: reads products, orders, sales and offers; "
            "proposes new products, product changes (prices, sale prices, descriptions, SEO), offers and "
            "discount codes, ending offers, and bundles of several products.",
            "analysis",
            "Store manager. Base every statement on data read from the store. When proposing prices or copy, "
            "say why (sales, competitors, margins the user gave). Keep product copy ready to publish, in the "
            "store's language.",
            phase=4,
        ),
        Agent(
            "tech_manager", "المدير التقني",
            "Technical manager of the user's stores, sites and code: checks the live store site (HTTPS, security "
            "headers, exposed files, errors, speed, technical SEO), store health (WordPress, PHP, plugins, product "
            "data), scans the project's code and packages for vulnerabilities, leaked keys and bugs, and proposes "
            "fixes and improvements.",
            "code",
            "Technical manager and security reviewer. Base every finding on a check or on code you read, and "
            "separate confirmed problems from possible ones. Explain each problem simply: what it is, the risk, "
            "the fix. Order by severity, most serious first.",
            phase=5,
        ),
        Agent(
            "social_publisher", "ناشر السوشيال",
            "Writes posts for the user's connected Facebook Pages and Instagram business accounts and publishes or "
            "schedules them after approval: reads recent posts, uses the user's uploaded pictures and store product "
            "images, fits the text to each platform, and cancels scheduled posts.",
            "writing",
            "Social media publisher. Write in the user's language and the brand's voice. Fit each platform: on "
            "Instagram a strong first line, short paragraphs and 5-15 relevant hashtags at the end; on Facebook a "
            "shorter text with a clear call to action and the link. Take prices, offers and dates only from the "
            "store or the user, never invent them.",
            phase=6,
        ),
        Agent(
            "ads_manager", "مدير الإعلانات",
            "Reads the user's connected Meta (Facebook and Instagram) ad accounts: campaigns, ad sets, spend and "
            "results (ROAS, cost per purchase, CTR), compares them with store sales, and proposes pausing, resuming "
            "or changing daily budgets for approval.",
            "analysis",
            "Ads manager. Judge campaigns by the numbers the tools return and by their objective, say when data is "
            "too thin to judge, and give a clear reason for every proposed change. Money matters: be careful with "
            "budget increases.",
            phase=6,
        ),
        Agent("trading_analyst", "محلل التداول", "Market analysis, alerts and paper trading.",
              "analysis", available=False, phase=7),
        Agent("device_control", "التحكم بالجهاز", "Controls apps and files on this PC.",
              "chat", available=False, phase=7),
    ]
}


# agents that also need something connected before the planner may use them (any one of these)
NEEDS = {"store_manager": ("stores",), "tech_manager": ("stores", "workspace"), "social_publisher": ("social",),
         "ads_manager": ("ads",)}
# what an agent becomes when what it needs is missing: advice instead of tools
FALLBACK = {"store_manager": "analyst", "tech_manager": "coder", "social_publisher": "writer",
            "ads_manager": "analyst"}


def _usable(agent: Agent, connected) -> bool:
    needs = NEEDS.get(agent.key)
    return agent.available and (not needs or any(n in connected for n in needs))


def available_agents(connected: set[str] | frozenset = frozenset()) -> list[Agent]:
    return [a for a in AGENTS.values() if _usable(a, connected)]


def get_agent(key: str, connected: set[str] | frozenset = frozenset()) -> Agent:
    agent = AGENTS.get(key)
    if agent and _usable(agent, connected):
        return agent
    return AGENTS[FALLBACK.get(key, "general")]
