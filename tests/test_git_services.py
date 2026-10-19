import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from yousef.app import build_app
from yousef.integrations import Vercel
from yousef.workspace import Services, Toolbox, Workspace, git, registry

from helpers import FakeProvider, XorCipher, factory_from, wait_for

GIT = shutil.which("git")


def sh(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_repo(root: Path, origin: str | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    sh(root, "init", "-q", "-b", "main")
    sh(root, "config", "user.email", "test@example.com")
    sh(root, "config", "user.name", "Test")
    (root / "cart.js").write_text("function total(items) {\n  return items.reduce((s, i) => s + i.price, 0);\n}\n")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "initial")
    if origin:
        sh(root, "remote", "add", "origin", origin)


class Script:
    def __init__(self, turns):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.coder_calls = []

    def __call__(self, messages, json_mode):
        if messages[0]["content"].startswith("You are the planner"):
            return json.dumps({"steps": [{"agent": "coder", "instruction": "do the work"}]})
        if json_mode:
            self.coder_calls.append(list(messages))  # a snapshot: the loop keeps appending to its list
            return self.turns.pop(0) if self.turns else json.dumps({"done": "تم"})
        return "تم"


class FakeApi:
    """Records requests and answers from a route table: (method, path) -> (status, body)."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                path = self.path.split("?")[0]
                outer.requests.append({"method": self.command, "path": self.path, "body": body,
                                       "auth": self.headers.get("Authorization")})
                status, payload = outer.routes.get((self.command, path), (404, {"message": "not found"}))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _go

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@unittest.skipIf(GIT is None, "git is not installed")
class GitFlowTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.bare = base / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.bare)], check=True)
        self.repo = base / "shop"
        make_repo(self.repo, str(self.bare))
        sh(self.repo, "push", "-q", "-u", "origin", "main")

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.dir.cleanup()

    def start(self, turns, **ws_fields):
        self.script = Script(turns)
        provider = FakeProvider("ollama", responder=self.script, models=["qwen2.5-coder:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}), cipher=XorCipher())
        registry.add(self.app.settings, str(self.repo))
        if ws_fields:
            registry.update(self.app.settings, "shop", ws_fields)
        tid = self.app.orchestrator.submit("أصلح مجموع السلة ليشمل الكمية وارفعه", workspace="shop")
        wait_for(lambda: self.app.memory.get_task(tid)["status"] in ("waiting", "done", "failed"))
        return tid

    EDIT = {"tool": "replace", "path": "cart.js", "old": "s + i.price", "new": "s + i.price * (i.qty ?? 1)"}

    def test_commit_and_push_after_approval(self):
        tid = self.start([
            {"tool": "git_status"},
            self.EDIT,
            {"tool": "git_commit", "message": "Count quantity in cart total", "branch": "fix/qty"},
            {"tool": "git_push"},
            {"done": "أصلحت المجموع، وجهّزت commit وpush."},
        ])
        task = self.app.memory.get_task(tid)
        step = task["steps"][0]
        self.assertEqual(step["status"], "awaiting_approval")
        self.assertEqual([a["kind"] for a in step["actions"]], ["git_commit", "git_push"])
        self.assertEqual(step["actions"][0]["payload"]["branch"], "fix/qty")
        self.assertEqual(sh(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "main")  # nothing ran yet
        status_result = self.script.coder_calls[1][-1]["content"]
        self.assertIn("## main", status_result)
        self.app.orchestrator.decide(step["id"], True)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        acts = {a["kind"]: a["status"] for a in task["steps"][0]["actions"]}
        self.assertEqual(acts, {"git_commit": "done", "git_push": "done"}, task["steps"][0]["actions"])
        self.assertEqual(sh(self.repo, "log", "-1", "--format=%s"), "Count quantity in cart total")
        self.assertEqual(sh(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "fix/qty")
        self.assertIn("Count quantity", sh(self.bare, "log", "fix/qty", "-1", "--format=%s"))
        self.assertIn("**push:**", task["final"])

    def test_files_only(self):
        tid = self.start([self.EDIT, {"tool": "git_commit", "message": "x"}, {"done": "ok"}])
        step = self.app.memory.get_task(tid)["steps"][0]
        self.app.orchestrator.decide(step["id"], True, scope="files")
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        self.assertIn("i.qty", (self.repo / "cart.js").read_text())
        self.assertEqual(sh(self.repo, "log", "-1", "--format=%s"), "initial")
        self.assertEqual(task["steps"][0]["actions"][0]["status"], "rejected")

    def test_allowed_commands_only(self):
        ok = f'"{sys.executable}" -c "print(40 + 2)"'
        tid = self.start([
            {"tool": "run", "command": ok},
            {"tool": "run", "command": "rm -rf /"},
            {"done": "الاختبارات تعمل."},
        ], commands=[ok])
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        calls = self.script.coder_calls
        self.assertIn("exit code 0\n42", calls[1][-1]["content"])
        self.assertIn("not an allowed command", calls[2][-1]["content"])
        system = calls[0][0]["content"]
        self.assertIn('"run"', system)
        self.assertIn("git_commit", system)
        self.assertIn("git_push", system)
        self.assertNotIn("open_pr", system)  # no GitHub token / github.com origin

    def test_reject_runs_nothing(self):
        tid = self.start([self.EDIT, {"tool": "git_commit", "message": "x"}, {"tool": "git_push"}, {"done": "ok"}])
        step = self.app.memory.get_task(tid)["steps"][0]
        self.app.orchestrator.decide(step["id"], False)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        self.assertNotIn("i.qty", (self.repo / "cart.js").read_text())
        self.assertEqual(sh(self.repo, "log", "-1", "--format=%s"), "initial")
        self.assertEqual({a["status"] for a in self.app.memory.get_task(tid)["steps"][0]["actions"]}, {"rejected"})


@unittest.skipIf(GIT is None, "git is not installed")
class GitHubPullRequestTest(unittest.TestCase):
    def test_pull_request_after_commit(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "shop"
            make_repo(repo, "https://github.com/owner/shop.git")
            api = FakeApi({("GET", "/repos/owner/shop"): (200, {"default_branch": "main"}),
                           ("POST", "/repos/owner/shop/pulls"): (201, {"number": 7, "html_url": "https://github.com/owner/shop/pull/7"})})
            script = Script([
                {"tool": "replace", "path": "cart.js", "old": "s + i.price", "new": "s + i.price * i.qty"},
                {"tool": "git_commit", "message": "Use quantity", "branch": "feat/qty"},
                {"tool": "open_pr", "title": "Use quantity in cart total", "body": "Fixes the total."},
                {"done": "جاهز"},
            ])
            provider = FakeProvider("ollama", responder=script, models=["m"], is_local=True)
            app = build_app(Path(d) / "data", factory=factory_from({"ollama": provider}), cipher=XorCipher())
            try:
                app.keystore.set("github", "ghp_testtoken123456")
                app.orchestrator.github_api = api.url
                registry.add(app.settings, str(repo))
                tid = app.orchestrator.submit("أصلح المجموع وافتح PR", workspace="shop")
                wait_for(lambda: app.memory.get_task(tid)["status"] == "waiting")
                self.assertIn("open_pr", script.coder_calls[0][0]["content"])
                step = app.memory.get_task(tid)["steps"][0]
                app.orchestrator.decide(step["id"], True)
                wait_for(lambda: app.memory.get_task(tid)["status"] == "done")
                task = app.memory.get_task(tid)
            finally:
                app.close()
                api.close()
            acts = {a["kind"]: (a["status"], a["result"]) for a in task["steps"][0]["actions"]}
            self.assertEqual(acts["open_pr"], ("done", "https://github.com/owner/shop/pull/7"))
            pr = next(r for r in api.requests if r["method"] == "POST")
            self.assertEqual(pr["body"]["head"], "feat/qty")
            self.assertEqual(pr["body"]["base"], "main")
            self.assertEqual(pr["auth"], "Bearer ghp_testtoken123456")


class VercelTest(unittest.TestCase):
    def test_report_with_build_errors(self):
        now = 1_800_000_000_000
        api = FakeApi({
            ("GET", "/v6/deployments"): (200, {"deployments": [
                {"uid": "dpl_bad", "name": "oman-store", "url": "oman-store-abc.vercel.app", "state": "ERROR",
                 "target": "production", "created": now, "meta": {"githubCommitRef": "main",
                                                                  "githubCommitMessage": "Add cart\n\nmore"}},
                {"uid": "dpl_ok", "name": "oman-store", "url": "oman-store-xyz.vercel.app", "state": "READY",
                 "created": now - 3_600_000, "meta": {}},
            ]}),
            ("GET", "/v3/deployments/dpl_bad/events"): (200, [
                {"type": "stdout", "payload": {"text": "Installing dependencies..."}},
                {"type": "stderr", "payload": {"text": "Error: Cannot find module 'react-dom'"}},
                {"type": "stdout", "payload": {"text": "Build failed with 1 error"}},
            ]),
        })
        try:
            report = Vercel("vtok_123456789", api.url).report("oman-store")
        finally:
            api.close()
        self.assertIn("ERROR (فشل)", report)
        self.assertIn("https://oman-store-abc.vercel.app", report)
        self.assertIn("commit: Add cart", report)
        self.assertIn("Cannot find module 'react-dom'", report)
        self.assertNotIn("Installing dependencies", report)
        self.assertEqual(api.requests[0]["auth"], "Bearer vtok_123456789")
        self.assertIn("projectId=oman-store", api.requests[0]["path"])

    def test_tool_needs_token(self):
        with tempfile.TemporaryDirectory() as d:
            tb = Toolbox(Workspace("w", d))
            self.assertIn("Vercel is not connected", tb.run({"tool": "vercel_status"}))
            self.assertNotIn("vercel_status", tb.help())


class GitHelpersTest(unittest.TestCase):
    def test_parse_github(self):
        for url in ["https://github.com/aswar/YOUSEF-AI.git", "git@github.com:aswar/YOUSEF-AI.git",
                    "https://github.com/aswar/YOUSEF-AI", "ssh://git@github.com/aswar/YOUSEF-AI.git"]:
            self.assertEqual(git.parse_github(url), ("aswar", "YOUSEF-AI"), url)
        self.assertIsNone(git.parse_github("https://gitlab.com/a/b.git"))

    def test_branch_names(self):
        for good in ["fix/qty", "feature-1", "yousef/cart.total"]:
            self.assertTrue(git.valid_branch(good), good)
        for bad in ["-x", "a..b", "a//b", "/x", "x/", "has space", "x.lock", ""]:
            self.assertFalse(git.valid_branch(bad), bad)

    def test_not_a_repo(self):
        with tempfile.TemporaryDirectory() as d:
            tb = Toolbox(Workspace("w", d), Services())
            self.assertIn("not a git repository", tb.run({"tool": "git_commit", "message": "x"}))
            self.assertFalse(git.is_repo(Path(d)))


class WorkspaceSettingsTest(unittest.TestCase):
    def test_commands_and_vercel_fields(self):
        with tempfile.TemporaryDirectory() as d:
            app = build_app(Path(d) / "data", factory=factory_from({}), cipher=XorCipher())
            try:
                proj = Path(d) / "p"
                proj.mkdir()
                registry.add(app.settings, str(proj))
                entry = registry.update(app.settings, "p", {"commands": "npm test\n  npm   run build \n\n",
                                                            "vercel_project": "my-site", "path": "/elsewhere"})
                self.assertEqual(entry["commands"], ["npm test", "npm run build"])
                self.assertEqual(entry["vercel_project"], "my-site")
                self.assertEqual(entry["path"], str(proj.resolve()))  # path cannot be changed this way
                with self.assertRaises(Exception):
                    registry.update(app.settings, "p", {"commands": ["x" * 400]})
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
