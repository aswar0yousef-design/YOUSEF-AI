import json
import math
import tempfile
import time
import unittest
from array import array
from pathlib import Path

from yousef.app import build_app
from yousef.voice.listener import FRAME, NativeListener, Segmenter, speakable

from helpers import FakeProvider, XorCipher, factory_from, wait_for
from test_device import FakeBackend as FakeDeviceBackend


def frame(amplitude: float, i: int = 0) -> bytes:
    return array("h", (int(amplitude * 32767 * math.sin(2 * math.pi * 440 * (i * FRAME + n) / 16000))
                       for n in range(FRAME))).tobytes()


class FakeSpeaker:
    missing_arabic = False

    def __init__(self):
        self.said = []

    def speak(self, text):
        self.said.append(text)
        return True

    def stop(self):
        pass


class FakeEngine:
    installed = True

    def __init__(self, results=()):
        self.results = list(results)
        self.loaded = False
        self.heard = []

    def load(self):
        self.loaded = True

    def transcribe(self, wav):
        self.heard.append(len(wav))
        return self.results.pop(0) if self.results else {"text": "", "noise": True}


class FakeMic:
    def __init__(self):
        self.on_frame = None

    def start(self, on_frame):
        self.on_frame = on_frame

    def stop(self):
        self.on_frame = None


class SegmenterTest(unittest.TestCase):
    def test_cuts_one_utterance(self):
        out = []
        seg = Segmenter(out.append)
        for i in range(50):
            seg.push(frame(0.001, i))
        for i in range(30):
            seg.push(frame(0.3, i))
        for i in range(45):
            seg.push(frame(0.001, i))
        self.assertEqual(len(out), 1)
        self.assertGreaterEqual(len(out[0]) // (FRAME * 2), 30 + 15)  # speech plus the pre-roll
        seg.push(frame(0.3))  # a click is not speech
        for i in range(45):
            seg.push(frame(0.001, i))
        self.assertEqual(len(out), 1)

    def test_speakable(self):
        text = "## النتيجة\n**البيتكوين** صاعد، انظر [الرابط](https://x.y).\n\n```python\nprint(1)\n```\nتفاصيل"
        self.assertEqual(speakable(text), "النتيجة البيتكوين صاعد، انظر الرابط.")


class Script:
    def __init__(self, turns=(), answer="الساعة الآن الثالثة."):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.answer = answer

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            agent = "device_control" if self.turns else "general"
            return json.dumps({"steps": [{"agent": agent, "instruction": "نفّذ"}]})
        if json_mode and "control the user's Windows PC" in system:
            return self.turns.pop(0) if self.turns else json.dumps({"done": "اقترحت فتح المفكرة."})
        return self.answer


class ListenerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.speaker = FakeSpeaker()
        self.device = FakeDeviceBackend()

    def tearDown(self):
        self.app.close()
        self.dir.cleanup()

    def make(self, script, engine=None):
        provider = FakeProvider("ollama", responder=script, models=["qwen2.5:7b"], is_local=True)
        self.mic = FakeMic()
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}), cipher=XorCipher(),
                             listener_mic=lambda: self.mic, listener_speaker=self.speaker, device_backend=self.device,
                             device_home=Path(self.dir.name))
        if engine is not None:
            self.app.listener.engine = engine
        return self.app.listener

    def test_wake_then_command(self):
        listener = self.make(Script())
        listener.handle({"text": "يوسف", "wake": True, "command": ""})
        self.assertEqual((listener.mode, self.speaker.said), ("command", ["نعم؟"]))
        listener.handle({"text": "كم الساعة؟", "wake": False, "command": "كم الساعة؟"})
        wait_for(lambda: len(self.speaker.said) == 2)
        self.assertEqual(self.speaker.said[1], "الساعة الآن الثالثة.")
        task = self.app.memory.list_tasks()[0]
        self.assertEqual((task["request"], task["source"]), ("كم الساعة؟", "voice"))
        listener.handle({"text": "كلام عابر بلا كلمة التنبيه", "wake": False, "command": "كلام"})
        self.assertEqual(len(self.app.memory.list_tasks()), 1)  # ignored

    def test_spoken_approval(self):
        listener = self.make(Script([{"tool": "propose_open_app", "app": "notepad"}, {"done": "اقترحت فتح المفكرة."}]))
        listener.handle({"text": "يوسف افتح المفكرة", "wake": True, "command": "افتح المفكرة"})
        wait_for(lambda: listener.mode == "approval")
        self.assertIn("قل: وافق، أو ارفض", self.speaker.said[-1])
        listener.handle({"text": "وافق", "wake": False, "command": "وافق", "reply": "approve"})
        wait_for(lambda: self.app.memory.list_tasks()[0]["status"] == "done")
        self.assertEqual(self.device.calls, [("open", "notepad.exe")])
        wait_for(lambda: any("فُتح" in s or "المفكرة" in s for s in self.speaker.said[-1:]))
        self.assertIn("حسنًا، أنفّذ.", self.speaker.said)

    def test_microphone_path_and_quiet_while_speaking(self):
        engine = FakeEngine([{"text": "يوسف ما الأخبار", "wake": True, "command": "ما الأخبار", "noise": False}])
        listener = self.make(Script(answer="لا جديد."), engine)
        listener.start()
        self.assertTrue(engine.loaded and listener.running)
        listener._speaking.set()  # Yousef is talking: the room is not listened to
        for i in range(80):
            self.mic.on_frame(frame(0.3, i))
        listener._speaking.clear()
        for i in range(20):
            self.mic.on_frame(frame(0.001, i))
        for i in range(30):
            self.mic.on_frame(frame(0.3, i))
        for i in range(45):
            self.mic.on_frame(frame(0.001, i))
        wait_for(lambda: "لا جديد." in self.speaker.said)
        self.assertEqual(len(engine.heard), 1)
        self.assertEqual(self.app.listener.status()["running"], True)
        listener.stop()
        self.assertFalse(listener.running)

    def test_api_toggle_needs_voice(self):
        listener = self.make(Script())
        self.assertFalse(listener.running)
        from yousef.server import Api
        api = Api(self.app)
        with self.assertRaises(Exception):  # no faster-whisper in tests: explained, not crashed
            api.voice_listener({}, {"enabled": True})
        listener.engine = FakeEngine()
        status, data = api.voice_listener({}, {"enabled": True})
        self.assertTrue(data["listener"]["running"])
        self.assertTrue(self.app.settings.get()["voice"]["listener"])
        api.voice_listener({}, {"enabled": False})
        self.assertFalse(self.app.settings.get()["voice"]["listener"])


if __name__ == "__main__":
    unittest.main()
