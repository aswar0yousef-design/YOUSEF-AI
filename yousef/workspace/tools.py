"""Tools the coder agent calls, one JSON object at a time.

Reads see the agent's own proposed changes (an overlay), so it can make
several edits to the same file. Nothing touches the disk here: write_file and
replace only add to `changes`, which the user reviews as diffs.
"""

from __future__ import annotations

from .files import MAX_WRITE_BYTES, READ_WINDOW, Workspace, WorkspaceError, make_diff

OBSERVATION_LIMIT = 8000

TOOL_HELP = """{"tool": "list_files", "path": ""}                                  list a folder ("" = project root)
{"tool": "read_file", "path": "src/app.py", "start": 1, "end": 200}   read lines of a file
{"tool": "search", "query": "text to find", "glob": "*.py"}            find text in project files (glob optional)
{"tool": "replace", "path": "src/app.py", "old": "exact current text", "new": "new text"}   propose an edit
{"tool": "write_file", "path": "src/new.py", "content": "full file content"}   propose a new file or full rewrite
{"done": "your answer for the user"}                                    finish"""


def _clip(text: str, limit: int = OBSERVATION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (cut at {limit} characters)"


def _crlf(original: str | None) -> bool:
    return bool(original) and original.count("\r\n") >= max(1, original.count("\n") // 2)


class Toolbox:
    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self.changes: dict[str, dict] = {}  # path -> {"old_text", "new_text"}
        self.trace: list[dict] = []

    # ---- overlay ---------------------------------------------------------------
    def current(self, rel: str) -> str | None:
        if rel in self.changes:
            return self.changes[rel]["new_text"]
        return self.ws.read_text(rel)

    def _propose(self, rel: str, new_text: str) -> str:
        if len(new_text.encode("utf-8")) > MAX_WRITE_BYTES:
            raise WorkspaceError("المحتوى أكبر من المسموح لملف واحد")
        if rel in self.changes:
            self.changes[rel]["new_text"] = new_text
        else:
            self.changes[rel] = {"old_text": self.ws.read_text(rel), "new_text": new_text}
        change = self.changes[rel]
        if change["old_text"] == new_text:
            del self.changes[rel]
            return f"No change: {rel} already has this content."
        _, added, removed = make_diff(rel, change["old_text"], new_text)
        kind = "new file" if change["old_text"] is None else "edit"
        return f"Proposed {kind} for {rel} (+{added} -{removed}). It will be written only if the user approves."

    def _path(self, call: dict) -> str:
        raw = str(call.get("path") or "").strip()
        full = self.ws.resolve(raw)
        return "" if full == self.ws.root else self.ws.rel(full)

    # ---- dispatch --------------------------------------------------------------
    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        target = str(call.get("path") or call.get("query") or "")
        try:
            handler = getattr(self, f"tool_{tool}", None)
            if handler is None:
                raise WorkspaceError(f'unknown tool "{tool}". Available tools:\n{TOOL_HELP}')
            result = handler(call)
            self.trace.append({"tool": tool, "target": target[:200], "ok": True})
            return _clip(result)
        except (WorkspaceError, OSError, TypeError, ValueError) as e:
            self.trace.append({"tool": tool, "target": target[:200], "ok": False, "note": str(e)[:200]})
            return f"Error: {e}"

    def tool_list_files(self, call: dict) -> str:
        rel = self._path(call)
        return self.ws.list(rel, depth=2)

    def tool_read_file(self, call: dict) -> str:
        rel = self._path(call)
        if not rel:
            raise WorkspaceError("path is required")
        text = self.current(rel)
        if text is None:
            raise WorkspaceError(f"{rel} does not exist")
        lines = text.splitlines()
        start = max(1, int(call.get("start") or 1))
        end = int(call.get("end") or start + READ_WINDOW - 1)
        end = min(len(lines), end, start + READ_WINDOW - 1)
        body = "\n".join(f"{n}| {lines[n - 1]}" for n in range(start, end + 1)) if lines else "(empty file)"
        note = " (includes your proposed changes)" if rel in self.changes else ""
        more = f"\n... {len(lines) - end} more lines (read with start={end + 1})" if end < len(lines) else ""
        return f"{rel}: lines {start}-{end} of {len(lines)}{note}\n{body}{more}"

    def tool_search(self, call: dict) -> str:
        glob = str(call.get("glob") or "").strip() or None
        hits = self.ws.search(str(call.get("query") or ""), glob)
        if not hits:
            return "No matches."
        return "\n".join(f"{rel}:{line}: {text}" for rel, line, text in hits)

    def tool_replace(self, call: dict) -> str:
        rel = self._path(call)
        old, new = call.get("old"), call.get("new")
        if not rel or not isinstance(old, str) or not isinstance(new, str) or not old:
            raise WorkspaceError('replace needs "path", "old" and "new"')
        text = self.current(rel)
        if text is None:
            raise WorkspaceError(f"{rel} does not exist; use write_file to create it")
        if _crlf(text):
            old, new = old.replace("\r\n", "\n").replace("\n", "\r\n"), new.replace("\r\n", "\n").replace("\n", "\r\n")
        count = text.count(old)
        if count == 0:
            raise WorkspaceError(f'the "old" text was not found in {rel}; read the file and copy it exactly')
        if count > 1 and not call.get("all"):
            raise WorkspaceError(f'the "old" text appears {count} times in {rel}; include more surrounding lines')
        return self._propose(rel, text.replace(old, new))

    def tool_write_file(self, call: dict) -> str:
        rel = self._path(call)
        content = call.get("content")
        if not rel or not isinstance(content, str):
            raise WorkspaceError('write_file needs "path" and "content"')
        full = self.ws.resolve(rel)
        self.ws.check_readable(full)
        if full.is_dir():
            raise WorkspaceError(f"{rel} is a folder")
        original = self.current(rel)
        if _crlf(original):
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")
        return self._propose(rel, content)

    # ---- results ---------------------------------------------------------------
    def change_list(self) -> list[dict]:
        out = []
        for rel, change in self.changes.items():
            diff, added, removed = make_diff(rel, change["old_text"], change["new_text"])
            out.append({"path": rel, "old_text": change["old_text"], "new_text": change["new_text"],
                        "diff": diff, "added": added, "removed": removed})
        return out
