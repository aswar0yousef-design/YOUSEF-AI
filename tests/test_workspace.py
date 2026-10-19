import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.config import Settings
from yousef.core.coder import run_coder
from yousef.core.jsonutil import extract_json
from yousef.server import make_server
from yousef.workspace import Toolbox, Workspace, WorkspaceError, is_secret, registry

from helpers import FakeProvider, XorCipher, factory_from, wait_for


def make_project(root: Path) -> None:
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def add(a, b):\n    return a - b\n\n\nprint(add(2, 3))\n")
    (root / "src" / "win.txt").write_bytes(b"line one\r\nline two\r\n")
    (root / "README.md").write_text("# Demo\n")
    (root / ".env").write_text("API_KEY=sk-live-SECRET\n")
    (root / ".env.example").write_text("API_KEY=\n")
    (root / "wp-config.php").write_text("<?php define('DB_PASSWORD', 'SECRET');")
    (root / "node_modules" / "lib").mkdir(parents=True)
    (root / "node_modules" / "lib" / "index.js").write_text("module.exports = 'SECRET-dep'")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x00\x00binary")


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name) / "proj"
        self.root.mkdir()
        make_project(self.root)
        self.ws = Workspace("proj", self.root)

    def tearDown(self):
        self.dir.cleanup()

    def test_paths_stay_inside(self):
        for bad in ["../outside.txt", "src/../../x", "/etc/passwd", ".git/config"]:
            with self.assertRaises(WorkspaceError, msg=bad):
                self.ws.resolve(bad)
        self.assertEqual(self.ws.rel(self.ws.resolve("src\\app.py")), "src/app.py")
        self.assertEqual(self.ws.resolve(str(self.root / "README.md")), self.root / "README.md")

    @unittest.skipIf(os.name == "nt", "symlinks need admin rights on Windows")
    def test_symlink_escape_blocked(self):
        outside = Path(self.dir.name) / "outside.txt"
        outside.write_text("private")
        (self.root / "link.txt").symlink_to(outside)
        with self.assertRaises(WorkspaceError):
            self.ws.read_text("link.txt")

    def test_secrets_blocked(self):
        for name in [".env", ".env.local", "wp-config.php", "id_rsa", "server.pem", "credentials.json", ".npmrc"]:
            self.assertTrue(is_secret(name), name)
        for name in [".env.example", "app.py", "secrets_manager.py", "README.md"]:
            self.assertFalse(is_secret(name), name)
        with self.assertRaises(WorkspaceError):
            self.ws.read_text(".env")
        with self.assertRaises(WorkspaceError):
            self.ws.read_text("wp-config.php")
        self.assertEqual(self.ws.read_text(".env.example"), "API_KEY=\n")

    def test_list_and_search(self):
        listing = self.ws.list("")
        self.assertIn("src/", listing)
        self.assertIn("app.py", listing)
        self.assertNotIn("node_modules", listing)
        self.assertNotIn(".git", listing)
        self.assertIn(".env  [", listing)
        self.assertIn("secret, not readable", listing)
        hits = self.ws.search("SECRET")
        self.assertEqual(hits, [])  # .env, wp-config.php and node_modules are never searched
        hits = self.ws.search("return a", glob="*.py")
        self.assertEqual(hits, [("src/app.py", 2, "return a - b")])

    def test_binary_refused(self):
        with self.assertRaises(WorkspaceError):
            self.ws.read_text("logo.png")


class ToolboxTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        make_project(self.root)
        self.tb = Toolbox(Workspace("proj", self.root))

    def tearDown(self):
        self.dir.cleanup()

    def test_read_numbers_lines(self):
        out = self.tb.run({"tool": "read_file", "path": "src/app.py"})
        self.assertIn("2|     return a - b", out)
        self.assertIn("lines 1-5 of 5", out)

    def test_replace_and_overlay(self):
        out = self.tb.run({"tool": "replace", "path": "src/app.py", "old": "return a - b", "new": "return a + b"})
        self.assertIn("Proposed edit", out)
        self.assertIn("return a - b", (self.root / "src" / "app.py").read_text())  # disk untouched
        self.assertIn("includes your proposed changes", self.tb.run({"tool": "read_file", "path": "src/app.py"}))
        self.tb.run({"tool": "replace", "path": "src/app.py", "old": "print(add(2, 3))", "new": "print(add(2, 2))"})
        changes = self.tb.change_list()
        self.assertEqual(len(changes), 1)
        self.assertEqual((changes[0]["added"], changes[0]["removed"]), (2, 2))
        self.assertIn("-    return a - b", changes[0]["diff"])

    def test_replace_errors(self):
        self.assertIn("not found", self.tb.run({"tool": "replace", "path": "src/app.py", "old": "nope", "new": "x"}))
        self.tb.run({"tool": "write_file", "path": "dup.txt", "content": "a\na\n"})
        self.assertIn("2 times", self.tb.run({"tool": "replace", "path": "dup.txt", "old": "a", "new": "b"}))
        self.assertIn("unknown tool", self.tb.run({"tool": "rm", "path": "x"}))
        self.assertIn("Error", self.tb.run({"tool": "read_file", "path": ".env"}))
        self.assertIn("Error", self.tb.run({"tool": "write_file", "path": "../evil.txt", "content": "x"}))
        self.assertFalse(self.tb.trace[-1]["ok"])

    def test_crlf_files_keep_crlf(self):
        self.tb.run({"tool": "replace", "path": "src/win.txt", "old": "line two\n", "new": "line 2\nline 3\n"})
        new_text = self.tb.change_list()[0]["new_text"]
        self.assertEqual(new_text, "line one\r\nline 2\r\nline 3\r\n")

    def test_apply_and_conflict(self):
        ws = self.tb.ws
        self.tb.run({"tool": "replace", "path": "src/app.py", "old": "a - b", "new": "a + b"})
        self.tb.run({"tool": "write_file", "path": "docs/new/guide.md", "content": "# Guide\n"})
        self.tb.run({"tool": "write_file", "path": "README.md", "content": "# Demo 2\n"})
        (self.root / "README.md").write_text("# Edited by the user meanwhile\n")
        results = {r.path: r.status for r in ws.apply(self.tb.change_list())}
        self.assertEqual(results, {"src/app.py": "applied", "docs/new/guide.md": "applied", "README.md": "conflict"})
        self.assertIn("a + b", (self.root / "src" / "app.py").read_text())
        self.assertEqual((self.root / "docs" / "new" / "guide.md").read_text(), "# Guide\n")
        self.assertIn("meanwhile", (self.root / "README.md").read_text())

    def test_bom_preserved(self):
        (self.root / "bom.txt").write_bytes(b"\xef\xbb\xbfhello\n")
        self.tb.run({"tool": "replace", "path": "bom.txt", "old": "hello", "new": "مرحبا"})
        self.tb.ws.apply(self.tb.change_list())
        self.assertEqual((self.root / "bom.txt").read_bytes(), b"\xef\xbb\xbf" + "مرحبا\n".encode())


class JsonTest(unittest.TestCase):
    def test_fences_inside_content(self):
        reply = json.dumps({"tool": "write_file", "path": "README.md", "content": "Run:\n```bash\nnpm i\n```\n"})
        self.assertEqual(extract_json(reply)["path"], "README.md")
        self.assertEqual(extract_json('Sure:\n```json\n{"done": "ok"}\n```')["done"], "ok")
        self.assertEqual(extract_json('I will read it {"tool": "read_file", "path": "a"} now')["tool"], "read_file")


class Scripted:
    """Answers the planner and the coder with scripted JSON."""

    def __init__(self, coder_turns, plan=None):
        self.coder_turns = list(coder_turns)
        self.plan = plan
        self.coder_calls = []

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            return json.dumps(self.plan or {"steps": [{"agent": "coder", "instruction": "fix the add function"}]})
        if json_mode and "tool calls" in system:
            self.coder_calls.append(messages)
            return self.coder_turns.pop(0) if self.coder_turns else json.dumps({"done": "finished"})
        return "plain answer"


class CoderLoopTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        make_project(self.root)
        self.settings = Settings(self.root.parent / f"{self.root.name}-s.json")

    def tearDown(self):
        self.dir.cleanup()

    def router(self, script):
        from yousef.keystore import KeyStore
        from yousef.models import Router
        provider = FakeProvider("ollama", responder=script, models=["qwen2.5-coder:7b"], is_local=True)
        return Router(self.settings, KeyStore(self.root.parent / "k.dat", cipher=XorCipher()),
                      factory_from({"ollama": provider}))

    def test_loop_reads_edits_and_finishes(self):
        script = Scripted([
            json.dumps({"tool": "search", "query": "def add"}),
            json.dumps({"tool": "read_file", "path": "src/app.py"}),
            json.dumps({"tool": "replace", "path": "src/app.py", "old": "a - b", "new": "a + b"}),
            json.dumps({"done": "أصلحت الجمع في src/app.py"}),
        ])
        res = run_coder(self.router(script), Workspace("proj", self.root), "SYSTEM tool calls", "fix add")
        self.assertEqual(res.text, "أصلحت الجمع في src/app.py")
        self.assertEqual(res.tool_calls, 3)
        self.assertEqual([t["tool"] for t in res.toolbox.trace], ["search", "read_file", "replace"])
        self.assertEqual(res.toolbox.change_list()[0]["path"], "src/app.py")
        first = script.coder_calls[0][1]["content"]
        self.assertIn("Project files", first)
        self.assertIn("src/", first)

    def test_bad_json_twice_returns_text(self):
        script = Scripted(["I think the bug is in add()", "still not json"])
        res = run_coder(self.router(script), Workspace("proj", self.root), "SYSTEM tool calls", "fix")
        self.assertEqual(res.text, "still not json")

    def test_tool_limit(self):
        turns = [json.dumps({"tool": "list_files", "path": ""})] * 5 + [json.dumps({"done": "stopped"})]
        res = run_coder(self.router(Scripted(turns)), Workspace("proj", self.root), "SYSTEM tool calls", "x",
                        max_calls=3)
        self.assertEqual(res.tool_calls, 3)
        self.assertIn("tool_limit", res.notes)


class ProjectTaskTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name) / "proj"
        self.root.mkdir()
        make_project(self.root)
        self.script = Scripted([
            json.dumps({"tool": "read_file", "path": "src/app.py"}),
            json.dumps({"tool": "replace", "path": "src/app.py", "old": "a - b", "new": "a + b"}),
            json.dumps({"done": "الدالة كانت تطرح بدل أن تجمع؛ اقترحت التصحيح."}),
        ])
        provider = FakeProvider("ollama", responder=self.script, models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}),
                             cipher=XorCipher())
        registry.add(self.app.settings, str(self.root))
        self.server = make_server(self.app, 0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.dir.cleanup()

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

    def task(self, tid):
        return self.call("GET", f"/api/tasks/{tid}")[1]["task"]

    def start_task(self, **extra):
        body = {"request": "صحّح دالة الجمع في المشروع لأنها تعطي نتيجة خاطئة", "workspace": "proj"}
        body.update(extra)
        status, out = self.call("POST", "/api/tasks", body)
        self.assertEqual(status, 201, out)
        tid = out["task"]["id"]
        wait_for(lambda: self.task(tid)["status"] == "waiting")
        return tid

    def test_approve_writes_file(self):
        tid = self.start_task()
        task = self.task(tid)
        step = task["steps"][0]
        self.assertEqual((step["agent"], step["status"]), ("coder", "awaiting_approval"))
        self.assertEqual(step["changes"][0]["path"], "src/app.py")
        self.assertIn("+    return a + b", step["changes"][0]["diff"])
        self.assertEqual([t["tool"] for t in step["trace"]], ["read_file", "replace"])
        self.assertIn("a - b", (self.root / "src" / "app.py").read_text())
        change = self.call("GET", f"/api/changes/{step['changes'][0]['id']}")[1]["change"]
        self.assertIn("a - b", change["old_text"])
        self.assertEqual(change["workspace"]["name"], "proj")
        self.assertEqual(self.call("POST", f"/api/steps/{step['id']}/decision", {"approve": True})[0], 200)
        wait_for(lambda: self.task(tid)["status"] == "done")
        self.assertIn("a + b", (self.root / "src" / "app.py").read_text())
        task = self.task(tid)
        self.assertEqual(task["steps"][0]["changes"][0]["status"], "applied")
        self.assertIn("طُبّقت التعديلات", task["final"])

    def test_reject_leaves_file(self):
        tid = self.start_task()
        step = self.task(tid)["steps"][0]
        self.call("POST", f"/api/steps/{step['id']}/decision", {"approve": False})
        wait_for(lambda: self.task(tid)["status"] == "done")
        self.assertIn("a - b", (self.root / "src" / "app.py").read_text())
        self.assertEqual(self.task(tid)["steps"][0]["changes"][0]["status"], "rejected")

    def test_editor_context_reaches_coder(self):
        tid = self.start_task(context={"file": "src/app.py", "language": "python", "selection": "return a - b",
                                       "start_line": 2, "end_line": 2})
        first = self.script.coder_calls[0][1]["content"]
        self.assertIn("The user has this file open: src/app.py (python)", first)
        self.assertIn("Selected code (lines 2-2)", first)
        self.assertEqual(self.task(tid)["context"]["file"], "src/app.py")

    def test_workspace_rules(self):
        self.assertEqual(self.call("POST", "/api/tasks", {"request": "x", "workspace": "/tmp"})[0], 403)
        self.assertEqual(self.call("POST", "/api/tasks", {"request": "x", "context": {"selection": 5}})[0], 400)
        sub = str(self.root / "src")
        self.assertEqual(registry.find(self.app.settings, sub)["name"], "proj")  # folders inside count
        listed = self.call("GET", "/api/workspaces")[1]["workspaces"]
        self.assertEqual([(w["name"], w["exists"]) for w in listed], [("proj", True)])
        self.assertEqual(self.call("POST", "/api/workspaces", {"path": "relative/path"})[0], 400)
        self.assertEqual(self.call("POST", "/api/workspaces", {"path": str(self.root)})[1]["workspace"]["name"], "proj")
        self.assertEqual(self.call("DELETE", "/api/workspaces/proj")[0], 200)
        self.assertEqual(self.call("GET", "/api/workspaces")[1]["workspaces"], [])


if __name__ == "__main__":
    unittest.main()
