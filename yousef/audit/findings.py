"""A finding from any audit, and the text the technical manager reads."""

from __future__ import annotations

from dataclasses import asdict, dataclass

SEVERITIES = ("critical", "high", "medium", "low", "info")
SEVERITY_AR = {"critical": "حرج", "high": "عالٍ", "medium": "متوسط", "low": "منخفض", "info": "معلومة"}


@dataclass
class Finding:
    severity: str          # one of SEVERITIES
    area: str              # security | errors | speed | seo | data | platform | code | dependencies
    title: str             # Arabic, one line
    detail: str = ""       # what was seen (never a secret's value)
    fix: str = ""          # Arabic, what to do
    where: str = ""        # URL, file:line or product ids

    def as_dict(self) -> dict:
        return asdict(self)


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: SEVERITIES.index(f.severity) if f.severity in SEVERITIES else 9)


def format_findings(findings: list[Finding], header: str, limit: int = 60) -> str:
    if not findings:
        return f"{header}: no problems found."
    findings = sort_findings(findings)
    counts = ", ".join(f"{s} {sum(1 for f in findings if f.severity == s)}" for s in SEVERITIES
                       if any(f.severity == s for f in findings))
    lines = [f"{header} ({counts}):"]
    for f in findings[:limit]:
        line = f"[{f.severity.upper()}/{f.area}] {f.title}"
        if f.where:
            line += f" — {f.where}"
        if f.detail:
            line += f"\n    seen: {f.detail}"
        if f.fix:
            line += f"\n    fix: {f.fix}"
        lines.append(line)
    if len(findings) > limit:
        lines.append(f"... {len(findings) - limit} more findings not shown")
    return "\n".join(lines)
