"""Several toolboxes behind one: an agent that works on files, stores and checks at once.

Each call goes to the toolbox that has the tool. They share one trace, and
their proposals are collected the same way as for a single toolbox: file
changes from the project toolbox, and actions (git first, then store changes).
"""

from __future__ import annotations

DONE_LINE = '{"done": "your answer for the user"}                                       finish'


class MultiToolbox:
    def __init__(self, *toolboxes):
        self.parts = [t for t in toolboxes if t is not None]
        self.trace: list[dict] = []
        for part in self.parts:
            part.trace = self.trace

    def get(self, cls):
        return next((p for p in self.parts if isinstance(p, cls)), None)

    def help(self) -> str:
        tools, notes = [], []
        for part in self.parts:
            for line in part.help().splitlines():
                if line.startswith('{"done"') or not line.strip():
                    continue
                (tools if line.startswith("{") else notes).append(line)
        return "\n".join(tools + [DONE_LINE] + ([""] + notes if notes else []))

    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        for part in self.parts:
            if tool and callable(getattr(part, f"tool_{tool}", None)):
                return part.run(call)
        self.trace.append({"tool": tool, "target": "", "ok": False, "note": "unknown tool"})
        return f'Error: unknown tool "{tool}". Available tools:\n{self.help()}'

    def change_list(self) -> list[dict]:
        out = []
        for part in self.parts:
            if hasattr(part, "change_list"):
                out += part.change_list()
        return out

    def action_list(self) -> list[dict]:
        out = []
        for part in self.parts:
            if hasattr(part, "action_list"):
                out += part.action_list()
        return out
