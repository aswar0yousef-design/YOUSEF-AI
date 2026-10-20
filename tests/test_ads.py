import json
import tempfile
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.integrations.ads_tools import AdsToolbox
from yousef.integrations.meta_ads import from_minor, summarize, to_minor

from helpers import FakeProvider, XorCipher, factory_from, wait_for
from meta_fake import PAGE_TOKENS, USER_TOKEN, FakeMeta
from store_fakes import FakeShopify


class Script:
    def __init__(self, turns):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.planner_calls, self.calls = [], []

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            self.planner_calls.append(list(messages))
            return json.dumps({"steps": [{"agent": "ads_manager", "instruction": "راجع الحملات"}]})
        if json_mode and "managing the user's Meta (Facebook and Instagram) ad accounts" in system:
            self.calls.append(list(messages))
            return self.turns.pop(0) if self.turns else json.dumps({"done": "تم"})
        return "تم"


class AdsBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.meta, self.shop = FakeMeta(), FakeShopify()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.meta.close()
        self.shop.close()
        self.dir.cleanup()

    def make_app(self, script=None, token=USER_TOKEN):
        provider = FakeProvider("ollama", responder=script or Script([]), models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}),
                             cipher=XorCipher(), meta_api=self.meta.url, meta_sleep=lambda s: None)
        self.app.social.connect(token, ads=self.app.ads)
        return self.app


class MoneyTest(unittest.TestCase):
    def test_minor_units_and_summary(self):
        self.assertEqual((to_minor(20, "USD"), to_minor(1500, "JPY"), from_minor("2450", "USD")), (2000, 1500, 24.5))
        s = summarize({"spend": "50", "clicks": "10", "actions": [{"action_type": "purchase", "value": "4"},
                                                                  {"action_type": "omni_purchase", "value": "5"}],
                       "action_values": [{"action_type": "omni_purchase", "value": "200"}]})
        self.assertEqual((s["purchases"], s["revenue"], s["roas"], s["cpa"]), (5.0, 200.0, 4.0, 10.0))  # omni first
        self.assertIsNone(summarize({})["roas"])


class ConnectTest(AdsBase):
    def test_user_token_also_connects_ads(self):
        self.make_app()
        self.assertEqual([(a["id"], a["currency"]) for a in self.app.ads.entries()], [("act_501", "USD")])
        self.assertEqual(self.app.keystore.get("meta_ads"), USER_TOKEN)
        self.assertEqual(self.app.ads.find("501")["id"], "act_501")

    def test_page_token_leaves_ads_alone(self):
        self.make_app(token=PAGE_TOKENS["102"])
        self.assertEqual(self.app.ads.entries(), [])


class ToolboxTest(AdsBase):
    def setUp(self):
        super().setUp()
        self.make_app()
        self.tb = AdsToolbox(self.app.ads)

    def test_read_and_propose(self):
        out = self.tb.run({"tool": "campaigns", "days": 7})
        self.assertIn("Total spend 245.00 USD", out)
        self.assertIn("601 | عرض الخريف | ACTIVE | 20.00 | - | 140.00 | 52000 | 1300 | 2.50 | 0.11 | 28 | 616.00 | 4.40 | 5.00", out)
        self.assertIn("602 | زيارات المتجر | ACTIVE | - |", out)
        self.assertIn("2026-10-17 | 140.00", self.tb.run({"tool": "daily", "id": "601"}))
        self.assertIn("set at the other level", self.tb.run({"tool": "propose_budget", "id": "602", "daily_budget": 20}))
        self.assertIn("701 | مسقط 25-45 | ACTIVE | 15.00", self.tb.run({"tool": "adsets", "campaign_id": "602"}))
        self.tb.run({"tool": "propose_budget", "id": "601", "daily_budget": 30, "reason": "ROAS 4.4"})
        p = self.tb.proposals[0]["payload"]
        self.assertEqual((p["spec"], p["level"], p["before"]["daily_budget"]), ({"daily_budget": 30.0}, "campaign", 20.0))
        self.assertTrue(any("زيادة 50%" in w for w in p["warnings"]))
        rows = {r["label"]: r["value"] for r in p["rows"]}
        self.assertEqual(rows["الميزانية اليومية"], "من 20.00 إلى 30.00 USD")
        self.tb.run({"tool": "propose_pause", "id": "701", "reason": "لا مشتريات"})
        self.assertEqual(self.tb.proposals[1]["payload"]["level"], "adset")
        self.assertIn("already active", self.tb.run({"tool": "propose_resume", "id": "601"}))
        self.assertEqual(self.meta.ad_updates, [])  # nothing changed yet


TURNS = [
    {"tool": "campaigns", "days": 7},
    {"tool": "sales_summary", "days": 7},
    {"tool": "adsets", "campaign_id": "602"},
    {"tool": "propose_budget", "id": "601", "daily_budget": 24, "reason": "ROAS 4.4 ومبيعات المتجر تؤكدها"},
    {"tool": "propose_pause", "id": "701", "reason": "105 دولار بلا مشتريات"},
    {"done": "عرض الخريف مربح (ROAS 4.4): أقترح رفع ميزانيته 20%. حملة الزيارات لا تبيع: أقترح إيقاف مجموعتها."},
]


class FlowTest(AdsBase):
    def start(self):
        self.script = Script(list(TURNS))
        self.make_app(self.script)
        self.app.stores.add("بيت العود", "shopify", self.shop.url, FakeShopify.TOKEN)
        tid = self.app.orchestrator.submit("كيف أداء حملاتي الإعلانية هذا الأسبوع؟ واقترح ما يلزم")
        wait_for(lambda: self.app.memory.get_task(tid)["status"] in ("waiting", "done", "failed"))
        return tid

    def test_approve_changes(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.assertEqual((step["agent"], step["status"]), ("ads_manager", "awaiting_approval"))
        self.assertTrue(all(t["ok"] for t in step["trace"]), step["trace"])
        self.assertEqual([a["kind"] for a in step["actions"]], ["ads_budget", "ads_pause"])
        self.assertIn('"ads_manager"', self.script.planner_calls[0][0]["content"])
        self.app.orchestrator.decide(step["id"], True)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        self.assertEqual(self.meta.ad_updates, [("601", {"daily_budget": 2400}), ("701", {"status": "PAUSED"})])
        task = self.app.memory.get_task(tid)
        self.assertEqual([a["status"] for a in task["steps"][0]["actions"]], ["done", "done"])
        self.assertIn("الإعلانات", task["final"])

    def test_conflict_when_changed_in_ads_manager(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.meta.campaigns["601"]["daily_budget"] = "2200"  # someone changed it meanwhile
        self.app.orchestrator.decide(step["id"], True)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        acts = self.app.memory.get_task(tid)["steps"][0]["actions"]
        self.assertEqual([a["status"] for a in acts], ["conflict", "done"])
        self.assertEqual(self.meta.ad_updates, [("701", {"status": "PAUSED"})])

    def test_reject(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.app.orchestrator.decide(step["id"], False)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        self.assertEqual(self.meta.ad_updates, [])
        self.assertIn("**الإعلانات:** رفضها المستخدم", self.app.memory.get_task(tid)["steps"][0]["output"])


if __name__ == "__main__":
    unittest.main()
