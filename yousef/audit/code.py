"""Static scan of a project folder: secrets in code and risky patterns.

The rules are hints, not proof: the technical manager reads the code around
each hit before proposing a fix. Secret values are never returned, only a
masked prefix and where they are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..workspace.files import MAX_FILE_BYTES, Workspace, decode
from .findings import Finding

PHP = (".php", ".phtml", ".inc")
JS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte")
MARKUP = (".html", ".htm", ".liquid", ".twig", ".blade.php")
PY = (".py",)
TEXT = PHP + JS + MARKUP + PY + (".json", ".yml", ".yaml", ".toml", ".ini", ".conf", ".xml", ".env.example", ".sh",
                                 ".rb", ".go", ".java", ".cs", ".txt", ".md")
INPUT = r"\$_(?:GET|POST|REQUEST|COOKIE|SERVER\[['\"](?:HTTP_|QUERY_STRING|REQUEST_URI))"
SANITIZED = re.compile(r"esc_html|esc_attr|esc_url|esc_textarea|wp_kses|htmlspecialchars|htmlentities|intval|absint|"
                       r"sanitize_|filter_var|\(int\)", re.I)
PLACEHOLDER = re.compile(r"your|example|xxxx|changeme|placeholder|dummy|test|<|\$\{|process\.env|getenv|os\.environ", re.I)
MAX_PER_RULE = 20
MAX_FINDINGS = 150


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    exts: tuple
    severity: str
    title: str
    fix: str
    secret: bool = False
    unless: re.Pattern | None = None  # the line is fine when this also matches


def _r(p, flags=0):
    return re.compile(p, flags)


RULES = [
    # secrets (any text file)
    Rule("shopify-token", _r(r"\bshp(?:at|ss|ca|pa)_[0-9a-fA-F]{32}\b"), TEXT, "critical", "رمز Shopify مكتوب في الكود",
         "انقله إلى متغير بيئة، وألغِ الرمز من Shopify وأنشئ رمزًا جديدًا (قد يكون انكشف).", True),
    Rule("woo-key", _r(r"\b(?:ck|cs)_[0-9a-f]{40}\b"), TEXT, "critical", "مفتاح WooCommerce مكتوب في الكود",
         "انقله إلى متغير بيئة، واحذف المفتاح من WooCommerce وأنشئ غيره.", True),
    Rule("stripe-key", _r(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}"), TEXT, "critical", "مفتاح Stripe حي مكتوب في الكود",
         "ألغِ المفتاح من لوحة Stripe فورًا، وانقل الجديد إلى متغير بيئة.", True),
    Rule("ai-key", _r(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{24,}"), TEXT, "critical", "مفتاح API (OpenAI/Anthropic) مكتوب في الكود",
         "ألغِ المفتاح وأنشئ غيره، وانقله إلى متغير بيئة.", True),
    Rule("aws-key", _r(r"\bAKIA[0-9A-Z]{16}\b"), TEXT, "critical", "مفتاح AWS مكتوب في الكود",
         "عطّل المفتاح من IAM فورًا وأنشئ غيره خارج الكود.", True),
    Rule("github-token", _r(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}"), TEXT, "critical",
         "رمز GitHub مكتوب في الكود", "ألغِ الرمز من إعدادات GitHub وأنشئ غيره.", True),
    Rule("private-key", _r(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), TEXT, "critical",
         "مفتاح خاص (private key) داخل المشروع", "احذفه من المشروع ومن تاريخ git، وولّد مفتاحًا جديدًا.", True),
    Rule("google-key", _r(r"\bAIza[0-9A-Za-z_\-]{35}\b"), TEXT, "medium", "مفتاح Google API مكتوب في الكود",
         "إن كان للمتصفح فقيّده بنطاق موقعك من Google Cloud؛ وإن كان للخادم فانقله إلى متغير بيئة.", True),
    Rule("password", _r(r"(?i)\b(?:password|passwd|db_password|secret|api_?key|access_?token|auth_?token)\b\s*[:=]>?\s*"
                        r"['\"]([^'\"\s]{8,})['\"]"), PHP + JS + PY + (".json", ".yml", ".yaml", ".ini", ".toml"),
         "high", "كلمة مرور أو مفتاح مكتوب نصًا في الكود", "انقله إلى متغير بيئة أو ملف إعدادات خارج git.", True),
    # PHP / WordPress
    Rule("php-command", _r(rf"\b(?:system|exec|shell_exec|passthru|popen|proc_open)\s*\([^;]*{INPUT}"), PHP, "critical",
         "تنفيذ أوامر نظام بمدخلات من الزائر", "لا تمرر مدخلات المستخدم لأوامر النظام؛ وإن لزم فاستخدم escapeshellarg وقائمة قيم مسموحة."),
    Rule("php-unserialize", _r(rf"\bunserialize\s*\([^;]*{INPUT}"), PHP, "critical", "unserialize على مدخلات الزائر",
         "استخدم json_decode بدلًا منه."),
    Rule("php-include", _r(rf"\b(?:include|require)(?:_once)?\b[^;]*{INPUT}"), PHP, "critical",
         "تضمين ملف بمسار يحدده الزائر", "استخدم قائمة ملفات مسموحة بدل المسار القادم من الطلب."),
    Rule("php-sql-input", _r(rf"\b(?:mysqli?_query|->query)\s*\([^;]*{INPUT}"), PHP, "critical",
         "استعلام SQL يحوي مدخلات الزائر مباشرة (حقن SQL)", "استخدم استعلامات مجهّزة ($wpdb->prepare أو PDO prepare)."),
    Rule("wpdb-unprepared", _r(r"\$wpdb->(?:query|get_results|get_row|get_var|get_col)\s*\(\s*(?:\"[^\"]*\$\w|'[^']*'\s*\.\s*\$)"),
         PHP, "high", "استعلام $wpdb بمتغيرات بدون prepare", "مرّر القيم عبر $wpdb->prepare( ... %s / %d ...).",
         unless=_r(r"prepare\s*\(")),
    Rule("php-echo-input", _r(rf"\b(?:echo|print)\b[^;]*{INPUT}"), PHP, "high", "طباعة مدخلات الزائر بدون تنقية (XSS)",
         "استخدم esc_html أو esc_attr أو htmlspecialchars عند الطباعة.", unless=SANITIZED),
    Rule("php-eval", _r(r"(?<![\w>])eval\s*\("), PHP, "high", "استخدام eval في PHP", "استبدله بمنطق صريح."),
    Rule("php-extract", _r(rf"\bextract\s*\(\s*{INPUT}"), PHP, "high", "extract على مدخلات الزائر",
         "اقرأ المتغيرات المطلوبة بأسمائها."),
    Rule("php-upload", _r(r"\bmove_uploaded_file\s*\("), PHP, "medium", "رفع ملفات من الزوار",
         "تحقّق من الامتداد ونوع الملف وحجمه، واحفظه خارج المجلدات التي تنفّذ PHP (أو استخدم wp_handle_upload)."),
    Rule("weak-hash", _r(r"\b(?:md5|sha1)\s*\(\s*\$\w*pass", re.I), PHP, "medium", "كلمات مرور تُحفظ بـ md5/sha1",
         "استخدم password_hash و password_verify."),
    # JavaScript / markup
    Rule("js-eval", _r(r"(?<![\w.$])eval\s*\("), JS + MARKUP, "high", "استخدام eval في JavaScript", "استبدله بمنطق صريح أو JSON.parse."),
    Rule("js-function", _r(r"\bnew\s+Function\s*\("), JS + MARKUP, "medium", "إنشاء كود من نص (new Function)",
         "تجنّبه، خاصة مع نصوص تأتي من الزائر."),
    Rule("js-innerhtml", _r(r"\.(?:innerHTML|outerHTML)\s*\+?=(?!=)"), JS + MARKUP, "medium",
         "إدراج HTML عبر innerHTML (XSS إن كان المحتوى من الزائر)", "استخدم textContent، أو نقِّ HTML قبل إدراجه."),
    Rule("react-html", _r(r"dangerouslySetInnerHTML"), JS, "medium", "dangerouslySetInnerHTML في React",
         "تأكد أن المحتوى منقّى (مثل DOMPurify) أو لا يأتي من الزائر."),
    Rule("token-storage", _r(r"localStorage\.setItem\(\s*['\"][^'\"]*(?:token|jwt|auth|session)", re.I), JS + MARKUP, "medium",
         "حفظ رمز الدخول في localStorage (يسرقه أي XSS)", "استخدم ملف تعريف ارتباط HttpOnly وSecure."),
    Rule("http-asset", _r(r"(?:src|href|action)\s*=\s*[\"']http://(?!localhost|127\.0\.0\.1)"), MARKUP + PHP + JS, "low",
         "رابط http غير آمن (محتوى مختلط)", "غيّره إلى https."),
    # Python
    Rule("py-eval", _r(r"(?<![\w.])(?:eval|exec)\s*\("), PY, "high", "eval/exec في Python", "استبدله بمنطق صريح."),
    Rule("py-shell", _r(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), PY, "high", "تشغيل أوامر عبر shell=True",
         "مرّر الأمر كقائمة بدون shell."),
    Rule("py-pickle", _r(r"\bpickle\.loads?\s*\("), PY, "medium", "pickle على بيانات قد تأتي من الخارج", "استخدم JSON."),
    Rule("py-yaml", _r(r"\byaml\.load\s*\((?![^)]*Loader)"), PY, "medium", "yaml.load بدون Loader آمن", "استخدم yaml.safe_load."),
    Rule("tls-off", _r(r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|CURLOPT_SSL_VERIFYPEER\s*,\s*(?:false|0)", re.I),
         PY + JS + PHP, "high", "تعطيل التحقق من شهادات TLS", "أعد التحقق؛ تعطيله يسمح باعتراض الاتصال."),
    Rule("debug-on", _r(r"^\s*(?:DEBUG\s*=\s*True|define\s*\(\s*['\"]WP_DEBUG['\"]\s*,\s*true)", re.I), PY + PHP, "medium",
         "وضع التصحيح مفعّل في الإعدادات", "تأكد أنه معطّل في الموقع المنشور."),
]

_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|fixtures?|vendor|node_modules)/|\.(?:test|spec)\.[jt]sx?$|_test\.py$|(^|/)test_")


def _mask(value: str, keep: int = 4) -> str:
    """Enough to recognise which key it is, never enough to use it."""
    return f"{value[:keep]}… ({len(value)} حرفًا)"


def redact(line: str) -> str:
    """A code line safe to show: any secret in it is masked."""
    for rule in RULES:
        if rule.secret and rule.id != "password":
            line = rule.pattern.sub(lambda m: _mask(m.group(0)), line)
    return re.sub(r"(?i)((?:password|passwd|secret|api_?key|token)\b\s*[:=]>?\s*['\"])[^'\"]{4,}(['\"])", r"\1•••\2", line)


def _matches_ext(rel: str, exts: tuple) -> bool:
    lower = rel.lower()
    return any(lower.endswith(e) for e in exts)


def scan_code(ws: Workspace, under: str = "") -> tuple[list[Finding], dict]:
    prefix = (ws.rel(ws.resolve(under)) + "/") if under.strip() else ""
    if prefix == "./":
        prefix = ""
    findings: list[Finding] = []
    per_rule: dict[str, int] = {}
    scanned = 0
    for full, rel in ws.iter_files():
        if prefix and not rel.startswith(prefix):
            continue
        if not _matches_ext(rel, TEXT) or rel.endswith(".min.js"):
            continue
        try:
            if full.stat().st_size > MAX_FILE_BYTES:
                continue
            text = decode(full.read_bytes())
        except OSError:
            continue
        if text is None:
            continue
        scanned += 1
        is_test = bool(_TEST_PATH.search(rel))
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            if len(line) > 3000:
                continue  # minified or generated
            known_key = False  # a recognised key on this line; the generic password rule would repeat it
            for rule in RULES:
                if (is_test and not rule.secret) or not _matches_ext(rel, rule.exts):
                    continue
                if rule.id == "password" and known_key:
                    continue
                m = rule.pattern.search(line)
                if not m or (rule.unless and rule.unless.search(line)):
                    continue
                if rule.secret:
                    known_key = True
                    value = m.group(m.lastindex or 0)
                    if rule.id == "password" and PLACEHOLDER.search(value):
                        continue
                    keep = 0 if rule.id == "private-key" else (2 if rule.id == "password" else 4)
                    detail = "مفتاح خاص كامل داخل الملف" if rule.id == "private-key" else f"القيمة تبدأ بـ {_mask(value, keep)}"
                else:
                    detail = redact(line.strip())[:160]
                if per_rule.get(rule.id, 0) >= MAX_PER_RULE or len(findings) >= MAX_FINDINGS:
                    continue
                per_rule[rule.id] = per_rule.get(rule.id, 0) + 1
                findings.append(Finding(rule.severity, "code", rule.title, detail, rule.fix, f"{rel}:{number}"))
        # WordPress handlers that change data without a nonce check (CSRF)
        if _matches_ext(rel, PHP) and not is_test:
            ajax = re.search(r"add_action\s*\(\s*['\"]wp_ajax_", text)
            if ajax and not re.search(r"check_ajax_referer|wp_verify_nonce|check_admin_referer", text):
                line_no = text[:ajax.start()].count("\n") + 1
                findings.append(Finding("medium", "code", "معالج AJAX في ووردبريس بدون فحص nonce (CSRF)", "",
                                        "أضف check_ajax_referer في بداية المعالج، وتحقق من صلاحية المستخدم.",
                                        f"{rel}:{line_no}"))
            route = re.search(r"register_rest_route\s*\(", text)
            if route and "permission_callback" not in text:
                line_no = text[:route.start()].count("\n") + 1
                findings.append(Finding("medium", "code", "مسار REST API بدون permission_callback", "",
                                        "أضف permission_callback يتحقق من الصلاحية (أو __return_true إن كان عامًا عن قصد).",
                                        f"{rel}:{line_no}"))
    return findings, {"files_scanned": scanned, "folder": prefix or "."}


def manifest_files(ws: Workspace) -> list[tuple]:
    names = ("package-lock.json", "package.json", "composer.lock", "requirements.txt")
    out = []
    for full, rel in ws.iter_files():
        if PurePosixPath(rel).name in names:
            out.append((full, rel))
    return out
