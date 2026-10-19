"""Pictures the user gives Yousef for posts, kept in the data folder.

Files are named by their content hash, so the same picture uploaded twice is
stored once, and a name can never point outside the folder.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_IMAGE_BYTES = 8_000_000
MAX_FILES = 500
NAME = re.compile(r"img-[0-9a-f]{16}\.(?:jpg|png)")
CONTENT_TYPE = {"jpg": "image/jpeg", "png": "image/png"}


class MediaError(ValueError):
    pass


def kind_of(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    raise MediaError("الصورة يجب أن تكون JPEG أو PNG")


class MediaLibrary:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, original: str = "") -> dict:
        if not data:
            raise MediaError("الملف فارغ")
        if len(data) > MAX_IMAGE_BYTES:
            raise MediaError("الصورة أكبر من 8MB")
        ext = kind_of(data)
        name = f"img-{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
        path = self.root / name
        if not path.exists():
            if len(self.list()) >= MAX_FILES:
                raise MediaError(f"مكتبة الصور ممتلئة ({MAX_FILES} صورة)؛ احذف بعضها")
            path.write_bytes(data)
            label = re.sub(r"[^\w\-. ]", "", original or "")[:80]
            if label:
                (self.root / f"{name}.label").write_text(label, encoding="utf-8")
        return self.describe(path)

    def describe(self, path: Path) -> dict:
        label_file = path.with_name(path.name + ".label")
        label = label_file.read_text(encoding="utf-8") if label_file.exists() else ""
        st = path.stat()
        return {"name": path.name, "label": label, "size": st.st_size, "added": st.st_mtime,
                "url": f"/api/media/{path.name}"}

    def list(self) -> list[dict]:
        files = [p for p in self.root.iterdir() if NAME.fullmatch(p.name)]
        return [self.describe(p) for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]

    def path(self, name: str) -> Path:
        name = str(name or "").removeprefix("media:")
        if not NAME.fullmatch(name):
            raise MediaError(f"اسم صورة غير صالح: {name}")
        path = self.root / name
        if not path.exists():
            raise MediaError(f"الصورة {name} غير موجودة في مكتبة يوسف")
        return path

    def read(self, name: str) -> tuple[bytes, str]:
        path = self.path(name)
        return path.read_bytes(), CONTENT_TYPE[path.suffix[1:]]

    def delete(self, name: str) -> bool:
        try:
            path = self.path(name)
        except MediaError:
            return False
        path.unlink()
        label = path.with_name(path.name + ".label")
        if label.exists():
            label.unlink()
        return True


