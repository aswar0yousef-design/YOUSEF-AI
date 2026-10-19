"""Tools the social publisher calls: read the accounts, propose posts, propose cancelling them.

Proposals are actions the user approves in the dashboard before anything is
published. Facebook keeps scheduled posts itself; Instagram posts scheduled for
later are published by Yousef at their time, so Yousef must be running then.
"""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse

from ..media import MediaError
from ..models.base import ProviderError
from .meta import FB_SCHEDULE_MAX, FB_SCHEDULE_MIN, IG_CAROUSEL_LIMIT, MetaError, caption_problems
from .social import PLATFORM_LABEL
from .store_tools import StoreError, StoreToolbox, TOOL_HELP as STORE_HELP, parse_when, show_when

OBSERVATION_LIMIT = 8000
MAX_PROPOSALS = 10
FB_TEXT_LIMIT = 63206
LOCAL_SCHEDULE_MAX = dt.timedelta(days=60)
PLATFORMS = ("facebook", "instagram")

TOOL_HELP = """{"tool": "list_accounts"}                                              connected Facebook Pages and their Instagram accounts
{"tool": "recent_posts", "account": "name", "platform": "instagram", "limit": 5}   the account's latest posts, to match its style and not repeat it
{"tool": "list_scheduled"}                                             posts already scheduled (ids for propose_cancel)
{"tool": "list_media"}                                                 pictures the user gave Yousef; use them as "media:<name>"
{"tool": "propose_post", "account": "name", "platforms": ["facebook", "instagram"], "text": "...", "text_instagram": "...", "images": ["media:img-....jpg", "https://..."], "link": "https://...", "when": "2026-10-25 19:00"}   propose a post. "text_instagram" is optional (a different caption for Instagram); "link" is Facebook only; omit "when" to publish right after approval
{"tool": "propose_cancel", "id": 12}                                   propose cancelling a scheduled post
{"done": "your answer for the user"}                                   finish"""
NOTES = """Posting notes: Instagram needs at least one picture (up to 10 make a carousel); its caption takes at most 2200 characters, 30 hashtags and 20 mentions, and links in it are not clickable. Facebook can post text alone, with a link, or with pictures. Pictures are the user's uploads ("media:<name>") or public https links such as the store's product images (get_product shows them). Times are in the user's time zone; Facebook can schedule from 10 minutes to 30 days ahead."""


def _clip(text: str, limit: int = OBSERVATION_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... (cut at {limit} characters)"


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


class StoreReader(StoreToolbox):
    """The store tools that only read: a post can describe products, never change them."""

    tool_propose_product = tool_propose_update = tool_propose_discount = None
    tool_propose_end_discount = tool_propose_bundle = None

    def help(self) -> str:
        keep = [line for line in STORE_HELP.splitlines()
                if line.startswith("{") and '"propose_' not in line and not line.startswith('{"done"')]
        return "\n".join(keep)


class SocialToolbox:
    def __init__(self, accounts, memory, media, default_account: str | None = None, now=None):
        self.accounts = accounts      # integrations.social.SocialAccounts
        self.memory = memory
        self.media = media            # media.MediaLibrary
        self.default_account = default_account
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.trace: list[dict] = []
        self.proposals: list[dict] = []
        self._pages: dict[str, object] = {}

    def help(self) -> str:
        return TOOL_HELP + "\n\n" + NOTES

    def describe_accounts(self) -> str:
        lines = []
        for e in self.accounts.entries():
            ig = f' + Instagram @{e["ig_username"]}' if e.get("ig_id") else " (no Instagram linked)"
            lines.append(f'- "{e["name"]}": Facebook Page{ig}')
        return "\n".join(lines) or "(none)"

    # ---- helpers -----------------------------------------------------------------------
    def _account(self, call: dict):
        ref = str(call.get("account") or "").strip() or self.default_account
        entry = self.accounts.find(ref)
        if entry is None:
            names = ", ".join(f'"{e["name"]}"' for e in self.accounts.entries()) or "none"
            if ref:
                raise MetaError(f'account "{ref}" not found. Connected accounts: {names}')
            raise MetaError(f'several accounts are connected; say which one with "account". Connected: {names}')
        if entry["id"] not in self._pages:
            self._pages[entry["id"]] = self.accounts.page(entry)
        return entry, self._pages[entry["id"]]

    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        target = str(call.get("account") or call.get("id") or "")
        try:
            handler = getattr(self, f"tool_{tool}", None)
            if handler is None:
                raise MetaError(f'unknown tool "{tool}". Available tools:\n{TOOL_HELP}')
            out = handler(call)
            self.trace.append({"tool": tool, "target": target[:200], "ok": True})
            return _clip(out)
        except (ProviderError, MediaError, ValueError, TypeError) as e:
            self.trace.append({"tool": tool, "target": target[:200], "ok": False, "note": str(e)[:200]})
            return f"Error: {e}"

    # ---- reading -----------------------------------------------------------------------
    def tool_list_accounts(self, call: dict) -> str:
        return self.describe_accounts()

    def tool_recent_posts(self, call: dict) -> str:
        entry, page = self._account(call)
        platform = str(call.get("platform") or "facebook").lower()
        limit = max(1, min(int(call.get("limit") or 5), 20))
        if platform == "instagram":
            if not entry.get("ig_id"):
                raise MetaError(f'"{entry["name"]}" has no Instagram account linked')
            posts = page.instagram_posts(limit)
            rows = [f"{p['time'] or ''} | {p['type'] or ''} | likes {p.get('likes')} | comments {p.get('comments')} | "
                    f"{_short(p['text'], 300)}" for p in posts]
        else:
            posts = page.facebook_posts(limit)
            rows = [f"{p['time'] or ''} | {_short(p['text'], 300)}" for p in posts]
        return "\n".join(rows) or "No posts yet."

    def tool_list_scheduled(self, call: dict) -> str:
        rows = []
        for p in self.memory.list_social_posts(("scheduled", "missed"), 30):
            when = show_when(dt.datetime.fromtimestamp(p["scheduled_at"], dt.timezone.utc).isoformat()) if p["scheduled_at"] else "-"
            state = "missed (Yousef was off)" if p["status"] == "missed" else "scheduled"
            rows.append(f"id {p['id']} | {p['account']} | {p['platform']} | {when} | {state} | {_short(p['text'], 100)}")
        return "\n".join(rows) or "Nothing scheduled."

    def tool_list_media(self, call: dict) -> str:
        files = self.media.list()[:40]
        return "\n".join(f"media:{f['name']} | {f['label'] or '-'} | {f['size'] // 1024} KB" for f in files) \
            or "The user has not uploaded any pictures."

    # ---- proposals ------------------------------------------------------------------------
    def _images(self, raw) -> list[dict]:
        if raw in (None, ""):
            return []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            raise MetaError('"images" must be a list')
        out = []
        for item in raw:
            ref = str(item or "").strip()
            if not ref:
                continue
            if ref.startswith("media:"):
                path = self.media.path(ref)
                out.append({"media": path.name, "preview": f"/api/media/{path.name}"})
            else:
                parsed = urllib.parse.urlparse(ref)
                if parsed.scheme != "https" or not parsed.netloc:
                    raise MetaError(f"picture links must be https: {ref[:80]}")
                out.append({"url": ref, "preview": ref})
        if len(out) > IG_CAROUSEL_LIMIT:
            raise MetaError(f"at most {IG_CAROUSEL_LIMIT} pictures in one post")
        return out

    def _when(self, value) -> dt.datetime | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        if re.fullmatch(r"[\d٠-٩]{4}[-/][\d٠-٩]{1,2}[-/][\d٠-٩]{1,2}", text):
            raise MetaError(f'"when" needs a time too, e.g. "{text} 19:00"')
        try:
            return parse_when(text)
        except StoreError as e:
            raise MetaError(str(e)) from None

    def tool_propose_post(self, call: dict) -> str:
        if len(self.proposals) >= MAX_PROPOSALS:
            raise MetaError("too many proposals in one task")
        entry, page = self._account(call)
        available = ["facebook"] + (["instagram"] if entry.get("ig_id") else [])
        platforms = call.get("platforms") or available
        if isinstance(platforms, str):
            platforms = [platforms]
        platforms = [str(p).lower().strip() for p in platforms]
        platforms = [p for p in PLATFORMS if p in platforms]  # fixed order, no repeats
        if not platforms:
            raise MetaError('"platforms" must include "facebook" and/or "instagram"')
        if "instagram" in platforms and "instagram" not in available:
            raise MetaError(f'"{entry["name"]}" has no Instagram account linked; post on Facebook only or link one')
        text = str(call.get("text") or "").strip()
        text_ig = str(call.get("text_instagram") or "").strip() or text
        images = self._images(call.get("images"))
        link = str(call.get("link") or "").strip() or None
        warnings = []
        if link:
            parsed = urllib.parse.urlparse(link)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise MetaError("link must be a full web address")
            if "facebook" not in platforms:
                raise MetaError("links are for Facebook posts; on Instagram write the address in the text or bio")
        if "facebook" in platforms:
            if not text and not images:
                raise MetaError("a Facebook post needs text or pictures")
            if len(text) > FB_TEXT_LIMIT:
                raise MetaError("Facebook text is too long")
        if "instagram" in platforms:
            if not images:
                raise MetaError("an Instagram post needs at least one picture (images)")
            problems = caption_problems(text_ig)
            if problems:
                raise MetaError("; ".join(problems))
            if re.search(r"https?://", text_ig):
                warnings.append("روابط إنستغرام غير قابلة للنقر داخل النص")
            try:
                quota = page.instagram_quota()
            except MetaError:
                quota = None
            if quota and quota[0] >= quota[1]:
                raise MetaError(f"Instagram daily limit reached ({quota[1]} posts in 24 hours)")
            if quota and quota[0] >= quota[1] * 0.9:
                warnings.append(f"اقتربت من حد إنستغرام اليومي ({quota[0]} من {quota[1]})")
        when = self._when(call.get("when"))
        now = self.now()
        if when is not None:
            if when <= now + dt.timedelta(minutes=1):
                raise MetaError('"when" is in the past; omit it to publish right after approval')
            if "facebook" in platforms and when - now < FB_SCHEDULE_MIN:
                raise MetaError("Facebook schedules at least 10 minutes ahead; pick a later time or omit \"when\"")
            if "facebook" in platforms and when - now > FB_SCHEDULE_MAX:
                raise MetaError("Facebook schedules at most 30 days ahead")
            if when - now > LOCAL_SCHEDULE_MAX:
                raise MetaError("schedule at most 60 days ahead")
            if "instagram" in platforms:
                warnings.append("منشور إنستغرام المجدول ينشره يوسف في موعده: أبقِ يوسف شغّالًا والجهاز متصلًا وقتها")
        rows = [{"label": "الحساب", "value": entry["name"] + (f" · @{entry['ig_username']}" if "instagram" in platforms else "")},
                {"label": "المنصات", "value": "، ".join(PLATFORM_LABEL[p] for p in platforms)},
                {"label": "الموعد", "value": show_when(when.isoformat()) if when else "فور الموافقة"},
                {"label": "الصور", "value": str(len(images)) if images else "بدون صور"}]
        if link:
            rows.append({"label": "الرابط", "value": link})
        payload = {"account_id": entry["id"], "account": entry["name"], "ig_username": entry.get("ig_username") or "",
                   "platforms": platforms, "text": text, "text_instagram": text_ig if "instagram" in platforms else "",
                   "images": images, "link": link, "when": when.isoformat() if when else None,
                   "title": _short(text or text_ig, 60), "rows": rows, "warnings": warnings}
        self.proposals.append({"kind": "social_post", "payload": payload})
        timing = f"scheduled for {show_when(when.isoformat())}" if when else "published right after approval"
        return (f"Proposed a post on {', '.join(platforms)} for \"{entry['name']}\", {timing}. "
                "Nothing is published unless the user approves.")

    def tool_propose_cancel(self, call: dict) -> str:
        try:
            post_id = int(call.get("id"))
        except (TypeError, ValueError):
            raise MetaError("propose_cancel needs the id from list_scheduled") from None
        post = self.memory.get_social_post(post_id)
        if not post or post["status"] not in ("scheduled", "missed"):
            raise MetaError(f"no scheduled post with id {post_id}")
        when = show_when(dt.datetime.fromtimestamp(post["scheduled_at"], dt.timezone.utc).isoformat()) if post["scheduled_at"] else ""
        rows = [{"label": "الحساب", "value": post["account"]}, {"label": "المنصة", "value": PLATFORM_LABEL[post["platform"]]},
                {"label": "كان موعده", "value": when}, {"label": "النص", "value": _short(post["text"], 200)},
                {"label": "بعد الموافقة", "value": "يُلغى ولا يُنشر"}]
        self.proposals.append({"kind": "social_cancel", "payload": {
            "post_id": post_id, "account": post["account"], "title": _short(post["text"], 60), "rows": rows,
            "warnings": []}})
        return f"Proposed cancelling scheduled post {post_id}. It is cancelled only if the user approves."

    def action_list(self) -> list[dict]:
        return list(self.proposals)
