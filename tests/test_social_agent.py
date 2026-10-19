import datetime as dt
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.integrations.social_tools import SocialToolbox, StoreReader
from yousef.media import MediaError, MediaLibrary
from yousef.memory import Memory
from yousef.server import make_server

from helpers import FakeProvider, XorCipher, factory_from, wait_for
from meta_fake import USER_TOKEN, FakeMeta
from store_fakes import FakeShopify

JPEG = b"\xff\xd8\xff\xe0" + b"1" * 3000
PNG = b"\x89PNG\r\n\x1a\n" + b"2" * 3000
P1 = "gid://shopify/Product/1"


def later(hours=0, minutes=0):
    t = dt.datetime.now().astimezone() + dt.timedelta(hours=hours, minutes=minutes)
    return t.strftime("%Y-%m-%d %H:%M")


class Script:
    def __init__(self, turns, plan=None):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.plan = plan or {"steps": [{"agent": "social_publisher", "instruction": "انشر عن العود"}]}
        self.planner_calls, self.calls = [], []

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            self.planner_calls.append(list(messages))
            return json.dumps(self.plan, ensure_ascii=False)
        if json_mode and "publishing for the user on their social accounts" in system:
            self.calls.append(list(messages))
            return self.turns.pop(0) if self.turns else json.dumps({"done": "تم"})
        return "تم"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.meta, self.shop = FakeMeta(), FakeShopify()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.meta.close()
        self.shop.close()
        self.dir.cleanup()

    def make_app(self, script=None):
        script = script or Script([])
        provider = FakeProvider("ollama", responder=script, models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}),
                             cipher=XorCipher(), meta_api=self.meta.url, meta_sleep=lambda s: None)
        self.app.social.connect(USER_TOKEN)
        self.app.stores.add("بيت العود", "shopify", self.shop.url, FakeShopify.TOKEN)
        return self.app


class MediaTest(unittest.TestCase):
    def test_library(self):
        with tempfile.TemporaryDirectory() as d:
            lib = MediaLibrary(Path(d) / "media")
            a = lib.save(JPEG, "عرض الخريف.jpg")
            self.assertRegex(a["name"], r"img-[0-9a-f]{16}\.jpg")
            self.assertEqual(lib.save(JPEG)["name"], a["name"])  # same picture stored once
            b = lib.save(PNG, "../../evil.png")
            self.assertEqual(b["label"], "....evil.png")
            self.assertEqual(len(lib.list()), 2)
            self.assertEqual(lib.read(a["name"]), (JPEG, "image/jpeg"))
            self.assertEqual(lib.path("media:" + b["name"]).name, b["name"])
            for bad in ("../settings.json", "img-zz.jpg", "media:img-0000000000000000.jpg"):
                with self.assertRaises(MediaError):
                    lib.path(bad)
            with self.assertRaises(MediaError):
                lib.save(b"GIF89a" + b"0" * 10)
            self.assertTrue(lib.delete(a["name"]))
            self.assertFalse(lib.delete(a["name"]))


class ToolboxTest(Base):
    def setUp(self):
        super().setUp()
        self.make_app()
        self.pic = self.app.media.save(JPEG, "oud.jpg")["name"]
        self.tb = SocialToolbox(self.app.social, self.app.memory, self.app.media)

    def propose(self, **call):
        return self.tb.run(dict({"tool": "propose_post", "account": "بيت العود"}, **call))

    def test_reads(self):
        self.app.social.page(self.app.social.find("101")).publish_facebook("منشور سابق عن اللبان")
        self.assertIn("@oman.oud", self.tb.run({"tool": "list_accounts"}))
        self.assertIn("منشور سابق", self.tb.run({"tool": "recent_posts", "account": "بيت العود"}))
        self.assertIn(f"media:{self.pic} | oud.jpg", self.tb.run({"tool": "list_media"}))
        self.assertIn("several accounts", self.tb.run({"tool": "recent_posts"}))

    def test_post_checks(self):
        self.assertIn("needs at least one picture", self.propose(text="عرض", platforms=["instagram"]))
        self.assertIn("no Instagram account", self.tb.run({"tool": "propose_post", "account": "لبان ظفار", "text": "x",
                                                           "platforms": ["instagram"], "images": [f"media:{self.pic}"]}))
        self.assertIn("must be https", self.propose(text="x", images=["http://a.com/x.jpg"]))
        self.assertIn("غير موجودة", self.propose(text="x", images=["media:img-0000000000000000.jpg"]))
        self.assertIn("30 وسمًا", self.propose(text="x " + "#a " * 31, images=[f"media:{self.pic}"]))
        self.assertIn("needs a time", self.propose(text="x", platforms=["facebook"], when="2026-12-01"))
        self.assertIn("at least 10 minutes", self.propose(text="x", platforms=["facebook"], when=later(minutes=5)))
        self.assertIn("in the past", self.propose(text="x", platforms=["facebook"], when="2020-01-01 10:00"))
        self.assertIn("links are for Facebook posts", self.tb.run({
            "tool": "propose_post", "account": "بيت العود", "text": "x", "platforms": ["instagram"],
            "images": [f"media:{self.pic}"], "link": "https://oman-oud.com"}))
        self.assertEqual(self.tb.proposals, [])

    def test_proposals(self):
        out = self.propose(text="عرض الخريف على العود 🔥", text_instagram="عرض الخريف ✨\n\n#عود #بخور",
                           images=[f"media:{self.pic}", "https://cdn.shopify.com/s/files/oud.png"],
                           link="https://oman-oud.com/autumn")
        self.assertIn("Proposed a post on facebook, instagram", out)
        p = self.tb.proposals[0]["payload"]
        self.assertEqual(p["platforms"], ["facebook", "instagram"])
        self.assertEqual(p["images"][0], {"media": self.pic, "preview": f"/api/media/{self.pic}"})
        rows = {r["label"]: r["value"] for r in p["rows"]}
        self.assertEqual((rows["الموعد"], rows["الصور"], rows["الحساب"]), ("فور الموافقة", "2", "بيت العود · @oman.oud"))
        self.meta.quota_used = 95
        self.propose(text="غدًا", platforms=["instagram"], images=[f"media:{self.pic}"], when=later(hours=20))
        p = self.tb.proposals[1]["payload"]
        self.assertTrue(any("أبقِ يوسف شغّالًا" in w for w in p["warnings"]))
        self.assertTrue(any("اقتربت من حد إنستغرام" in w for w in p["warnings"]))
        self.assertEqual(self.meta.posts, {})  # nothing published yet

    def test_store_reader_cannot_change_products(self):
        reader = StoreReader(self.app.stores)
        self.assertIn("images: https://cdn.shopify.com/s/files/oud.png https://cdn.shopify.com/s/files/oud-box.jpg",
                      reader.run({"tool": "get_product", "id": P1}))
        self.assertIn("unknown tool", reader.run({"tool": "propose_update", "id": P1, "price": "1"}))
        self.assertNotIn("propose_", reader.help())


TURNS = [
    {"tool": "recent_posts", "account": "بيت العود", "platform": "instagram"},
    {"tool": "get_product", "store": "بيت العود", "id": P1},
    {"tool": "propose_update", "store": "بيت العود", "id": P1, "price": "1"},  # not available here
    {"tool": "propose_post", "account": "بيت العود", "text": "بخور العود الملكي الآن بـ 8.500 ر.ع 🔥",
     "text_instagram": "بخور العود الملكي ✨\n\n#عود #بخور_عماني", "images": ["https://cdn.shopify.com/s/files/oud.png"],
     "link": "https://oman-oud.com/products/oud"},
    {"tool": "propose_post", "account": "بيت العود", "text": "تذكير: عرض الخريف ينتهي الليلة",
     "images": ["https://cdn.shopify.com/s/files/oud-box.jpg"], "when": later(hours=3)},
    {"done": "اقترحت منشورًا الآن على فيسبوك وإنستغرام، وتذكيرًا مجدولًا بعد 3 ساعات."},
]


class FlowTest(Base):
    def start(self, request="اكتب منشورًا عن بخور العود وانشره، وجدول تذكيرًا الليلة", turns=TURNS, **extra):
        self.script = Script(list(turns))
        self.make_app(self.script)
        tid = self.app.orchestrator.submit(request, **extra)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] in ("waiting", "done", "failed"))
        return tid

    def test_publish_now_and_schedule(self):
        tid = self.start()
        task = self.app.memory.get_task(tid)
        step = task["steps"][0]
        self.assertEqual((step["agent"], step["status"]), ("social_publisher", "awaiting_approval"), task.get("error"))
        self.assertEqual([t["ok"] for t in step["trace"]], [True, True, False, True, True])
        self.assertEqual([a["kind"] for a in step["actions"]], ["social_post", "social_post"])
        planner = self.script.planner_calls[0][0]["content"]
        self.assertIn('"بيت العود" (+ Instagram @oman.oud)', planner)
        self.assertIn('"social_publisher"', planner)
        system = self.script.calls[0][0]["content"]
        self.assertIn("published only after the user approves", system)
        self.assertNotIn('"propose_update"', system)
        self.assertEqual(self.meta.posts, {})

        self.app.orchestrator.decide(step["id"], True)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        self.assertEqual([a["status"] for a in task["steps"][0]["actions"]], ["done", "done"])
        fb_now = [p for p in self.meta.posts.values() if p["published"]]
        self.assertEqual([p["message"] for p in fb_now], ["بخور العود الملكي الآن بـ 8.500 ر.ع 🔥"])
        ig = list(self.meta.media.values())
        self.assertEqual([m["caption"] for m in ig], ["بخور العود الملكي ✨\n\n#عود #بخور_عماني"])
        rows = self.app.memory.list_social_posts()
        by = {(r["platform"], r["status"]) for r in rows}
        # the reminder goes to both platforms: Facebook keeps its schedule, Yousef publishes the Instagram one
        self.assertEqual(by, {("facebook", "published"), ("instagram", "published"), ("facebook", "scheduled"),
                              ("instagram", "scheduled")})
        scheduled = next(r for r in rows if r["status"] == "scheduled" and r["platform"] == "facebook")
        self.assertTrue(scheduled["native"])
        local = next(r for r in rows if r["status"] == "scheduled" and r["platform"] == "instagram")
        self.assertFalse(local["native"])
        self.assertFalse(self.meta.posts[scheduled["remote_id"]]["published"])
        self.assertIn("المنشورات", task["final"])

    def test_reject_and_exclude(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.app.orchestrator.decide(step["id"], True, exclude=[step["actions"][0]["id"]])
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        self.assertEqual([p["published"] for p in self.meta.posts.values()], [False])  # only the scheduled one
        self.assertEqual(self.meta.media, {})

    def test_short_request_uses_planner(self):
        self.start("انشر عن العود", turns=[{"done": "ما الذي تريد نشره بالضبط؟"}])
        self.assertEqual(len(self.script.planner_calls), 1)


class SchedulerTest(Base):
    def setUp(self):
        super().setUp()
        self.make_app()
        self.pic = self.app.media.save(JPEG, "oud.jpg")["name"]
        self.pub = self.app.publisher

    def payload(self, platforms, when=None, text="منشور"):
        return {"account_id": "101", "account": "بيت العود", "platforms": platforms, "text": text,
                "text_instagram": text + " #عود", "images": [{"media": self.pic}], "link": None,
                "when": when}

    def test_instagram_later_missed_and_cancel(self):
        soon = (dt.datetime.now().astimezone() + dt.timedelta(hours=1)).isoformat()
        lines, failures = self.pub.publish(self.payload(["instagram"], soon))
        self.assertEqual(failures, 0)
        self.assertIn("سينشره يوسف", lines[0])
        post = self.app.memory.list_social_posts(("scheduled",))[0]
        self.assertEqual(self.pub.run_due(time.time()), 0)  # not yet
        self.assertEqual(self.pub.run_due(post["scheduled_at"] + 30), 1)
        done = self.app.memory.get_social_post(post["id"])
        self.assertEqual((done["status"], done["text"]), ("published", "منشور #عود"))
        self.assertTrue(done["url"].startswith("https://instagram.com/p/"))
        # a post found long after its time waits for the user
        self.pub.publish(self.payload(["instagram"], soon, "متأخر"))
        late = self.app.memory.list_social_posts(("scheduled",))[0]
        self.pub.run_due(late["scheduled_at"] + 3 * 3600)
        self.assertEqual(self.app.memory.get_social_post(late["id"])["status"], "missed")
        self.assertEqual(self.pub.publish_now(late["id"])["status"], "published")
        # cancelling a local one never reaches Meta
        self.pub.publish(self.payload(["instagram"], soon, "ألغِه"))
        third = self.app.memory.list_social_posts(("scheduled",))[0]
        self.pub.cancel(third["id"])
        self.assertEqual(self.app.memory.get_social_post(third["id"])["status"], "cancelled")
        self.assertEqual(len(self.meta.media), 2)

    def test_facebook_native(self):
        when = (dt.datetime.now().astimezone() + dt.timedelta(hours=2)).isoformat()
        self.pub.publish(self.payload(["facebook"], when))
        post = self.app.memory.list_social_posts(("scheduled",))[0]
        self.assertTrue(post["native"])
        self.pub.run_due(post["scheduled_at"] + 120)
        self.assertEqual(self.app.memory.get_social_post(post["id"])["status"], "published")
        self.pub.publish(self.payload(["facebook"], when, "يُلغى"))
        second = self.app.memory.list_social_posts(("scheduled",))[0]
        self.pub.cancel(second["id"])
        self.assertIn(second["remote_id"], self.meta.deleted)

    def test_interrupted_publish_is_not_retried(self):
        pid = self.app.memory.add_social_post("101", "بيت العود", "instagram", "x", [], "scheduled", scheduled_at=time.time())
        self.assertTrue(self.app.memory.claim_social_post(pid))
        self.assertFalse(self.app.memory.claim_social_post(pid))
        memory = Memory(Path(self.dir.name) / "data" / "memory.db")
        memory.mark_interrupted()
        self.assertEqual(memory.get_social_post(pid)["status"], "failed")
        memory.close()


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        self.make_app()
        self.server = make_server(self.app, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, method, path, body=None, raw=None, ctype=None, name=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"X-Yousef-Client": "1"}
        data = None
        if raw is not None:
            data, headers["Content-Type"] = raw, ctype
            if name:
                headers["X-File-Name"] = name
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        conn.request(method, path, data, headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        if resp.getheader("Content-Type", "").startswith("application/json"):
            return resp.status, json.loads(payload or b"{}"), resp
        return resp.status, payload, resp

    def test_media_and_posts(self):
        status, out, _ = self.call("POST", "/api/media", raw=PNG, ctype="image/png", name="%D8%B9%D9%88%D8%AF.png")
        self.assertEqual(status, 201)
        name = out["media"]["name"]
        self.assertEqual(out["media"]["label"], "عود.png")
        status, data, resp = self.call("GET", f"/api/media/{name}")
        self.assertEqual((status, data, resp.getheader("Content-Type")), (200, PNG, "image/png"))
        self.assertEqual(self.call("POST", "/api/media", raw=b"GIF89a....", ctype="image/gif")[0], 415)
        self.assertEqual(self.call("POST", "/api/media", raw=b"not an image", ctype="image/jpeg")[0], 400)
        self.assertEqual(self.call("GET", "/api/media/img-0000000000000000.jpg")[0], 404)
        self.assertEqual(self.call("GET", "/api/media/../settings.json")[0], 404)
        self.assertEqual([f["name"] for f in self.call("GET", "/api/media")[1]["files"]], [name])
        when = (dt.datetime.now().astimezone() + dt.timedelta(hours=1)).isoformat()
        self.app.publisher.publish({"account_id": "101", "account": "بيت العود", "platforms": ["instagram"],
                                    "text": "x", "images": [{"media": name}], "when": when})
        posts = self.call("GET", "/api/social/posts?status=scheduled,missed")[1]["posts"]
        self.assertEqual(len(posts), 1)
        status, out, _ = self.call("POST", f"/api/social/posts/{posts[0]['id']}/cancel", {})
        self.assertEqual((status, out["post"]["status"]), (200, "cancelled"))
        self.assertEqual(self.call("POST", f"/api/social/posts/{posts[0]['id']}/cancel", {})[0], 400)
        self.assertEqual(self.call("DELETE", f"/api/media/{name}")[1]["files"], [])


if __name__ == "__main__":
    unittest.main()
