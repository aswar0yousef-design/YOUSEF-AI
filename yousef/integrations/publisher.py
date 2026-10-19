"""Carries out approved posts, and publishes scheduled Instagram posts at their time.

Facebook keeps its own schedule (native scheduling), so a Facebook post set
for later only needs a row here to be listed and cancelled. Instagram's API
has no scheduling: Yousef publishes those posts itself when they fall due, as
long as it is running. A post found more than LATE seconds after its time is
marked "missed" and waits for the user, because a late post may no longer fit
(an offer that ended, a greeting for a day that passed).
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from ..media import MediaError
from ..models.base import ProviderError
from .social import PLATFORM_LABEL

log = logging.getLogger("yousef.social")

LATE = 2 * 3600
TICK = 30


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    return dt.datetime.fromisoformat(iso).timestamp()


class SocialPublisher:
    def __init__(self, memory, accounts, media, late: int = LATE, clock=time.time):
        self.memory = memory
        self.accounts = accounts
        self.media = media
        self.late = late
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---- helpers ------------------------------------------------------------------
    def _images(self, refs: list[dict]) -> list[dict]:
        out = []
        for ref in refs or []:
            if ref.get("media"):
                out.append({"path": str(self.media.path(ref["media"]))})
            elif ref.get("url"):
                out.append({"url": ref["url"]})
        return out

    def _page(self, account_id: str):
        entry = self.accounts.find(account_id)
        if entry is None:
            raise ProviderError("الحساب لم يعد مربوطًا")
        return entry, self.accounts.page(entry)

    # ---- approved actions ------------------------------------------------------------
    def publish(self, payload: dict, task_id: int | None = None, action_id: int | None = None) -> tuple[list[str], int]:
        """Publish or schedule one approved post on each of its platforms. Returns (lines, failures)."""
        lines, failures = [], 0
        refs = payload.get("images") or []
        when = payload.get("when")
        at = _ts(when)
        try:
            entry, page = self._page(payload["account_id"])
            images = self._images(refs)
        except (ProviderError, MediaError) as e:
            return [f"- تعذّر النشر على «{payload.get('account')}»: {e}"], len(payload.get("platforms") or [1])
        for platform in payload.get("platforms") or []:
            text = payload.get("text") or ""
            if platform == "instagram" and payload.get("text_instagram"):
                text = payload["text_instagram"]
            label = f"{PLATFORM_LABEL[platform]} «{entry['name']}»"
            row = dict(account_id=entry["id"], account=entry["name"], platform=platform, text=text, images=refs,
                       task_id=task_id, action_id=action_id, link=payload.get("link") if platform == "facebook" else None,
                       scheduled_at=at)
            try:
                if platform == "facebook":
                    out = page.publish_facebook(text, images, payload.get("link"), when)
                    if when:
                        self.memory.add_social_post(status="scheduled", native=True, remote_id=out["id"], **row)
                        lines.append(f"- {label}: جُدول في فيسبوك ({when[:16].replace('T', ' ')})")
                    else:
                        self.memory.add_social_post(status="published", remote_id=out["id"], url=out.get("url"), **row)
                        lines.append(f"- {label}: نُشر {out.get('url') or ''}".rstrip())
                elif when:
                    self.memory.add_social_post(status="scheduled", **row)
                    lines.append(f"- {label}: سينشره يوسف في موعده ({when[:16].replace('T', ' ')}) إن كان شغّالًا")
                else:
                    out = page.publish_instagram(text, images)
                    self.memory.add_social_post(status="published", remote_id=out["id"], url=out.get("url"), **row)
                    lines.append(f"- {label}: نُشر {out.get('url') or ''}".rstrip())
            except (ProviderError, MediaError, OSError) as e:
                failures += 1
                self.memory.add_social_post(status="failed", error=str(e)[:500], **row)
                lines.append(f"- {label}: تعذّر — {e}")
        return lines, failures

    def cancel(self, post_id: int) -> str:
        post = self.memory.get_social_post(post_id)
        if not post or post["status"] not in ("scheduled", "missed"):
            raise ProviderError("لا يوجد منشور مجدول بهذا الرقم")
        if post["native"] and post["remote_id"]:
            _, page = self._page(post["account_id"])
            page.delete_post(post["remote_id"])
        self.memory.update_social_post(post_id, status="cancelled")
        return f"أُلغي منشور {PLATFORM_LABEL[post['platform']]} على «{post['account']}»"

    def publish_now(self, post_id: int) -> dict:
        """The user's own click on a missed (or failed) local post: publish it now."""
        post = self.memory.get_social_post(post_id)
        if not post or post["native"] or post["status"] not in ("missed", "failed", "scheduled"):
            raise ProviderError("هذا المنشور لا يمكن نشره الآن من يوسف")
        if not self.memory.claim_social_post(post_id, post["status"]):
            raise ProviderError("المنشور يُنشر بالفعل")
        return self._publish_row(self.memory.get_social_post(post_id))

    def _publish_row(self, post: dict) -> dict:
        try:
            _, page = self._page(post["account_id"])
            images = self._images(post["images"])
            if post["platform"] == "instagram":
                out = page.publish_instagram(post["text"], images)
            else:
                out = page.publish_facebook(post["text"], images, post.get("link"))
        except (ProviderError, MediaError, OSError) as e:
            self.memory.update_social_post(post["id"], status="failed", error=str(e)[:500])
            log.warning("scheduled post %s failed: %s", post["id"], e)
            return self.memory.get_social_post(post["id"])
        self.memory.update_social_post(post["id"], status="published", remote_id=out["id"], url=out.get("url") or None,
                                       error=None)
        return self.memory.get_social_post(post["id"])

    # ---- the schedule -------------------------------------------------------------------
    def run_due(self, now: float | None = None) -> int:
        """Handle every post whose time has come; returns how many were published."""
        now = self.clock() if now is None else now
        done = 0
        with self._lock:
            for post in self.memory.due_social_posts(now):
                if post["native"]:
                    # Facebook published it at its time; fetch the link once it is live
                    if now - post["scheduled_at"] < 60:
                        continue
                    url = None
                    try:
                        _, page = self._page(post["account_id"])
                        url = page.permalink(post["remote_id"]) or None
                    except ProviderError:
                        pass
                    self.memory.update_social_post(post["id"], status="published", url=url)
                    continue
                if now - post["scheduled_at"] > self.late:
                    self.memory.update_social_post(post["id"], status="missed",
                                                   error="فات موعده ويوسف مغلق؛ انشره الآن أو ألغه")
                    continue
                if self.memory.claim_social_post(post["id"]):
                    if self._publish_row(self.memory.get_social_post(post["id"]))["status"] == "published":
                        done += 1
        return done

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="social-scheduler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_due()
            except Exception:
                log.exception("social scheduler tick failed")
            self._stop.wait(TICK)

    def stop(self) -> None:
        self._stop.set()
