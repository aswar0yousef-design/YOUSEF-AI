"""The technical manager: checks the user's stores, sites and project code, then proposes fixes.

It gets every toolbox the request allows: the check tools, the project files
(and git) when a folder is attached, and the store tools when stores are
connected. Fixes are ordinary proposals that wait for the user's approval.
"""

from __future__ import annotations

from ..audit import AuditToolbox
from ..integrations.store_tools import StoreToolbox
from ..models import Router
from ..workspace import Toolbox
from .multitool import MultiToolbox
from .toolloop import LoopResult, run_tool_loop

TECH_MAX_CALLS = 24

TECH_PROMPT = """You are the technical manager of the user's online stores, websites and code, working with tools.
{context}

Reply with exactly ONE JSON object per turn, using one of these forms:
{tools}

How to work:
1. Check first, with the check tools above that fit the request.
2. Confirm before fixing: the checks are hints. Read the code around each code finding and drop false positives.
3. Fix what your tools can fix, as proposals: code edits with replace or write_file, product data with propose_update. The user approves each one before anything changes. Never claim a fix is already applied.
4. For what your tools cannot fix (hosting or server settings, plugin and theme updates, PHP version, certificates, Shopify admin settings), give exact steps: where to click, or which line to add and in which file.

Rules:
- Check only the user's own connected stores and project folder. Never attack, exploit, brute-force or try passwords, and never check other sites.
- Never write out a secret's value (keys, tokens, passwords), not even partly; say where it is. To fix a written-in secret, read it from an environment variable or config outside git instead, and tell the user to revoke the old one.
- Secret files (.env, keys, certificates, wp-config.php) are blocked; do not try to read them. Keys and passwords inside other files are shown as markers like [[SECRET_1]]: keep a marker exactly as it is when your edit keeps that value.
- Keep fixes small and safe: fix the problem without changing what the code does otherwise. Do not edit vendor, minified or generated files.
- When you finish, reply with "done": a report in the user's language, ordered by severity (critical, high, medium, low): what you checked, what you found, what you propose to fix now, and what the user must do by hand. Say what you could not check.
- You have at most {limit} tool calls."""


def describe(store_tools: StoreToolbox | None, workspace_tools: Toolbox | None) -> str:
    lines = []
    if store_tools is not None:
        lines.append("Connected stores:\n" + store_tools.describe_stores())
    if workspace_tools is not None:
        lines.append(f'Project folder: "{workspace_tools.ws.name}"')
    return "\n".join(lines) or "(nothing connected)"


def build_toolbox(stores=None, default_store: str | None = None, workspace=None, services=None,
                  auditor=None, osv=None, wp_latest=None) -> MultiToolbox:
    store_tools = StoreToolbox(stores, default_store) if stores is not None and stores.entries() else None
    workspace_tools = Toolbox(workspace, services) if workspace is not None else None
    extra = {"wp_latest": wp_latest} if wp_latest is not None else {}
    audit = AuditToolbox(store_tools, workspace, auditor=auditor, osv=osv, **extra)
    return MultiToolbox(audit, workspace_tools, store_tools)


def run_tech_manager(router: Router, toolbox: MultiToolbox, system: str, task_text: str,
                     max_calls: int = TECH_MAX_CALLS) -> LoopResult:
    store_tools = toolbox.get(StoreToolbox)
    workspace_tools = toolbox.get(Toolbox)
    system = system + "\n\n" + TECH_PROMPT.format(context=describe(store_tools, workspace_tools),
                                                  tools=toolbox.help(), limit=max_calls)
    first = task_text
    if workspace_tools is not None:
        overview = workspace_tools.ws.list("", depth=2)
        first += f"\n\nProject files (top levels):\n{overview}"
    return run_tool_loop(router, toolbox, system, first, task_type="code", max_calls=max_calls,
                         name="tech_manager")
