"""Runs the VS Code extension (with a mocked editor API) against a real YOUSEF server."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from xml.dom import minidom

from yousef.app import build_app
from yousef.server import make_server

from helpers import FakeProvider, XorCipher, factory_from

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def scripted_coder():
    turns = [
        {"tool": "read_file", "path": "src/cart.js"},
        {"tool": "replace", "path": "src/cart.js", "old": "    sum += item.price;",
         "new": "    sum += item.price * (item.qty ?? 1);"},
        {"done": "كان المجموع يتجاهل الكمية؛ اقترحت ضرب السعر في qty."},
    ]

    def respond(messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            return json.dumps({"steps": [{"agent": "coder", "instruction": "أصلح المجموع"}]})
        if json_mode:
            return json.dumps(turns.pop(0), ensure_ascii=False) if turns else json.dumps({"done": "لا تغييرات أخرى."})
        return "تغيّر حساب المجموع ليشمل الكمية."
    return respond


@unittest.skipIf(NODE is None, "node is not installed")
class ExtensionTest(unittest.TestCase):
    def test_extension_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "oman-store"
            (project / "src").mkdir(parents=True)
            cart = project / "src" / "cart.js"
            cart.write_text("export function total(items) {\n  let sum = 0;\n  for (const item of items) {\n"
                            "    sum += item.price;\n  }\n  return sum;\n}\n")
            provider = FakeProvider("ollama", responder=scripted_coder(), models=["qwen2.5-coder:7b"], is_local=True)
            app = build_app(Path(d) / "data", factory=factory_from({"ollama": provider}), cipher=XorCipher())
            server = make_server(app, 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                proc = subprocess.run([NODE, str(ROOT / "vscode" / "test" / "run.js"),
                                       f"http://127.0.0.1:{server.server_port}", str(project)],
                                      capture_output=True, text=True, timeout=90)
            finally:
                server.shutdown()
                server.server_close()
                app.close()
            lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
            self.assertTrue(lines, proc.stdout + proc.stderr)
            out = json.loads(lines[-1])
            self.assertNotIn("error", out, out)
            self.assertEqual(out["helpers"], "ok")
            for command in ["yousef.ask", "yousef.explain", "yousef.fix", "yousef.allowWorkspace", "yousef.focus"]:
                self.assertIn(command, out["commands"])
            self.assertEqual(out["status"], {"online": True, "project": {"name": "oman-store", "allowed": False}})
            self.assertEqual(out["permission_prompt"], "shown")
            self.assertEqual(out["created"], {"source": "vscode", "workspace": "oman-store", "file": "src/cart.js",
                                              "start_line": 4})
            self.assertTrue(out["focused"])
            self.assertTrue(out["waiting_notice"])
            self.assertEqual(out["diff"], {"title": "src/cart.js ↔ اقتراح يوسف", "left_has_qty": False,
                                           "right_has_qty": True})
            self.assertEqual(out["done"], {"final_has_applied": True, "change_status": "applied"})
            self.assertFalse(out["second_prompted"])
            self.assertEqual(out["stored_ids"], 2)
            self.assertEqual(out["clipboard"], "abc")
            self.assertTrue(out["offline"])
            self.assertIn("qty", cart.read_text())


class VsixTest(unittest.TestCase):
    def test_package_layout(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("build_vsix", ROOT / "tools" / "build_vsix.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            path = mod.build(Path(d) / "x.vsix")
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                manifest = minidom.parseString(z.read("extension.vsixmanifest"))
                types = minidom.parseString(z.read("[Content_Types].xml"))
                pkg = json.loads(z.read("extension/package.json"))
        for name in ["extension/package.json", "extension/extension.js", "extension/lib/client.js",
                     "extension/media/chat.js", "extension/media/icon.png", "extension/README.md"]:
            self.assertIn(name, names)
        self.assertFalse(any("/test/" in n for n in names))
        identity = manifest.getElementsByTagName("Identity")[0]
        self.assertEqual((identity.getAttribute("Id"), identity.getAttribute("Version")), (pkg["name"], pkg["version"]))
        exts = {e.getAttribute("Extension") for e in types.getElementsByTagName("Default")}
        self.assertTrue({".js", ".json", ".png", ".svg", ".css", ".md"} <= exts)


if __name__ == "__main__":
    unittest.main()
