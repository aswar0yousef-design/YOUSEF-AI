"""Facebook Pages and Instagram business accounts through Meta's official Graph API.

One long-lived user token (or a Page token) is enough: Yousef reads the Pages
it manages, each with its own Page token and its linked Instagram account.
Nothing here publishes on its own; the orchestrator calls publish_* only after
the user approves a post.

Facebook: text, link, one or several photos, and native scheduling (10 minutes
to 30 days ahead). Instagram: one photo or a carousel of up to 10, as JPEG from
a public link. Local or non-JPEG pictures are first uploaded to the Page as an
unpublished photo, whose JPEG on Facebook's servers Instagram can fetch; that
copy is deleted after publishing.
"""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..models.base import DEFAULT_OPENER, ProviderError

GRAPH = "https://graph.facebook.com"
VERSION = "v26.0"
SCOPES = ("pages_show_list", "pages_read_engagement", "pages_manage_posts", "instagram_basic",
          "instagram_content_publish", "business_management")
IG_CAPTION_LIMIT = 2200
IG_HASHTAG_LIMIT = 30
IG_MENTION_LIMIT = 20
IG_CAROUSEL_LIMIT = 10
FB_SCHEDULE_MIN = dt.timedelta(minutes=10)
FB_SCHEDULE_MAX = dt.timedelta(days=30)
MAX_IMAGE_BYTES = 8_000_000
CONTAINER_WAITS = (0, 2, 3, 5, 10, 20, 30, 60, 60, 60)  # seconds; images are usually ready at once


class MetaError(ProviderError):
    pass


def explain(message: str) -> str:
    """Graph API errors in plain Arabic, with the original text kept for the log."""
    text = str(message)
    hints = [
        (("Error validating access token", "Session has expired", "code 190", "OAuthException"),
         "رمز Meta غير صالح أو انتهت صلاحيته؛ أعد ربط الحساب من الإعدادات"),
        (("permission", "(#10)", "(#200)"), "الرمز لا يملك الصلاحية المطلوبة؛ تأكد من صلاحيات التطبيق (pages_manage_posts، instagram_content_publish)"),
        (("Page Publishing Authorization", "publishing authorization"), "فيسبوك يطلب «تفويض النشر للصفحة» (PPA) من إعدادات الصفحة قبل النشر"),
        (("Media download has failed", "9004", "Only photo or video can be accepted"), "تعذّر على إنستغرام جلب الصورة؛ يجب أن تكون JPEG على رابط عام"),
        (("aspect ratio",), "نسبة أبعاد الصورة غير مقبولة في إنستغرام (المسموح بين 4:5 و1.91:1)"),
        (("scheduled publish time", "scheduled_publish_time"), "موعد الجدولة في فيسبوك يجب أن يكون بعد 10 دقائق على الأقل وخلال 30 يومًا"),
        (("Application request limit", "(#4)", "(#17)", "(#32)"), "تجاوزت حد طلبات Meta مؤقتًا؛ حاول بعد قليل"),
    ]
    for needles, hint in hints:
        if any(n.lower() in text.lower() for n in needles):
            return f"{hint} ({text[:200]})"
    return text[:300]


def _multipart(fields: dict, file_field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = "----yousef" + secrets.token_hex(12)
    out = bytearray()
    for key, value in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    safe = filename.replace('"', "")
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{safe}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode("utf-8")
    out += data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


class Graph:
    """Minimal Graph API client; the token goes in the query string as Meta documents."""

    def __init__(self, token: str, api: str = GRAPH, version: str = VERSION, opener=None):
        if not token or not token.strip():
            raise MetaError("رمز Meta فارغ")
        self.token = token.strip()
        self.base = f"{api.rstrip('/')}/{version}"
        self.opener = opener or DEFAULT_OPENER

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
                 raw: bytes | None = None, ctype: str | None = None, timeout: float = 60):
        query = dict(params or {})
        query["access_token"] = self.token
        url = f"{self.base}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"
        data, headers = None, {"Accept": "application/json"}
        if raw is not None:
            data, headers["Content-Type"] = raw, ctype
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                err = json.loads(e.read().decode("utf-8", "replace")).get("error", {})
                detail = f"{err.get('message', '')} (code {err.get('code')})"
            except Exception:
                pass
            raise MetaError(explain(f"HTTP {e.code}: {detail}")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MetaError(f"تعذّر الاتصال بـ Meta: {getattr(e, 'reason', e)}") from None
        try:
            return json.loads(text) if text else {}
        except ValueError:
            raise MetaError("ردّ غير مفهوم من Meta") from None

    def get(self, path: str, **params):
        return self._request("GET", path, params)

    def post(self, path: str, body: dict | None = None, **params):
        return self._request("POST", path, params, body=body or {})

    def upload(self, path: str, fields: dict, filename: str, data: bytes):
        raw, ctype = _multipart({k: v for k, v in fields.items() if v is not None}, "source", filename, data)
        return self._request("POST", path, raw=raw, ctype=ctype, timeout=120)

    def delete(self, path: str):
        return self._request("DELETE", path)


def connect(token: str, api: str = GRAPH, version: str = VERSION) -> list[dict]:
    """The Pages this token manages: [{"id", "name", "token", "ig_id", "ig_username"}].

    Works with a user token (lists its Pages) or with a single Page token.
    """
    g = Graph(token, api, version)
    fields = "id,name,access_token,tasks,instagram_business_account{id,username}"
    try:
        data = g.get("me/accounts", fields=fields, limit=100)
        pages = data.get("data") or []
    except MetaError as e:
        if "accounts" not in str(e) and "nonexisting field" not in str(e):
            raise
        # a Page token: /me is the Page itself
        me = g.get("me", fields="id,name,instagram_business_account{id,username}")
        pages = [dict(me, access_token=token)]
    out = []
    for p in pages:
        tasks = p.get("tasks")
        if tasks is not None and "CREATE_CONTENT" not in tasks:
            continue  # this user cannot post on that Page
        ig = p.get("instagram_business_account") or {}
        out.append({"id": str(p["id"]), "name": p.get("name") or str(p["id"]), "token": p.get("access_token") or "",
                    "ig_id": str(ig.get("id") or ""), "ig_username": ig.get("username") or ""})
    if not out:
        raise MetaError("لم أجد صفحة فيسبوك تستطيع النشر عليها بهذا الرمز. تأكد من صلاحية pages_show_list وأنك مدير الصفحة.")
    if any(not p["token"] for p in out):
        raise MetaError("لم يُرجع Meta رمز الصفحة؛ أضف صلاحية pages_manage_posts وأعد توليد الرمز.")
    return out


def _when(value) -> dt.datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        when = value
    else:
        when = dt.datetime.fromisoformat(str(value))
    if when.tzinfo is None:
        when = when.astimezone()
    return when


def check_facebook_schedule(when, now: dt.datetime | None = None) -> None:
    when = _when(when)
    if when is None:
        return
    now = now or dt.datetime.now(dt.timezone.utc)
    if when - now < FB_SCHEDULE_MIN:
        raise MetaError("موعد النشر في فيسبوك يجب أن يكون بعد 10 دقائق على الأقل")
    if when - now > FB_SCHEDULE_MAX:
        raise MetaError("فيسبوك يسمح بالجدولة حتى 30 يومًا فقط")


def caption_problems(text: str) -> list[str]:
    problems = []
    if len(text) > IG_CAPTION_LIMIT:
        problems.append(f"نص إنستغرام أطول من {IG_CAPTION_LIMIT} حرف ({len(text)})")
    tags = [w for w in text.split() if w.startswith("#") and len(w) > 1]
    if len(tags) > IG_HASHTAG_LIMIT:
        problems.append(f"إنستغرام يقبل {IG_HASHTAG_LIMIT} وسمًا كحد أقصى ({len(tags)})")
    mentions = [w for w in text.split() if w.startswith("@") and len(w) > 1]
    if len(mentions) > IG_MENTION_LIMIT:
        problems.append(f"إنستغرام يقبل {IG_MENTION_LIMIT} إشارة كحد أقصى ({len(mentions)})")
    return problems


class Page:
    """One Facebook Page (and its Instagram account) with the Page's own token."""

    def __init__(self, entry: dict, token: str, api: str = GRAPH, version: str = VERSION, sleep=time.sleep):
        self.entry = entry
        self.id = entry["id"]
        self.ig_id = entry.get("ig_id") or ""
        self.g = Graph(token, api, version)
        self.sleep = sleep

    # ---- reading ----------------------------------------------------------------------
    def info(self) -> dict:
        data = self.g.get(self.id, fields="id,name,link,followers_count,fan_count")
        out = {"id": data.get("id"), "name": data.get("name"), "link": data.get("link"),
               "followers": data.get("followers_count", data.get("fan_count"))}
        if self.ig_id:
            ig = self.g.get(self.ig_id, fields="id,username,followers_count,media_count")
            out["instagram"] = {"username": ig.get("username"), "followers": ig.get("followers_count"),
                                "posts": ig.get("media_count")}
        return out

    def facebook_posts(self, limit: int = 10) -> list[dict]:
        data = self.g.get(f"{self.id}/posts", fields="id,message,created_time,permalink_url,full_picture",
                          limit=max(1, min(limit, 50)))
        return [{"id": p["id"], "text": p.get("message") or "", "time": p.get("created_time"),
                 "url": p.get("permalink_url"), "image": p.get("full_picture")} for p in data.get("data") or []]

    def facebook_scheduled(self) -> list[dict]:
        data = self.g.get(f"{self.id}/scheduled_posts", fields="id,message,scheduled_publish_time,created_time")
        out = []
        for p in data.get("data") or []:
            when = p.get("scheduled_publish_time")
            if isinstance(when, (int, float)):
                when = dt.datetime.fromtimestamp(when, dt.timezone.utc).isoformat()
            out.append({"id": p["id"], "text": p.get("message") or "", "when": when})
        return out

    def instagram_posts(self, limit: int = 10) -> list[dict]:
        if not self.ig_id:
            return []
        data = self.g.get(f"{self.ig_id}/media",
                          fields="id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count",
                          limit=max(1, min(limit, 50)))
        return [{"id": m["id"], "text": m.get("caption") or "", "time": m.get("timestamp"), "url": m.get("permalink"),
                 "type": m.get("media_product_type") or m.get("media_type"), "likes": m.get("like_count"),
                 "comments": m.get("comments_count")} for m in data.get("data") or []]

    def instagram_quota(self) -> tuple[int, int] | None:
        """(used, total) of API posts in the last 24 hours."""
        if not self.ig_id:
            return None
        data = self.g.get(f"{self.ig_id}/content_publishing_limit", fields="config,quota_usage")
        row = (data.get("data") or [{}])[0]
        total = (row.get("config") or {}).get("quota_total") or 100
        return int(row.get("quota_usage") or 0), int(total)

    # ---- photos ---------------------------------------------------------------------------
    def _photo(self, image: dict, **fields) -> dict:
        """Upload one picture to the Page. image: {"url": ...} or {"path": ...}."""
        fields = {k: v for k, v in fields.items() if v is not None}
        if image.get("path"):
            path = Path(image["path"])
            data = path.read_bytes()
            if len(data) > MAX_IMAGE_BYTES:
                raise MetaError(f"الصورة {path.name} أكبر من 8MB")
            form = {k: (json.dumps(v) if isinstance(v, bool) else v) for k, v in fields.items()}
            return self.g.upload(f"{self.id}/photos", form, path.name, data)
        if image.get("url"):
            return self.g.post(f"{self.id}/photos", dict(fields, url=image["url"]))
        raise MetaError("صورة بلا رابط ولا ملف")

    def _jpeg_url(self, image: dict) -> tuple[str, str | None]:
        """A public JPEG link Instagram can fetch, and the temporary Facebook photo to delete after."""
        url = image.get("url") or ""
        if url and not image.get("path") and urllib.parse.urlparse(url).path.lower().endswith((".jpg", ".jpeg")):
            return url, None
        photo = self._photo(image, published=False)
        pid = str(photo["id"])
        data = self.g.get(pid, fields="images")
        sizes = sorted(data.get("images") or [], key=lambda i: i.get("width") or 0, reverse=True)
        fitting = [i for i in sizes if (i.get("width") or 0) <= 1440] or sizes
        if not fitting:
            raise MetaError("لم يُرجع فيسبوك رابطًا للصورة المرفوعة")
        return fitting[0]["source"], pid

    # ---- publishing ---------------------------------------------------------------------------
    def permalink(self, object_id: str, field: str = "permalink_url") -> str:
        try:
            return self.g.get(object_id, fields=field).get(field) or ""
        except MetaError:
            return ""

    def publish_facebook(self, text: str, images: list[dict] | None = None, link: str | None = None,
                         when=None) -> dict:
        """Publish now, or schedule natively when `when` is given. Returns {"id", "url", "scheduled"}."""
        images = images or []
        when = _when(when)
        check_facebook_schedule(when)
        timing = {"published": False, "scheduled_publish_time": int(when.timestamp())} if when else {}
        if not images:
            body = {"message": text}
            if link:
                body["link"] = link
            post = self.g.post(f"{self.id}/feed", dict(body, **timing))
            post_id = str(post["id"])
        elif len(images) == 1:
            photo = self._photo(images[0], message=text or None, **timing)
            post_id = str(photo.get("post_id") or photo["id"])
        else:
            media = []
            for image in images:
                photo = self._photo(image, published=False, temporary=True if when else None)
                media.append({"media_fbid": str(photo["id"])})
            post = self.g.post(f"{self.id}/feed", dict({"message": text, "attached_media": media}, **timing))
            post_id = str(post["id"])
        return {"id": post_id, "url": "" if when else self.permalink(post_id), "scheduled": when.isoformat() if when else None}

    def _wait(self, container_id: str) -> None:
        for pause in CONTAINER_WAITS:
            if pause:
                self.sleep(pause)
            status = self.g.get(container_id, fields="status_code,status").get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise MetaError(f"رفض إنستغرام الوسائط ({status})")
        raise MetaError("إنستغرام لم ينتهِ من تجهيز الوسائط بعد؛ حاول لاحقًا")

    def publish_instagram(self, caption: str, images: list[dict]) -> dict:
        if not self.ig_id:
            raise MetaError("لا يوجد حساب إنستغرام تجاري مرتبط بهذه الصفحة")
        if not images:
            raise MetaError("منشور إنستغرام يحتاج صورة واحدة على الأقل")
        if len(images) > IG_CAROUSEL_LIMIT:
            raise MetaError(f"ألبوم إنستغرام حتى {IG_CAROUSEL_LIMIT} صور")
        problems = caption_problems(caption)
        if problems:
            raise MetaError("؛ ".join(problems))
        quota = self.instagram_quota()
        if quota and quota[0] >= quota[1]:
            raise MetaError(f"وصلت حد إنستغرام: {quota[1]} منشور خلال 24 ساعة")
        temporary = []
        try:
            urls = []
            for image in images:
                url, temp = self._jpeg_url(image)
                urls.append(url)
                if temp:
                    temporary.append(temp)
            if len(urls) == 1:
                container = str(self.g.post(f"{self.ig_id}/media", {"image_url": urls[0], "caption": caption})["id"])
            else:
                children = [str(self.g.post(f"{self.ig_id}/media", {"image_url": u, "is_carousel_item": True})["id"])
                            for u in urls]
                for child in children:
                    self._wait(child)
                container = str(self.g.post(f"{self.ig_id}/media", {"media_type": "CAROUSEL", "caption": caption,
                                                                     "children": ",".join(children)})["id"])
            self._wait(container)
            media_id = str(self.g.post(f"{self.ig_id}/media_publish", {"creation_id": container})["id"])
        finally:
            for pid in temporary:
                try:
                    self.g.delete(pid)
                except MetaError:
                    pass  # an unpublished photo left behind is harmless
        return {"id": media_id, "url": self.permalink(media_id, "permalink")}

    def delete_post(self, post_id: str) -> None:
        self.g.delete(post_id)
