import datetime as dt
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.integrations.meta import MetaError, caption_problems, connect
from yousef.server import make_server

from helpers import FakeProvider, XorCipher, factory_from
from meta_fake import PAGE_TOKENS, USER_TOKEN, FakeMeta


def soon(minutes):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat()


class SocialBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.meta = FakeMeta()
        self.sleeps = []
        provider = FakeProvider("ollama", responder=lambda m, j: "ok", models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}),
                             cipher=XorCipher(), meta_api=self.meta.url, meta_sleep=self.sleeps.append)

    def tearDown(self):
        self.app.close()
        self.meta.close()
        self.dir.cleanup()

    def page(self, ref="بيت العود"):
        return self.app.social.page(self.app.social.find(ref))


class ConnectTest(SocialBase):
    def test_user_token_finds_pages_and_instagram(self):
        added = self.app.social.connect(USER_TOKEN)
        self.assertEqual([(a["id"], a["ig_username"]) for a in added], [("101", "oman.oud"), ("102", "")])
        self.assertEqual(self.app.keystore.get("social:101"), PAGE_TOKENS["101"])
        settings_text = (Path(self.dir.name) / "data" / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("EAA", settings_text)  # tokens never sit in settings
        public = self.app.social.public()
        self.assertEqual(public[0]["platforms"], ["facebook", "instagram"])
        self.assertEqual(public[1]["platforms"], ["facebook"])
        self.assertEqual(self.app.social.find("@oman.oud")["id"], "101")
        self.assertEqual(self.app.social.find("لبان ظفار")["id"], "102")
        self.assertIsNone(self.app.social.find(None))  # two accounts: must say which
        info = self.app.social.test("101")
        self.assertEqual((info["name"], info["instagram"]["username"]), ("بيت العود", "oman.oud"))
        # connecting again refreshes instead of duplicating
        self.app.social.connect(USER_TOKEN)
        self.assertEqual(len(self.app.social.entries()), 2)
        self.assertTrue(self.app.social.remove("102"))
        self.assertIsNone(self.app.keystore.get("social:102"))

    def test_page_token_and_bad_token(self):
        pages = connect(PAGE_TOKENS["102"], self.meta.url)
        self.assertEqual([(p["id"], p["token"]) for p in pages], [("102", PAGE_TOKENS["102"])])
        with self.assertRaises(MetaError) as cm:
            connect("EAAexpired", self.meta.url)
        self.assertIn("أعد ربط الحساب", str(cm.exception))


class PublishTest(SocialBase):
    def setUp(self):
        super().setUp()
        self.app.social.connect(USER_TOKEN)

    def test_facebook_text_link_and_photos(self):
        page = self.page()
        out = page.publish_facebook("عرض الخريف بدأ!", link="https://oman-oud.com/autumn")
        post = self.meta.posts[out["id"]]
        self.assertEqual((post["message"], post["link"], post["published"]), ("عرض الخريف بدأ!", "https://oman-oud.com/autumn", True))
        self.assertTrue(out["url"].startswith("https://facebook.com/101_"))
        one = page.publish_facebook("بخور جديد", images=[{"url": "https://cdn.shopify.com/a.png"}])
        self.assertEqual(self.meta.posts[one["id"]]["photos"], [next(iter(self.meta.photos))])
        img = Path(self.dir.name) / "luban.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"0" * 2000)
        many = page.publish_facebook("ألبوم", images=[{"path": str(img)}, {"url": "https://cdn.shopify.com/b.jpg"}])
        post = self.meta.posts[many["id"]]
        self.assertEqual(len(post["photos"]), 2)
        self.assertTrue(all(not self.meta.photos[p]["published"] for p in post["photos"]))  # attached, not separate posts
        uploaded = self.meta.photos[post["photos"][0]]
        self.assertEqual(uploaded["source"], "luban.jpg")  # local file sent as multipart

    def test_facebook_schedule(self):
        page = self.page()
        when = soon(120)
        out = page.publish_facebook("غدًا: باقة الضيافة", when=when)
        post = self.meta.posts[out["id"]]
        self.assertFalse(post["published"])
        self.assertEqual(post["scheduled"], int(dt.datetime.fromisoformat(when).timestamp()))
        self.assertEqual(out["scheduled"][:16], dt.datetime.fromisoformat(when).isoformat()[:16])
        self.assertEqual([p["id"] for p in page.facebook_scheduled()], [out["id"]])
        for bad in (soon(3), soon(60 * 24 * 40)):
            with self.assertRaises(MetaError):
                page.publish_facebook("x", when=bad)
        page.delete_post(out["id"])
        self.assertEqual(page.facebook_scheduled(), [])

    def test_instagram_photo_and_carousel(self):
        page = self.page()
        direct = page.publish_instagram("بخور العود الملكي #عود", [{"url": "https://cdn.shopify.com/oud.jpg"}])
        self.assertTrue(direct["url"].startswith("https://instagram.com/p/"))
        self.assertEqual(self.meta.photos, {})  # a public JPEG goes straight to Instagram
        # PNG and local files go through an unpublished Facebook photo, deleted afterwards
        img = Path(self.dir.name) / "box.png"
        img.write_bytes(b"\x89PNG" + b"0" * 500)
        out = page.publish_instagram("باقة الضيافة", [{"url": "https://cdn.shopify.com/a.png"}, {"path": str(img)},
                                                     {"url": "https://cdn.shopify.com/c.jpeg"}])
        carousel = self.meta.containers[self.meta.media[out["id"]]["container"]]
        self.assertEqual(carousel["media_type"], "CAROUSEL")
        children = [self.meta.containers[c] for c in carousel["children"].split(",")]
        self.assertTrue(all(c["is_carousel_item"] for c in children))
        self.assertTrue(children[0]["image_url"].endswith("_1080.jpg"))  # the size within Instagram's 1440 px
        self.assertEqual(len(self.meta.deleted), 2)
        self.assertEqual(self.meta.photos, {})
        self.assertEqual(len(page.instagram_posts()), 2)

    def test_instagram_waits_and_limits(self):
        page = self.page()
        self.meta.slow_containers = 2
        page.publish_instagram("x", [{"url": "https://cdn.shopify.com/oud.jpg"}])
        self.assertEqual(self.sleeps, [2, 3])
        self.meta.quota_used = 100
        with self.assertRaises(MetaError) as cm:
            page.publish_instagram("x", [{"url": "https://cdn.shopify.com/oud.jpg"}])
        self.assertIn("100 منشور", str(cm.exception))
        with self.assertRaises(MetaError):
            page.publish_instagram("x", [])
        with self.assertRaises(MetaError):
            self.page("لبان ظفار").publish_instagram("x", [{"url": "https://a/b.jpg"}])  # no Instagram account
        self.assertEqual(caption_problems("#a " * 31), ["إنستغرام يقبل 30 وسمًا كحد أقصى (31)"])
        self.assertTrue(caption_problems("x" * 2201))


class SocialApiTest(SocialBase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.app, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"X-Yousef-Client": "1"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, data, headers)
        resp = conn.getresponse()
        out = json.loads(resp.read() or b"{}")
        conn.close()
        return resp.status, out

    def test_stop_for_update(self):
        self.assertEqual(self.call("POST", "/api/system/stop")[0], 200)
        import time as _t
        for _ in range(40):
            _t.sleep(0.05)
            try:
                self.call("GET", "/api/status")
            except OSError:
                break
        else:
            self.fail("server still answering after stop")

    def test_routes(self):
        status, out = self.call("POST", "/api/social", {"token": "EAAbad"})
        self.assertEqual(status, 400)
        self.assertIn("رمز Meta", out["error"])
        status, out = self.call("POST", "/api/social", {"token": USER_TOKEN})
        self.assertEqual(status, 201)
        self.assertEqual([a["name"] for a in out["added"]], ["بيت العود", "لبان ظفار"])
        self.assertNotIn("token", json.dumps(out))
        status, out = self.call("POST", "/api/social/101/test")
        self.assertEqual((status, out["info"]["followers"]), (200, 1200))
        self.page().publish_facebook("منشور قديم")
        status, out = self.call("GET", "/api/social/101/posts")
        self.assertEqual([p["text"] for p in out["facebook"]], ["منشور قديم"])
        status, out = self.call("DELETE", "/api/social/101")
        self.assertEqual([a["id"] for a in out["accounts"]], ["102"])
        self.assertEqual(self.call("DELETE", "/api/social/101")[0], 404)


if __name__ == "__main__":
    unittest.main()
