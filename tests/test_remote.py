import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.remote import RemoteAccess, RemoteError, cookie_token
from yousef.server import make_server, remote_allowed

from helpers import FakeProvider, XorCipher, factory_from


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class PairingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.remote = RemoteAccess(Path(self.dir.name) / "remote.json", clock=self.clock)

    def tearDown(self):
        self.dir.cleanup()

    def test_codes_tokens_and_limits(self):
        with self.assertRaisesRegex(RemoteError, "لا يوجد رمز"):
            self.remote.pair("123456", "x")
        code = self.remote.new_code()["code"]
        self.assertRegex(code, r"^\d{6}$")
        token = self.remote.pair(code, "جوالي", "192.168.1.20")
        with self.assertRaises(RemoteError):  # one phone per code
            self.remote.pair(code, "another", "192.168.1.21")
        device = self.remote.check(token, "192.168.1.20")
        self.assertEqual(device["name"], "جوالي")
        self.assertIsNone(self.remote.check("not-a-token"))
        stored = (Path(self.dir.name) / "remote.json").read_text()
        self.assertNotIn(token, stored)  # only the hash is kept
        again = RemoteAccess(Path(self.dir.name) / "remote.json", clock=self.clock)
        self.assertIsNotNone(again.check(token))
        self.assertTrue(again.revoke(device["id"]))
        self.assertIsNone(again.check(token))

    def test_wrong_tries_and_expiry(self):
        code = self.remote.new_code()["code"]
        wrong = "000000" if code != "000000" else "111111"
        for i in range(5):
            with self.assertRaises(RemoteError):
                self.remote.pair(wrong, "x", f"10.0.0.{i}")
        with self.assertRaisesRegex(RemoteError, "لا يوجد رمز"):
            self.remote.pair(code, "x", "10.0.0.9")  # five wrong tries ended the code
        code = self.remote.new_code()["code"]
        self.clock.now += 601
        with self.assertRaisesRegex(RemoteError, "لا يوجد رمز"):
            self.remote.pair(code, "x", "10.0.0.10")
        for _ in range(10):
            try:
                self.remote.pair("999999", "x", "10.0.0.66")
            except RemoteError:
                pass
        self.remote.new_code()
        with self.assertRaisesRegex(RemoteError, "محاولات كثيرة"):
            self.remote.pair("123456", "x", "10.0.0.66")

    def test_cookie_and_rules(self):
        self.assertEqual(cookie_token("a=1; yousef_session=abc; b=2"), "abc")
        self.assertIsNone(cookie_token("yousef_sessionx=abc"))
        self.assertTrue(remote_allowed("POST", "/api/tasks"))
        self.assertTrue(remote_allowed("POST", "/api/steps/4/decision"))
        self.assertTrue(remote_allowed("GET", "/api/tasks"))
        for method, path in (("PUT", "/api/settings"), ("PUT", "/api/keys/openai"), ("POST", "/api/workspaces"),
                             ("GET", "/api/remote"), ("POST", "/api/system/stop"), ("POST", "/api/stores"),
                             ("DELETE", "/api/device/apps/x"), ("POST", "/api/remote/code")):
            self.assertFalse(remote_allowed(method, path), (method, path))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ListenerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        provider = FakeProvider("ollama", models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}), cipher=XorCipher())
        self.server = make_server(self.app, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.rport = free_port()

    def tearDown(self):
        self.server.api.listener.stop()
        self.server.shutdown()
        self.server.server_close()
        self.app.close()
        self.dir.cleanup()

    def call(self, method, path, body=None, port=None, host=None, cookie=None, origin=None, client=True):
        port = port or self.server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{port}"}
        if client:
            headers["X-Yousef-Client"] = "1"
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, data, headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            out = json.loads(raw or b"{}")
        except ValueError:
            out = raw
        return resp.status, out, resp.getheader("Set-Cookie")

    def phone(self, method, path, body=None, cookie=None, origin=None):
        return self.call(method, path, body, port=self.rport, host=f"192.168.1.5:{self.rport}", cookie=cookie,
                         origin=origin)

    def test_pair_and_use(self):
        status, r, _ = self.call("PUT", "/api/remote", {"enabled": True, "port": self.rport})
        self.assertEqual((status, r["running"], r["enabled"]), (200, True, True))
        self.assertEqual(self.phone("GET", "/")[0], 200)                       # the page itself is public
        self.assertEqual(self.phone("GET", "/pair")[0], 200)
        self.assertEqual(self.phone("GET", "/api/tasks")[0], 401)               # the data is not
        self.assertEqual(self.phone("POST", "/api/remote/pair", {"code": "123456"})[0], 403)
        code = self.call("POST", "/api/remote/code", {})[1]["code"]["code"]
        status, _, set_cookie = self.phone("POST", "/api/remote/pair", {"code": code, "name": "جوالي"})
        self.assertEqual(status, 200)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)
        cookie = set_cookie.split(";")[0]
        self.assertEqual(self.phone("GET", "/api/tasks", cookie=cookie)[0], 200)
        status, task, _ = self.phone("POST", "/api/tasks", {"request": "مرحبا"}, cookie=cookie)
        self.assertEqual(status, 201)
        for method, path, body in (("PUT", "/api/settings", {"mode": "cloud_first"}), ("GET", "/api/remote", None),
                                   ("PUT", "/api/keys/openai", {"key": "sk-x" * 10}), ("POST", "/api/remote/code", {})):
            self.assertEqual(self.phone(method, path, body, cookie=cookie)[0], 403, path)
        self.assertEqual(self.phone("POST", "/api/tasks", {"request": "x"}, cookie=cookie,
                                    origin="http://evil.example")[0], 403)
        devices = self.call("GET", "/api/remote")[1]["devices"]
        self.assertEqual([d["name"] for d in devices], ["جوالي"])
        self.assertEqual(self.call("GET", "/api/tasks", host="192.168.1.5:80")[0], 403)  # the PC server stays local-only
        self.assertEqual(self.phone("POST", "/api/remote/logout", {}, cookie=cookie)[0], 200)
        self.assertEqual(self.phone("GET", "/api/tasks", cookie=cookie)[0], 401)
        status, r, _ = self.call("PUT", "/api/remote", {"enabled": False})
        self.assertFalse(r["running"])
        with self.assertRaises(OSError):
            self.phone("GET", "/")

    def test_port_rules(self):
        status, r, _ = self.call("PUT", "/api/remote", {"enabled": True, "port": 80})
        self.assertEqual(status, 400)
        self.assertFalse(self.server.api.listener.running)


if __name__ == "__main__":
    unittest.main()
