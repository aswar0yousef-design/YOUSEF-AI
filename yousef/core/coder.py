"""The coder agent's tool loop.

Works with any model (local or cloud) through a plain JSON protocol instead of
provider-specific function calling: each turn the model answers with one JSON
object, either a tool call or {"done": ...}. Old tool results are shortened so
long sessions still fit a local model's context window.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..models import Router
from ..workspace import Services, Toolbox, Workspace
from .jsonutil import extract_json

log = logging.getLogger("yousef.coder")

MAX_TOOL_CALLS = 16
KEEP_FULL_RESULTS = 5

TOOL_PROMPT = """You are working inside the user's project folder "{name}" with tools.
Reply with exactly ONE JSON object per turn, using one of these forms:
{tools}

Rules:
- Paths are relative to the project root. Read a file before changing it, and copy the "old" text exactly.
- Prefer small "replace" edits. Use "write_file" for new files or complete rewrites.
- Edits, commits, pushes and pull requests are proposals: the user reviews them and approves before anything happens.
  After approval they run in this order: write files, commit, push, open the pull request. Never claim any of it already happened.
- "run" works on the files on disk, so it does not see your proposed edits yet.
- Secret files (.env, keys, certificates, wp-config.php) are blocked; do not try to read them.
- When you can answer, reply with "done": explain what you found and which files you changed and why, in the user's language.
- You have at most {limit} tool calls."""


@dataclass
class CoderResult:
    text: str
    provider: str = ""
    model: str = ""
    toolbox: Toolbox | None = None
    tool_calls: int = 0
    notes: list[str] = field(default_factory=list)


def _shrink_old_results(messages: list[dict]) -> None:
    """Keep the newest tool results in full; older ones become one-line stubs."""
    results = [i for i, m in enumerate(messages) if m["role"] == "user" and m["content"].startswith("Result of ")]
    for i in results[:-KEEP_FULL_RESULTS]:
        content = messages[i]["content"]
        if len(content) > 400:
            messages[i] = {"role": "user", "content": content[:300] + "\n... (older result shortened)"}


def run_coder(router: Router, workspace: Workspace, system: str, task_text: str,
              max_calls: int = MAX_TOOL_CALLS, services: Services | None = None) -> CoderResult:
    toolbox = Toolbox(workspace, services)
    system = system + "\n\n" + TOOL_PROMPT.format(name=workspace.name, tools=toolbox.help(), limit=max_calls)
    overview = toolbox.run({"tool": "list_files", "path": ""})
    toolbox.trace.clear()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{task_text}\n\nProject files (top levels):\n{overview}\n\nReply with one JSON object."},
    ]
    result = CoderResult(text="", toolbox=toolbox)
    bad_replies = 0
    while True:
        completion = router.complete("code", messages, json_mode=True)
        result.provider, result.model = completion.provider, completion.model
        try:
            action = extract_json(completion.text)
            if not isinstance(action, dict):
                raise ValueError("not an object")
        except ValueError:
            bad_replies += 1
            if bad_replies >= 2:
                result.text = completion.text.strip() or "لم يُرجع النموذج إجابة قابلة للفهم."
                return result
            messages.append({"role": "assistant", "content": completion.text[:2000]})
            messages.append({"role": "user", "content": "Reply with ONE JSON object only, as described."})
            continue
        if "done" in action:
            done = action["done"]
            result.text = done if isinstance(done, str) else json.dumps(done, ensure_ascii=False)
            return result
        if result.tool_calls >= max_calls:
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user", "content": 'Tool limit reached. Reply now with {"done": "..."} summarising your work.'})
            final = router.complete("code", messages, json_mode=True)
            try:
                data = extract_json(final.text)
                result.text = str(data.get("done") or final.text) if isinstance(data, dict) else final.text
            except ValueError:
                result.text = final.text
            result.notes.append("tool_limit")
            return result
        observation = toolbox.run(action)
        result.tool_calls += 1
        log.info("coder tool %s -> %s", action.get("tool"), observation[:80].replace("\n", " "))
        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"Result of {action.get('tool')}:\n{observation}"})
        _shrink_old_results(messages)
