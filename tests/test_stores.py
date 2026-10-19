import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.config import Settings
from yousef.integrations import Shopify, StoreError, Stores, WooCommerce, sales_summary
from yousef.integrations.shopify import API_VERSION, shop_domain
from yousef.integrations.woocommerce import site_url
from yousef.keystore import KeyStore
from yousef.server import make_server

from helpers import XorCipher, factory_from
from store_fakes import FakeShopify, FakeWoo

PII = ("customer", "email", "phone", "billingAddress", "shippingAddress", "firstName", "lastName")


class UrlTest(unittest.TestCase):
    def test_shop_domain(self):
        for raw in ["oman-oud", "oman-oud.myshopify.com", "https://oman-oud.myshopify.com/admin/products",
                    "https://admin.shopify.com/store/oman-oud/products"]:
            self.assertEqual(shop_domain(raw), "oman-oud.myshopify.com", raw)
        with self.assertRaises(StoreError):
            shop_domain("")

    def test_site_url(self):
        self.assertEqual(site_url("my-shop.com/wp-admin/edit.php"), "https://my-shop.com")
        self.assertEqual(site_url("https://my-shop.com/shop/wp-json/wc/v3"), "https://my-shop.com/shop")
        with self.assertRaises(StoreError):
            site_url("http://my-shop.com")  # keys must not travel over plain http
        self.assertEqual(site_url("http://127.0.0.1:8080"), "http://127.0.0.1:8080")


class ShopifyTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeShopify()
        self.shop = Shopify(self.fake.url, FakeShopify.TOKEN)

    def tearDown(self):
        self.fake.close()

    def test_reads(self):
        info = self.shop.info()
        self.assertEqual((info["name"], info["currency"], info["products"]), ("متجر العطور", "OMR", 1))
        self.assertIn(f"/admin/api/{API_VERSION}/graphql.json", self.fake.requests[0]["path"])
        products = self.shop.products(search="عود")
        self.assertEqual(products[0]["price"], "8.500")
        self.assertEqual(products[0]["status"], "active")
        self.assertEqual(self.fake.requests[-1]["variables"]["q"], "عود")
        orders = self.shop.orders(days=30)
        self.assertEqual([o["number"] for o in orders], ["#1001", "#1002"])
        self.assertEqual(orders[0]["total"], 17.0)
        query = self.fake.requests[-1]["query"]
        for word in PII:
            self.assertNotIn(word, query, f"orders query must not ask for {word}")
        self.assertTrue(self.fake.requests[-1]["variables"]["q"].startswith("created_at:>="))

    def test_create_is_draft_and_sets_price(self):
        result = self.shop.create_product({"title": "مبخرة فضية", "description": "<p>مبخرة</p>", "price": "12.000",
                                           "sku": "MB-1", "tags": ["هدايا"]})
        create = next(r for r in self.fake.requests if "productCreate" in r["query"])
        self.assertEqual(create["variables"]["product"]["status"], "DRAFT")
        self.assertEqual(create["variables"]["product"]["descriptionHtml"], "<p>مبخرة</p>")
        bulk = next(r for r in self.fake.requests if "productVariantsBulkUpdate" in r["query"])
        self.assertEqual(bulk["variables"]["variants"][0]["price"], "12.000")
        self.assertEqual(bulk["variables"]["variants"][0]["inventoryItem"], {"sku": "MB-1"})
        self.assertEqual(result["url"], f"https://admin.shopify.com/store/127/products/2")  # local fake host

    def test_update_and_errors(self):
        self.shop.update_product("gid://shopify/Product/1", {"title": "بخور العود", "price": "9.000"})
        self.assertEqual(self.fake.products["gid://shopify/Product/1"]["title"], "بخور العود")
        self.assertEqual(self.fake.products["gid://shopify/Product/1"]["variants"]["nodes"][0]["price"], "9.000")
        with self.assertRaises(StoreError) as cm:
            self.shop.update_product("gid://shopify/Product/1", {"title": "BAD"})
        self.assertIn("title is invalid", str(cm.exception))
        with self.assertRaises(StoreError):
            Shopify(self.fake.url, "wrong-token").info()


class WooTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeWoo()
        self.woo = WooCommerce(self.fake.url, f"{FakeWoo.KEY}:{FakeWoo.SECRET}")

    def tearDown(self):
        self.fake.close()

    def test_reads_without_personal_data(self):
        self.assertEqual(self.woo.info()["currency"], "OMR")
        products = self.woo.products()
        self.assertEqual((products[0]["title"], products[0]["price"], products[0]["status"]), ("لبان حوجري", "5.000", "active"))
        orders = self.woo.orders()
        self.assertEqual(orders[0]["items"], [{"title": "لبان حوجري", "quantity": 2}])
        fields = self.fake.requests[-1]["query"]["_fields"][0]
        self.assertNotIn("billing", fields)
        self.assertNotIn("shipping", fields)

    def test_writes(self):
        created = self.woo.create_product({"title": "بخور جديد", "price": "3.500"})
        post = next(r for r in self.fake.requests if r["method"] == "POST")
        self.assertEqual((post["body"]["status"], post["body"]["regular_price"], post["body"]["type"]), ("draft", "3.500", "simple"))
        self.assertIn("post.php?post=8", created["url"])
        self.woo.update_product(7, {"price": "6.000", "status": "active"})
        self.assertEqual(self.fake.products[7]["regular_price"], "6.000")
        self.assertEqual(self.fake.products[7]["status"], "publish")
        with self.assertRaises(StoreError):
            WooCommerce(self.fake.url, "ck_x:cs_wrong").products()
        with self.assertRaises(StoreError):
            WooCommerce(self.fake.url, "no-colon")


class StoresRegistryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.fake = FakeShopify()
        self.settings = Settings(root / "s.json")
        self.keys = KeyStore(root / "k.dat", cipher=XorCipher())
        self.stores = Stores(self.settings, self.keys)

    def tearDown(self):
        self.fake.close()
        self.dir.cleanup()

    def test_add_tests_connection_first(self):
        with self.assertRaises(StoreError):
            self.stores.add("متجري", "shopify", self.fake.url, "wrong-token")
        self.assertEqual(self.stores.entries(), [])
        entry, info = self.stores.add("متجري", "shopify", self.fake.url, FakeShopify.TOKEN)
        self.assertEqual(info["currency"], "OMR")
        self.assertTrue(self.keys.has(f"store:{entry['id']}"))
        self.assertNotIn(FakeShopify.TOKEN, (Path(self.dir.name) / "s.json").read_text())
        self.assertEqual(self.stores.find("")["id"], entry["id"])  # single store is the default
        self.assertEqual(self.stores.find("متجري")["id"], entry["id"])
        with self.assertRaises(StoreError):
            self.stores.add("متجري", "shopify", self.fake.url, FakeShopify.TOKEN)  # duplicate name
        self.assertTrue(self.stores.remove(entry["id"]))
        self.assertFalse(self.keys.has(f"store:{entry['id']}"))

    def test_summary(self):
        orders = Shopify(self.fake.url, FakeShopify.TOKEN).orders()
        s = sales_summary(orders)
        self.assertEqual(s["orders"], 2)
        self.assertEqual(s["revenue"], {"OMR": 25.5})
        self.assertEqual(s["top_products"][0], {"title": "بخور العود الملكي", "quantity": 3})
        self.assertEqual(s["statuses"], {"paid": 1, "pending": 1})


class StoreApiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.shopify, self.woo = FakeShopify(), FakeWoo()
        self.app = build_app(self.dir.name, factory=factory_from({}), cipher=XorCipher())
        self.server = make_server(self.app, 0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.shopify.close()
        self.woo.close()
        self.dir.cleanup()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}", "X-Yousef-Client": "1"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        out = json.loads(resp.read())
        conn.close()
        return resp.status, out

    def test_store_endpoints(self):
        status, out = self.call("POST", "/api/stores", {"name": "شوبيفاي", "platform": "shopify", "url": self.shopify.url,
                                                        "token": FakeShopify.TOKEN})
        self.assertEqual(status, 201, out)
        sid = out["store"]["id"]
        status, out = self.call("POST", "/api/stores", {"name": "ووكومرس", "platform": "woocommerce", "url": self.woo.url,
                                                        "consumer_key": FakeWoo.KEY, "consumer_secret": FakeWoo.SECRET})
        self.assertEqual(status, 201, out)
        status, out = self.call("POST", "/api/stores", {"name": "خطأ", "platform": "woocommerce", "url": self.woo.url,
                                                        "consumer_key": "ck_x", "consumer_secret": "cs_bad"})
        self.assertEqual(status, 400)
        self.assertIn("تعذّر الاتصال", out["error"])
        listed = self.call("GET", "/api/stores")[1]["stores"]
        self.assertEqual([(s["name"], s["connected"]) for s in listed], [("شوبيفاي", True), ("ووكومرس", True)])
        raw = json.dumps(self.call("GET", "/api/settings")[1])
        self.assertNotIn(FakeShopify.TOKEN, raw)
        self.assertNotIn(FakeWoo.SECRET, raw)
        self.assertEqual(self.call("POST", f"/api/stores/{sid}/test", {})[1]["info"]["currency"], "OMR")
        summary = self.call("GET", f"/api/stores/{sid}/summary?days=30")[1]["summary"]
        self.assertEqual(summary["orders"], 2)
        self.assertEqual(self.call("DELETE", f"/api/stores/{sid}")[0], 200)
        self.assertEqual(len(self.call("GET", "/api/stores")[1]["stores"]), 1)


if __name__ == "__main__":
    unittest.main()
