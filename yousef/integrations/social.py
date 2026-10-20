"""Connected social accounts: Facebook Pages (with their Instagram accounts) and their tokens.

settings["social"] keeps [{"id": page id, "name", "ig_id", "ig_username"}]; each
Page token lives encrypted in keys.dat as "social:<page id>".
"""

from __future__ import annotations

from .meta import GRAPH, VERSION, MetaError, Page, connect

PLATFORM_LABEL = {"facebook": "فيسبوك", "instagram": "إنستغرام"}


def secret_name(page_id: str) -> str:
    return f"social:{page_id}"


class SocialAccounts:
    def __init__(self, settings, keystore, api: str = GRAPH, version: str = VERSION, sleep=None):
        self.last_ads: list[dict] = []
        self.settings = settings
        self.keystore = keystore
        self.api = api
        self.version = version
        self.sleep = sleep

    def entries(self) -> list[dict]:
        return self.settings.get()["social"]

    def public(self) -> list[dict]:
        out = []
        for e in self.entries():
            platforms = ["facebook"] + (["instagram"] if e.get("ig_id") else [])
            out.append(dict(e, platforms=platforms, connected=self.keystore.has(secret_name(e["id"]))))
        return out

    def find(self, ref: str | None) -> dict | None:
        """By Page id, Page name or Instagram username; with one account, an empty ref means it."""
        accounts = self.entries()
        if not ref:
            return accounts[0] if len(accounts) == 1 else None
        ref_l = str(ref).strip().lower().lstrip("@")
        for e in accounts:
            if ref_l in (e["id"], e["name"].lower(), (e.get("ig_username") or "").lower()):
                return e
        return None

    def page(self, entry: dict) -> Page:
        token = self.keystore.get(secret_name(entry["id"]))
        if not token:
            raise MetaError(f"رمز صفحة «{entry['name']}» غير محفوظ على هذا الجهاز؛ أعد ربط الحساب")
        extra = {"sleep": self.sleep} if self.sleep else {}
        return Page(entry, token, self.api, self.version, **extra)

    def connect(self, token: str, ads=None) -> list[dict]:
        """Find the Pages this token manages, test each, and save them (existing ones are refreshed).
        With `ads` (AdAccounts), a user token that can also read ad accounts connects those too."""
        pages = connect(token, self.api, self.version)[:20]
        if ads is not None and not any(p["token"] == token for p in pages):  # a user token, not a Page token
            try:
                self.last_ads = ads.connect(token)
            except MetaError:
                self.last_ads = []  # no ads permissions on this token: ads stay optional
        current = {e["id"]: e for e in self.entries()}
        for p in pages:
            self.keystore.set(secret_name(p["id"]), p["token"])
            current[p["id"]] = {"id": p["id"], "name": p["name"][:100], "ig_id": p["ig_id"],
                                "ig_username": p["ig_username"][:60]}
        self.settings.update({"social": list(current.values())[:20]})
        return [current[p["id"]] for p in pages]

    def test(self, page_id: str) -> dict:
        entry = self.find(page_id)
        if not entry:
            raise MetaError("الحساب غير موجود")
        return self.page(entry).info()

    def remove(self, page_id: str) -> bool:
        current = self.entries()
        kept = [e for e in current if e["id"] != page_id]
        if len(kept) == len(current):
            return False
        self.settings.update({"social": kept})
        self.keystore.delete(secret_name(page_id))
        return True
