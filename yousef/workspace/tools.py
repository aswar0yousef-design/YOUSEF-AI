"""Tools the coder agent calls, one JSON object at a time.

Reads see the agent's own proposed changes (an overlay), so it can make
several edits to the same file. Nothing touches the disk here: write_file and
replace only add to `changes`, which the user reviews as diffs. Keys written
into files are shown to the model as [[SECRET_n]] markers (see secrets.py).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from . import git
from .files import MAX_WRITE_BYTES, READ_WINDOW, Workspace, WorkspaceError, make_diff
from .secrets import SecretMasker

OBSERVATION_LIMIT = 8000
RUN_TIMEOUT = 300
ACTION_ORDER = ("git_commit", "git_push", "open_pr")

TOOL_HELP = """{"tool": "list_files", "path": ""}                                  list a folder ("" = project root)
{"tool": "read_file", "path": "src/app.py", "start": 1, "end": 200}   read lines of a file
{"tool": "search", "query": "text to find", "glob": "*.py"}            find text in project files (glob optional)
{"tool": "replace", "path": "src/app.py", "old": "exact current text", "new": "new text"}   propose an edit
{"tool": "write_file", "path": "src/new.py", "content": "full file content"}   propose a new file or full rewrite
{"done": "your answer for the user"}                                    finish"""

GIT_HELP = """{"tool": "git_status"}                                                 branch and changed files
{"tool": "git_diff", "path": "src/app.py"}                               uncommitted changes (path optional)
{"tool": "git_commit", "message": "Fix cart total", "branch": "fix/cart-total"}   propose a commit of your edits (branch optional; "all": true commits every change in the folder)"""
PUSH_HELP = """{"tool": "git_push"}                                                   propose pushing the branch to origin (Vercel and CI build pushed commits)"""
PR_HELP = """{"tool": "open_pr", "title": "...", "body": "...", "base": "main"}      propose a GitHub pull request for the branch (base optional)"""
VERCEL_HELP = """{"tool": "vercel_status", "project": "my-site"}                      latest Vercel deployments and build errors (project optional)"""


@dataclass
class Services:
    """What this project may use besides its files."""
    commands: list[str] = field(default_factory=list)  # exact commands the user allowed, e.g. "npm test"
    git: bool = False
    has_remote: bool = False
    github: object | None = None  # integrations.GitHub
    github_repo: tuple[str, str] | None = None
    vercel: object | None = None  # integrations.Vercel
    vercel_project: str | None = None


def run_command(root, command: str, timeout: int = RUN_TIMEOUT) -> str:
    flags = 0x08000000 if os.name == "nt" else 0
    env = dict(os.environ, CI="1", FORCE_COLOR="0", NO_COLOR="1")
    try:
        proc = subprocess.run(command, shell=True, cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env, creationflags=flags)
    except subprocess.TimeoutExpired:
        return f"Stopped after {timeout} seconds."
    out = (proc.stdout + "\n" + proc.stderr).strip()
    if len(out) > 6000:
        out = "... (start cut)\n" + out[-6000:]
    return f"exit code {proc.returncode}\n{out or '(no output)'}"


def _clip(text: str, limit: int = OBSERVATION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (cut at {limit} characters)"


def _crlf(original: str | None) -> bool:
    return bool(original) and original.count("\r\n") >= max(1, original.count("\n") // 2)


class Toolbox:
    def __init__(self, workspace: Workspace, services: Services | None = None):
        self.ws = workspace
        self.services = services or Services()
        self.changes: dict[str, dict] = {}  # path -> {"old_text", "new_text"}
        self.actions: dict[str, dict] = {}  # kind -> payload; run after approval
        self.trace: list[dict] = []
        self.masker = SecretMasker()

    def help(self) -> str:
        """The tools this project can use, for the model's instructions."""
        s = self.services
        parts = [TOOL_HELP.rsplit("\n", 1)[0]]
        if s.git:
            parts.append(GIT_HELP)
            if s.has_remote:
                parts.append(PUSH_HELP)
            if s.github and s.github_repo:
                parts.append(PR_HELP)
        if s.commands:
            allowed = ", ".join(f'"{c}"' for c in s.commands)
            parts.append(f'{{"tool": "run", "command": "npm test"}}                              run one of the allowed commands: {allowed}')
        if s.vercel:
            parts.append(VERCEL_HELP)
        parts.append(TOOL_HELP.rsplit("\n", 1)[1])
        return "\n".join(parts)

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
            result = self.masker.mask(handler(call))
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
        old, new = self.masker.unmask(old), self.masker.unmask(new)
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
        content = self.masker.unmask(content)
        full = self.ws.resolve(rel)
        self.ws.check_readable(full)
        if full.is_dir():
            raise WorkspaceError(f"{rel} is a folder")
        original = self.current(rel)
        if _crlf(original):
            content = content.replace("\r\n", "\n").replace("\n", "\r\n")
        return self._propose(rel, content)

    # ---- git, commands, deployments ---------------------------------------------
    def _need_git(self) -> None:
        if not self.services.git:
            raise WorkspaceError("this folder is not a git repository")

    def tool_git_status(self, call: dict) -> str:
        self._need_git()
        return git.status(self.ws.root)

    def tool_git_diff(self, call: dict) -> str:
        self._need_git()
        rel = self._path(call) if call.get("path") else None
        return git.diff(self.ws.root, rel or None)

    def tool_run(self, call: dict) -> str:
        command = " ".join(str(call.get("command") or "").split())
        allowed = {" ".join(c.split()): c for c in self.services.commands}
        if command not in allowed:
            listed = ", ".join(self.services.commands) or "none"
            raise WorkspaceError(f'"{command}" is not an allowed command for this project. Allowed: {listed}')
        note = "\n(Ran on the files on disk; your proposed edits are not written yet.)" if self.changes else ""
        return run_command(self.ws.root, allowed[command]) + note

    def tool_vercel_status(self, call: dict) -> str:
        if not self.services.vercel:
            raise WorkspaceError("Vercel is not connected (add a Vercel token in settings)")
        project = str(call.get("project") or "").strip() or self.services.vercel_project
        return self.services.vercel.report(project or None)

    # ---- actions that run after approval -----------------------------------------
    def tool_git_commit(self, call: dict) -> str:
        self._need_git()
        message = str(call.get("message") or "").strip()
        if not message or len(message) > 2000:
            raise WorkspaceError("git_commit needs a short message")
        branch = str(call.get("branch") or "").strip() or None
        if branch and not git.valid_branch(branch):
            raise WorkspaceError(f"invalid branch name: {branch}")
        self.actions["git_commit"] = {"message": message, "branch": branch, "all": bool(call.get("all"))}
        where = f" on new branch {branch}" if branch else ""
        return f"Proposed commit{where}: {message.splitlines()[0]}. It runs after the user approves, once your edits are written."

    def tool_git_push(self, call: dict) -> str:
        self._need_git()
        if not self.services.has_remote:
            raise WorkspaceError("this repository has no 'origin' remote")
        self.actions["git_push"] = {}
        return "Proposed push to origin. It runs after the commit, once the user approves."

    def tool_open_pr(self, call: dict) -> str:
        if not (self.services.github and self.services.github_repo):
            raise WorkspaceError("GitHub is not connected for this repository (needs a GitHub token and a github.com origin)")
        title = str(call.get("title") or "").strip()
        if not title:
            raise WorkspaceError("open_pr needs a title")
        self.actions["open_pr"] = {"title": title[:200], "body": str(call.get("body") or "")[:20000],
                                   "base": str(call.get("base") or "").strip() or None}
        return "Proposed pull request. It opens after the push, once the user approves."

    def action_list(self) -> list[dict]:
        return [{"kind": k, "payload": self.actions[k]} for k in ACTION_ORDER if k in self.actions]

    # ---- results ---------------------------------------------------------------
    def change_list(self) -> list[dict]:
        out = []
        for rel, change in self.changes.items():
            diff, added, removed = make_diff(rel, change["old_text"], change["new_text"])
            out.append({"path": rel, "old_text": change["old_text"], "new_text": change["new_text"],
                        "diff": diff, "added": added, "removed": removed})
        return out
