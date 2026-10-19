"""Safe access to a project folder the user allowed.

Every path is resolved inside the folder (no "..", no symlink escapes, no
.git internals). Secret files are never read: their contents could otherwise
be sent to a cloud model. Writes happen only through apply(), which the
orchestrator calls after the user approves the diff.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "out",
    ".next", ".nuxt", ".svelte-kit", ".cache", ".parcel-cache", ".turbo", ".idea", ".vs", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "target", "vendor", ".vercel", ".gradle",
}
SECRET_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.crt", "*.cer", "*.keystore", "*.jks",
    "*.kdbx", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "credentials", "credentials.json",
    "credentials*.json", "secrets.json", "secrets.yml", "secrets.yaml", "secrets.toml", "*.secret",
    "client_secret*.json", ".npmrc", ".pypirc", ".netrc", ".git-credentials", "wp-config.php", "auth.json",
    "service-account*.json",
)
ALLOWED_ENV_TEMPLATES = (".env.example", ".env.sample", ".env.template")

MAX_FILE_BYTES = 1_000_000   # larger files are not read or searched
MAX_WRITE_BYTES = 400_000
READ_WINDOW = 400            # lines returned by one read
MAX_LIST_ENTRIES = 400
MAX_SEARCH_FILES = 6000


class WorkspaceError(ValueError):
    pass


def _norm(path: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def is_secret(name: str) -> bool:
    lower = name.lower()
    if lower in ALLOWED_ENV_TEMPLATES:
        return False
    return any(fnmatch.fnmatch(lower, pattern) for pattern in SECRET_PATTERNS)


def decode(data: bytes) -> str | None:
    """Text content, or None for binary / unknown encodings."""
    if b"\x00" in data[:8192]:
        return None
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def make_diff(path: str, old: str | None, new: str) -> tuple[str, int, int]:
    old_lines = (old or "").splitlines()
    new_lines = new.splitlines()
    lines = list(difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{path}" if old is not None else "/dev/null",
                                      tofile=f"b/{path}", lineterm="", n=3))
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    return "\n".join(lines), added, removed


@dataclass
class ApplyResult:
    path: str
    status: str  # applied | conflict | error
    note: str = ""


class Workspace:
    def __init__(self, name: str, root: str | Path):
        self.name = name
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"المجلد غير موجود: {self.root}")

    # ---- paths ----------------------------------------------------------------
    def resolve(self, rel: str) -> Path:
        rel = (rel or "").strip().replace("\\", "/")
        candidate = Path(rel)
        if candidate.is_absolute() or (len(rel) > 1 and rel[1] == ":"):
            full = candidate.resolve()
        else:
            full = (self.root / rel.lstrip("/")).resolve()
        root_n, full_n = _norm(self.root), _norm(full)
        if full_n != root_n and not full_n.startswith(root_n + os.sep):
            raise WorkspaceError("هذا المسار خارج مجلد المشروع")
        if ".git" in PurePosixPath(self.rel(full)).parts:
            raise WorkspaceError("مجلد .git غير متاح")
        return full

    def rel(self, full: Path) -> str:
        try:
            return full.relative_to(self.root).as_posix() or "."
        except ValueError:
            return Path(os.path.relpath(full, self.root)).as_posix()

    def check_readable(self, full: Path) -> None:
        if is_secret(full.name):
            raise WorkspaceError(f"{full.name} ملف أسرار، ولا يُقرأ حمايةً لمفاتيحك")

    # ---- reading --------------------------------------------------------------
    def read_text(self, rel: str) -> str | None:
        """Current file text, or None if it does not exist."""
        full = self.resolve(rel)
        self.check_readable(full)
        if not full.exists():
            return None
        if full.is_dir():
            raise WorkspaceError(f"{rel} مجلد وليس ملفًا")
        if full.stat().st_size > MAX_FILE_BYTES:
            raise WorkspaceError(f"{rel} أكبر من {MAX_FILE_BYTES // 1000}KB")
        text = decode(full.read_bytes())
        if text is None:
            raise WorkspaceError(f"{rel} ملف ثنائي أو بترميز غير مدعوم")
        return text

    def list(self, rel: str = "", depth: int = 2) -> str:
        start = self.resolve(rel)
        if not start.is_dir():
            raise WorkspaceError(f"{rel or '.'} ليس مجلدًا")
        lines: list[str] = []
        truncated = False

        def walk(folder: Path, level: int) -> None:
            nonlocal truncated
            try:
                entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return
            for entry in entries:
                if len(lines) >= MAX_LIST_ENTRIES:
                    truncated = True
                    return
                if entry.is_dir():
                    if entry.name in IGNORED_DIRS or entry.is_symlink():
                        continue
                    lines.append("  " * level + entry.name + "/")
                    if level + 1 < depth:
                        walk(entry, level + 1)
                else:
                    mark = "  (secret, not readable)" if is_secret(entry.name) else ""
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    lines.append("  " * level + f"{entry.name}  [{_size(size)}]{mark}")

        walk(start, 0)
        if truncated:
            lines.append(f"... (more than {MAX_LIST_ENTRIES} entries; list a subfolder)")
        return "\n".join(lines) or "(empty folder)"

    def iter_files(self, glob: str | None = None):
        count = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not os.path.islink(os.path.join(dirpath, d))]
            for filename in filenames:
                if is_secret(filename):
                    continue
                full = Path(dirpath) / filename
                rel = self.rel(full)
                if glob and not (fnmatch.fnmatch(filename.lower(), glob.lower()) or fnmatch.fnmatch(rel.lower(), glob.lower())):
                    continue
                count += 1
                if count > MAX_SEARCH_FILES:
                    return
                yield full, rel

    def search(self, query: str, glob: str | None = None, limit: int = 60) -> list[tuple[str, int, str]]:
        query = (query or "").strip()
        if not query:
            raise WorkspaceError("نص البحث فارغ")
        needle = query.lower()
        hits = []
        for full, rel in self.iter_files(glob):
            try:
                if full.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = decode(full.read_bytes())
            except OSError:
                continue
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    hits.append((rel, number, line.strip()[:240]))
                    if len(hits) >= limit:
                        return hits
        return hits

    # ---- writing (after approval only) ----------------------------------------
    def apply(self, changes: list[dict]) -> list[ApplyResult]:
        """Write approved changes. A file that changed since the proposal is left alone."""
        results = []
        for change in changes:
            rel = change["path"]
            try:
                full = self.resolve(rel)
                self.check_readable(full)
                current, bom = None, b""
                if full.exists():
                    raw = full.read_bytes()
                    current = decode(raw)
                    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
                if current != change["old_text"]:
                    results.append(ApplyResult(rel, "conflict", "تغيّر الملف بعد اقتراح التعديل، فلم يُكتب"))
                    continue
                full.parent.mkdir(parents=True, exist_ok=True)
                tmp = full.with_name(full.name + ".yousef-tmp")
                tmp.write_bytes(bom + change["new_text"].encode("utf-8"))
                os.replace(tmp, full)
                results.append(ApplyResult(rel, "applied"))
            except (OSError, WorkspaceError) as e:
                results.append(ApplyResult(rel, "error", str(e)[:300]))
        return results


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"
