"""Local API key storage.

On Windows keys are encrypted with DPAPI (CryptProtectData), which ties them to
the current Windows user account: another user on the same PC, or anyone who
copies the file to another machine, cannot decrypt them. Keys never leave the
device and are never sent back to the dashboard.

On other systems (used for development and tests) a base64 fallback is used and
the file is restricted to the owner. That fallback is not encryption.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from pathlib import Path

from .config import KEY_NAMES

_ENTROPY = b"YousefAI-keystore-v1"


class KeyStoreError(ValueError):
    pass


class PlainCipher:
    """Development fallback for non-Windows systems. Not encryption."""

    name = "plain"
    secure = False

    def encrypt(self, data: bytes) -> bytes:
        return data

    def decrypt(self, data: bytes) -> bytes:
        return data


class DPAPICipher:
    """Windows Data Protection API, scoped to the current user."""

    name = "dpapi"
    secure = True

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        self._ctypes = ctypes
        self._blob_type = DATA_BLOB
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_p = ctypes.POINTER(DATA_BLOB)
        crypt32.CryptProtectData.argtypes = [
            blob_p, wintypes.LPCWSTR, blob_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_p,
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [
            blob_p, ctypes.c_void_p, blob_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob_p,
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        self._crypt32 = crypt32
        self._kernel32 = kernel32

    def _make_blob(self, data: bytes):
        ctypes = self._ctypes
        buffer = ctypes.create_string_buffer(data, len(data))
        blob = self._blob_type(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
        return blob, buffer  # keep buffer alive while blob is used

    def _call(self, func, data: bytes, *, protect: bool) -> bytes:
        ctypes = self._ctypes
        blob_in, _buf_in = self._make_blob(data)
        entropy, _buf_e = self._make_blob(_ENTROPY)
        blob_out = self._blob_type()
        flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
        description = "YOUSEF AI" if protect else None
        ok = func(ctypes.byref(blob_in), description, ctypes.byref(entropy), None, None, flags, ctypes.byref(blob_out))
        if not ok:
            raise KeyStoreError("Windows could not protect or read the key (DPAPI error).")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            self._kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))

    def encrypt(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptProtectData, data, protect=True)

    def decrypt(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptUnprotectData, data, protect=False)


def default_cipher():
    if sys.platform == "win32":
        return DPAPICipher()
    return PlainCipher()


def validate_key(key: str) -> str:
    if not isinstance(key, str):
        raise KeyStoreError("key must be text")
    key = key.strip()
    if len(key) < 8 or len(key) > 512:
        raise KeyStoreError("key length looks wrong")
    if any(ch.isspace() for ch in key):
        raise KeyStoreError("key must not contain spaces or line breaks")
    return key


class KeyStore:
    def __init__(self, path: Path, cipher=None):
        self.path = path
        self.cipher = cipher or default_cipher()
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.path)
        if not self.cipher.secure:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    @staticmethod
    def _check_provider(provider: str) -> None:
        if provider not in KEY_NAMES:
            raise KeyStoreError(f"unknown provider: {provider}")

    def set(self, provider: str, key: str) -> None:
        self._check_provider(provider)
        key = validate_key(key)
        with self._lock:
            data = self._load()
            data[provider] = base64.b64encode(self.cipher.encrypt(key.encode("utf-8"))).decode("ascii")
            data["_cipher"] = self.cipher.name
            self._save(data)

    def get(self, provider: str) -> str | None:
        self._check_provider(provider)
        with self._lock:
            data = self._load()
        token = data.get(provider)
        if not token:
            return None
        try:
            return self.cipher.decrypt(base64.b64decode(token)).decode("utf-8")
        except Exception:
            # Unreadable (e.g. file copied from another user/PC): treat as missing.
            return None

    def has(self, provider: str) -> bool:
        return self.get(provider) is not None

    def delete(self, provider: str) -> None:
        self._check_provider(provider)
        with self._lock:
            data = self._load()
            if data.pop(provider, None) is not None:
                self._save(data)

    def status(self) -> dict:
        return {p: self.has(p) for p in KEY_NAMES}

    @property
    def secure(self) -> bool:
        return bool(self.cipher.secure)
