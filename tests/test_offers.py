import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.integrations.shopify import StoreError
from yousef.integrations.store_tools import StoreToolbox, parse_when

from helpers import FakeProvider, XorCipher, factory_from, wait_for
from store_fakes import FakeShopify, FakeWoo

P1, P2 = "gid://shopify/Product/1", "gid://shopify/Product/2"
FUTURE = (dt.date.today() + dt.timedelta(days=10)).isoformat()
LATER = (dt.date.today() + dt.timedelta(days=20)).isoformat()


class DatesTest(unittest.TestCase):
    def test_parse_when(self):
        end = parse_when("2026-10-31", end=True)
        self.assertEqual((end.hour, end.minute, end.second), (23, 59, 59))
        self.assertIsNotNone(end.tzinfo)
        self.assertEqual(parse_when("٢٠٢٦-١٠-٣١").date(), dt.date(2026, 10, 31))
        self.assertEqual(parse_when("2026-10-31T10:00:00Z").utcoffset(), dt.timedelta(0))
        self.assertEqual(parse_when("2026/1/5").date(), dt.date(2026, 1, 5))
        for bad in ["tomorrow", "31-10-2026", ""]:
            with self.assertRaises(StoreError, msg=bad):
                parse_when(bad)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.shop, self.woo = FakeShopify(), FakeWoo()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.shop.close()
        self.woo.close()
        self.dir.cleanup()

    def make_app(self, responder=None):
        provider = FakeProvider("ollama", responder=responder or (lambda m, j: "تم"), models=["m"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}), cipher=XorCipher())
        self.app.stores.add("بيت العود", "shopify", self.shop.url, FakeShopify.TOKEN)
        self.app.stores.add("لبان ظفار", "woocommerce", self.woo.url, f"{FakeWoo.KEY}:{FakeWoo.SECRET}")
        return self.app


class OfferToolsTest(Base):
    def setUp(self):
        super().setUp()
        self.make_app()
        self.tb = StoreToolbox(self.app.stores)

    def payload(self, i=-1):
        return self.tb.proposals[i]["payload"]

    def rows(self, i=-1):
        return {r["label"]: r["value"] for r in self.payload(i)["rows"]}

    def test_shopify_code_offer(self):
        out = self.tb.run({"tool": "propose_discount", "store": "بيت العود", "title": "عرض الخريف", "type": "percentage",
                           "value": 20, "code": "AUTUMN20", "products": [P1], "ends": FUTURE, "once_per_customer": True})
        self.assertIn("Proposed offer", out)
        rows = self.rows()
        self.assertEqual(rows["الخصم"], "20%")
        self.assertEqual(rows["الطريقة"], "كود: AUTUMN20")
        self.assertEqual(rows["يشمل"], "بخور العود الملكي")
        self.assertEqual(rows["يبدأ"], "عند الموافقة")
        self.assertEqual(rows["ينتهي"], FUTURE)
        self.assertEqual(rows["لكل عميل"], "مرة واحدة")
        spec = self.payload()["spec"]
        self.assertEqual((spec["kind"], spec["value"], spec["product_ids"]), ("percentage", "20", [P1]))
        self.assertTrue(spec["ends"].startswith(FUTURE + "T23:59:59"))
        self.assertEqual(self.shop.discounts, {})  # nothing created yet

    def test_shopify_automatic_and_checks(self):
        self.tb.run({"tool": "propose_discount", "store": "بيت العود", "type": "fixed", "value": "7", "products": [P2]})
        rows = self.rows()
        self.assertEqual(rows["الطريقة"], "تلقائي عند الدفع (بدون كود)")
        self.assertEqual(rows["الخصم"], "7 OMR على كل قطعة")
        self.assertIn("قيمة الخصم تساوي سعر أحد المنتجات أو تزيد عليه", self.payload()["warnings"])
        self.assertIn("between 1 and 100", self.tb.run({"tool": "propose_discount", "store": "بيت العود", "value": 150}))
        self.assertIn("future", self.tb.run({"tool": "propose_discount", "store": "بيت العود", "value": 10,
                                             "ends": "2020-01-01"}))
        self.assertIn("code:", self.tb.run({"tool": "propose_discount", "store": "بيت العود", "value": 10,
                                            "code": "عرض"}))

    def test_duplicate_code_and_listing(self):
        self.app.stores.client(self.app.stores.find("بيت العود")).create_discount({
            "title": "صيف", "kind": "percentage", "value": "10", "code": "SUMMER", "product_ids": [],
            "starts": "2026-06-01T00:00:00+04:00", "ends": None})
        listing = self.tb.run({"tool": "list_discounts", "store": "بيت العود"})
        self.assertIn("صيف | active | code SUMMER | 10% off", listing)
        self.assertIn("already used", self.tb.run({"tool": "propose_discount", "store": "بيت العود", "value": 5,
                                                   "code": "summer"}))
        did = next(iter(self.shop.discounts))
        self.tb.run({"tool": "propose_end_discount", "store": "بيت العود", "id": did})
        self.assertEqual(self.rows()["بعد الموافقة"], "يتوقف فورًا")
        self.shop.discounts[did]["status"] = "EXPIRED"
        self.assertIn("already ended", self.tb.run({"tool": "propose_end_discount", "store": "بيت العود", "id": did}))

    def test_woo_offers(self):
        out = self.tb.run({"tool": "propose_discount", "store": "لبان ظفار", "value": 15})
        self.assertIn("need a code", out)
        self.assertIn("sale_from", out)
        self.tb.run({"tool": "propose_discount", "store": "لبان ظفار", "value": 15, "code": "LUBAN15",
                     "starts": FUTURE, "ends": LATER, "minimum": "10", "usage_limit": 50})
        rows = self.rows()
        self.assertEqual(rows["الحد الأدنى للطلب"], "10 OMR")
        self.assertEqual(rows["حد الاستخدام"], "50 مرة")
        self.assertIn("كوبونات WooCommerce لا تدعم تاريخ بداية", " ".join(self.payload()["warnings"]))

    def test_sale_schedule_and_seo(self):
        self.tb.run({"tool": "propose_update", "store": "لبان ظفار", "id": "7", "sale_price": "4", "sale_from": FUTURE,
                     "sale_to": LATER})
        p = self.payload()
        self.assertEqual(p["after"], {"sale_price": "4", "sale_from": f"{FUTURE}T00:00:00", "sale_to": f"{LATER}T23:59:59"})
        self.assertEqual(p["before"], {"sale_price": "", "sale_from": "", "sale_to": ""})
        self.assertIn("do not support", self.tb.run({"tool": "propose_update", "store": "لبان ظفار", "id": "7",
                                                     "seo_title": "x"}))
        self.tb.run({"tool": "propose_update", "store": "بيت العود", "id": P1, "seo_title": "ع" * 80})
        self.assertIn("عنوان SEO أطول من 70 حرفًا، وقد يُقص في نتائج البحث", self.payload()["warnings"])

    def test_bundles(self):
        out = self.tb.run({"tool": "propose_bundle", "store": "بيت العود", "title": "باقة العود والمبخرة",
                           "items": [{"id": P1, "quantity": 1}, {"id": P2, "quantity": 2}], "price": "15"})
        self.assertIn("Proposed bundle", out)
        rows = self.rows()
        self.assertEqual(rows["المكوّنات"], "بخور العود الملكي × 1، مبخرة نحاسية × 2")
        self.assertEqual(rows["سعر الباقة"], "15 OMR")
        self.assertEqual(rows["لو بيعت منفصلة"], "20.500 OMR (توفير 26%)")
        self.assertEqual(rows["الحالة"], "مسودة")
        self.assertIn("باقة Shopify أصلية", rows["المخزون"])
        self.assertTrue(self.payload()["spec"]["native"])
        self.assertIn("at least two units", self.tb.run({"tool": "propose_bundle", "store": "بيت العود", "title": "x",
                                                         "items": [{"id": P1}], "price": "5"}))
        self.assertIn("appear once", self.tb.run({"tool": "propose_bundle", "store": "بيت العود", "title": "x",
                                                  "items": [{"id": P1}, {"id": P1}], "price": "5"}))
        self.tb.run({"tool": "propose_bundle", "store": "بيت العود", "title": "باقة غالية",
                     "items": [{"id": P1}, {"id": P2}], "price": "20"})
        self.assertIn("سعر الباقة لا يقل عن مجموع منتجاتها منفصلة", self.payload()["warnings"])
        self.tb.run({"tool": "propose_bundle", "store": "لبان ظفار", "title": "باقة اللبان",
                     "items": [{"id": "7", "quantity": 3}], "price": "12"})
        self.assertIn("غير مرتبط", self.rows()["المخزون"])
        self.assertFalse(self.payload()["spec"]["native"])

    def test_bundle_option_limit(self):
        many = [{"id": f"gid://shopify/ProductOption/9{i}", "name": f"خيار {i}",
                 "optionValues": [{"name": "أ"}, {"name": "ب"}]} for i in range(4)]
        self.shop.products[P2]["options"] = many
        out = self.tb.run({"tool": "propose_bundle", "store": "بيت العود", "title": "x",
                           "items": [{"id": P1}, {"id": P2}], "price": "10"})
        self.assertIn("ثلاثة خيارات على الأكثر", out)


class ExecutionTest(Base):
    def test_offers_and_bundles_run_after_approval(self):
        self.shop.products[P1]["seo"]["description"] = "وصف قديم"
        turns = [
            {"tool": "propose_discount", "store": "بيت العود", "title": "عرض العود", "value": 20, "products": [P1],
             "ends": FUTURE},
            {"tool": "propose_bundle", "store": "بيت العود", "title": "باقة العود", "description": "<p>هدية</p>",
             "items": [{"id": P1, "quantity": 1}, {"id": P2, "quantity": 1}], "price": "12.500"},
            {"tool": "propose_update", "store": "بيت العود", "id": P1, "seo_title": "بخور عود ملكي فاخر"},
            {"tool": "propose_discount", "store": "لبان ظفار", "title": "كوبون اللبان", "value": 10, "code": "LUBAN10",
             "ends": FUTURE, "once_per_customer": True},
            {"tool": "propose_update", "store": "لبان ظفار", "id": "7", "sale_price": "4.5", "sale_to": LATER},
            {"done": "جهّزت العروض والباقة."},
        ]

        def respond(messages, json_mode):
            if messages[0]["content"].startswith("You are the planner"):
                return json.dumps({"steps": [{"agent": "store_manager", "instruction": "عروض"}]})
            if json_mode:
                return json.dumps(turns.pop(0), ensure_ascii=False) if turns else json.dumps({"done": "تم"})
            return "تم"

        app = self.make_app(respond)
        tid = app.orchestrator.submit("جهّز عروض الخريف وباقة للعود والمبخرة في المتجرين")
        wait_for(lambda: app.memory.get_task(tid)["status"] == "waiting")
        step = app.memory.get_task(tid)["steps"][0]
        self.assertEqual([a["kind"] for a in step["actions"]],
                         ["store_create_discount", "store_create_bundle", "store_update_product",
                          "store_create_discount", "store_update_product"])
        app.orchestrator.decide(step["id"], True)
        wait_for(lambda: app.memory.get_task(tid)["status"] == "done", timeout=20)
        task = app.memory.get_task(tid)
        statuses = [(a["kind"], a["status"]) for a in task["steps"][0]["actions"]]
        self.assertTrue(all(s == "done" for _, s in statuses), task["steps"][0]["actions"])

        auto = next(d for d in self.shop.discounts.values() if d["title"] == "عرض العود")
        self.assertEqual(auto["__typename"], "DiscountAutomaticBasic")
        gets = auto["input"]["customerGets"]
        self.assertEqual(gets["value"], {"percentage": 0.2})
        self.assertEqual(gets["items"], {"products": {"productsToAdd": [P1]}})
        self.assertEqual(auto["input"]["context"], {"all": "ALL"})

        bundle = next(p for p in self.shop.products.values() if p["title"] == "باقة العود")
        self.assertEqual(bundle["status"], "DRAFT")
        self.assertEqual(bundle["variants"]["nodes"][0]["price"], "12.500")
        self.assertEqual(bundle["descriptionHtml"], "<p>هدية</p>")
        names = [s["name"] for c in bundle["bundle_components"] for s in c["optionSelections"]]
        self.assertEqual(names, ["Title", "Title - مبخرة نحاسية"])  # unique option names
        self.assertEqual(self.shop.products[P1]["seo"], {"title": "بخور عود ملكي فاخر", "description": "وصف قديم"})

        coupon = next(iter(self.woo.coupons.values()))
        self.assertEqual((coupon["code"], coupon["discount_type"], coupon["amount"], coupon["usage_limit_per_user"]),
                         ("luban10", "percent", "10", 1))
        expected = (dt.date.fromisoformat(FUTURE) + dt.timedelta(days=1)).isoformat() + "T00:00:00"
        self.assertEqual(coupon["date_expires"], expected)
        self.assertEqual((self.woo.products[7]["sale_price"], self.woo.products[7]["date_on_sale_to"]),
                         ("4.5", f"{LATER}T23:59:59"))
        self.assertIn("أُنشئت الباقة «باقة العود»", task["final"])
        self.assertIn("(مسودة)", task["final"])

    def test_end_offer(self):
        app = self.make_app()
        client = app.stores.client(app.stores.find("بيت العود"))
        created = client.create_discount({"title": "قديم", "kind": "percentage", "value": "5", "code": "OLD5",
                                          "product_ids": [], "starts": "2026-01-01T00:00:00+04:00", "ends": None})
        client.end_discount(created["id"])
        self.assertEqual(self.shop.discounts[created["id"]]["status"], "EXPIRED")
        wclient = app.stores.client(app.stores.find("لبان ظفار"))
        coupon = wclient.create_discount({"title": "c", "kind": "fixed", "value": "2", "code": "TWO", "product_ids": [],
                                          "starts": "x", "ends": None})
        wclient.end_discount(coupon["id"])
        self.assertLess(self.woo.coupons[coupon["id"]]["date_expires"], dt.date.today().isoformat())
        self.assertEqual(wclient.discounts()[0]["status"], "expired")


if __name__ == "__main__":
    unittest.main()
