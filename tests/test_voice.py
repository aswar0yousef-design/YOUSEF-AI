import http.client
import json
import math
import struct
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from yousef.app import build_app
from yousef.config import Settings, SettingsError
from yousef.server import make_server
from yousef.voice import (AudioError, VoiceEngine, VoiceNotInstalled, VoiceNotReady, classify_reply, is_noise,
                          read_wav, split_wake, write_wav)
from yousef.voice import engine as engine_mod

from helpers import FakeProvider, XorCipher, factory_from, wait_for

try:
    import numpy  # noqa: F401  (loaded once here so module patching never reloads it)
except ImportError:
    numpy = None

WAKE = ["يوسف", "يا يوسف", "Yousef", "Youssef", "Yusuf"]


def tone(seconds=1.0, rate=16000, channels=1):
    frames = int(seconds * rate)
    samples = []
    for i in range(frames):
        v = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
        samples.extend([v] * channels)
    return struct.pack(f"<{len(samples)}h", *samples)


def wav(seconds=1.0, rate=16000, channels=1):
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(tone(seconds, rate, channels))
    return buf.getvalue()


class AudioTest(unittest.TestCase):
    def test_mono(self):
        clip = read_wav(wav(1.0))
        self.assertEqual(clip.rate, 16000)
        self.assertAlmostEqual(clip.seconds, 1.0, places=2)

    def test_stereo_downmixed(self):
        clip = read_wav(wav(0.5, rate=48000, channels=2))
        self.assertAlmostEqual(clip.seconds, 0.5, places=2)
        self.assertEqual(len(clip.pcm), 48000 // 2 * 2)

    def test_rejects(self):
        with self.assertRaises(AudioError):
            read_wav(b"not a wav")
        with self.assertRaises(AudioError):
            read_wav(wav(0.05))
        with self.assertRaises(AudioError):
            read_wav(wav(61, rate=8000))
        self.assertEqual(read_wav(write_wav(tone(0.5))).rate, 16000)


class WakeTest(unittest.TestCase):
    def test_wake_and_command(self):
        cases = {
            "يا يوسف، اكتب منشورًا عن العطور": (True, "اكتب منشورًا عن العطور"),
            "يوسف": (True, ""),
            "يُوسف! افتح الإعدادات": (True, "افتح الإعدادات"),
            "هلا يوسف كيف الحال؟": (True, "كيف الحال؟"),
            "يايوسف اكتب لي": (True, "اكتب لي"),
            "Yousef, write a post about perfume": (True, "write a post about perfume"),
            "Hey Youssef!": (True, ""),
            "يوسيف اكتب": (True, "اكتب"),
            "اكتب قصة عن يوسف": (False, "اكتب قصة عن يوسف"),
            "قال يوسف إنه سيأتي": (False, "قال يوسف إنه سيأتي"),
            "يونس اكتب": (False, "يونس اكتب"),
        }
        for text, expected in cases.items():
            self.assertEqual(split_wake(text, WAKE), expected, text)

    def test_replies(self):
        cases = {"وافق": "approve", "نعم نفذ": "approve", "أيوه": "approve", "yes": "approve", "OK, do it": "approve",
                 "لا تنفذ": "reject", "ألغِ": "reject", "No.": "reject", "ارفض الخطوة": "reject",
                 "ربما": None, "اكتب لي منشورًا طويلًا عن العطور العمانية القديمة": None, "": None}
        for text, expected in cases.items():
            self.assertEqual(classify_reply(text), expected, text)

    def test_noise(self):
        for text in ["ترجمة نانسي قنقر", "", "  ...  ", "Thank you for watching!", "اشتركوا في القناة"]:
            self.assertTrue(is_noise(text), text)
        for text in ["اكتب منشور", "Thank you Yousef, now write the plan for next week please"]:
            self.assertFalse(is_noise(text), text)


class FakeBackend:
    def __init__(self, model, device, root, script=None, fail=False):
        if fail:
            raise RuntimeError("cudnn_ops64_9.dll not found")
        self.model, self.device = model, device
        self.script = list(script or [])
        self.calls = []

    def transcribe(self, clip, language, prompt, prompts=None):
        self.calls.append((clip.seconds, language, prompt))
        text = self.script.pop(0) if self.script else "يا يوسف، اكتب منشورًا"
        return text, "ar"


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.settings = Settings(Path(self.dir.name) / "s.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_not_installed(self):
        with mock.patch.object(engine_mod, "whisper_installed", return_value=False):
            eng = VoiceEngine(self.settings, Path(self.dir.name))
            self.assertEqual(eng.status()["state"], "not_installed")
            with self.assertRaises(VoiceNotInstalled):
                eng.transcribe(wav())
            with self.assertRaises(VoiceNotInstalled):
                eng.load()

    def test_lifecycle(self):
        backends = []

        def factory(model, device, root):
            b = FakeBackend(model, device, root, script=["يا يوسف، حلل المنافسين", "ترجمة نانسي قنقر"])
            backends.append(b)
            return b

        eng = VoiceEngine(self.settings, Path(self.dir.name), backend_factory=factory)
        self.assertEqual(eng.status()["state"], "idle")
        with self.assertRaises(VoiceNotReady):
            eng.transcribe(wav())  # starts loading
        eng.wait_loaded()
        self.assertEqual(eng.status()["state"], "ready")
        result = eng.transcribe(wav(1.5))
        self.assertEqual((result["wake"], result["command"]), (True, "حلل المنافسين"))
        self.assertEqual(result["seconds"], 1.5)
        self.assertIsNone(backends[0].calls[0][1])  # language auto
        self.assertIn("يوسف", backends[0].calls[0][2])  # prompt biases the name
        noise = eng.transcribe(wav())
        self.assertTrue(noise["noise"])
        self.assertEqual(noise["text"], "")
        self.assertFalse(noise["wake"])

        self.settings.update({"voice": {"model": "base", "language": "ar"}})
        self.assertEqual(eng.status()["state"], "idle")  # needs reload
        eng.load()
        eng.wait_loaded()
        self.assertEqual(backends[-1].model, "base")
        eng.transcribe(wav())
        self.assertEqual(backends[-1].calls[0][1], "ar")
        self.assertTrue(backends[-1].calls[0][2].startswith("يا يوسف، اكتب منشورًا"))  # an Arabic sentence

    def test_load_error(self):
        eng = VoiceEngine(self.settings, Path(self.dir.name),
                          backend_factory=lambda m, d, r: FakeBackend(m, d, r, fail=True))
        eng.load()
        eng.wait_loaded()
        status = eng.status()
        self.assertEqual(status["state"], "error")
        self.assertIn("cudnn", status["error"])
        with self.assertRaises(VoiceNotReady) as cm:
            eng.transcribe(wav())
        self.assertIn("cudnn", str(cm.exception))

    def test_voice_settings_validation(self):
        s = self.settings.update({"voice": {"wake_words": "يوسف، Jarvis"}})
        self.assertEqual(s["voice"]["wake_words"], ["يوسف", "Jarvis"])
        self.assertEqual(s["voice"]["model"], "large-v3-turbo")
        for bad in ({"model": "huge"}, {"device": "tpu"}, {"language": "fr"}, {"wake_words": []},
                    {"wake_words": ["x"]}, {"preload": "yes"}):
            with self.assertRaises(SettingsError, msg=bad):
                self.settings.update({"voice": bad})


@unittest.skipIf(numpy is None, "numpy not installed")
class WhisperBackendTest(unittest.TestCase):
    """Runs the real WhisperBackend code against a stand-in faster_whisper module."""

    def test_calls_faster_whisper_correctly(self):
        captured = {}

        class Seg:
            def __init__(self, text, nsp=0.1, lp=-0.3):
                self.text, self.no_speech_prob, self.avg_logprob = text, nsp, lp

        class WhisperModel:
            def __init__(self, model, **kw):
                captured["init"] = (model, kw)

            def transcribe(self, audio, **kw):
                captured["audio"] = audio
                captured["kw"] = kw
                segs = [Seg(" يا يوسف،"), Seg(" اكتب منشورًا"), Seg(" شكرا", nsp=0.9, lp=-1.5)]
                return iter(segs), types.SimpleNamespace(language="ar")

        fake = types.ModuleType("faster_whisper")
        fake.WhisperModel = WhisperModel
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
            backend = engine_mod.WhisperBackend("small", "cpu", Path("/tmp/x"))
            clip = read_wav(wav(1.0, rate=48000))
            text, lang = backend.transcribe(clip, None, "يوسف، Yousef.")
        self.assertEqual((text, lang), ("يا يوسف، اكتب منشورًا", "ar"))
        model, kw = captured["init"]
        self.assertEqual((model, kw["device"], kw["compute_type"]), ("small", "cpu", "int8"))
        audio = captured["audio"]
        self.assertEqual(str(audio.dtype), "float32")
        self.assertEqual(len(audio), 16000)  # resampled 48k -> 16k
        self.assertLessEqual(float(abs(audio).max()), 1.0)
        self.assertTrue(captured["kw"]["vad_filter"])
        self.assertIsNone(captured["kw"]["language"])


    def test_auto_reads_other_languages_as_arabic_or_english(self):
        calls = []

        class Seg:
            def __init__(self, text):
                self.text, self.no_speech_prob, self.avg_logprob = text, 0.1, -0.3

        class WhisperModel:
            def __init__(self, model, **kw):
                pass

            def transcribe(self, audio, **kw):
                calls.append((kw["language"], kw["initial_prompt"]))
                if kw["language"] is None:  # Gulf Arabic heard as Persian
                    info = types.SimpleNamespace(language="fa", language_probability=0.41,
                                                 all_language_probs=[("fa", 0.41), ("ar", 0.38), ("ur", 0.1), ("en", 0.02)])
                    return iter([Seg(" گیبریش")]), info
                return iter([Seg(" يا يوسف، كم طلب اليوم؟")]), types.SimpleNamespace(language=kw["language"])

        fake = types.ModuleType("faster_whisper")
        fake.WhisperModel = WhisperModel
        texts = engine_mod.prompts(["يوسف", "Yousef"])
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
            backend = engine_mod.WhisperBackend("large-v3-turbo", "cpu", Path("/tmp/x"))
            clip = read_wav(wav(1.0))
            text, lang = backend.transcribe(clip, None, texts["auto"], texts)
            self.assertEqual((text, lang), ("يا يوسف، كم طلب اليوم؟", "ar"))
            self.assertEqual(calls, [(None, "يوسف، Yousef."), ("ar", texts["ar"])])
            calls.clear()
            backend.transcribe(clip, "en", texts["en"], texts)  # a fixed language is never re-read
            self.assertEqual(calls, [("en", "Yousef, write an Instagram post about the new offer in the store.")])


class GpuFallbackTest(unittest.TestCase):
    def test_cpu_when_gpu_fails(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Settings(Path(d) / "s.json")
            settings.update({"voice": {"device": "cuda"}})
            made = []

            def factory(model, device, root):
                if device == "cuda":
                    raise RuntimeError("Could not load library cudnn_ops64_9.dll")
                made.append(device)
                return FakeBackend(model, device, root)

            eng = VoiceEngine(settings, Path(d), backend_factory=factory)
            eng.load()
            eng.wait_loaded()
            status = eng.status()
            self.assertEqual((status["state"], status["device_used"], made), ("ready", "cpu", ["cpu"]))
            self.assertIn("install-voice-gpu.bat", status["note"])
            eng.load()  # same settings: no reload loop
            eng.wait_loaded()
            self.assertEqual(made, ["cpu"])


class VoiceApiTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.local = FakeProvider("ollama", models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(self.dir.name, factory=factory_from({"ollama": self.local}), cipher=XorCipher(),
                             voice_backend=lambda m, d, r: FakeBackend(m, d, r))
        self.server = make_server(self.app, 0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.dir.cleanup()

    def post_audio(self, data, ctype="audio/wav"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/voice/transcribe", body=data,
                     headers={"Host": f"127.0.0.1:{self.port}", "X-Yousef-Client": "1", "Content-Type": ctype})
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        return resp.status, body

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

    def test_transcribe_flow(self):
        self.assertEqual(self.call("GET", "/api/voice/status")[1]["state"], "idle")
        status, body = self.post_audio(wav())
        self.assertEqual(status, 503)  # loading now
        self.app.voice.wait_loaded()
        status, body = self.post_audio(wav())
        self.assertEqual(status, 200, body)
        self.assertEqual((body["wake"], body["command"]), (True, "اكتب منشورًا"))
        self.assertEqual(self.post_audio(wav(), ctype="text/plain")[0], 415)
        self.assertEqual(self.post_audio(b"RIFF....junk")[0], 400)

    def test_voice_task_gets_spoken_style(self):
        status, body = self.call("POST", "/api/tasks", {"request": "اكتب منشورًا عن عطر جديد", "source": "voice"})
        self.assertEqual(status, 201)
        tid = body["task"]["id"]
        wait_for(lambda: self.call("GET", f"/api/tasks/{tid}")[1]["task"]["status"] == "done")
        task = self.call("GET", f"/api/tasks/{tid}")[1]["task"]
        self.assertEqual(task["source"], "voice")
        agent_call = next(c for c in self.local.calls if not c["json_mode"])
        self.assertIn("read aloud", agent_call["messages"][0]["content"])
        self.assertEqual(self.call("POST", "/api/tasks", {"request": "x", "source": "fax"})[0], 400)

    def test_text_task_has_no_voice_hint(self):
        status, body = self.call("POST", "/api/tasks", {"request": "اكتب منشورًا عن عطر جديد"})
        tid = body["task"]["id"]
        wait_for(lambda: self.call("GET", f"/api/tasks/{tid}")[1]["task"]["status"] == "done")
        agent_call = next(c for c in self.local.calls if not c["json_mode"])
        self.assertNotIn("read aloud", agent_call["messages"][0]["content"])


class NotInstalledApiTest(unittest.TestCase):
    def test_501(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(engine_mod, "whisper_installed", return_value=False):
            app = build_app(d, factory=factory_from({}), cipher=XorCipher())
            server = make_server(app, 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                conn.request("POST", "/api/voice/transcribe", body=wav(),
                             headers={"Host": f"127.0.0.1:{server.server_port}", "X-Yousef-Client": "1",
                                      "Content-Type": "audio/wav"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 501)
                self.assertIn("install-voice.bat", json.loads(resp.read())["error"])
            finally:
                server.shutdown()
                server.server_close()
                app.close()


class MigrationTest(unittest.TestCase):
    def test_old_database_gets_source_column(self):
        import sqlite3
        from yousef.memory import Memory
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.db"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL, "
                       "final TEXT, error TEXT, feedback INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
            db.execute("INSERT INTO tasks(request, status, created_at, updated_at) VALUES ('old', 'done', 1, 1)")
            db.commit()
            db.close()
            mem = Memory(path)
            self.assertEqual(mem.get_task(1)["source"], "text")
            tid = mem.create_task("new", "voice")
            self.assertEqual(mem.get_task(tid)["source"], "voice")
            mem.close()


if __name__ == "__main__":
    unittest.main()
