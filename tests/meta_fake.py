"""A fake Meta Graph API (v26.0) for tests: Pages, photos, scheduling and Instagram publishing."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

USER_TOKEN = "EAAuser" + "x" * 60
PAGE_TOKENS = {"101": "EAApage101" + "y" * 60, "102": "EAApage102" + "z" * 60}


def _multipart(body: bytes, ctype: str) -> dict:
    boundary = re.search(r"boundary=(\S+)", ctype).group(1).encode()
    out = {}
    for part in body.split(b"--" + boundary):
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        data = data[:-2] if data.endswith(b"\r\n") else data
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        filename = re.search(rb'filename="([^"]*)"', head)
        out[name] = {"filename": filename.group(1).decode(), "size": len(data)} if filename else data.decode()
    return out


class FakeMeta:
    def __init__(self):
        self.pages = {
            "101": {"name": "بيت العود", "ig": "201", "ig_username": "oman.oud", "followers": 1200},
            "102": {"name": "لبان ظفار", "ig": "", "ig_username": "", "followers": 300},
        }
        self.posts = {}       # id -> {"page", "message", "published", "scheduled", "photos", "link"}
        self.photos = {}      # id -> {"page", "published", "source": url or filename, "message"}
        self.containers = {}  # id -> {"ig", "image_url", "caption", "children", "carousel_item", "checks"}
        self.media = {}       # id -> {"ig", "caption", "container"}
        self.deleted = []
        self.calls = []
        self.quota_used = 3
        self.slow_containers = 0  # how many status checks return IN_PROGRESS before FINISHED
        self.next = 1000
        # ads: one account in USD with two campaigns (the second budgets on its ad set)
        self.ad_accounts = {"act_501": {"name": "إعلانات بيت العود", "currency": "USD", "account_status": 1}}
        self.campaigns = {
            "601": {"account": "act_501", "name": "عرض الخريف", "status": "ACTIVE", "objective": "OUTCOME_SALES",
                    "daily_budget": "2000", "lifetime_budget": None},
            "602": {"account": "act_501", "name": "زيارات المتجر", "status": "ACTIVE", "objective": "OUTCOME_TRAFFIC",
                    "daily_budget": None, "lifetime_budget": None},
        }
        self.adsets = {"701": {"campaign": "602", "name": "مسقط 25-45", "status": "ACTIVE", "daily_budget": "1500",
                               "lifetime_budget": None}}
        self.insights = {
            "601": {"spend": "140.00", "impressions": "52000", "reach": "30000", "clicks": "1300", "ctr": "2.5",
                    "cpc": "0.108", "cpm": "2.69", "actions": [{"action_type": "omni_purchase", "value": "28"},
                                                               {"action_type": "purchase", "value": "28"}],
                    "action_values": [{"action_type": "omni_purchase", "value": "616.00"}],
                    "purchase_roas": [{"action_type": "omni_purchase", "value": "4.4"}]},
            "602": {"spend": "105.00", "impressions": "61000", "reach": "41000", "clicks": "600", "ctr": "0.98",
                    "cpc": "0.175", "cpm": "1.72", "actions": [{"action_type": "link_click", "value": "600"}]},
            "701": {"spend": "105.00", "impressions": "61000", "clicks": "600", "ctr": "0.98", "cpc": "0.175"},
        }
        self.ad_updates = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self, method):
                parsed = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                ctype = self.headers.get("Content-Type") or ""
                body = {}
                if raw and ctype.startswith("application/json"):
                    body = json.loads(raw)
                elif raw and ctype.startswith("multipart/form-data"):
                    body = _multipart(raw, ctype)
                path = parsed.path
                outer.calls.append((method, path, {k: v for k, v in q.items() if k != "access_token"}, body))
                status, payload = outer.answer(method, path, q, body)
                data = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

            def do_DELETE(self):
                self._go("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _id(self):
        self.next += 1
        return str(self.next)

    @staticmethod
    def _err(message, code=100, status=400):
        return status, {"error": {"message": message, "type": "OAuthException", "code": code}}

    def _ads(self, method, parts, q, body):
        node = parts[0]
        if node in self.ad_accounts and parts[1:] == ["campaigns"]:
            rows = [dict({"id": k, "effective_status": v["status"]}, **{f: v[f] for f in ("name", "status", "objective",
                     "daily_budget", "lifetime_budget")}) for k, v in self.campaigns.items() if v["account"] == node]
            return 200, {"data": rows}
        if node in self.ad_accounts and parts[1:] == ["insights"]:
            rows = [dict(self.insights[k], campaign_id=k, campaign_name=v["name"]) for k, v in self.campaigns.items()
                    if v["account"] == node]
            return 200, {"data": rows}
        if node in self.campaigns and parts[1:] == ["adsets"]:
            return 200, {"data": [dict({"id": k, "campaign_id": node, "effective_status": v["status"]},
                                       **{f: v[f] for f in ("name", "status", "daily_budget", "lifetime_budget")})
                                  for k, v in self.adsets.items() if v["campaign"] == node]}
        if parts[1:] == ["insights"]:
            if q.get("time_increment") == "1":
                return 200, {"data": [dict(self.insights[node], date_start=f"2026-10-{d:02d}") for d in (17, 18, 19)]}
            if q.get("level") == "adset":
                return 200, {"data": [dict(self.insights[k], adset_id=k, adset_name=v["name"])
                                      for k, v in self.adsets.items() if v["campaign"] == node]}
        obj = self.campaigns.get(node) or self.adsets.get(node)
        if obj is not None and len(parts) == 1:
            if method == "POST":
                self.ad_updates.append((node, body))
                if "status" in body:
                    obj["status"] = body["status"]
                if "daily_budget" in body:
                    if obj["daily_budget"] is None:
                        return self._err("(#100) daily_budget is set at the ad set level")
                    obj["daily_budget"] = str(body["daily_budget"])
                return 200, {"success": True}
            return 200, {"id": node, "name": obj["name"], "status": obj["status"], "daily_budget": obj["daily_budget"],
                         "lifetime_budget": obj["lifetime_budget"]}
        return self._err("unsupported ads request")

    def _page_for(self, token):
        return next((p for p, t in PAGE_TOKENS.items() if t == token), None)

    def answer(self, method, path, q, body):
        m = re.fullmatch(r"/v26\.0/(.+)", path)
        if not m:
            return self._err("Unknown version")
        parts = m.group(1).split("/")
        token = q.get("access_token")
        token_page = self._page_for(token)
        if token != USER_TOKEN and token_page is None:
            return self._err("Error validating access token: Session has expired", code=190)

        if parts == ["me", "adaccounts"]:
            if token != USER_TOKEN:
                return self._err("(#100) Tried accessing nonexisting field (adaccounts) on node type (Page)")
            return 200, {"data": [{"id": k, "account_id": k[4:], "name": v["name"], "currency": v["currency"],
                                   "account_status": v["account_status"]} for k, v in self.ad_accounts.items()]}
        ad_node = parts[0] in self.ad_accounts or parts[0] in self.campaigns or parts[0] in self.adsets
        if ad_node:
            if token != USER_TOKEN:
                return self._err("(#200) Requires ads_read permission", code=200)
            return self._ads(method, parts, q, body)
        if parts == ["me", "accounts"]:
            if token_page:
                return self._err("(#100) Tried accessing nonexisting field (accounts) on node type (Page)")
            data = []
            for pid, p in self.pages.items():
                row = {"id": pid, "name": p["name"], "access_token": PAGE_TOKENS[pid],
                       "tasks": ["ANALYZE", "ADVERTISE", "MODERATE", "CREATE_CONTENT", "MANAGE"]}
                if p["ig"]:
                    row["instagram_business_account"] = {"id": p["ig"], "username": p["ig_username"]}
                data.append(row)
            return 200, {"data": data}
        if parts == ["me"]:
            if token_page:
                p = self.pages[token_page]
                row = {"id": token_page, "name": p["name"]}
                if p["ig"]:
                    row["instagram_business_account"] = {"id": p["ig"], "username": p["ig_username"]}
                return 200, row
            return 200, {"id": "9", "name": "Yousef"}

        node = parts[0]
        # everything below needs the Page token of the Page that owns the object
        owner = (node if node in self.pages else
                 next((pid for pid, p in self.pages.items() if p["ig"] == node), None) or
                 (self.posts.get(node) or self.photos.get(node) or {}).get("page") or
                 next((pid for pid, p in self.pages.items()
                       if p["ig"] and p["ig"] == (self.containers.get(node) or self.media.get(node) or {}).get("ig")), None))
        if owner is None:
            return self._err(f"Unsupported get request. Object with ID '{node}' does not exist")
        if token_page != owner:
            return self._err("(#200) The user hasn't authorized the application to perform this action", code=200)

        if method == "DELETE" and len(parts) == 1:
            self.deleted.append(node)
            self.posts.pop(node, None)
            self.photos.pop(node, None)
            return 200, {"success": True}

        if node in self.pages:
            page = self.pages[node]
            if len(parts) == 1:
                return 200, {"id": node, "name": page["name"], "link": f"https://facebook.com/{node}",
                             "followers_count": page["followers"]}
            edge = parts[1]
            if edge == "feed" and method == "POST":
                if body.get("published") is False and not body.get("scheduled_publish_time"):
                    return self._err("unpublished posts need scheduled_publish_time")
                pid = f"{node}_{self._id()}"
                self.posts[pid] = {"page": node, "message": body.get("message"), "link": body.get("link"),
                                   "published": body.get("published", True) is not False,
                                   "scheduled": body.get("scheduled_publish_time"),
                                   "photos": [a["media_fbid"] for a in body.get("attached_media") or []]}
                return 200, {"id": pid}
            if edge == "photos" and method == "POST":
                published = body.get("published", True) not in (False, "false")
                scheduled = body.get("scheduled_publish_time")
                photo_id = self._id()
                source = body.get("url") or (body.get("source") or {}).get("filename")
                if not source:
                    return self._err("(#324) Requires upload file")
                self.photos[photo_id] = {"page": node, "published": published, "source": source,
                                         "message": body.get("message"), "scheduled": scheduled}
                out = {"id": photo_id}
                if published or scheduled:
                    post_id = f"{node}_{self._id()}"
                    self.posts[post_id] = {"page": node, "message": body.get("message"), "published": published,
                                           "scheduled": scheduled, "photos": [photo_id], "link": None}
                    out["post_id"] = post_id
                return 200, out
            if edge == "posts":
                rows = [{"id": k, "message": v["message"], "created_time": "2026-10-18T10:00:00+0000",
                         "permalink_url": f"https://facebook.com/{k}"} for k, v in self.posts.items()
                        if v["page"] == node and v["published"]]
                return 200, {"data": rows[::-1]}
            if edge == "scheduled_posts":
                rows = [{"id": k, "message": v["message"], "scheduled_publish_time": v["scheduled"]}
                        for k, v in self.posts.items() if v["page"] == node and not v["published"]]
                return 200, {"data": rows}

        ig_page = next((pid for pid, p in self.pages.items() if p["ig"] == node), None)
        if ig_page:
            if len(parts) == 1:
                return 200, {"id": node, "username": self.pages[ig_page]["ig_username"], "followers_count": 850,
                             "media_count": len([x for x in self.media.values() if x["ig"] == node])}
            edge = parts[1]
            if edge == "content_publishing_limit":
                return 200, {"data": [{"config": {"quota_total": 100, "quota_duration": 86400},
                                       "quota_usage": self.quota_used}]}
            if edge == "media" and method == "POST":
                if body.get("media_type") == "CAROUSEL":
                    children = body["children"].split(",")
                    if len(children) > 10 or any(c not in self.containers for c in children):
                        return self._err("invalid children")
                elif not str(body.get("image_url") or "").lower().split("?")[0].endswith((".jpg", ".jpeg")):
                    return self._err("Only photo or video can be accepted as media type. (code 9004)", code=9004)
                cid = self._id()
                self.containers[cid] = dict(body, ig=node, checks=0)
                return 200, {"id": cid}
            if edge == "media" and method == "GET":
                rows = [{"id": k, "caption": v["caption"], "media_type": "IMAGE", "media_product_type": "FEED",
                         "permalink": f"https://instagram.com/p/{k}", "timestamp": "2026-10-18T10:00:00+0000",
                         "like_count": 12, "comments_count": 2} for k, v in self.media.items() if v["ig"] == node]
                return 200, {"data": rows[::-1]}
            if edge == "media_publish" and method == "POST":
                c = self.containers.get(body.get("creation_id"))
                if not c or c.get("status") != "FINISHED":
                    return self._err("Media ID is not available")
                self.quota_used += 1
                mid = self._id()
                self.media[mid] = {"ig": node, "caption": c.get("caption"), "container": body["creation_id"]}
                return 200, {"id": mid}

        if node in self.containers and len(parts) == 1:
            c = self.containers[node]
            c["checks"] += 1
            c["status"] = "IN_PROGRESS" if c["checks"] <= self.slow_containers else "FINISHED"
            return 200, {"status_code": c["status"], "id": node}
        if node in self.media and len(parts) == 1:
            return 200, {"id": node, "permalink": f"https://instagram.com/p/{node}"}
        if node in self.photos and len(parts) == 1:
            return 200, {"id": node, "images": [
                {"width": 2048, "height": 2048, "source": f"https://scontent.fbcdn.example/{node}_2048.jpg"},
                {"width": 1080, "height": 1080, "source": f"https://scontent.fbcdn.example/{node}_1080.jpg"}]}
        if node in self.posts and len(parts) == 1:
            return 200, {"id": node, "permalink_url": f"https://facebook.com/{node}"}
        return self._err("unsupported")
