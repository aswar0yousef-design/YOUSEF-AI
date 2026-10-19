import http.client
import json
import tempfile
import threading
import unittest

from yousef.app import build_app
from yousef.server import make_server

from helpers import FakeOllama, XorCipher, plan_json, wait_for


class ServerTestBase(unittest.TestCase):
    factory = None

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ollama = FakeOllama(planner_reply=plan_json(
            ("analyst", "حلّل المنافسين", False), ("writer", "انشر المنشور", True)))
        self.app = build_app(self.dir.name, cipher=XorCipher())
        self.app.settings.update({"ollama_url": self.ollama.url})
        self.server = make_server(self.app, 0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.ollama.close()
        self.dir.cleanup()

    def call(self, method, path, body=None, headers=None, client=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if client:
            h["X-Yousef-Client"] = "1"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        ctype = resp.getheader("Content-Type", "")
        payload = json.loads(raw) if ctype.startswith("application/json") else raw.decode()
        return resp.status, payload, resp


class ApiTest(ServerTestBase):
    def test_dashboard_served_with_csp(self):
        status, body, resp = self.call("GET", "/", client=False)
        self.assertEqual(status, 200)
        self.assertIn("YOUSEF AI", body)
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        for path in ("/app.js", "/style.css", "/icon.svg"):
            self.assertEqual(self.call("GET", path, client=False)[0], 200, path)

    def test_status(self):
        status, body, _ = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["ollama"]["reachable"])
        self.assertEqual(body["ollama"]["active_model"], "qwen2.5:7b")  # embed model filtered out
        self.assertFalse(body["cloud"]["anthropic"]["has_key"])

    def test_full_task_with_approval(self):
        status, body, _ = self.call("POST", "/api/tasks", {"request": "حلّل منافسي متجر العطور واكتب منشورًا وانشره"})
        self.assertEqual(status, 201)
        tid = body["task"]["id"]

        def waiting_step():
            task = self.call("GET", f"/api/tasks/{tid}")[1]["task"]
            return next((s for s in task["steps"] if s["status"] == "awaiting_approval"), None)

        step = wait_for(waiting_step)
        status, _, _ = self.call("POST", f"/api/steps/{step['id']}/decision", {"approve": True})
        self.assertEqual(status, 200)
        task = wait_for(lambda: (t := self.call("GET", f"/api/tasks/{tid}")[1]["task"])["status"] == "done" and t)
        self.assertEqual([s["provider"] for s in task["steps"]], ["ollama", "ollama"])
        self.assertNotIn("<think>", task["final"])
        listed = self.call("GET", "/api/tasks")[1]["tasks"]
        self.assertEqual([t["id"] for t in listed], [tid])

        status, _, _ = self.call("POST", f"/api/tasks/{tid}/feedback", {"good": True, "note": "ممتاز"})
        self.assertEqual(status, 200)
        lessons = self.call("GET", "/api/lessons")[1]["lessons"]
        self.assertEqual(lessons[0]["kind"], "success")

    def test_keys_never_returned(self):
        secret = "sk-ant-SECRET-abcdef123456"
        status, body, _ = self.call("PUT", "/api/keys/anthropic", {"key": secret})
        self.assertEqual((status, body["keys"]["anthropic"]), (200, True))
        for path in ("/api/settings", "/api/status"):
            raw = json.dumps(self.call("GET", path)[1])
            self.assertNotIn("SECRET", raw, path)
        self.assertEqual(self.call("PUT", "/api/keys/anthropic", {"key": "bad key"})[0], 400)
        self.assertEqual(self.call("DELETE", "/api/keys/anthropic")[1]["keys"]["anthropic"], False)

    def test_settings_validation(self):
        status, body, _ = self.call("PUT", "/api/settings", {"mode": "local_only"})
        self.assertEqual((status, body["settings"]["mode"]), (200, "local_only"))
        status, body, _ = self.call("PUT", "/api/settings", {"mode": "nope"})
        self.assertEqual(status, 400)

    def test_models_endpoint(self):
        self.assertEqual(self.call("GET", "/api/models/ollama")[1]["models"], ["qwen2.5:7b"])
        self.assertEqual(self.call("GET", "/api/models/anthropic")[0], 400)  # no key yet
        self.assertEqual(self.call("GET", "/api/models/unknown")[0], 404)

    def test_rules(self):
        status, body, _ = self.call("POST", "/api/lessons", {"text": "اكتب بلهجة عمانية"})
        self.assertEqual(status, 201)
        lid = body["id"]
        self.assertEqual(self.call("POST", "/api/lessons", {"text": "  "})[0], 400)
        self.assertEqual(self.call("DELETE", f"/api/lessons/{lid}")[1]["lessons"], [])

    def test_errors(self):
        self.assertEqual(self.call("POST", "/api/tasks", {"request": ""})[0], 400)
        self.assertEqual(self.call("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/tasks")[0], 405)
        self.assertEqual(self.call("GET", "/api/tasks/999")[0], 404)
        self.assertEqual(self.call("POST", "/api/steps/999/decision", {"approve": True})[0], 409)
        self.assertEqual(self.call("GET", "/../keys.dat", client=False)[0], 404)


class SecurityTest(ServerTestBase):
    def test_write_requires_client_header(self):
        status, _, _ = self.call("POST", "/api/tasks", {"request": "hi"}, client=False)
        self.assertEqual(status, 403)

    def test_foreign_origin_rejected(self):
        status, _, _ = self.call("POST", "/api/tasks", {"request": "hi"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_foreign_host_rejected(self):
        status, _, _ = self.call("GET", "/api/status", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(status, 403)

    def test_form_post_rejected(self):
        status, _, _ = self.call("PUT", "/api/settings", headers={"Content-Type": "text/plain", "Content-Length": "2"},
                                 body=None)
        self.assertIn(status, (400, 415))


if __name__ == "__main__":
    unittest.main()
