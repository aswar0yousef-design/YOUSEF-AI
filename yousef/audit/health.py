"""Store health: product data problems, and for WooCommerce the platform itself."""

from __future__ import annotations

import re
from collections import Counter

from ..models.base import ProviderError, http_json
from .findings import Finding

WP_VERSION_API = "https://api.wordpress.org/core/version-check/1.7/"
OLDEST_SUPPORTED_PHP = (8, 2)  # PHP 8.1 security support ended 31 Dec 2025; 8.2 ends 31 Dec 2026
_TAG = re.compile(r"<[^>]+>")


def version_tuple(text) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", str(text or ""))[:4])


def latest_wordpress() -> str | None:
    try:
        data = http_json("GET", WP_VERSION_API, timeout=10) or {}
        offers = data.get("offers") or []
        return offers[0].get("current") or offers[0].get("version") if offers else None
    except ProviderError:
        return None


def _names(products: list[dict], limit: int = 8) -> str:
    shown = ", ".join(f"{p['title']} ({p['id']})" for p in products[:limit])
    return shown + (f" و{len(products) - limit} غيرها" if len(products) > limit else "")


def product_findings(products: list[dict], platform: str) -> tuple[list[Finding], dict]:
    active = [p for p in products if p.get("status") == "active"]
    findings: list[Finding] = []

    def add(group, severity, area, title, fix):
        if group:
            findings.append(Finding(severity, area, f"{title}: {len(group)}", "", fix, _names(group)))

    add([p for p in active if p.get("price") in (None, "") or _number(p.get("price")) == 0], "high", "data",
        "منتجات منشورة بسعر صفر أو بلا سعر", "صحّح السعر أو أعد المنتج إلى مسودة.")
    add([p for p in active if not p.get("images")], "medium", "data", "منتجات منشورة بلا صورة",
        "أضف صورة واضحة لكل منتج؛ المنتجات بلا صور نادرًا ما تُباع.")
    add([p for p in active if len(_TAG.sub(" ", p.get("description") or "").strip()) < 40], "medium", "seo",
        "منتجات منشورة بوصف قصير جدًا أو بلا وصف", "اكتب وصفًا يشرح المنتج وفوائده؛ يفيد البيع ومحركات البحث.")
    add([p for p in active if p.get("inventory") is not None and p["inventory"] <= 0], "medium", "data",
        "منتجات منشورة نفدت كميتها", "أعد تعبئة المخزون أو أخفِ المنتج مؤقتًا.")
    add([p for p in products if not (p.get("sku") or "").strip()], "low", "data", "منتجات بلا SKU",
        "أضف رمز SKU لتسهيل الجرد والربط مع أنظمة أخرى.")
    skus = Counter((p.get("sku") or "").strip() for p in products if (p.get("sku") or "").strip())
    duplicated = [p for p in products if skus.get((p.get("sku") or "").strip(), 0) > 1]
    add(duplicated, "medium", "data", "منتجات تتشارك نفس SKU", "اجعل لكل منتج رمز SKU فريدًا.")
    if platform == "shopify":
        add([p for p in active if not (p.get("seo_description") or "").strip()], "low", "seo",
            "منتجات منشورة بلا وصف SEO", "أضف وصفًا مختصرًا يظهر في نتائج البحث (حتى 160 حرفًا).")
    stats = {"products_checked": len(products), "active": len(active)}
    return findings, stats


def _number(value) -> float:
    try:
        return float(str(value))
    except ValueError:
        return -1.0


def woocommerce_findings(status: dict, latest_wp: str | None = None) -> list[Finding]:
    env = status.get("environment") or {}
    security = status.get("security") or {}
    findings: list[Finding] = []
    php = env.get("php_version")
    if php and version_tuple(php)[:2] < OLDEST_SUPPORTED_PHP:
        findings.append(Finding("high", "platform", f"إصدار PHP قديم ولم يعد يتلقى تحديثات أمنية ({php})", "",
                                "ارفع PHP إلى 8.3 أو أحدث من لوحة الاستضافة بعد التأكد من توافق الإضافات.", "PHP"))
    elif php and version_tuple(php)[:2] == OLDEST_SUPPORTED_PHP:
        findings.append(Finding("low", "platform", f"PHP {php} ينتهي دعمه الأمني آخر 2026", "",
                                "خطّط للترقية إلى 8.3 أو أحدث.", "PHP"))
    wp = env.get("wp_version")
    if wp and latest_wp and version_tuple(wp) < version_tuple(latest_wp):
        findings.append(Finding("high" if version_tuple(wp)[:2] < version_tuple(latest_wp)[:2] else "medium", "platform",
                                f"ووردبريس غير محدّث ({wp}، والأحدث {latest_wp})", "",
                                "خذ نسخة احتياطية ثم حدّث ووردبريس من لوحة التحكم.", "WordPress"))
    if env.get("wp_debug_mode"):
        findings.append(Finding("high", "security", "وضع التصحيح WP_DEBUG مفعّل في الموقع المنشور",
                                "قد تظهر رسائل الأخطاء ومسارات الملفات للزوار",
                                "اجعل WP_DEBUG = false في wp-config.php.", "wp-config.php"))
    if security.get("secure_connection") is False:
        findings.append(Finding("high", "security", "المتجر لا يعمل عبر اتصال آمن (HTTPS)", "",
                                "فعّل SSL واجعل عنوان الموقع يبدأ بـ https في إعدادات ووردبريس.", "WordPress"))
    if security.get("hide_errors") is False:
        findings.append(Finding("medium", "security", "رسائل أخطاء PHP قد تظهر للزوار", "",
                                "عطّل display_errors في إعدادات PHP.", "PHP"))
    outdated = [p for p in status.get("active_plugins") or []
                if p.get("version_latest") and version_tuple(p.get("version")) < version_tuple(p["version_latest"])]
    for p in outdated:
        major = version_tuple(p.get("version"))[:1] < version_tuple(p["version_latest"])[:1]
        findings.append(Finding("high" if major else "medium", "platform",
                                f"إضافة غير محدّثة: {p.get('name')} ({p.get('version')} ← {p['version_latest']})",
                                "الإصدارات القديمة من الإضافات أشهر مصدر لاختراق مواقع ووردبريس",
                                "خذ نسخة احتياطية ثم حدّثها من «الإضافات».", p.get("plugin", "")))
    theme = status.get("theme") or {}
    if theme.get("version_latest") and version_tuple(theme.get("version")) < version_tuple(theme["version_latest"]):
        findings.append(Finding("medium", "platform",
                                f"القالب غير محدّث: {theme.get('name')} ({theme.get('version')} ← {theme['version_latest']})",
                                "", "حدّثه بعد نسخة احتياطية؛ عدّل عبر قالب ابن حتى لا تضيع تعديلاتك.", "theme"))
    if theme.get("has_outdated_templates"):
        findings.append(Finding("medium", "errors", "القالب يحوي قوالب WooCommerce قديمة",
                                "قد تسبب أخطاء في صفحات المنتج أو الدفع",
                                "حدّث القالب أو انسخ القوالب المحدّثة من WooCommerce ← الحالة.", "theme"))
    return findings


def store_health(entry: dict, client, wp_latest=latest_wordpress) -> tuple[list[Finding], dict]:
    products = client.all_products(250)
    findings, stats = product_findings(products, entry["platform"])
    if entry["platform"] == "woocommerce":
        try:
            status = client.system_status()
        except ProviderError as e:
            findings.append(Finding("info", "platform", "تعذّر قراءة حالة نظام WooCommerce", str(e)[:150],
                                    "يحتاج مفتاح REST API لمستخدم بصلاحية مدير.", "system_status"))
        else:
            env = status.get("environment") or {}
            stats.update({"woocommerce": env.get("version"), "wordpress": env.get("wp_version"), "php": env.get("php_version"),
                          "plugins": len(status.get("active_plugins") or [])})
            findings += woocommerce_findings(status, wp_latest() if wp_latest else None)
    return findings, stats
