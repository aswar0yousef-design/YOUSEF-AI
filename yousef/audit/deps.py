"""Known vulnerabilities in a project's dependencies, from the open OSV database.

Only package names and versions are sent to api.osv.dev (Google's open source
vulnerability database), never code. Exact versions come from lock files;
without one, versions from package.json are approximate.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from ..models.base import ProviderError, http_json
from ..workspace.files import Workspace
from .code import manifest_files
from .findings import Finding

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
OSV_VULN = "https://api.osv.dev/v1/vulns/"
MAX_PACKAGES = 3000
MAX_DETAILS = 25
SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "MEDIUM": "medium", "LOW": "low"}
ORDER = ("critical", "high", "medium", "low", "info")


def _osv_post(url: str, body: dict) -> dict:
    return http_json("POST", url, body=body, timeout=30) or {}


def _osv_get(url: str) -> dict:
    return http_json("GET", url, timeout=20) or {}


def _clean_version(spec: str) -> str | None:
    m = re.search(r"\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.\-]+)?", str(spec or ""))
    return m.group(0) if m else None


def collect(ws: Workspace) -> tuple[list[dict], list[str]]:
    packages: list[dict] = []
    sources: list[str] = []
    files = manifest_files(ws)
    locked_dirs = {str(PurePosixPath(rel).parent) for _, rel in files if rel.endswith("package-lock.json")}
    for full, rel in files:
        name = PurePosixPath(rel).name
        folder = str(PurePosixPath(rel).parent)
        try:
            text = full.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        found = []
        try:
            if name == "package-lock.json":
                data = json.loads(text)
                for path, info in (data.get("packages") or {}).items():
                    if path and "node_modules/" in path and info.get("version"):
                        found.append(("npm", path.rsplit("node_modules/", 1)[1], info["version"], True))
                if not data.get("packages"):  # lockfileVersion 1
                    for pkg, info in (data.get("dependencies") or {}).items():
                        if info.get("version"):
                            found.append(("npm", pkg, info["version"], True))
            elif name == "package.json" and folder not in locked_dirs:
                data = json.loads(text)
                for key in ("dependencies", "devDependencies"):
                    for pkg, spec in (data.get(key) or {}).items():
                        version = _clean_version(spec)
                        if version:
                            found.append(("npm", pkg, version, False))
            elif name == "composer.lock":
                data = json.loads(text)
                for info in (data.get("packages") or []) + (data.get("packages-dev") or []):
                    version = str(info.get("version") or "").lstrip("v")
                    if info.get("name") and version:
                        found.append(("Packagist", info["name"], version, True))
            elif name == "requirements.txt":
                for line in text.splitlines():
                    m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*==\s*([0-9][^\s;#]*)", line)
                    if m:
                        found.append(("PyPI", m.group(1), m.group(2), True))
        except ValueError:
            continue
        if found:
            sources.append(rel)
        for eco, pkg, version, exact in found:
            packages.append({"ecosystem": eco, "name": pkg, "version": version, "source": rel, "exact": exact})
    seen, unique = set(), []
    for p in packages:
        key = (p["ecosystem"], p["name"], p["version"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique[:MAX_PACKAGES], sources


def _fixed_in(vuln: dict, name: str) -> str | None:
    for affected in vuln.get("affected") or []:
        if (affected.get("package") or {}).get("name") != name:
            continue
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if event.get("fixed"):
                    return event["fixed"]
    return None


def _severity(vuln: dict) -> str:
    level = str((vuln.get("database_specific") or {}).get("severity") or "").upper()
    return SEVERITY.get(level, "medium")


def check_dependencies(ws: Workspace, post=_osv_post, get=_osv_get) -> tuple[list[Finding], dict]:
    packages, sources = collect(ws)
    stats = {"packages": len(packages), "manifests": sources}
    if not packages:
        return [], stats
    hits: list[tuple[dict, list[str]]] = []
    for start in range(0, len(packages), 1000):
        chunk = packages[start:start + 1000]
        data = post(OSV_BATCH, {"queries": [{"package": {"name": p["name"], "ecosystem": p["ecosystem"]},
                                             "version": p["version"]} for p in chunk]})
        for p, result in zip(chunk, data.get("results") or []):
            ids = [v.get("id") for v in (result or {}).get("vulns") or [] if v.get("id")]
            if ids:
                hits.append((p, ids))
    details: dict[str, dict] = {}
    for _, ids in hits:
        for vid in ids:
            if vid not in details and len(details) < MAX_DETAILS:
                try:
                    details[vid] = get(OSV_VULN + vid)
                except ProviderError:
                    details[vid] = {}
    findings = []
    for p, ids in hits:
        known = [details[i] for i in ids if details.get(i)]
        severity = min((_severity(v) for v in known), key=ORDER.index, default="medium")
        summaries = [v.get("summary") for v in known if v.get("summary")]
        fixes = [f for f in (_fixed_in(v, p["name"]) for v in known) if f]
        cves = sorted({a for v in known for a in v.get("aliases") or [] if a.startswith("CVE-")})
        title = f"{p['name']} {p['version']}: {len(ids)} {'ثغرة معروفة' if len(ids) == 1 else 'ثغرات معروفة'}"
        detail = "; ".join(summaries[:2]) or ", ".join(ids[:3])
        if cves:
            detail += f" ({', '.join(cves[:3])})"
        if not p["exact"]:
            detail += " — الإصدار تقريبي من package.json؛ شغّل npm install لإنشاء package-lock.json لنتيجة أدق"
        if fixes:
            newest = max(fixes, key=lambda v: [int(x) for x in re.findall(r"\d+", v)])
            fix = f"حدّث {p['name']} إلى {newest} أو أحدث."
        else:
            fix = f"حدّث {p['name']} إلى أحدث إصدار، أو استبدله إن لم يعد مدعومًا."
        findings.append(Finding(severity, "dependencies", title, detail, fix, p["source"]))
    stats["vulnerable"] = len(findings)
    return findings, stats
