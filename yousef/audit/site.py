"""A non-intrusive health check of a store's public website.

Only reads: the home page, robots.txt/sitemap, and a short fixed list of
well-known paths that should never be public (.env, .git, backups of
wp-config.php, debug logs, phpinfo). It sends no forms, guesses no passwords
and exploits nothing. When a sensitive file is exposed, only that fact is
reported; its contents are never kept or passed on.
"""

from __future__ import annotations

import datetime as dt
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .findings import Finding

TIMEOUT = 10
MAX_PAGE = 3_000_000
MAX_PROBE = 65_536
USER_AGENT = "YOUSEF-AI site check (owner audit)"


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    url: str
    seconds: float = 0.0
    error: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url: str, limit: int = MAX_PAGE, follow: bool = True) -> Response:
    handlers = [] if follow else [_NoRedirect()]
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    started = time.time()
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            body = resp.read(limit)
            return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, body, resp.geturl(),
                            time.time() - started)
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read(limit)
        except Exception:
            pass
        return Response(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body, url, time.time() - started)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return Response(0, {}, b"", url, time.time() - started, error=str(getattr(e, "reason", e))[:200])


def cert_days_left(host: str, port: int = 443) -> int | None:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            not_after = tls.getpeercert().get("notAfter")
    if not not_after:
        return None
    expires = dt.datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), dt.timezone.utc)
    return (expires - dt.datetime.now(dt.timezone.utc)).days


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.links: list[dict] = []
        self.h1 = 0
        self.images = 0
        self.images_no_alt = 0
        self.scripts = 0
        self.lang = ""
        self.assets: list[str] = []
        self.forms: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            self.links.append(a)
            if "stylesheet" in a.get("rel", "").lower() and a.get("href"):
                self.assets.append(a["href"])
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.images += 1
            if not a.get("alt", "").strip():
                self.images_no_alt += 1
            if a.get("src"):
                self.assets.append(a["src"])
        elif tag in ("script", "iframe"):
            if tag == "script":
                self.scripts += 1
            if a.get("src"):
                self.assets.append(a["src"])
        elif tag == "form":
            self.forms.append(a.get("action", ""))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def blocks_everyone(robots: str) -> bool:
    """True when the group for all crawlers ("User-agent: *") disallows the whole site."""
    agents: list[str] = []
    in_rules = False
    for raw in robots.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:  # a new group starts
                agents, in_rules = [], False
            agents.append(value)
        elif key in ("disallow", "allow"):
            in_rules = True
            if key == "disallow" and value == "/" and "*" in agents:
                return True
    return False


def _wp_backup(body: bytes) -> bool:
    return b"DB_PASSWORD" in body or b"DB_NAME" in body


# path, WordPress only?, "is it really exposed" check, severity, title, fix
PROBES = [
    ("/.env", False, lambda b: re.search(rb"^[A-Z][A-Z0-9_]{2,}=", b, re.M) is not None, "critical",
     "ملف .env مكشوف للعامة (قد يحوي كلمات مرور ومفاتيح)",
     "احذفه من مجلد الموقع العام أو امنع الوصول إليه، ثم غيّر كل كلمات المرور والمفاتيح التي فيه فورًا."),
    ("/.git/HEAD", False, lambda b: b.startswith(b"ref:"), "critical",
     "مستودع git مكشوف (.git): يمكن تنزيل كود الموقع وتاريخه",
     "امنع الوصول إلى مجلد .git من إعدادات الخادم أو احذفه من المجلد العام، وغيّر أي سر كان في الكود."),
    ("/wp-config.php.bak", True, _wp_backup, "critical", "نسخة احتياطية من wp-config.php مكشوفة",
     "احذف النسخة فورًا وغيّر كلمة مرور قاعدة البيانات ومفاتيح الأمان (salts)."),
    ("/wp-config.php~", True, _wp_backup, "critical", "نسخة احتياطية من wp-config.php مكشوفة",
     "احذف النسخة فورًا وغيّر كلمة مرور قاعدة البيانات ومفاتيح الأمان (salts)."),
    ("/wp-config.php.save", True, _wp_backup, "critical", "نسخة محفوظة من wp-config.php مكشوفة",
     "احذف النسخة فورًا وغيّر كلمة مرور قاعدة البيانات ومفاتيح الأمان (salts)."),
    ("/wp-config.old", True, _wp_backup, "critical", "نسخة قديمة من wp-config.php مكشوفة",
     "احذف النسخة فورًا وغيّر كلمة مرور قاعدة البيانات ومفاتيح الأمان (salts)."),
    ("/wp-content/debug.log", True, lambda b: b"PHP " in b, "high", "سجل أخطاء ووردبريس مكشوف (debug.log)",
     "عطّل WP_DEBUG_LOG في الموقع المنشور أو انقل السجل خارج المجلد العام، ثم احذف الملف."),
    ("/debug.log", False, lambda b: b"PHP " in b, "high", "سجل أخطاء مكشوف (debug.log)",
     "انقل السجل خارج المجلد العام واحذف الملف."),
    ("/phpinfo.php", False, lambda b: b"phpinfo()" in b or b"PHP Version" in b, "high",
     "صفحة phpinfo مكشوفة (تكشف إعدادات الخادم)", "احذف الملف من الموقع."),
    ("/info.php", False, lambda b: b"phpinfo()" in b or b"PHP Version" in b, "high",
     "صفحة phpinfo مكشوفة (تكشف إعدادات الخادم)", "احذف الملف من الموقع."),
    ("/xmlrpc.php", True, lambda b: b"XML-RPC server accepts POST requests only" in b, "medium",
     "xmlrpc.php مفعّل (يُستغل لتخمين كلمات المرور بكثافة)",
     "عطّله إن لم تستخدم تطبيق ووردبريس للجوال أو Jetpack، بإضافة أمان أو بقاعدة في الخادم."),
    ("/wp-json/wp/v2/users", True, lambda b: b'"slug"' in b, "medium",
     "أسماء مستخدمي ووردبريس متاحة للعامة عبر REST API",
     "قيّد نقطة users في REST API لغير المسجلين (إضافة أمان أو كود صغير في القالب الابن)."),
    ("/wp-content/uploads/", True, lambda b: b"Index of /" in b, "medium", "عرض محتوى مجلد الرفع مفعّل",
     "عطّل عرض المجلدات (Options -Indexes في .htaccess أو من إعدادات الاستضافة)."),
    ("/readme.html", True, lambda b: b"WordPress" in b, "low", "ملف readme.html يكشف أن الموقع ووردبريس وقد يكشف إصداره",
     "احذف readme.html من جذر الموقع."),
]

HEADER_CHECKS = [
    ("strict-transport-security", "medium", "ترويسة HSTS غير موجودة",
     "فعّل Strict-Transport-Security حتى يفتح المتصفح موقعك بـ https دائمًا."),
    ("x-content-type-options", "low", "ترويسة X-Content-Type-Options غير موجودة",
     "أضف X-Content-Type-Options: nosniff."),
    ("referrer-policy", "low", "ترويسة Referrer-Policy غير موجودة",
     "أضف Referrer-Policy: strict-origin-when-cross-origin."),
]


class SiteAuditor:
    def __init__(self, fetcher=fetch, cert_check=cert_days_left):
        self.fetch = fetcher
        self.cert_check = cert_check

    def audit(self, site: str, wordpress: bool = False) -> tuple[list[Finding], dict]:
        site = site.rstrip("/")
        parsed = urllib.parse.urlparse(site)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"invalid site address: {site}")
        findings: list[Finding] = []
        stats: dict = {"site": site}

        home = self.fetch(site + "/")
        stats["status"] = home.status
        if home.status == 0:
            if "CERTIFICATE" in home.error.upper() or "SSL" in home.error.upper():
                findings.append(Finding("critical", "security", "شهادة TLS غير صالحة: المتصفحات ستحذّر زوارك",
                                        home.error, "جدّد الشهادة أو صحّحها من لوحة الاستضافة.", site))
            else:
                findings.append(Finding("critical", "errors", "تعذّر فتح الموقع", home.error,
                                        "تأكد أن الموقع يعمل وأن الرابط صحيح.", site))
            return findings, stats
        if home.status >= 500:
            findings.append(Finding("critical", "errors", f"الصفحة الرئيسية ترجع خطأ خادم {home.status}", "",
                                    "راجع سجل أخطاء الخادم.", site))
        elif home.status >= 400:
            findings.append(Finding("high", "errors", f"الصفحة الرئيسية ترجع {home.status}", "", "", site))

        self._transport(site, parsed, home, findings, stats)
        self._headers(home, findings)
        if home.status < 400:
            self._page(site, home, findings, stats)
        self._crawl_basics(site, wordpress, findings, stats)
        self._probes(site, wordpress, findings, stats)
        return findings, stats

    # ---- parts ----------------------------------------------------------------------
    def _transport(self, site, parsed, home, findings, stats):
        if parsed.scheme != "https" or not home.url.startswith("https://"):
            findings.append(Finding("critical", "security", "الموقع لا يعمل بـ HTTPS",
                                    f"الصفحة فُتحت على {home.url}",
                                    "فعّل شهادة SSL (مجانية من Let's Encrypt في معظم الاستضافات) ووجّه كل الزيارات إلى https.",
                                    site))
            return
        plain = self.fetch(f"http://{parsed.netloc}/", limit=MAX_PROBE)
        if plain.status and not plain.url.startswith("https://"):
            findings.append(Finding("high", "security", "نسخة http من الموقع لا تتحوّل إلى https",
                                    f"http://{parsed.netloc}/ يفتح بدون تحويل",
                                    "أضف تحويلًا دائمًا (301) من http إلى https.", f"http://{parsed.netloc}/"))
        try:
            days = self.cert_check(parsed.hostname, parsed.port or 443)
        except (OSError, ssl.SSLError, ValueError) as e:
            days = None
            findings.append(Finding("high", "security", "تعذّر التحقق من شهادة TLS", str(e)[:150], "", site))
        if days is not None:
            stats["certificate_days_left"] = days
            if days < 0:
                findings.append(Finding("critical", "security", "شهادة TLS منتهية", f"انتهت منذ {-days} يومًا",
                                        "جدّد الشهادة فورًا.", site))
            elif days < 14:
                findings.append(Finding("high", "security", f"شهادة TLS تنتهي خلال {days} يومًا", "",
                                        "تأكد أن التجديد التلقائي يعمل، أو جدّدها الآن.", site))

    def _headers(self, home, findings):
        h = home.headers
        for name, severity, title, fix in HEADER_CHECKS:
            if name == "strict-transport-security" and not home.url.startswith("https://"):
                continue
            if name not in h:
                findings.append(Finding(severity, "security", title, "", fix, home.url))
        csp = h.get("content-security-policy", "")
        if "frame-ancestors" not in csp and "x-frame-options" not in h:
            findings.append(Finding("medium", "security", "الموقع يمكن تضمينه داخل مواقع أخرى (clickjacking)",
                                    "لا X-Frame-Options ولا frame-ancestors في CSP",
                                    "أضف X-Frame-Options: SAMEORIGIN أو frame-ancestors 'self'.", home.url))
        for name in ("server", "x-powered-by"):
            value = h.get(name, "")
            if re.search(r"\d+\.\d+", value):
                findings.append(Finding("low", "security", f"ترويسة {name} تكشف إصدار البرنامج", value,
                                        "أخفِ رقم الإصدار من إعدادات الخادم أو PHP (expose_php = Off).", home.url))

    def _page(self, site, home, findings, stats):
        stats["home_bytes"] = len(home.body)
        stats["home_seconds"] = round(home.seconds, 2)
        if home.seconds > 3:
            findings.append(Finding("medium", "speed", f"الصفحة الرئيسية بطيئة ({home.seconds:.1f} ثانية)", "",
                                    "فعّل التخزين المؤقت وضغط الصور، وقلّل الإضافات والسكربتات.", site))
        if len(home.body) > 2_000_000:
            findings.append(Finding("medium", "speed", f"كود الصفحة الرئيسية كبير ({len(home.body) // 1024} KB)", "",
                                    "قلّل الأقسام والسكربتات المضمّنة في الصفحة.", site))
        page = _Page()
        try:
            page.feed(home.body.decode("utf-8", "replace"))
        except Exception:
            return
        stats.update({"title": page.title.strip()[:120], "images": page.images, "scripts": page.scripts})
        title = page.title.strip()
        if not title:
            findings.append(Finding("medium", "seo", "الصفحة الرئيسية بلا عنوان <title>", "", "أضف عنوانًا يصف متجرك.", site))
        elif len(title) > 65:
            findings.append(Finding("low", "seo", f"عنوان الصفحة طويل ({len(title)} حرفًا)", title[:80],
                                    "اجعله أقل من 60 حرفًا تقريبًا.", site))
        desc = page.meta.get("description", "").strip()
        if not desc:
            findings.append(Finding("medium", "seo", "لا يوجد وصف meta description للصفحة الرئيسية", "",
                                    "أضف وصفًا جذابًا بين 120 و160 حرفًا.", site))
        if page.h1 == 0:
            findings.append(Finding("low", "seo", "لا يوجد عنوان H1 في الصفحة الرئيسية", "", "", site))
        elif page.h1 > 1:
            findings.append(Finding("low", "seo", f"يوجد {page.h1} عناوين H1 في الصفحة الرئيسية", "",
                                    "اكتفِ بعنوان H1 واحد.", site))
        if page.images_no_alt:
            findings.append(Finding("low", "seo", f"{page.images_no_alt} من {page.images} صورة بلا نص بديل (alt)", "",
                                    "أضف وصفًا قصيرًا لكل صورة؛ يفيد محركات البحث وقارئات الشاشة.", site))
        if not page.lang:
            findings.append(Finding("low", "seo", "وسم html بلا لغة (lang)", "", 'أضف lang="ar" أو اللغة المناسبة.', site))
        if "viewport" not in page.meta:
            findings.append(Finding("medium", "seo", "لا يوجد وسم viewport: الموقع لن يظهر جيدًا على الجوال", "", "", site))
        if not any("canonical" in l.get("rel", "").lower() for l in page.links):
            findings.append(Finding("info", "seo", "لا يوجد رابط canonical", "", "", site))
        if not page.meta.get("og:title") or not page.meta.get("og:image"):
            findings.append(Finding("low", "seo", "وسوم Open Graph ناقصة (شكل الرابط عند مشاركته في السوشيال)", "",
                                    "أضف og:title وog:image وog:description.", site))
        if home.url.startswith("https://"):
            mixed = sorted({a for a in page.assets if a.startswith("http://")})
            if mixed:
                findings.append(Finding("medium", "security", f"محتوى مختلط: {len(mixed)} ملفًا يُحمَّل عبر http",
                                        ", ".join(mixed[:3]), "غيّر روابطها إلى https.", site))
            insecure_forms = [f for f in page.forms if f.startswith("http://")]
            if insecure_forms:
                findings.append(Finding("high", "security", "نموذج يرسل بياناته عبر http", insecure_forms[0],
                                        "اجعل عنوان الإرسال https.", site))
        generator = page.meta.get("generator", "")
        if generator:
            stats["generator"] = generator
            if re.search(r"\d+\.\d+", generator):
                findings.append(Finding("low", "security", "الموقع يعلن إصدار برنامجه في الصفحة", generator,
                                        "أخفِ وسم generator (إضافة أمان أو سطر في القالب الابن).", site))

    def _crawl_basics(self, site, wordpress, findings, stats):
        robots = self.fetch(site + "/robots.txt", limit=MAX_PROBE)
        robots_text = robots.body.decode("utf-8", "replace") if robots.status == 200 else ""
        if "user-agent" not in robots_text.lower():
            findings.append(Finding("low", "seo", "ملف robots.txt غير موجود", "", "أضفه مع رابط خريطة الموقع.",
                                    site + "/robots.txt"))
        elif blocks_everyone(robots_text):
            findings.append(Finding("high", "seo", "robots.txt يمنع محركات البحث من الموقع كله",
                                    "User-agent: * ثم Disallow: /", "احذف هذا السطر إن كان الموقع جاهزًا للنشر.",
                                    site + "/robots.txt"))
        host = urllib.parse.urlparse(site).netloc
        listed = [u for u in re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots_text)
                  if urllib.parse.urlparse(u).netloc == host]  # only this site's own sitemap
        candidates = listed[:1] or [site + p for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")]
        found = False
        for url in candidates:
            r = self.fetch(url, limit=MAX_PROBE)
            if r.status == 200 and (b"<urlset" in r.body or b"<sitemapindex" in r.body):
                found = True
                break
        stats["sitemap"] = found
        if not found:
            findings.append(Finding("medium", "seo", "خريطة الموقع (sitemap.xml) غير موجودة", "",
                                    "فعّلها من إضافة SEO أو إعدادات المنصة، وأضفها في Google Search Console.", site))

    def _probes(self, site, wordpress, findings, stats):
        exposed = 0
        for path, wp_only, check, severity, title, fix in PROBES:
            if wp_only and not wordpress:
                continue
            r = self.fetch(site + path, limit=MAX_PROBE, follow=False)
            if r.status == 200 and check(r.body):
                exposed += 1
                findings.append(Finding(severity, "security", title, f"{path} يرجع 200 للجميع", fix, site + path))
        stats["probes"] = sum(1 for p in PROBES if wordpress or not p[1])
        stats["exposed"] = exposed
