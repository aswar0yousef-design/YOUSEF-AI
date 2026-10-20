"""The device agent: reads this PC's state and proposes safe actions the user approves."""

from __future__ import annotations

from ..integrations.device_tools import DeviceToolbox
from ..models import Router
from .multitool import MultiToolbox
from .toolloop import LoopResult, run_tool_loop

DEVICE_MAX_CALLS = 8

DEVICE_PROMPT = """You control the user's Windows PC with a short list of safe tools.

Reply with exactly ONE JSON object per turn, using one of these forms:
{tools}

Rules:
- Read state with system_info, running_apps or list_apps when the request needs it; answer questions about the PC from what they return.
- For actions, propose exactly what was asked (open a program, a page, a folder or a document, volume, media, close a program, lock). Each one runs only after the user approves; never claim it is done.
- Programs must be in the allowed list (list_apps). If one is missing, say the user can add it in Settings, then Device control.
- You cannot run commands, delete or move files, install software, shut down, or type into programs. Say so plainly when asked, and suggest the nearest safe action.
- When you are done, reply with "done": a short answer in the user's language saying what you proposed or found.
- You have at most {limit} tool calls."""


def build_toolbox(device) -> MultiToolbox:
    return MultiToolbox(DeviceToolbox(device))


def run_device_control(router: Router, toolbox: MultiToolbox, system: str, task_text: str,
                       max_calls: int = DEVICE_MAX_CALLS) -> LoopResult:
    system = system + "\n\n" + DEVICE_PROMPT.format(tools=toolbox.help(), limit=max_calls)
    return run_tool_loop(router, toolbox, system, task_text, task_type="chat", max_calls=max_calls,
                         name="device_control")
