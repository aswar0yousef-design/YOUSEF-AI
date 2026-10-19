"""Check tools for the technical manager.

Live checks run only against the user's own connected stores, and code checks
only inside the project folder the user allowed. The tools read and report;
fixes are proposed through the file and store tools and wait for approval.
"""

from __future__ import annotations

import urllib.parse

from ..integrations.stores import PLATFORM_LABEL
from ..models.base import ProviderError
from ..workspace.files import Workspace, WorkspaceError
from .code import scan_code
from .deps import _osv_get, _osv_post, check_dependencies
from .findings import SEVERITIES, SEVERITY_AR, Finding, format_findings
from .health import latest_wordpress, store_health
from .site import SiteAuditor

OBSERVATION_LIMIT = 9000

STORE_AUDIT_HELP = """{"tool": "site_check", "store": "name"}                                   check the store's live website: HTTPS and certificate, security headers, exposed files (.env, .git, backups, logs), server errors, speed and SEO basics
{"tool": "store_health", "store": "name"}                                 store health: product data problems; for WooCommerce also WordPress, PHP, plugin and theme versions and risky settings"""
CODE_AUDIT_HELP = """{"tool": "scan_code", "path": ""}                                       scan the project's code for written-in keys and passwords and risky patterns (SQL injection, XSS, eval, missing nonce...); path optional
{"tool": "check_dependencies"}                                            known vulnerabilities (CVE) in the project's packages, from package-lock.json, package.json, composer.lock or requirements.txt"""


def _clip(text: str, limit: int = OBSERVATION_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... (cut at {limit} characters)"


def counts(findings: list[Finding]) -> str:
    return "، ".join(f"{SEVERITY_AR[s]} {n}" for s in SEVERITIES
                     if (n := sum(1 for f in findings if f.severity == s))) or "لا مشاكل"


def site_address(entry: dict, client) -> str:
    """The public website of a connected store (Shopify: its primary domain)."""
    site = ""
    if entry["platform"] == "shopify":
        try:
            site = str(client.info().get("site") or "")
        except ProviderError:
            site = ""
    site = site or str(getattr(client, "base", "") or entry.get("url") or "")
    if "://" not in site:
        site = "https://" + site
    parsed = urllib.parse.urlparse(site)
    return f"{parsed.scheme}://{parsed.netloc}"


class AuditToolbox:
    def __init__(self, store_tools=None, workspace: Workspace | None = None, auditor: SiteAuditor | None = None,
                 osv: tuple | None = None, wp_latest=latest_wordpress):
        self.store_tools = store_tools  # StoreToolbox: resolves store names and keeps one client per store
        self.ws = workspace
        self.auditor = auditor or SiteAuditor()
        self.osv_post, self.osv_get = osv or (_osv_post, _osv_get)
        self.wp_latest = wp_latest
        self.trace: list[dict] = []
        self.reports: list[dict] = []  # {"check", "target", "counts", "findings"} for the final report
        self._done: dict[tuple, str] = {}

    def help(self) -> str:
        parts = []
        if self.store_tools is not None:
            parts.append(STORE_AUDIT_HELP)
        if self.ws is not None:
            parts.append(CODE_AUDIT_HELP)
        return "\n".join(parts)

    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        target = str(call.get("store") or call.get("path") or "")
        try:
            handler = getattr(self, f"tool_{tool}", None)
            if handler is None:
                raise ValueError(f'unknown tool "{tool}". Available tools:\n{self.help()}')
            out, note = handler(call)
            self.trace.append({"tool": tool, "target": target[:200], "ok": True, "note": note})
            return _clip(out)
        except (ProviderError, WorkspaceError, ValueError, TypeError, OSError) as e:
            self.trace.append({"tool": tool, "target": target[:200], "ok": False, "note": str(e)[:200]})
            return f"Error: {e}"

    def _report(self, check: str, target: str, findings: list[Finding], header: str, stats: dict) -> tuple[str, str]:
        self.reports.append({"check": check, "target": target, "counts": counts(findings),
                             "findings": [f.as_dict() for f in findings]})
        facts = ", ".join(f"{k}: {v}" for k, v in stats.items() if v not in (None, "", [], {}))
        return format_findings(findings, header) + (f"\n\nChecked: {facts}" if facts else ""), counts(findings)

    def _need_store(self, call: dict):
        if self.store_tools is None:
            raise ValueError("no store is connected; add one in the Stores panel first")
        return self.store_tools._store(call)

    def _need_workspace(self) -> Workspace:
        if self.ws is None:
            raise ValueError("no project folder is attached to this request")
        return self.ws

    # ---- live checks --------------------------------------------------------------------
    def tool_site_check(self, call: dict) -> tuple[str, str]:
        entry, client = self._need_store(call)
        key = ("site", entry["id"])
        if key in self._done:
            return "Already checked in this task; results above:\n" + self._done[key], "مكرر"
        site = site_address(entry, client)
        findings, stats = self.auditor.audit(site, wordpress=entry["platform"] == "woocommerce")
        header = f'Live check of "{entry["name"]}" ({PLATFORM_LABEL[entry["platform"]]}) at {site}'
        if entry["platform"] == "shopify":
            header += " — Shopify hosts the server: fix server settings through theme code or apps, not hosting"
        out, note = self._report("site", site, findings, header, stats)
        self._done[key] = out
        return out, note

    def tool_store_health(self, call: dict) -> tuple[str, str]:
        entry, client = self._need_store(call)
        findings, stats = store_health(entry, client, self.wp_latest)
        header = f'Store health of "{entry["name"]}" ({PLATFORM_LABEL[entry["platform"]]})'
        return self._report("store", entry["name"], findings, header, stats)

    # ---- project checks -----------------------------------------------------------------
    def tool_scan_code(self, call: dict) -> tuple[str, str]:
        ws = self._need_workspace()
        findings, stats = scan_code(ws, str(call.get("path") or ""))
        header = f'Code scan of "{ws.name}" (hints: read the code to confirm each one before fixing)'
        return self._report("code", ws.name, findings, header, stats)

    def tool_check_dependencies(self, call: dict) -> tuple[str, str]:
        ws = self._need_workspace()
        findings, stats = check_dependencies(ws, self.osv_post, self.osv_get)
        if not stats["manifests"]:
            return "No package manifest found (package-lock.json, package.json, composer.lock, requirements.txt).", "لا ملفات حزم"
        stats = {"packages": stats["packages"], "files": ", ".join(stats["manifests"][:5])}
        header = f'Dependency check of "{ws.name}" (OSV database)'
        return self._report("dependencies", ws.name, findings, header, stats)
