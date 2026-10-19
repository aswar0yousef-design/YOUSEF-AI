import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.integrations.shopify import StoreError
from yousef.integrations.store_tools import StoreToolbox, clean_fields, parse_price
from yousef.server import make_server

from helpers import FakeProvider, XorCipher, factory_from, wait_for
from store_fakes import FakeShopify, FakeWoo

SHOP_PID = "gid://shopify/Product/1"


class Script:
    """Scripted model: a plan for the planner, then tool calls for the store manager."""

    def __init__(self, turns, plan=None):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.plan = plan or {"steps": [{"agent": "store_manager", "instruction": "do the store work"}]}
        self.planner_calls, self.store_calls = [], []

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            self.planner_calls.append(list(messages))
            return json.dumps(self.plan, ensure_ascii=False)
        if json_mode and "managing the user's online stores" in system:
            self.store_calls.append(list(messages))
            return self.turns.pop(0) if self.turns else json.dumps({"done": "تم"})
        return "إجابة عامة"


class ParsingTest(unittest.TestCase):
    def test_prices(self):
        cases = {"٩٫٥": "9.5", "9.500 ر.ع": "9.500", "OMR 12": "12", 7: "7", 1.23456: "1.235", "1,250.5": "1250.5"}
        for raw, expected in cases.items():
            self.assertEqual(parse_price(raw), expected, raw)
        for bad in [-1, "abc", True, None, "1e9", "NaN"]:
            with self.assertRaises(StoreError, msg=bad):
                parse_price(bad)

    def test_fields(self):
        with self.assertRaises(StoreError) as cm:
            clean_fields("shopify", {"sale_price": "3"}, creating=False)
        self.assertIn("compare_at_price", str(cm.exception))
        with self.assertRaises(StoreError):
            clean_fields("woocommerce", {"compare_at_price": "3"}, creating=False)
        self.assertEqual(clean_fields("woocommerce", {"status": "publish"}, False), {"status": "active"})
        self.assertEqual(clean_fields("shopify", {"tags": "عود، هدايا, عود"}, False), {"tags": ["عود", "هدايا"]})
        self.assertEqual(clean_fields("shopify", {"title": "x"}, True), {"title": "x", "status": "draft"})
        with self.assertRaises(StoreError):
            clean_fields("shopify", {"price": "5"}, creating=True)  # no title
        with self.assertRaises(StoreError):
            clean_fields("shopify", {"status": "deleted"}, False)


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.shopify = FakeShopify()
        self.woo = FakeWoo()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.shopify.close()
        self.woo.close()
        self.dir.cleanup()

    def make_app(self, script, stores=("shopify", "woo")):
        provider = FakeProvider("ollama", responder=script, models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}),
                             cipher=XorCipher())
        if "shopify" in stores:
            self.shop_entry, _ = self.app.stores.add("بيت العود", "shopify", self.shopify.url, FakeShopify.TOKEN)
        if "woo" in stores:
            self.woo_entry, _ = self.app.stores.add("لبان ظفار", "woocommerce", self.woo.url,
                                                    f"{FakeWoo.KEY}:{FakeWoo.SECRET}")
        return self.app


class ToolboxTest(StoreTestBase):
    def setUp(self):
        super().setUp()
        self.make_app(Script([]))
        self.tb = StoreToolbox(self.app.stores)

    def test_reads(self):
        products = self.tb.run({"tool": "list_products", "store": "بيت العود"})
        self.assertIn(f"{SHOP_PID} | بخور العود الملكي | active | 8.500 | OUD-1 | 12", products)
        product = self.tb.run({"tool": "get_product", "store": "بيت العود", "id": SHOP_PID})
        self.assertIn("description: بخور فاخر", product)  # HTML stripped
        orders = self.tb.run({"tool": "list_orders", "store": "بيت العود"})
        self.assertIn("#1001 | 2026-10-10 | 17.0 OMR | paid | fulfilled | بخور العود الملكي ×2", orders)
        summary = self.tb.run({"tool": "sales_summary", "store": "بيت العود"})
        self.assertIn("2 orders", summary)
        self.assertIn("revenue OMR: 25.5", summary)
        self.assertIn("بخور العود الملكي (3)", summary)
        woo = self.tb.run({"tool": "list_products", "store": "لبان ظفار"})
        self.assertIn("7 | لبان حوجري | active | 5.000 | LUBAN | 30", woo)
        self.assertTrue(all(t["ok"] for t in self.tb.trace))

    def test_store_must_be_named_when_several(self):
        out = self.tb.run({"tool": "list_products"})
        self.assertIn("several stores are connected", out)
        self.assertIn("بيت العود", out)
        self.assertIn("not found", self.tb.run({"tool": "store_info", "store": "متجر آخر"}))
        tb = StoreToolbox(self.app.stores, default_store=self.woo_entry["id"])
        self.assertIn("لبان حوجري", tb.run({"tool": "list_products"}))

    def test_proposals(self):
        out = self.tb.run({"tool": "propose_update", "store": "بيت العود", "id": SHOP_PID, "price": "2"})
        self.assertIn("8.500", out)
        self.tb.run({"tool": "propose_update", "store": "بيت العود", "id": SHOP_PID, "title": "بخور العود الملكي الفاخر"})
        self.assertEqual(len(self.tb.proposals), 1)  # merged into one proposal for the product
        p = self.tb.proposals[0]["payload"]
        self.assertEqual(p["before"], {"price": "8.500", "title": "بخور العود الملكي"})
        self.assertEqual(p["after"], {"price": "2", "title": "بخور العود الملكي الفاخر"})
        self.assertEqual(p["variant_id"], "gid://shopify/ProductVariant/11")
        self.assertTrue(any("-76%" in w for w in p["warnings"]), p["warnings"])
        self.assertIn("No change", self.tb.run({"tool": "propose_update", "store": "بيت العود", "id": SHOP_PID,
                                                "sku": "OUD-1"}))
        self.tb.run({"tool": "propose_product", "store": "لبان ظفار", "title": "لبان نجدي", "price": "٤٫٥"})
        new = self.tb.proposals[1]
        self.assertEqual(new["kind"], "store_create_product")
        self.assertEqual(new["payload"]["after"], {"title": "لبان نجدي", "price": "4.5", "status": "draft"})
        self.assertEqual(self.shopify.products[SHOP_PID]["variants"]["nodes"][0]["price"], "8.500")  # nothing ran
        self.assertEqual(len(self.woo.products), 1)

    def test_variants_need_choice(self):
        self.shopify.products[SHOP_PID]["variants"]["nodes"].append(
            {"id": "gid://shopify/ProductVariant/12", "title": "Large", "price": "15.000", "compareAtPrice": None,
             "sku": "OUD-L", "inventoryQuantity": 3})
        out = self.tb.run({"tool": "propose_update", "store": "بيت العود", "id": SHOP_PID, "price": "14"})
        self.assertIn("has 2 variants", out)
        self.tb.run({"tool": "propose_update", "store": "بيت العود", "id": SHOP_PID, "price": "14",
                     "variant_id": "gid://shopify/ProductVariant/12"})
        self.assertEqual(self.tb.proposals[0]["payload"]["before"], {"price": "15.000"})


class AgentFlowTest(StoreTestBase):
    TURNS = [
        {"tool": "sales_summary", "store": "بيت العود"},
        {"tool": "propose_update", "store": "بيت العود", "id": SHOP_PID, "price": "7.500"},
        {"tool": "propose_product", "store": "لبان ظفار", "title": "لبان حوجري ممتاز", "price": "6", "sku": "LUBAN-P"},
        {"done": "العود هو الأكثر مبيعًا. أقترح خفض سعره قليلًا، وإضافة لبان ممتاز كمسودة."},
    ]

    def start(self, request="راجع مبيعات المتجرين واقترح تحسينات على الأسعار والمنتجات", **extra):
        self.script = Script(self.TURNS)
        self.make_app(self.script)
        tid = self.app.orchestrator.submit(request, **extra)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] in ("waiting", "done", "failed"))
        return tid

    def test_approve_with_one_excluded(self):
        tid = self.start()
        task = self.app.memory.get_task(tid)
        step = task["steps"][0]
        self.assertEqual((step["agent"], step["status"]), ("store_manager", "awaiting_approval"))
        self.assertEqual([t["tool"] for t in step["trace"]], ["sales_summary", "propose_update", "propose_product"])
        kinds = [a["kind"] for a in step["actions"]]
        self.assertEqual(kinds, ["store_update_product", "store_create_product"])
        planner_system = self.script.planner_calls[0][0]["content"]
        self.assertIn("store_manager", planner_system)
        self.assertIn('"بيت العود" (Shopify)', planner_system)
        store_system = self.script.store_calls[0][0]["content"]
        self.assertIn("connected online stores", store_system)
        self.assertIn('"لبان ظفار" (WooCommerce)', store_system)

        create_id = step["actions"][1]["id"]
        self.app.orchestrator.decide(step["id"], True, exclude=[create_id])
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        acts = {a["kind"]: a for a in task["steps"][0]["actions"]}
        self.assertEqual(acts["store_update_product"]["status"], "done")
        self.assertIn("admin.shopify.com", acts["store_update_product"]["result"])
        self.assertEqual(acts["store_create_product"]["status"], "rejected")
        self.assertEqual(self.shopify.products[SHOP_PID]["variants"]["nodes"][0]["price"], "7.500")
        self.assertEqual(len(self.woo.products), 1)  # creation was unticked
        self.assertIn("تغييرات المتجر", task["final"])
        self.assertIn("عُدّل «بخور العود الملكي»", task["final"])

    def test_create_runs_as_draft(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.app.orchestrator.decide(step["id"], True)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        created = [p for p in self.woo.products.values() if p["name"] == "لبان حوجري ممتاز"]
        self.assertEqual(len(created), 1)
        self.assertEqual((created[0]["status"], created[0]["regular_price"], created[0]["sku"]), ("draft", "6", "LUBAN-P"))
        self.assertIn("(مسودة)", self.app.memory.get_task(tid)["final"])

    def test_conflict_when_store_changed(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.shopify.products[SHOP_PID]["variants"]["nodes"][0]["price"] = "9.000"  # changed by someone else
        self.app.orchestrator.decide(step["id"], True)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        acts = {a["kind"]: a for a in self.app.memory.get_task(tid)["steps"][0]["actions"]}
        self.assertEqual(acts["store_update_product"]["status"], "conflict")
        self.assertIn("price", acts["store_update_product"]["result"])
        self.assertEqual(self.shopify.products[SHOP_PID]["variants"]["nodes"][0]["price"], "9.000")
        self.assertEqual(acts["store_create_product"]["status"], "done")  # independent of the conflict

    def test_reject_changes_nothing(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.app.orchestrator.decide(step["id"], False)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        self.assertEqual(self.shopify.products[SHOP_PID]["variants"]["nodes"][0]["price"], "8.500")
        self.assertEqual(len(self.woo.products), 1)
        self.assertEqual({a["status"] for a in self.app.memory.get_task(tid)["steps"][0]["actions"]}, {"rejected"})

    def test_short_request_with_picked_store(self):
        self.script = Script([{"tool": "sales_summary"}, {"done": "طلبان هذا الشهر."}])
        self.make_app(self.script)
        tid = self.app.orchestrator.submit("كم طلب هذا الشهر؟", store=self.shop_entry["id"])
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        self.assertEqual(task["steps"][0]["agent"], "store_manager")
        self.assertEqual(task["store"], "بيت العود")
        self.assertEqual(self.script.planner_calls, [])  # short request skips the planner
        self.assertIn("2 orders", self.script.store_calls[1][-1]["content"])  # default store was used
        self.assertEqual(task["final"], "طلبان هذا الشهر.")

    def test_no_stores_no_store_manager(self):
        self.script = Script([], plan={"steps": [{"agent": "store_manager", "instruction": "حلل متجري"}]})
        self.make_app(self.script, stores=())
        tid = self.app.orchestrator.submit("حلّل أداء متجري الإلكتروني واقترح خطة لزيادة المبيعات")
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        self.assertEqual(task["steps"][0]["agent"], "analyst")
        self.assertNotIn("store_manager", self.script.planner_calls[0][0]["content"])
        with self.assertRaises(ValueError):
            self.app.orchestrator.submit("x", store="nope")


class StoreApiTest(StoreTestBase):
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

    def test_api(self):
        script = Script([{"tool": "propose_update", "id": SHOP_PID, "price": "8"}, {"done": "اقتراح جاهز"}])
        self.make_app(script, stores=("shopify",))
        server = make_server(self.app, 0)
        self.port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            self.assertEqual(self.call("POST", "/api/tasks", {"request": "x", "store": "nope"})[0], 404)
            status, out = self.call("POST", "/api/tasks", {"request": "خفّض سعر بخور العود إلى 8 ريالات",
                                                           "store": "بيت العود"})
            self.assertEqual(status, 201, out)
            tid = out["task"]["id"]
            self.assertEqual(out["task"]["store"], "بيت العود")
            wait_for(lambda: self.call("GET", f"/api/tasks/{tid}")[1]["task"]["status"] == "waiting")
            step = self.call("GET", f"/api/tasks/{tid}")[1]["task"]["steps"][0]
            action = step["actions"][0]
            self.assertEqual(action["payload"]["before"], {"price": "8.500"})
            self.assertEqual(action["payload"]["after"], {"price": "8"})
            bad = self.call("POST", f"/api/steps/{step['id']}/decision", {"approve": True, "exclude": ["x"]})
            self.assertEqual(bad[0], 400)
            self.assertEqual(self.call("POST", f"/api/steps/{step['id']}/decision", {"approve": True})[0], 200)
            wait_for(lambda: self.call("GET", f"/api/tasks/{tid}")[1]["task"]["status"] == "done")
            self.assertEqual(self.shopify.products[SHOP_PID]["variants"]["nodes"][0]["price"], "8")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
