import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from yousef.app import build_app
from yousef.audit import AuditToolbox, SiteAuditor, check_dependencies, format_findings, scan_code
from yousef.audit.health import product_findings, woocommerce_findings
from yousef.audit.site import Response, blocks_everyone
from yousef.core.agents import available_agents, get_agent
from yousef.core.multitool import MultiToolbox
from yousef.workspace import Toolbox, Workspace, registry

from helpers import FakeProvider, XorCipher, factory_from, wait_for
from store_fakes import FakeShopify, FakeWoo

SHOP_TOKEN_IN_CODE = "shpat_" + "0123456789abcdef" * 2
AI_KEY_IN_CODE = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz123456"

AJAX_PHP = """<?php
add_action('wp_ajax_save_note', 'save_note');
function save_note() {
    global $wpdb;
    $id = $_POST['id'];
    $wpdb->query("DELETE FROM notes WHERE id = $id");
    echo $_GET['msg'];
    echo esc_html($_GET['ok']);
}
$token = "%s";
""" % SHOP_TOKEN_IN_CODE

FILES = {
    "plugin/ajax.php": AJAX_PHP,
    "src/app.js": "el.innerHTML = userInput;\nconst x = eval(code);\nlocalStorage.setItem('authToken', t);\n",
    "tests/test_app.js": "eval('1+1');\n",
    "config.py": f'API_KEY = "{AI_KEY_IN_CODE}"\npassword = "your-password-here"\n',
    "settings.py": "DEBUG = True\nrequests.get(url, verify=False)\n",
    ".env": "SECRET=never-read-this\n",
    "package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {
        "": {"name": "theme"}, "node_modules/lodash": {"version": "4.17.20"},
        "node_modules/express": {"version": "4.21.2"}}}),
    "requirements.txt": "requests==2.19.0\nflask>=2\n",
}

LODASH = {"id": "GHSA-35jh-r3h4-6jhm", "summary": "Command Injection in lodash", "aliases": ["CVE-2021-23337"],
          "database_specific": {"severity": "HIGH"},
          "affected": [{"package": {"name": "lodash", "ecosystem": "npm"},
                        "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "4.17.21"}]}]}]}
REQUESTS = {"id": "PYSEC-2018-28", "summary": "requests leaks credentials on redirect", "aliases": ["CVE-2018-18074"],
            "affected": [{"package": {"name": "requests", "ecosystem": "PyPI"},
                          "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.20.0"}]}]}]}


class FakeOSV:
    def __init__(self):
        self.posted = []

    def post(self, url, body):
        self.posted.append(body)
        known = {"lodash": LODASH["id"], "requests": REQUESTS["id"]}
        return {"results": [{"vulns": [{"id": known[q["package"]["name"]]}]} if q["package"]["name"] in known else {}
                            for q in body["queries"]]}

    def get(self, url):
        vid = url.rsplit("/", 1)[1]
        return {LODASH["id"]: LODASH, REQUESTS["id"]: REQUESTS}[vid]


GOOD_HEADERS = {"Strict-Transport-Security": "max-age=31536000", "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "strict-origin-when-cross-origin", "Content-Security-Policy": "frame-ancestors 'none'"}
GOOD_PAGE = ('<html lang="ar"><head><title>بيت العود</title><meta name="description" content="أجود أنواع العود والبخور">'
             '<meta name="viewport" content="width=device-width"><link rel="canonical" href="https://oman-oud.com/">'
             '<meta property="og:title" content="بيت العود"><meta property="og:image" content="https://cdn/x.jpg">'
             '</head><body><h1>بيت العود</h1><img src="https://cdn/a.jpg" alt="عود"></body></html>')
WP_PAGE = ('<html><head><title>لبان ظفار</title><meta name="generator" content="WordPress 6.4.2">'
           '<script src="http://cdn.example.com/old.js"></script></head>'
           '<body><h1>لبان</h1><h1>ظفار</h1><img src="/a.jpg"><form action="http://luban.example/login"></form></body></html>')


class FakeSite:
    """Answers the auditor's requests from a table, without the network."""

    def __init__(self):
        self.pages = {}
        self.https_redirect = set()
        self.calls = []

    def add(self, url, body="", status=200, headers=None):
        u = urlparse(url)
        self.pages[(u.netloc, u.path or "/")] = (status, {k.lower(): v for k, v in (headers or {}).items()},
                                                 body.encode() if isinstance(body, str) else body)

    def __call__(self, url, limit=1_000_000, follow=True):
        self.calls.append(url)
        u = urlparse(url)
        if u.scheme == "http" and u.netloc in self.https_redirect:
            return Response(200, {}, b"", f"https://{u.netloc}{u.path}", 0.1)
        status, headers, body = self.pages.get((u.netloc, u.path or "/"), (404, {}, b"Not Found"))
        return Response(status, headers, body[:limit], url, 0.2)


def shop_site():
    site = FakeSite()
    site.https_redirect.add("oman-oud.com")
    site.add("https://oman-oud.com/", GOOD_PAGE, headers=GOOD_HEADERS)
    site.add("https://oman-oud.com/robots.txt", "User-agent: *\nDisallow: /cart\n\nUser-agent: Nutch\nDisallow: /\n"
                                                "Sitemap: https://oman-oud.com/sitemap.xml")
    site.add("https://oman-oud.com/sitemap.xml", "<sitemapindex></sitemapindex>")
    site.add("https://oman-oud.com/.env", "<html>page not found</html>")  # soft 404: not a real .env
    return site


class SiteAuditTest(unittest.TestCase):
    def test_wordpress_site_problems(self):
        site = FakeSite()
        site.add("https://luban.example/", WP_PAGE, headers={"Server": "Apache/2.4.41", "X-Powered-By": "PHP/7.4.33"})
        site.add("https://luban.example/.env", "APP_KEY=base64:abc\nDB_PASSWORD=supersecret-value\n")
        site.add("https://luban.example/.git/HEAD", "", status=403)
        site.add("https://luban.example/xmlrpc.php", "XML-RPC server accepts POST requests only.")
        site.add("https://luban.example/wp-json/wp/v2/users", '[{"id":1,"slug":"admin"}]')
        site.add("https://luban.example/robots.txt", "User-agent: *\nDisallow: /")
        findings, stats = SiteAuditor(fetcher=site, cert_check=lambda h, p: 5).audit("https://luban.example",
                                                                                     wordpress=True)
        titles = {f.title: f for f in findings}
        env = next(f for f in findings if ".env" in f.title)
        self.assertEqual(env.severity, "critical")
        text = format_findings(findings, "check")
        self.assertNotIn("supersecret", text)  # exposed contents are never passed on
        self.assertNotIn("APP_KEY", text)
        self.assertFalse(any(".git" in t for t in titles))  # 403 is not exposed
        self.assertIn("نسخة http من الموقع لا تتحوّل إلى https", titles)
        self.assertIn("شهادة TLS تنتهي خلال 5 يومًا", titles)
        self.assertIn("robots.txt يمنع محركات البحث من الموقع كله", titles)
        self.assertIn("خريطة الموقع (sitemap.xml) غير موجودة", titles)
        self.assertIn("xmlrpc.php مفعّل (يُستغل لتخمين كلمات المرور بكثافة)", titles)
        self.assertIn("ترويسة x-powered-by تكشف إصدار البرنامج", titles)
        self.assertIn("نموذج يرسل بياناته عبر http", titles)
        self.assertTrue(any(t.startswith("محتوى مختلط") for t in titles))
        self.assertIn("يوجد 2 عناوين H1 في الصفحة الرئيسية", titles)
        self.assertEqual(stats["exposed"], 3)
        # only GET requests to the site itself
        self.assertTrue(all(urlparse(u).netloc == "luban.example" for u in site.calls))

    def test_clean_shopify_site(self):
        site = shop_site()
        findings, stats = SiteAuditor(fetcher=site, cert_check=lambda h, p: 80).audit("https://oman-oud.com")
        self.assertEqual([f for f in findings if f.severity in ("critical", "high", "medium")], [])
        self.assertTrue(stats["sitemap"])
        self.assertFalse(any("wp-" in u or "xmlrpc" in u for u in site.calls))  # no WordPress probes on Shopify

    def test_robots_groups(self):
        self.assertFalse(blocks_everyone("User-agent: Nutch\nDisallow: /\n\nUser-agent: *\nDisallow: /admin"))
        self.assertTrue(blocks_everyone("User-agent: *\nDisallow: /\n"))

    def test_real_fetch_does_not_follow_probe_redirects(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/.git/HEAD":
                    self.send_response(302)
                    self.send_header("Location", "/fake-head")
                    self.end_headers()
                    return
                body = {"/": b"<html><title>x</title></html>", "/fake-head": b"ref: refs/heads/main",
                        "/.env": b"DB_PASSWORD=x\n"}.get(self.path)
                self.send_response(200 if body else 404)
                self.end_headers()
                self.wfile.write(body or b"")

        server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            findings, _ = SiteAuditor(cert_check=lambda h, p: 90).audit(url)
        finally:
            server.shutdown()
            server.server_close()
        titles = [f.title for f in findings]
        self.assertIn("الموقع لا يعمل بـ HTTPS", titles)
        self.assertTrue(any(".env" in t for t in titles))
        self.assertFalse(any(".git" in t for t in titles))


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name) / "theme"
        for rel, text in FILES.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        self.ws = Workspace("theme", root)

    def tearDown(self):
        self.dir.cleanup()


class CodeAuditTest(ProjectTest):
    def test_scan(self):
        findings, stats = scan_code(self.ws)
        by_where = {(f.where, f.title) for f in findings}
        self.assertIn(("plugin/ajax.php:6", "استعلام $wpdb بمتغيرات بدون prepare"), by_where)
        self.assertIn(("plugin/ajax.php:7", "طباعة مدخلات الزائر بدون تنقية (XSS)"), by_where)
        self.assertNotIn(("plugin/ajax.php:8", "طباعة مدخلات الزائر بدون تنقية (XSS)"), by_where)  # esc_html
        self.assertIn(("plugin/ajax.php:2", "معالج AJAX في ووردبريس بدون فحص nonce (CSRF)"), by_where)
        self.assertIn(("plugin/ajax.php:10", "رمز Shopify مكتوب في الكود"), by_where)
        self.assertIn(("src/app.js:2", "استخدام eval في JavaScript"), by_where)
        self.assertIn(("src/app.js:1", "إدراج HTML عبر innerHTML (XSS إن كان المحتوى من الزائر)"), by_where)
        self.assertIn(("src/app.js:3", "حفظ رمز الدخول في localStorage (يسرقه أي XSS)"), by_where)
        self.assertIn(("settings.py:2", "تعطيل التحقق من شهادات TLS"), by_where)
        self.assertFalse(any(f.where.startswith("tests/") for f in findings))  # test code is not flagged
        self.assertFalse(any(f.where.startswith(".env") for f in findings))  # secret files are never read
        config = [f for f in findings if f.where.startswith("config.py")]
        self.assertEqual([f.title for f in config], ["مفتاح API (OpenAI/Anthropic) مكتوب في الكود"])  # once; placeholder skipped
        text = format_findings(findings, "scan")
        for secret in ("0123456789abcdef", "abcdefghijklmnop", "never-read-this"):
            self.assertNotIn(secret, text)
        self.assertEqual(stats["folder"], ".")
        only_src, _ = scan_code(self.ws, "src")
        self.assertTrue(only_src and all(f.where.startswith("src/") for f in only_src))

    def test_dependencies(self):
        osv = FakeOSV()
        findings, stats = check_dependencies(self.ws, osv.post, osv.get)
        self.assertEqual(stats["packages"], 3)  # lodash, express, requests (flask>=2 is not pinned)
        sent = osv.posted[0]["queries"]
        self.assertTrue(all(set(q) == {"package", "version"} and set(q["package"]) == {"name", "ecosystem"}
                            for q in sent))  # names and versions only, never code
        lodash = next(f for f in findings if f.title.startswith("lodash"))
        self.assertEqual(lodash.severity, "high")
        self.assertIn("CVE-2021-23337", lodash.detail)
        self.assertIn("4.17.21", lodash.fix)
        req = next(f for f in findings if f.title.startswith("requests"))
        self.assertEqual((req.severity, req.where), ("medium", "requirements.txt"))
        self.assertIn("2.20.0", req.fix)

    def test_multitool(self):
        audit = AuditToolbox(None, self.ws, osv=(FakeOSV().post, FakeOSV().get))
        files = Toolbox(self.ws)
        tb = MultiToolbox(audit, files)
        help_text = tb.help()
        self.assertEqual(help_text.count('{"done"'), 1)
        self.assertIn("scan_code", help_text)
        self.assertNotIn("site_check", help_text)  # no store connected
        self.assertIn("read_file", help_text)
        out = tb.run({"tool": "scan_code"})
        self.assertIn("رمز Shopify مكتوب في الكود", out)
        self.assertIn("files_scanned", out)
        self.assertIn("lodash", tb.run({"tool": "check_dependencies"}))
        self.assertIn("no store is connected", tb.run({"tool": "site_check", "store": "x"}))
        self.assertIn("unknown tool", tb.run({"tool": "hack_it"}))
        tb.run({"tool": "replace", "path": "src/app.js", "old": "el.innerHTML = userInput;",
                "new": "el.textContent = userInput;"})
        self.assertEqual([c["path"] for c in tb.change_list()], ["src/app.js"])
        self.assertEqual([t["tool"] for t in tb.trace],
                         ["scan_code", "check_dependencies", "site_check", "hack_it", "replace"])
        self.assertIn("حرج", tb.trace[0]["note"])
        self.assertEqual(audit.reports[0]["check"], "code")


class SecretMaskTest(ProjectTest):
    def test_model_never_sees_keys_but_edits_keep_them(self):
        tb = Toolbox(self.ws)
        shown = tb.run({"tool": "read_file", "path": "plugin/ajax.php"})
        self.assertIn('10| $token = "[[SECRET_1]]";', shown)
        self.assertNotIn(SHOP_TOKEN_IN_CODE, shown)
        self.assertNotIn(AI_KEY_IN_CODE, tb.run({"tool": "search", "query": "API_KEY"}))
        self.assertIn('password = "your-password-here"', tb.run({"tool": "read_file", "path": "config.py"}))
        # an edit near the key keeps it
        tb.run({"tool": "replace", "path": "plugin/ajax.php", "old": '$token = "[[SECRET_1]]";',
                "new": '// shop token\n$token = "[[SECRET_1]]";'})
        self.assertIn(f'// shop token\n$token = "{SHOP_TOKEN_IN_CODE}";', tb.changes["plugin/ajax.php"]["new_text"])
        # replacing it with an environment variable removes it
        tb.run({"tool": "replace", "path": "plugin/ajax.php", "old": '$token = "[[SECRET_1]]";',
                "new": "$token = getenv('SHOPIFY_TOKEN');"})
        self.assertNotIn(SHOP_TOKEN_IN_CODE, tb.changes["plugin/ajax.php"]["new_text"])
        self.assertIn("is not a marker", tb.run({"tool": "write_file", "path": "x.php", "content": "[[SECRET_7]]"}))
        self.assertNotIn("x.php", tb.changes)


class HealthTest(unittest.TestCase):
    def test_woocommerce_platform(self):
        findings = woocommerce_findings(SYSTEM, "6.8.3")
        titles = {f.title: f.severity for f in findings}
        self.assertEqual(titles["إصدار PHP قديم ولم يعد يتلقى تحديثات أمنية (7.4.33)"], "high")
        self.assertEqual(titles["ووردبريس غير محدّث (6.4.2، والأحدث 6.8.3)"], "high")
        self.assertEqual(titles["وضع التصحيح WP_DEBUG مفعّل في الموقع المنشور"], "high")
        self.assertEqual(titles["إضافة غير محدّثة: Contact Form 7 (5.1.0 ← 6.1)"], "high")
        self.assertEqual(titles["القالب غير محدّث: Storefront (4.1.0 ← 4.6.0)"], "medium")
        self.assertIn("رسائل أخطاء PHP قد تظهر للزوار", titles)
        self.assertFalse(any("WooCommerce (" in t for t in titles))  # up to date

    def test_product_data(self):
        products = [
            {"id": "1", "title": "أ", "status": "active", "price": "0", "images": [], "description": "", "sku": "A",
             "inventory": 0},
            {"id": "2", "title": "ب", "status": "active", "price": "5", "images": ["x"], "description": "و" * 50,
             "sku": "A", "inventory": 3, "seo_description": "x"},
            {"id": "3", "title": "ج", "status": "draft", "price": "", "images": [], "description": "", "sku": ""},
        ]
        findings, stats = product_findings(products, "shopify")
        titles = [f.title for f in findings]
        self.assertIn("منتجات منشورة بسعر صفر أو بلا سعر: 1", titles)  # the draft is not counted
        self.assertIn("منتجات تتشارك نفس SKU: 2", titles)
        self.assertIn("منتجات بلا SKU: 1", titles)
        self.assertIn("منتجات منشورة بلا وصف SEO: 1", titles)
        self.assertEqual(stats, {"products_checked": 3, "active": 2})


SYSTEM = {
    "environment": {"version": "9.8.0", "wp_version": "6.4.2", "php_version": "7.4.33", "wp_debug_mode": True},
    "active_plugins": [{"plugin": "contact-form-7/wp-contact-form-7.php", "name": "Contact Form 7",
                        "version": "5.1.0", "version_latest": "6.1"},
                       {"plugin": "woocommerce/woocommerce.php", "name": "WooCommerce", "version": "9.8.0",
                        "version_latest": "9.8.0"}],
    "theme": {"name": "Storefront", "version": "4.1.0", "version_latest": "4.6.0", "has_outdated_templates": True},
    "security": {"secure_connection": True, "hide_errors": False},
}


class AgentRegistryTest(unittest.TestCase):
    def test_needs(self):
        self.assertEqual(get_agent("tech_manager").key, "coder")  # nothing to check: advice only
        self.assertEqual(get_agent("tech_manager", frozenset({"workspace"})).key, "tech_manager")
        self.assertEqual(get_agent("tech_manager", frozenset({"stores"})).key, "tech_manager")
        self.assertNotIn("tech_manager", [a.key for a in available_agents()])
        self.assertIn("tech_manager", [a.key for a in available_agents(frozenset({"stores"}))])
        self.assertEqual(get_agent("social_publisher").key, "writer")  # no account: drafts only
        self.assertEqual(get_agent("social_publisher", frozenset({"social"})).key, "social_publisher")
        self.assertEqual(get_agent("ads_manager").key, "analyst")  # no ad account: advice only
        self.assertEqual(get_agent("ads_manager", frozenset({"ads"})).key, "ads_manager")


class TechScript:
    def __init__(self, turns, plan=None):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.plan = plan or {"steps": [{"agent": "tech_manager", "instruction": "افحص المتجر والكود وأصلح المشاكل"}]}
        self.planner_calls, self.tech_calls = [], []

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            self.planner_calls.append(list(messages))
            return json.dumps(self.plan, ensure_ascii=False)
        if json_mode and "technical manager of the user's online stores" in system:
            self.tech_calls.append(list(messages))
            return self.turns.pop(0) if self.turns else json.dumps({"done": "تم"})
        return "التقرير النهائي"


TURNS = [
    {"tool": "site_check", "store": "بيت العود"},
    {"tool": "store_health", "store": "لبان ظفار"},
    {"tool": "scan_code"},
    {"tool": "check_dependencies"},
    {"tool": "read_file", "path": "plugin/ajax.php"},
    {"tool": "replace", "path": "plugin/ajax.php", "old": "    echo $_GET['msg'];",
     "new": "    echo esc_html($_GET['msg']);"},
    {"tool": "propose_update", "store": "بيت العود", "id": "gid://shopify/Product/1",
     "seo_description": "بخور العود الملكي من بيت العود: رائحة فاخرة تدوم طويلًا."},
    {"done": "## التقرير\n- عالٍ: طباعة مدخلات بدون تنقية (اقترحت إصلاحها)\n- حرج: رمز Shopify في الكود: ألغِه من Shopify"},
]


class TechFlowTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.shopify, self.woo = FakeShopify(), FakeWoo()
        self.root = Path(self.dir.name) / "theme"
        for rel, text in FILES.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.shopify.close()
        self.woo.close()
        self.dir.cleanup()

    def start(self, request="افحص متجري وموقعي والكود وأصلح الثغرات", turns=TURNS, workspace="theme", **extra):
        self.script = TechScript(list(turns))
        provider = FakeProvider("ollama", responder=self.script, models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}),
                             cipher=XorCipher())
        self.app.stores.add("بيت العود", "shopify", self.shopify.url, FakeShopify.TOKEN)
        self.app.stores.add("لبان ظفار", "woocommerce", self.woo.url, f"{FakeWoo.KEY}:{FakeWoo.SECRET}")
        registry.add(self.app.settings, str(self.root))
        o = self.app.orchestrator
        self.site = shop_site()
        o.site_auditor = SiteAuditor(fetcher=self.site, cert_check=lambda h, p: 80)
        self.osv = FakeOSV()
        o.osv = (self.osv.post, self.osv.get)
        o.wp_latest = lambda: "6.8.3"
        tid = o.submit(request, workspace=workspace, **extra)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] in ("waiting", "done", "failed"))
        return tid

    def test_checks_then_fixes_after_approval(self):
        tid = self.start()
        task = self.app.memory.get_task(tid)
        step = task["steps"][0]
        self.assertEqual((step["agent"], step["status"]), ("tech_manager", "awaiting_approval"))
        tools = [t["tool"] for t in step["trace"]]
        self.assertEqual(tools, ["site_check", "store_health", "scan_code", "check_dependencies", "read_file",
                                 "replace", "propose_update"])
        self.assertTrue(all(t["ok"] for t in step["trace"]), step["trace"])
        planner = self.script.planner_calls[0][0]["content"]
        self.assertIn('"tech_manager"', planner)
        self.assertIn("the connected stores and their live sites and the project folder \"theme\"", planner)
        system = self.script.tech_calls[0][0]["content"]
        self.assertIn('Project folder: "theme"', system)
        self.assertIn('"لبان ظفار" (WooCommerce)', system)
        self.assertIn("Never attack, exploit", system)
        observations = "\n".join(m["content"] for m in self.script.tech_calls[-1] if m["role"] == "user")
        self.assertIn("Live check of \"بيت العود\" (Shopify) at https://oman-oud.com", observations)
        self.assertIn("إصدار PHP قديم", observations)
        self.assertNotIn("0123456789abcdef", observations)  # the key in the code stays masked
        self.assertEqual([c["path"] for c in step["changes"]], ["plugin/ajax.php"])
        self.assertEqual([a["kind"] for a in step["actions"]], ["store_update_product"])
        self.assertNotIn("esc_html($_GET['msg'])", (self.root / "plugin/ajax.php").read_text())  # nothing yet

        self.app.orchestrator.decide(step["id"], True)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        self.assertIn("echo esc_html($_GET['msg']);", (self.root / "plugin/ajax.php").read_text())
        self.assertEqual(self.shopify.products["gid://shopify/Product/1"]["seo"]["description"],
                         "بخور العود الملكي من بيت العود: رائحة فاخرة تدوم طويلًا.")
        output = task["steps"][0]["output"]
        self.assertIn("طُبّقت التعديلات على", output)
        self.assertIn("تغييرات المتجر", output)

    def test_files_only_leaves_store_alone(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.app.orchestrator.decide(step["id"], True, scope="files")
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        step = self.app.memory.get_task(tid)["steps"][0]
        self.assertIn("echo esc_html($_GET['msg']);", (self.root / "plugin/ajax.php").read_text())
        self.assertEqual(step["actions"][0]["status"], "rejected")
        self.assertIn("اخترت تطبيق الملفات فقط", step["output"])
        self.assertEqual(self.shopify.products["gid://shopify/Product/1"]["seo"]["description"], "")

    def test_reject_changes_nothing(self):
        tid = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.app.orchestrator.decide(step["id"], False)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        step = self.app.memory.get_task(tid)["steps"][0]
        self.assertEqual(AJAX_PHP, (self.root / "plugin/ajax.php").read_text())
        self.assertEqual({c["status"] for c in step["changes"]} | {a["status"] for a in step["actions"]}, {"rejected"})
        self.assertIn("رفضها المستخدم", step["output"])

    def test_short_check_request_reaches_the_planner(self):
        # a store is picked, which would normally skip the planner and go to the store manager
        tid = self.start("افحص متجري", turns=[{"tool": "site_check"}, {"tool": "store_health", "store": "x"},
                                              {"done": "لا مشاكل خطيرة"}], workspace=None, store="بيت العود")
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        self.assertEqual(len(self.script.planner_calls), 1)
        step = task["steps"][0]
        self.assertEqual(step["agent"], "tech_manager")
        self.assertEqual([t["ok"] for t in step["trace"]], [True, False])  # the picked store; unknown names fail
        self.assertIn('store "x" not found', step["trace"][1]["note"])
        system = self.script.tech_calls[0][0]["content"]
        self.assertNotIn('{"tool": "scan_code"', system)  # no project folder attached
        self.assertNotIn("Project folder", system)


if __name__ == "__main__":
    unittest.main()
