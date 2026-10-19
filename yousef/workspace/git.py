"""Git in a project folder, using the git installed on the PC.

Reads (status, diff) are free. Commit and push only run from approved actions.
Pushing uses the user's own git credentials (Git Credential Manager on Windows),
so no GitHub password ever passes through YOUSEF AI. GIT_TERMINAL_PROMPT=0 makes
a missing credential fail quickly instead of waiting for input forever.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .files import WorkspaceError

OUTPUT_LIMIT = 8000


def _env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    return env


def run(root: Path, *args: str, timeout: float = 30) -> tuple[int, str]:
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashing on Windows
    try:
        proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=_env(), creationflags=flags)
    except FileNotFoundError:
        raise WorkspaceError("git غير مثبّت على هذا الجهاز (https://git-scm.com)") from None
    except subprocess.TimeoutExpired:
        raise WorkspaceError(f"انتهت مهلة git {' '.join(args[:2])}") from None
    out = (proc.stdout + proc.stderr).strip()
    if len(out) > OUTPUT_LIMIT:
        out = out[:OUTPUT_LIMIT] + "\n... (cut)"
    return proc.returncode, out


def is_repo(root: Path) -> bool:
    try:
        code, out = run(root, "rev-parse", "--is-inside-work-tree", timeout=10)
    except WorkspaceError:
        return False
    return code == 0 and out.strip() == "true"


def _ok(root: Path, *args: str, timeout: float = 30) -> str:
    code, out = run(root, *args, timeout=timeout)
    if code != 0:
        raise WorkspaceError(f"git {args[0]}: {out[:500] or 'failed'}")
    return out


def status(root: Path) -> str:
    return _ok(root, "status", "--short", "--branch") or "(clean)"


def diff(root: Path, path: str | None = None) -> str:
    args = ["diff", "--stat", "--patch", "HEAD"]
    if path:
        args += ["--", path]
    code, out = run(root, *args)
    if code != 0:  # no commits yet
        code, out = run(root, "diff", "--patch", *(["--", path] if path else []))
    return out or "(no uncommitted changes)"


def current_branch(root: Path) -> str:
    return _ok(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def remote_url(root: Path, name: str = "origin") -> str | None:
    code, out = run(root, "remote", "get-url", name, timeout=10)
    return out.strip() if code == 0 and out.strip() else None


_GITHUB = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.IGNORECASE)


def parse_github(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    m = _GITHUB.search(url.strip())
    return (m.group(1), m.group(2)) if m else None


_BRANCH = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/\-]{1,80}(?<!/)(?<!\.lock)$")


def valid_branch(name: str) -> bool:
    return bool(_BRANCH.match(name or "")) and not name.startswith("-")


def commit(root: Path, message: str, files: list[str] | None, branch: str | None = None) -> str:
    """Stage `files` (or everything when files is None) and commit, optionally on a new branch."""
    lines = []
    if branch:
        if not valid_branch(branch):
            raise WorkspaceError(f"اسم فرع غير صالح: {branch}")
        code, _ = run(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        lines.append(_ok(root, "checkout", branch) if code == 0 else _ok(root, "checkout", "-b", branch))
    if files is None:
        _ok(root, "add", "-A")
    else:
        if not files:
            raise WorkspaceError("لا توجد ملفات لإضافتها إلى commit")
        _ok(root, "add", "--", *files)
    lines.append(_ok(root, "commit", "-m", message))
    head = _ok(root, "rev-parse", "--short", "HEAD").strip()
    return f"commit {head} on {current_branch(root)}"


def push(root: Path) -> str:
    branch = current_branch(root)
    if branch == "HEAD":
        raise WorkspaceError("لا يوجد فرع حالي (detached HEAD)")
    _ok(root, "push", "-u", "origin", branch, timeout=120)
    return f"pushed {branch} to origin"
