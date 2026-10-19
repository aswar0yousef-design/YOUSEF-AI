import tempfile
import unittest

from yousef.app import build_app
from yousef.core.orchestrator import parse_plan
from yousef.core.permissions import requires_approval

from helpers import FakeProvider, XorCipher, factory_from, plan_json, wait_for


class PermissionsTest(unittest.TestCase):
    def test_actions_need_approval(self):
        for text in ["انشر المنشور على إنستغرام", "أرسل الإيميل للعميل", "اكتب المنشور ثم انشره",
                     "احذف المنتجات القديمة", "Publish the post to Instagram", "Write it and then send it to the team",
                     "Delete old orders", "نفذ صفقة شراء على BTC"]:
            self.assertTrue(requires_approval(text, False), text)

    def test_drafting_does_not(self):
        for text in ["اكتب منشورًا لنشره على إنستغرام", "حلل سلوك الشراء عند العملاء",
                     "Write a post to publish later", "Draft an email for the supplier", "قارن أسعار المنافسين"]:
            self.assertFalse(requires_approval(text, False), text)

    def test_planner_flag_wins(self):
        self.assertTrue(requires_approval("anything", True))


class ParsePlanTest(unittest.TestCase):
    def test_fenced_and_unknown_agent(self):
        steps = parse_plan('```json\n{"steps":[{"agent":"wizard","instruction":"do it"}]}\n```')
        self.assertEqual(steps, [{"agent": "general", "instruction": "do it", "needs_confirmation": False}])

    def test_planned_agents_not_assigned(self):
        steps = parse_plan(plan_json(("store_manager", "update products", False)))
        self.assertEqual(steps[0]["agent"], "general")

    def test_keyword_overrides_flag(self):
        steps = parse_plan(plan_json(("writer", "انشر المنشور", False)))
        self.assertTrue(steps[0]["needs_confirmation"])

    def test_text_around_json_and_limits(self):
        many = plan_json(*[("coder", f"step {i}", False) for i in range(9)])
        self.assertEqual(len(parse_plan("Here you go: " + many + " done")), 5)

    def test_garbage(self):
        for bad in ["no json here", '{"steps": []}', '{"steps": [{"agent": "coder"}]}']:
            with self.assertRaises(ValueError):
                parse_plan(bad)


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.plan = plan_json(("analyst", "حلّل المنافسين", False), ("writer", "اكتب خطة التسويق", False))
        self.local = FakeProvider("ollama", responder=self.respond, models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(self.dir.name, factory=factory_from({"ollama": self.local}), cipher=XorCipher())

    def respond(self, messages, json_mode):
        if json_mode:
            return self.plan
        last = messages[-1]["content"]
        if "Write the final answer" in last:
            return "FINAL ANSWER"
        return f"result for: {last[-30:]}"

    def tearDown(self):
        self.app.close()
        self.dir.cleanup()

    def run_task(self, request):
        orch = self.app.orchestrator
        tid = orch.submit(request)
        orch.wait(tid, 10)
        return self.app.memory.get_task(tid)

    def test_multi_step_flow(self):
        task = self.run_task("حلّل منافسي متجر العطور واكتب خطة تسويق لشهر كامل مع منشورات")
        self.assertEqual(task["status"], "done", task.get("error"))
        self.assertEqual([s["agent"] for s in task["steps"]], ["analyst", "writer"])
        self.assertTrue(all(s["status"] == "done" and s["provider"] == "ollama" for s in task["steps"]))
        self.assertEqual(task["final"], "FINAL ANSWER")
        # the second agent saw the first agent's result
        writer_call = [c for c in self.local.calls if not c["json_mode"]][1]
        self.assertIn("Result of an earlier step", writer_call["messages"][-1]["content"])

    def test_short_request_skips_planner(self):
        task = self.run_task("مرحبا")
        self.assertEqual(task["status"], "done")
        self.assertFalse(any(c["json_mode"] for c in self.local.calls))
        self.assertEqual(task["final"], "result for: مرحبا")

    def test_bad_plan_falls_back_to_one_step(self):
        self.plan = "I think you should... (no json)"
        task = self.run_task("اشرح لي الفرق بين Shopify و WooCommerce للمتاجر الصغيرة")
        self.assertEqual(task["status"], "done")
        self.assertEqual(len(task["steps"]), 1)
        self.assertEqual(task["steps"][0]["agent"], "general")

    def test_approval_approve(self):
        self.plan = plan_json(("writer", "اكتب المنشور", False), ("writer", "انشر المنشور على إنستغرام", True))
        orch, mem = self.app.orchestrator, self.app.memory
        tid = orch.submit("اكتب منشورًا عن العرض الجديد وانشره على إنستغرام")
        step = wait_for(lambda: next((s for s in mem.get_task(tid)["steps"] if s["status"] == "awaiting_approval"), None))
        self.assertEqual(mem.get_task(tid)["status"], "waiting")
        self.assertEqual(step["idx"], 1)
        self.assertFalse(orch.decide(mem.get_task(tid)["steps"][0]["id"], True))  # not waiting
        self.assertTrue(orch.decide(step["id"], True))
        orch.wait(tid, 10)
        task = mem.get_task(tid)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["steps"][1]["status"], "done")

    def test_approval_reject(self):
        self.plan = plan_json(("writer", "احذف المنتجات القديمة", True))
        orch, mem = self.app.orchestrator, self.app.memory
        tid = orch.submit("نظّف المتجر من المنتجات القديمة واحذفها كلها")
        step = wait_for(lambda: next((s for s in mem.get_task(tid)["steps"] if s["status"] == "awaiting_approval"), None))
        orch.decide(step["id"], False)
        orch.wait(tid, 10)
        task = mem.get_task(tid)
        self.assertEqual(task["steps"][0]["status"], "skipped")
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["final"], "لم تُنفَّذ أي خطوة.")

    def test_cancel_while_waiting(self):
        self.plan = plan_json(("writer", "انشر الآن", True))
        orch, mem = self.app.orchestrator, self.app.memory
        tid = orch.submit("انشر إعلان التخفيضات على كل الحسابات الآن")
        wait_for(lambda: mem.get_task(tid)["status"] == "waiting")
        self.assertTrue(orch.cancel(tid))
        orch.wait(tid, 10)
        task = mem.get_task(tid)
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(task["steps"][0]["status"], "skipped")

    def test_no_model_fails_clearly(self):
        self.local.fail = True
        self.app.router.ollama_status(refresh=True)
        task = self.run_task("اكتب لي قصيدة قصيرة عن البحر في مسقط")
        self.assertEqual(task["status"], "failed")
        self.assertIn("Ollama", task["error"])

    def test_feedback_becomes_lesson_and_is_used(self):
        task = self.run_task("اكتب منشورات انستغرام لمتجر العطور")
        self.assertTrue(self.app.orchestrator.feedback(task["id"], False, "طويلة جدًا، أريدها أقصر"))
        self.app.memory.add_lesson("rule", "استخدم اللهجة العمانية")
        self.local.calls.clear()
        self.run_task("اكتب منشورات انستغرام جديدة لمتجر العطور")
        planner = next(c for c in self.local.calls if c["json_mode"])
        system = planner["messages"][0]["content"]
        self.assertIn("طويلة جدًا", system)
        self.assertIn("اللهجة العمانية", system)
        agent_system = next(c for c in self.local.calls if not c["json_mode"])["messages"][0]["content"]
        self.assertIn("اللهجة العمانية", agent_system)

    def test_history_is_context(self):
        self.run_task("اسم متجري هو بخور الريّان وأبيع العطور العُمانية")
        self.local.calls.clear()
        self.run_task("اكتب وصفًا قصيرًا للمتجر على إنستغرام")
        agent_call = next(c for c in self.local.calls if not c["json_mode"])
        self.assertTrue(any("بخور الريّان" in m["content"] for m in agent_call["messages"][1:-1]))

    def test_empty_and_long_requests(self):
        with self.assertRaises(ValueError):
            self.app.orchestrator.submit("   ")
        with self.assertRaises(ValueError):
            self.app.orchestrator.submit("x" * 9000)


if __name__ == "__main__":
    unittest.main()
