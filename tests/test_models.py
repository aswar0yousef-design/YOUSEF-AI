import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yousef.config import Settings
from yousef.keystore import KeyStore
from yousef.models import NoModelError, Router
from yousef.models import providers as P
from yousef.models.base import NO_PROXY_OPENER, ProviderError, clean_output, http_json, split_system
from yousef.models.router import default_factory

from helpers import FakeOllama, FakeProvider, XorCipher, factory_from


class SplitSystemTest(unittest.TestCase):
    def test_alternation(self):
        system, turns = split_system([
            {"role": "system", "content": "S"},
            {"role": "assistant", "content": "dangling"},
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
        ])
        self.assertEqual(system, "S")
        self.assertEqual(turns, [{"role": "user", "content": "a\n\nb"}, {"role": "assistant", "content": "c"}])

    def test_clean_output(self):
        self.assertEqual(clean_output("<think>x\ny</think>\n  Hi"), "Hi")


class ProviderPayloadTest(unittest.TestCase):
    MSGS = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]

    def test_anthropic(self):
        with mock.patch.object(P, "http_json", return_value={"content": [{"type": "text", "text": "ok"}]}) as h:
            out = P.AnthropicProvider("k-123456789").chat("claude-sonnet-5-5", self.MSGS)
        self.assertEqual(out, "ok")
        _, url = h.call_args.args
        kw = h.call_args.kwargs
        self.assertTrue(url.endswith("/v1/messages"))
        self.assertEqual(kw["headers"]["x-api-key"], "k-123456789")
        self.assertEqual(kw["body"]["system"], "SYS")
        self.assertEqual(kw["body"]["messages"], [{"role": "user", "content": "hi"}])

    def test_openai_json_mode(self):
        reply = {"choices": [{"message": {"content": "{}"}}]}
        with mock.patch.object(P, "http_json", return_value=reply) as h:
            P.OpenAIProvider("sk-123456789").chat("gpt-x", self.MSGS, json_mode=True)
        body = h.call_args.kwargs["body"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["messages"], self.MSGS)
        self.assertEqual(h.call_args.kwargs["headers"]["Authorization"], "Bearer sk-123456789")

    def test_gemini(self):
        reply = {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": "مرحبا"}]}}]}
        msgs = self.MSGS + [{"role": "assistant", "content": "x"}, {"role": "user", "content": "y"}]
        with mock.patch.object(P, "http_json", return_value=reply) as h:
            out = P.GeminiProvider("AIza123456789").chat("models/gemini-pro", msgs, json_mode=True)
        self.assertEqual(out, "مرحبا")
        _, url = h.call_args.args
        body = h.call_args.kwargs["body"]
        self.assertTrue(url.endswith("/models/gemini-pro:generateContent"))
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model", "user"])
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "SYS")
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")

    def test_gemini_blocked(self):
        with mock.patch.object(P, "http_json", return_value={"promptFeedback": {"blockReason": "SAFETY"}}):
            with self.assertRaises(P.ProviderError):
                P.GeminiProvider("AIza123456789").chat("gemini-pro", self.MSGS)

    def test_openai_model_filter(self):
        reply = {"data": [{"id": "gpt-x"}, {"id": "text-embedding-3"}, {"id": "whisper-1"}, {"id": "o4-mini"}]}
        with mock.patch.object(P, "http_json", return_value=reply):
            self.assertEqual(P.OpenAIProvider("sk-123456789").list_models(), ["gpt-x", "o4-mini"])


class OllamaLiveTest(unittest.TestCase):
    """Real HTTP against a fake Ollama server."""

    def setUp(self):
        self.ollama = FakeOllama()

    def tearDown(self):
        self.ollama.close()

    def test_models_and_chat(self):
        p = P.OllamaProvider(self.ollama.url)
        self.assertEqual(p.chat_models(), ["qwen2.5:7b"])
        text = p.chat("qwen2.5:7b", [{"role": "user", "content": "hi"}], json_mode=True)
        self.assertIn("steps", text)
        self.assertEqual(self.ollama.requests[-1]["format"], "json")
        self.assertFalse(self.ollama.requests[-1]["stream"])

    def test_http_errors_become_provider_errors(self):
        with self.assertRaises(P.ProviderError) as cm:
            P.OllamaProvider(self.ollama.url).chat("missing:1b", [{"role": "user", "content": "hi"}])
        self.assertIn("404", str(cm.exception))
        self.ollama.close()
        with self.assertRaises(P.ProviderError):
            http_json("GET", f"{self.ollama.url}/api/tags", timeout=2, opener=NO_PROXY_OPENER)
        self.ollama = FakeOllama()  # for tearDown


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.settings = Settings(root / "s.json")
        self.keys = KeyStore(root / "k.dat", cipher=XorCipher())
        self.local = FakeProvider("ollama", models=["qwen2.5:7b"], is_local=True)
        self.claude = FakeProvider("anthropic")
        self.gemini = FakeProvider("gemini")
        self.providers = {"ollama": self.local, "anthropic": self.claude, "gemini": self.gemini}
        self.settings.update({"cloud_models": {"anthropic": "claude-sonnet-5-5", "gemini": "gemini-x"}})
        self.router = Router(self.settings, self.keys, factory_from(self.providers))

    def tearDown(self):
        self.dir.cleanup()

    def names(self, task_type):
        return [(p.name, m) for p, m in self.router.candidates(task_type)]

    def test_default_routing(self):
        self.assertEqual(self.names("chat")[0], ("ollama", "qwen2.5:7b"))
        self.assertEqual(self.names("code"), [("anthropic", "claude-sonnet-5-5"), ("gemini", "gemini-x"),
                                              ("ollama", "qwen2.5:7b")])

    def test_local_only_mode(self):
        self.settings.update({"mode": "local_only"})
        self.assertEqual(self.names("code"), [("ollama", "qwen2.5:7b")])

    def test_cloud_first_mode(self):
        self.settings.update({"mode": "cloud_first"})
        self.assertEqual(self.names("chat")[0][0], "anthropic")

    def test_cloud_without_model_skipped(self):
        self.settings.update({"cloud_models": {"anthropic": ""}})
        self.assertNotIn("anthropic", [n for n, _ in self.names("code")])

    def test_fallback_on_failure(self):
        self.claude.fail = True
        result = self.router.complete("code", [{"role": "user", "content": "x"}])
        self.assertEqual(result.provider, "gemini")

    def test_ollama_down_uses_cloud(self):
        self.local.fail = True
        self.router.ollama_status(refresh=True)
        self.assertEqual(self.names("chat")[0][0], "anthropic")

    def test_no_models(self):
        router = Router(self.settings, self.keys, factory_from({}))
        with self.assertRaises(NoModelError):
            router.complete("chat", [{"role": "user", "content": "x"}])

    def test_all_fail(self):
        for p in self.providers.values():
            p.fail = True
        self.router.ollama_status(refresh=True)
        with self.assertRaises(NoModelError) as cm:
            self.router.complete("code", [{"role": "user", "content": "x"}])
        self.assertIn("anthropic", str(cm.exception))

    def test_default_factory_needs_key(self):
        s = self.settings.get()
        self.assertIsNone(default_factory("anthropic", s, self.keys))
        self.keys.set("anthropic", "sk-ant-123456789")
        self.assertIsInstance(default_factory("anthropic", s, self.keys), P.AnthropicProvider)
        self.assertIsInstance(default_factory("ollama", s, self.keys), P.OllamaProvider)


if __name__ == "__main__":
    unittest.main()


class ReasoningModelTest(unittest.TestCase):
    """gpt-oss and empty JSON answers from Ollama."""

    def setUp(self):
        import threading as _th
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.requests = []
        self.replies = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                outer.requests.append(body)
                content, reason = outer.replies.pop(0) if outer.replies else ('{"done": "ok"}', "stop")
                status = 200
                if reason == "crash":
                    status, data = 500, json.dumps({"error": content}).encode()
                else:
                    data = json.dumps({"message": {"role": "assistant", "content": content, "thinking": "..."},
                                       "done": True, "done_reason": reason}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        _th.Thread(target=self.server.serve_forever, daemon=True).start()
        from yousef.models.providers import OllamaProvider
        self.provider = OllamaProvider(f"http://127.0.0.1:{self.server.server_port}", 8192)
        self.provider.retry_pause = 0

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_gpt_oss_gets_words_not_a_grammar(self):
        msgs = [{"role": "system", "content": "You are a tool user."}, {"role": "user", "content": "go"}]
        self.replies = [('{"tool": "list_stores"}', "stop")]
        self.assertEqual(self.provider.chat("gpt-oss:20b", msgs, json_mode=True), '{"tool": "list_stores"}')
        body = self.requests[0]
        self.assertNotIn("format", body)
        self.assertEqual((body["think"], body["options"]["num_ctx"]), ("low", 8192))  # the user's context size
        self.assertIn("single JSON object only", body["messages"][0]["content"])
        self.assertEqual(msgs[0]["content"], "You are a tool user.")  # the caller's messages are untouched
        self.provider.chat("gpt-oss:20b", msgs)  # plain chat: reasoning level only
        self.assertNotIn("single JSON object", self.requests[1]["messages"][0]["content"])
        self.assertEqual(self.requests[1]["think"], "low")

    def test_empty_json_answer_is_asked_again(self):
        msgs = [{"role": "user", "content": "go"}]
        self.replies = [("", "stop"), ('{"done": "تم"}', "stop")]
        self.assertEqual(self.provider.chat("qwen2.5:7b", msgs, json_mode=True), '{"done": "تم"}')
        first, second = self.requests
        self.assertEqual(first["format"], "json")
        self.assertNotIn("think", first)  # qwen2.5 has no reasoning switch; sending one would be an error
        self.assertNotIn("format", second)
        self.assertIn("single JSON object only", second["messages"][0]["content"])
        self.replies = [("", "length"), ("", "length")]
        with self.assertRaises(ProviderError) as cm:
            self.provider.chat("qwen2.5:7b", msgs, json_mode=True)
        self.assertIn("ran out of context", str(cm.exception))

    def test_runner_crash_is_retried_then_explained(self):
        crash = ("llama-server process has terminated: exit status 0xc0000409: The system detected an overrun of a "
                 "stack-based buffer in this application.: CUDA error: shared object initialization failed")
        msgs = [{"role": "user", "content": "مرحبا"}]
        self.replies = [(crash, "crash"), ("أهلًا", "stop")]
        self.assertEqual(self.provider.chat("gpt-oss:20b", msgs), "أهلًا")  # Ollama restarts its runner
        self.assertEqual(len(self.requests), 2)
        self.replies = [(crash, "crash"), (crash, "crash")]
        with self.assertRaises(ProviderError) as cm:
            self.provider.chat("gpt-oss:20b", msgs)
        self.assertIn("ذاكرة كرت الشاشة", str(cm.exception))
        self.assertIn("qwen2.5:7b", str(cm.exception))
        self.replies = [('json: unknown field "think"', "crash"), ("ok", "stop")]
        self.assertEqual(self.provider.chat("gpt-oss:20b", msgs), "ok")  # an older Ollama without reasoning levels
        self.assertNotIn("think", self.requests[-1])
