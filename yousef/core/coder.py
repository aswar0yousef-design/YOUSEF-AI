"""The coder agent: the shared tool loop with the project-folder toolbox."""

from __future__ import annotations

from ..models import Router
from ..workspace import Services, Toolbox, Workspace
from .toolloop import MAX_TOOL_CALLS, LoopResult, run_tool_loop

CoderResult = LoopResult  # kept for older imports

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
- Keys and passwords written inside files are shown as markers like [[SECRET_1]]. Keep a marker exactly as it is when your edit keeps that value; never guess or write out the real value.
- When you can answer, reply with "done": explain what you found and which files you changed and why, in the user's language.
- You have at most {limit} tool calls."""


def run_coder(router: Router, workspace: Workspace, system: str, task_text: str,
              max_calls: int = MAX_TOOL_CALLS, services: Services | None = None) -> LoopResult:
    toolbox = Toolbox(workspace, services)
    system = system + "\n\n" + TOOL_PROMPT.format(name=workspace.name, tools=toolbox.help(), limit=max_calls)
    overview = toolbox.run({"tool": "list_files", "path": ""})
    toolbox.trace.clear()
    first = f"{task_text}\n\nProject files (top levels):\n{overview}"
    return run_tool_loop(router, toolbox, system, first, task_type="code", max_calls=max_calls, name="coder")
