"""A tool loop any agent can use, with any model (local or cloud).

Instead of provider-specific function calling, the model answers each turn with
one JSON object: a tool call, or {"done": "..."} to finish. Old tool results are
shortened so long sessions still fit a local model's context window.

A toolbox is any object with:
    run(call: dict) -> str   execute one tool call, return text for the model
    trace: list[dict]        what was called (shown to the user)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..models import Router
from .jsonutil import extract_json

log = logging.getLogger("yousef.toolloop")

MAX_TOOL_CALLS = 16
KEEP_FULL_RESULTS = 4
RESULTS_BUDGET = 12000  # characters of tool results kept in full (the newest one always is)


@dataclass
class LoopResult:
    text: str
    provider: str = ""
    model: str = ""
    toolbox: object = None
    tool_calls: int = 0
    notes: list[str] = field(default_factory=list)


def _shrink_old_results(messages: list[dict]) -> None:
    """Keep the newest tool results in full; older ones become short stubs.

    A local model often has an 8K-token context, so besides a count there is a size budget: three long
    product lists in full would push the instructions out of what the model can see."""
    results = [i for i, m in enumerate(messages) if m["role"] == "user" and m["content"].startswith("Result of ")]
    kept, used = 0, 0
    for i in reversed(results):
        content = messages[i]["content"]
        if kept == 0 or (kept < KEEP_FULL_RESULTS and used + len(content) <= RESULTS_BUDGET):
            kept += 1
            used += len(content)
            continue
        if len(content) > 400:
            messages[i] = {"role": "user", "content": content[:300] + "\n... (older result shortened)"}


def run_tool_loop(router: Router, toolbox, system: str, first_message: str, task_type: str = "code",
                  max_calls: int = MAX_TOOL_CALLS, name: str = "agent") -> LoopResult:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"{first_message}\n\nReply with one JSON object."},
    ]
    result = LoopResult(text="", toolbox=toolbox)
    bad_replies = 0
    max_bad = 3
    while True:
        completion = router.complete(task_type, messages, json_mode=True)
        result.provider, result.model = completion.provider, completion.model
        try:
            action = extract_json(completion.text)
            if not isinstance(action, dict):
                raise ValueError("not an object")
        except ValueError:
            bad_replies += 1
            log.warning("%s: %s/%s reply is not a JSON tool call (%d/%d): %r", name, completion.provider,
                        completion.model, bad_replies, max_bad, completion.text[:300])
            prose = completion.text.strip()
            if (prose and bad_replies >= 2) or bad_replies >= max_bad:
                if prose:
                    result.text = prose  # the model answered in plain words: still an answer
                else:
                    result.text = (f"النموذج {completion.model} لم يُرجع ردًا بالصيغة التي تحتاجها الأدوات (JSON)، "
                                   "فلم أستطع العمل على المتجر أو الملفات. أعد المحاولة، أو اختر نموذجًا محليًا آخر "
                                   "من الإعدادات (مثل qwen2.5:7b أو qwen3:8b)، أو أضف مفتاح Claude أو OpenAI أو Gemini.")
                    result.notes.append("bad_format")
                return result
            if completion.text.strip():
                messages.append({"role": "assistant", "content": completion.text[:2000]})
            first_tool = next((line for line in str(getattr(toolbox, "help", lambda: "")()).splitlines()
                               if line.startswith('{"tool"')), "")
            example = first_tool.split("  ")[0].strip() if first_tool else '{"tool": "..."}'
            messages.append({"role": "user", "content": "Your reply was not a JSON object. Reply with ONE JSON object "
                                                        f"only, for example {example} or {{\"done\": \"...\"}}."})
            continue
        if "done" in action:
            done = action["done"]
            result.text = done if isinstance(done, str) else json.dumps(done, ensure_ascii=False)
            return result
        if result.tool_calls >= max_calls:
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user", "content": 'Tool limit reached. Reply now with {"done": "..."} summarising your work.'})
            final = router.complete(task_type, messages, json_mode=True)
            try:
                data = extract_json(final.text)
                result.text = str(data.get("done") or final.text) if isinstance(data, dict) else final.text
            except ValueError:
                result.text = final.text
            result.notes.append("tool_limit")
            return result
        observation = toolbox.run(action)
        result.tool_calls += 1
        log.info("%s tool %s -> %s", name, action.get("tool"), observation[:80].replace("\n", " "))
        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        messages.append({"role": "user", "content": f"Result of {action.get('tool')}:\n{observation}"})
        _shrink_old_results(messages)
