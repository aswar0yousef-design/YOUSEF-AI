import base64
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from yousef.app import build_app
from yousef.integrations.image_tools import ImageToolbox
from yousef.integrations.images import ImageError, find_image

from helpers import FakeProvider, XorCipher, factory_from, wait_for

JPEG = b"\xff\xd8\xff\xe0" + b"7" * 2000


class FakeImageApis:
    """OpenAI images and Gemini interactions, enough to test requests and fallbacks."""

    def __init__(self):
        self.requests = []
        self.refuse = {"gpt-image-2.5-flare"}   # e.g. the account has no access to the newest model
        self.gemini_interactions = True
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, dict(self.headers), body))
                b64 = base64.b64encode(JPEG).decode()
                if self.path.startswith("/v1/images/"):
                    if self.headers.get("Authorization") != "Bearer sk-openai-test":
                        return self._send(401, {"error": {"message": "Incorrect API key provided"}})
                    if body["model"] in outer.refuse:
                        return self._send(404, {"error": {"message": f"The model {body['model']} does not exist"}})
                    return self._send(200, {"data": [{"b64_json": b64}], "output_format": "jpeg"})
                if self.path == "/v1beta/interactions":
                    if not outer.gemini_interactions:
                        return self._send(404, {"error": {"message": "Not found"}})
                    return self._send(200, {"id": "i1", "outputs": [{"type": "text", "text": "ok"},
                                                                    {"type": "image", "mime_type": "image/jpeg", "data": b64}]})
                if self.path.endswith(":generateContent"):
                    return self._send(200, {"candidates": [{"content": {"parts": [
                        {"text": "here"}, {"inlineData": {"mimeType": "image/jpeg", "data": b64}}]}}]})
                return self._send(404, {"error": {"message": "unknown"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Script:
    def __init__(self, turns, agent="designer"):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.agent = agent

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            return json.dumps({"steps": [{"agent": self.agent, "instruction": "اصنع صورة"}]})
        if json_mode:
            return self.turns.pop(0) if self.turns else json.dumps({"done": "تم"})
        return "تم"


class ImageBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.apis = FakeImageApis()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.apis.close()
        self.dir.cleanup()

    def make_app(self, script=None, keys=("openai",)):
        provider = FakeProvider("ollama", responder=script or Script([]), models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}),
                             cipher=XorCipher(), image_urls={"openai_url": self.apis.url + "/v1",
                                                             "gemini_url": self.apis.url + "/v1beta"})
        if "openai" in keys:
            self.app.keystore.set("openai", "sk-openai-test")
        if "gemini" in keys:
            self.app.keystore.set("gemini", "AIza-gemini-test")
        return self.app


class MakerTest(ImageBase):
    def test_openai_falls_back_to_a_model_the_account_has(self):
        self.make_app()
        out = self.app.images.make("مبخرة نحاسية على طاولة خشبية، إضاءة دافئة", "portrait")
        self.assertEqual((out["data"], out["provider"], out["model"]), (JPEG, "openai", "gpt-image-2"))
        tried = [r[2]["model"] for r in self.apis.requests]
        self.assertEqual(tried, ["gpt-image-2.5-flare", "gpt-image-2"])
        self.assertEqual(self.apis.requests[-1][2]["size"], "1024x1280")  # 4:5 that Instagram accepts
        self.assertEqual(self.apis.requests[-1][2]["output_format"], "jpeg")
        self.app.images.make("نفس الصورة بخلفية بيضاء", "square", source=(JPEG, "image/jpeg"))
        path, _, body = self.apis.requests[-1]
        self.assertEqual(path, "/v1/images/edits")
        self.assertTrue(body["images"][0]["image_url"].startswith("data:image/jpeg;base64,"))

    def test_gemini_and_settings(self):
        self.make_app(keys=("gemini",))
        out = self.app.images.make("بخور", "landscape")
        self.assertEqual((out["provider"], out["model"]), ("gemini", "gemini-nano-banana-2.1"))
        path, headers, body = self.apis.requests[-1]
        self.assertEqual((path, body["response_format"]["aspect_ratio"]), ("/v1beta/interactions", "16:9"))
        self.apis.gemini_interactions = False  # an older endpoint setup: generateContent
        self.assertEqual(self.app.images.make("بخور")["data"], JPEG)
        self.assertTrue(self.apis.requests[-1][0].endswith("gemini-nano-banana-2.1:generateContent"))
        self.app.settings.update({"images": {"provider": "off"}})
        self.assertFalse(self.app.images.available)
        with self.assertRaises(ImageError):
            self.app.images.make("x")

    def test_find_image(self):
        b64 = base64.b64encode(b"\x89PNG....").decode()
        self.assertEqual(find_image({"a": [{"inline_data": {"mime_type": "image/png", "data": b64}}]})[1], "image/png")
        self.assertIsNone(find_image({"outputs": [{"type": "text", "text": "no picture"}]}))


class ToolTest(ImageBase):
    def test_tool_saves_and_limits(self):
        self.make_app()
        tb = ImageToolbox(self.app.images, self.app.media, limit=2)
        first = self.app.media.save(JPEG + b"x", "منتج")["name"]
        out = tb.run({"tool": "generate_image", "prompt": "العود على خلفية رخامية", "from": f"media:{first}"})
        self.assertIn("Created media:img-", out)
        made = tb.made[0]
        self.assertEqual(self.app.media.read(made)[0], JPEG)
        self.assertIn("openai", self.app.media.describe(self.app.media.path(made))["label"])
        tb.run({"tool": "generate_image", "prompt": "٢"})
        self.assertIn("limit reached", tb.run({"tool": "generate_image", "prompt": "٣"}))
        self.assertEqual([t["ok"] for t in tb.trace], [True, True, False])
        self.assertTrue(tb.trace[0]["note"].startswith("media:img-"))
        self.assertIn("media:" + first, tb.run({"tool": "list_media"}))


class DesignerFlowTest(ImageBase):
    def test_designer_makes_pictures_without_approval(self):
        script = Script([{"tool": "generate_image", "prompt": "باقة بخور فاخرة، تصوير منتجات", "shape": "square"},
                         {"done": "صنعت صورة مربعة للباقة."}])
        self.make_app(script)
        self.assertIn("designer", [a.key for a in __import__("yousef.core.agents", fromlist=["x"]).available_agents(
            self.app.orchestrator._connected())])
        tid = self.app.orchestrator.submit("اصنع صورة لباقة بخور فاخرة لإنستغرام")
        wait_for(lambda: self.app.memory.get_task(tid)["status"] in ("done", "failed"))
        task = self.app.memory.get_task(tid)
        step = task["steps"][0]
        self.assertEqual((task["status"], step["agent"]), ("done", "designer"))
        self.assertTrue(step["trace"][0]["note"].startswith("media:img-"))
        self.assertEqual(len(self.app.media.list()), 1)

    def test_no_key_no_designer(self):
        self.make_app(keys=())
        self.assertNotIn("images", self.app.orchestrator._connected())


if __name__ == "__main__":
    unittest.main()
