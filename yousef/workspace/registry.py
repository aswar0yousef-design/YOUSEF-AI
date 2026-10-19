"""The list of project folders the user allowed (kept in settings.json)."""

from __future__ import annotations

import os
from pathlib import Path

from .files import Workspace, WorkspaceError


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def list_workspaces(settings) -> list[dict]:
    return [dict(w, exists=Path(w["path"]).is_dir()) for w in settings.get()["workspaces"]]


def find(settings, ref: str | None) -> dict | None:
    """Look up an allowed folder by name or by path (any folder inside it counts too)."""
    if not ref:
        return None
    ref = str(ref).strip()
    folders = settings.get()["workspaces"]
    for w in folders:
        if w["name"] == ref:
            return w
    try:
        target = os.path.normcase(os.path.normpath(str(Path(ref).expanduser().resolve())))
    except OSError:
        return None
    best = None
    for w in folders:
        root = os.path.normcase(os.path.normpath(str(Path(w["path"]).resolve())))
        if target == root or target.startswith(root + os.sep):
            if best is None or len(w["path"]) > len(best["path"]):
                best = w
    return best


def add(settings, path: str, name: str | None = None) -> dict:
    path = str(path or "").strip().strip('"')
    if not path:
        raise WorkspaceError("أدخل مسار المجلد")
    full = Path(path).expanduser()
    if not full.is_absolute():
        raise WorkspaceError("اكتب المسار كاملًا، مثل C:\\Users\\you\\projects\\my-site")
    if not full.is_dir():
        raise WorkspaceError(f"المجلد غير موجود: {full}")
    full = full.resolve()
    if full == Path(full.anchor):
        raise WorkspaceError("لا يمكن السماح بقرص كامل؛ اختر مجلد المشروع نفسه")
    if full == Path.home().resolve():
        raise WorkspaceError("لا يمكن السماح بمجلد المستخدم كاملًا؛ اختر مجلد المشروع نفسه")
    current = settings.get()["workspaces"]
    for w in current:
        if _same(w["path"], str(full)):
            return w
    base = (name or full.name or "project").strip()[:60]
    taken = {w["name"].lower() for w in current}
    candidate, n = base, 2
    while candidate.lower() in taken:
        candidate = f"{base}-{n}"
        n += 1
    entry = {"name": candidate, "path": str(full)}
    settings.update({"workspaces": current + [entry]})
    return entry


def remove(settings, name: str) -> bool:
    current = settings.get()["workspaces"]
    kept = [w for w in current if w["name"] != name]
    if len(kept) == len(current):
        return False
    settings.update({"workspaces": kept})
    return True


def open_workspace(settings, name: str) -> Workspace:
    entry = find(settings, name)
    if entry is None:
        raise WorkspaceError(f"المشروع «{name}» غير مسموح به")
    return Workspace(entry["name"], entry["path"])
