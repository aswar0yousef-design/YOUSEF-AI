import json
import tempfile
import unittest
from pathlib import Path

from yousef.config import DEFAULT_SETTINGS, Settings, SettingsError
from yousef.keystore import KeyStore, KeyStoreError
from yousef.memory import Memory, tokens

from helpers import XorCipher


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "settings.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_defaults_written(self):
        s = Settings(self.path)
        self.assertEqual(s.get(), DEFAULT_SETTINGS)
        self.assertTrue(self.path.exists())

    def test_update_merges_and_persists(self):
        s = Settings(self.path)
        s.update({"mode": "local_only", "routing": {"code": "local"}, "cloud_models": {"anthropic": " claude-sonnet-5-5 "}})
        again = Settings(self.path).get()
        self.assertEqual(again["mode"], "local_only")
        self.assertEqual(again["routing"]["code"], "local")
        self.assertEqual(again["routing"]["chat"], "local")  # untouched keys kept
        self.assertEqual(again["cloud_models"]["anthropic"], "claude-sonnet-5-5")

    def test_invalid_values_rejected(self):
        s = Settings(self.path)
        for patch in ({"mode": "turbo"}, {"ollama_url": "ftp://x"}, {"routing": {"code": "moon"}},
                      {"cloud_order": ["openai", "openai"]}, {"port": 80}, {"history_turns": 99}):
            with self.assertRaises(SettingsError, msg=patch):
                s.update(patch)
        self.assertEqual(s.get(), DEFAULT_SETTINGS)

    def test_unknown_keys_ignored(self):
        s = Settings(self.path)
        s.update({"api_key": "sk-should-not-be-here"})
        self.assertNotIn("api_key", s.get())
        self.assertNotIn("sk-should", self.path.read_text())

    def test_broken_file_recovers(self):
        self.path.write_text("{not json", encoding="utf-8")
        s = Settings(self.path)
        self.assertEqual(s.get(), DEFAULT_SETTINGS)
        self.assertTrue(self.path.with_suffix(".broken.json").exists())


class KeyStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "keys.dat"
        self.ks = KeyStore(self.path, cipher=XorCipher())

    def tearDown(self):
        self.dir.cleanup()

    def test_roundtrip_and_not_plaintext(self):
        key = "sk-ant-api03-TESTKEY-1234567890"
        self.ks.set("anthropic", key)
        self.assertEqual(self.ks.get("anthropic"), key)
        self.assertNotIn("TESTKEY", self.path.read_text())
        self.assertEqual(self.ks.status(), {"anthropic": True, "openai": False, "gemini": False})

    def test_delete(self):
        self.ks.set("openai", "sk-proj-abcdefghijk")
        self.ks.delete("openai")
        self.assertFalse(self.ks.has("openai"))

    def test_validation(self):
        with self.assertRaises(KeyStoreError):
            self.ks.set("mistral", "abcdefghijk")
        with self.assertRaises(KeyStoreError):
            self.ks.set("openai", "short")
        with self.assertRaises(KeyStoreError):
            self.ks.set("openai", "has space inside key")

    def test_unreadable_key_is_missing(self):
        self.path.write_text(json.dumps({"gemini": "!!!not-base64!!!"}))
        self.assertIsNone(self.ks.get("gemini"))


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.mem = Memory(Path(self.dir.name) / "m.db")

    def tearDown(self):
        self.mem.close()
        self.dir.cleanup()

    def test_arabic_tokens_normalized(self):
        self.assertIn("منشورات", tokens("المنشورات"))
        self.assertEqual(tokens("إنستغرام"), tokens("انستغرام"))

    def test_relevant_lessons(self):
        self.mem.add_lesson("rule", "اكتب بلهجة عمانية")
        self.mem.add_lesson("failure", "الطلب: «منشورات انستغرام للعطور» — طويلة جدًا")
        self.mem.add_lesson("success", "الطلب: «كود بايثون لقراءة CSV»")
        found = self.mem.relevant_lessons("اكتب منشورات إنستغرام لمتجر العطور")
        kinds = [l["kind"] for l in found]
        self.assertIn("rule", kinds)
        self.assertIn("failure", kinds)
        self.assertNotIn("success", kinds)

    def test_tasks_steps_and_interrupt(self):
        tid = self.mem.create_task("hello")
        sids = self.mem.add_steps(tid, [{"agent": "general", "instruction": "a"},
                                       {"agent": "coder", "instruction": "b", "needs_confirmation": True}])
        self.mem.update_task(tid, status="running")
        self.mem.update_step(sids[0], status="done", output="x")
        self.assertEqual(self.mem.mark_interrupted(), 1)
        task = self.mem.get_task(tid)
        self.assertEqual(task["status"], "interrupted")
        self.assertEqual([s["status"] for s in task["steps"]], ["done", "skipped"])
        self.assertTrue(task["steps"][1]["needs_confirmation"])

    def test_turns_and_new_conversation(self):
        tid = self.mem.create_task("q1")
        self.mem.update_task(tid, status="done", final="a1")
        self.assertEqual(self.mem.recent_turns(4), [{"role": "user", "content": "q1"},
                                                    {"role": "assistant", "content": "a1"}])
        self.mem.new_conversation()
        self.assertEqual(self.mem.recent_turns(4), [])
        self.assertEqual(self.mem.list_tasks(), [])

    def test_rejects_unknown_fields(self):
        tid = self.mem.create_task("x")
        with self.assertRaises(ValueError):
            self.mem.update_task(tid, request="changed")


if __name__ == "__main__":
    unittest.main()
