"""Using Yousef from the phone on the home network: pairing and sessions.

The phone listener is off until the user turns it on. A phone joins with a
6-digit code shown on the PC (valid 10 minutes, 5 wrong tries end it), and
then carries a session cookie. Only a hash of each session token is kept, so
the file never holds anything a thief could replay. Every device can be
removed from the PC at any time.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path

CODE_SECONDS = 600
CODE_TRIES = 5
IP_TRIES = 10            # pairing attempts per address
IP_WINDOW = 600          # ... in this many seconds
MAX_DEVICES = 10
COOKIE = "yousef_session"
SEEN_EVERY = 60          # write last_seen at most once a minute per device


class RemoteError(ValueError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def lan_addresses() -> list[str]:
    """This PC's addresses on the home network, for the phone to open."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; it picks the outgoing interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for addr in socket.gethostbyname_ex(socket.gethostname())[2]:
            if addr not in found:
                found.append(addr)
    except OSError:
        pass
    return [a for a in found if not a.startswith("127.")]


class RemoteAccess:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()
        self._code: tuple[str, float, int] | None = None  # (code, expires, wrong tries)
        self._ip_tries: dict[str, list[float]] = {}
        self._devices = self._load()

    # ---- storage ------------------------------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [d for d in data.get("devices", []) if isinstance(d, dict) and len(d.get("hash", "")) == 64]
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": self._devices}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- pairing ------------------------------------------------------------------------
    def new_code(self) -> dict:
        with self._lock:
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._code = (code, self.clock() + CODE_SECONDS, 0)
            return {"code": code, "expires_in": CODE_SECONDS}

    def code_status(self) -> dict | None:
        with self._lock:
            if not self._code or self._code[1] <= self.clock():
                return None
            return {"code": self._code[0], "expires_in": int(self._code[1] - self.clock())}

    def pair(self, code: str, name: str, ip: str = "") -> str:
        """A right code -> a new session token for this phone (shown to it once, in a cookie)."""
        now = self.clock()
        with self._lock:
            tries = [t for t in self._ip_tries.get(ip, []) if t > now - IP_WINDOW]
            if len(tries) >= IP_TRIES:
                raise RemoteError("محاولات كثيرة؛ انتظر عشر دقائق ثم حاول")
            tries.append(now)
            self._ip_tries[ip] = tries
            if not self._code or self._code[1] <= now:
                raise RemoteError("لا يوجد رمز اقتران فعّال؛ اطلب رمزًا جديدًا من يوسف على الكمبيوتر")
            expected, expires, wrong = self._code
            if not hmac.compare_digest(str(code or "").strip(), expected):
                wrong += 1
                self._code = None if wrong >= CODE_TRIES else (expected, expires, wrong)
                raise RemoteError("الرمز غير صحيح" + ("؛ انتهى هذا الرمز، اطلب رمزًا جديدًا" if wrong >= CODE_TRIES else ""))
            self._code = None  # one phone per code
            if len(self._devices) >= MAX_DEVICES:
                raise RemoteError(f"وصلت إلى {MAX_DEVICES} أجهزة؛ أزل جهازًا قديمًا من الإعدادات")
            token = secrets.token_urlsafe(32)
            digest = _hash(token)
            self._devices.append({"id": digest[:12], "hash": digest, "name": (str(name or "").strip() or "جوال")[:40],
                                  "created": now, "last_seen": now, "ip": ip[:45]})
            self._save()
            return token

    # ---- sessions -----------------------------------------------------------------------
    def check(self, token: str | None, ip: str = "") -> dict | None:
        if not token or len(token) > 100:
            return None
        digest = _hash(token)
        with self._lock:
            for d in self._devices:
                if hmac.compare_digest(d["hash"], digest):
                    now = self.clock()
                    if now - d.get("last_seen", 0) > SEEN_EVERY or d.get("ip") != ip:
                        d["last_seen"], d["ip"] = now, ip[:45]
                        self._save()
                    return d
        return None

    def devices(self) -> list[dict]:
        with self._lock:
            return [{k: d[k] for k in ("id", "name", "created", "last_seen", "ip")} for d in self._devices]

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            before = len(self._devices)
            self._devices = [d for d in self._devices if d["id"] != device_id]
            if len(self._devices) != before:
                self._save()
                return True
            return False


def cookie_token(header: str | None) -> str | None:
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == COOKIE and value:
            return value
    return None
