import json
import tempfile
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.config import SettingsError
from yousef.device import DeviceError, check_app
from yousef.integrations.device_tools import DeviceToolbox

from helpers import FakeProvider, XorCipher, factory_from, wait_for


class FakeBackend:
    name = "windows"

    def __init__(self):
        self.calls = []

    def open(self, target):
        self.calls.append(("open", target))

    def key(self, vk, times):
        self.calls.append(("key", vk, times))

    def lock(self):
        self.calls.append(("lock",))

    def close(self, image):
        self.calls.append(("close", image))
        return True

    def running(self):
        return [{"name": "chrome.exe", "count": 12, "memory_mb": 1800.0}, {"name": "Code.exe", "count": 6, "memory_mb": 900.0}]

    def system_info(self):
        return {"os": "Windows 11", "machine": "YOUSEF-PC", "cpu": "AMD Ryzen 5 3600", "cores": 12, "memory_gb": 34.3,
                "memory_free_gb": 18.2, "memory_load": 47, "battery": None, "uptime_hours": 5.5, "time": "2026-10-20 15:00",
                "drives": [{"drive": "C:\\", "total_gb": 1000.0, "free_gb": 420.0}]}


class Script:
    def __init__(self, turns):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.planner_calls = []

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            self.planner_calls.append(system)
            return json.dumps({"steps": [{"agent": "device_control", "instruction": "نفّذ"}]})
        if json_mode and "control the user's Windows PC" in system:
            return self.turns.pop(0) if self.turns else json.dumps({"done": "تم"})
        return "تم"


class DeviceBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name) / "home"
        for sub in ("Downloads", "Documents", ".ssh"):
            (self.home / sub).mkdir(parents=True)
        (self.home / "Documents" / "report.pdf").write_bytes(b"%PDF-1.7")
        (self.home / "Documents" / "run.bat").write_text("echo hi")
        (self.home / ".ssh" / "notes.txt").write_text("x")
        self.backend = FakeBackend()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.dir.cleanup()

    def make_app(self, script=None):
        provider = FakeProvider("ollama", responder=script or Script([]), models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}), cipher=XorCipher(),
                             device_backend=self.backend, device_home=self.home)
        return self.app


class RulesTest(DeviceBase):
    def test_places_and_apps(self):
        self.make_app()
        d = self.app.device
        self.assertEqual(d.folder("downloads"), (self.home / "Downloads").resolve())
        self.assertEqual(d.folder("التنزيلات"), (self.home / "Downloads").resolve())
        self.assertEqual(d.document(str(self.home / "Documents" / "report.pdf")).name, "report.pdf")
        with self.assertRaisesRegex(DeviceError, "لا يفتح يوسف ملفات .bat"):
            d.document(str(self.home / "Documents" / "run.bat"))
        with self.assertRaisesRegex(DeviceError, "مفاتيح خاصة"):
            d.document(str(self.home / ".ssh" / "notes.txt"))
        with self.assertRaisesRegex(DeviceError, "مجلدك الشخصي"):
            d.folder(self.dir.name)  # outside home and projects
        self.assertEqual(d.app("Google Chrome")["name"], "chrome")
        with self.assertRaisesRegex(DeviceError, "قائمة البرامج المسموحة"):
            d.app("cmd")
        for bad in ({"name": "evil", "target": "cmd.exe /c del *"}, {"name": "Bad Name", "target": "x.exe"},
                    {"name": "ps", "target": "C:\\Windows\\System32\\powershell.ps1"}):
            with self.assertRaises(DeviceError):
                check_app(bad)
        with self.assertRaises(SettingsError):
            self.app.settings.update({"device": {"apps": [{"name": "x", "target": "rm -rf"}]}})
        self.app.settings.update({"device": {"apps": [{"name": "photoshop", "label": "فوتوشوب",
                                                       "target": "C:\\Program Files\\Adobe\\Photoshop.exe"}]}})
        self.assertEqual(d.app("فوتوشوب")["target"], "C:\\Program Files\\Adobe\\Photoshop.exe")

    def test_execute_rechecks(self):
        self.make_app()
        d = self.app.device
        self.assertEqual(d.execute("device_volume", {"action": "up", "steps": 99}), "رُفع الصوت 50% تقريبًا")
        self.assertEqual(self.backend.calls[-1], ("key", 0xAF, 25))  # capped
        d.execute("device_open_url", {"url": "https://example.com"})
        d.execute("device_close_app", {"app": "chrome"})
        d.execute("device_lock", {})
        self.assertEqual(self.backend.calls[1:], [("open", "https://example.com"), ("close", "chrome.exe"), ("lock",)])
        for kind, payload in (("device_open_url", {"url": "file:///C:/Windows/System32/cmd.exe"}),
                              ("device_open_app", {"app": "cmd"}), ("device_close_app", {"app": "explorer"}),
                              ("device_shutdown", {})):
            with self.assertRaises(DeviceError):
                d.execute(kind, payload)


class ToolboxTest(DeviceBase):
    def test_tools(self):
        self.make_app()
        tb = DeviceToolbox(self.app.device)
        self.assertIn("Memory: 34.3 GB total, 18.2 GB free (47% in use)", tb.run({"tool": "system_info"}))
        self.assertIn("chrome.exe | 12 | 1800 MB", tb.run({"tool": "running_apps"}))
        self.assertIn("calculator — الآلة الحاسبة", tb.run({"tool": "list_apps"}))
        tb.run({"tool": "propose_open_app", "app": "chrome"})
        self.assertEqual(tb.run({"tool": "propose_open_app", "app": "chrome"}), "Already proposed.")
        tb.run({"tool": "propose_open_folder", "path": "downloads"})
        tb.run({"tool": "propose_volume", "action": "down", "steps": 3})
        self.assertIn("Error", tb.run({"tool": "propose_open_url", "url": "javascript:alert(1)"}))
        self.assertIn("Error", tb.run({"tool": "propose_volume", "action": "up", "steps": "lots"}))
        self.assertEqual([p["kind"] for p in tb.proposals], ["device_open_app", "device_open_folder", "device_volume"])
        self.assertEqual(tb.proposals[1]["payload"]["path"], "downloads")
        self.assertEqual(self.backend.calls, [])  # nothing ran


class FlowTest(DeviceBase):
    def test_short_request_reaches_device_control(self):
        script = Script([{"tool": "propose_open_app", "app": "chrome"}, {"tool": "propose_volume", "action": "up", "steps": 5},
                         {"done": "اقترحت فتح كروم ورفع الصوت."}])
        self.make_app(script)
        tid = self.app.orchestrator.submit("افتح كروم وارفع الصوت")
        wait_for(lambda: self.app.memory.get_task(tid)["status"] in ("waiting", "done", "failed"))
        step = self.app.memory.get_task(tid)["steps"][0]
        self.assertEqual((step["agent"], step["status"]), ("device_control", "awaiting_approval"))
        self.assertIn('"device_control"', script.planner_calls[0])
        self.app.orchestrator.decide(step["id"], True, exclude=[step["actions"][1]["id"]])
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        self.assertEqual([a["status"] for a in task["steps"][0]["actions"]], ["done", "rejected"])
        self.assertEqual(self.backend.calls, [("open", "chrome.exe")])
        self.assertIn("**الجهاز:**\n- فُتح Google Chrome", task["steps"][0]["output"])


if __name__ == "__main__":
    unittest.main()
