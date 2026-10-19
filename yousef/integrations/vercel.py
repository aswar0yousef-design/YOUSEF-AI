"""Vercel REST API (read-only): recent deployments and why a build failed.

Deploying itself happens through git push: Vercel builds every pushed commit,
so YOUSEF never needs deploy rights, only a token that can read deployments.
"""

from __future__ import annotations

import time
import urllib.parse

from ..models.base import ProviderError, http_json

API = "https://api.vercel.com"
STATE_AR = {
    "READY": "جاهز", "ERROR": "فشل", "BUILDING": "يُبنى", "QUEUED": "في الانتظار", "INITIALIZING": "يبدأ",
    "CANCELED": "أُلغي",
}


def _ago(ms: int | None) -> str:
    if not ms:
        return "?"
    minutes = int((time.time() * 1000 - ms) / 60000)
    if minutes < 1:
        return "now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} h ago" if hours < 48 else f"{hours // 24} days ago"


class Vercel:
    def __init__(self, token: str, base_url: str = API, team: str | None = None):
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.team = team

    def _get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        if self.team:
            params["teamId"] = self.team
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        return http_json("GET", f"{self.base_url}{path}{query}", headers={"Authorization": f"Bearer {self.token}"},
                         timeout=20)

    def deployments(self, project: str | None = None, limit: int = 5) -> list[dict]:
        params = {"limit": str(limit)}
        if project:
            params["projectId"] = project
        data = self._get("/v6/deployments", params) or {}
        out = []
        for d in data.get("deployments", []):
            meta = d.get("meta") or {}
            out.append({
                "id": d.get("uid") or d.get("id"),
                "project": d.get("name"),
                "url": f"https://{d['url']}" if d.get("url") else "",
                "state": d.get("state") or d.get("readyState") or "?",
                "target": d.get("target") or "preview",
                "created": d.get("created") or d.get("createdAt"),
                "branch": meta.get("githubCommitRef", ""),
                "commit": (meta.get("githubCommitMessage") or "").splitlines()[0][:100] if meta.get("githubCommitMessage") else "",
            })
        return out

    def build_errors(self, deployment_id: str, limit: int = 40) -> list[str]:
        data = self._get(f"/v3/deployments/{urllib.parse.quote(deployment_id)}/events", {"limit": "200"}) or []
        lines = []
        for event in data if isinstance(data, list) else []:
            payload = event.get("payload") or {}
            text = (payload.get("text") or event.get("text") or "").rstrip()
            if text and (event.get("type") in ("stderr", "fatal") or "error" in text.lower()):
                lines.append(text[:300])
        return lines[-limit:]

    def report(self, project: str | None = None) -> str:
        try:
            deps = self.deployments(project)
        except ProviderError as e:
            raise ProviderError(f"Vercel: {e}") from None
        if not deps:
            return f"No deployments found{f' for project {project}' if project else ''}."
        lines = []
        for d in deps:
            lines.append(f"- {d['project']} [{d['target']}] {d['state']} ({STATE_AR.get(d['state'], d['state'])}), "
                         f"{_ago(d['created'])}, {d['url']}"
                         + (f", branch {d['branch']}" if d["branch"] else "")
                         + (f", commit: {d['commit']}" if d["commit"] else ""))
        failed = next((d for d in deps if d["state"] == "ERROR"), None)
        if failed:
            try:
                errors = self.build_errors(failed["id"])
            except ProviderError:
                errors = []
            if errors:
                lines.append(f"\nBuild errors of the latest failed deployment ({failed['url']}):")
                lines.extend(f"  {e}" for e in errors)
        return "\n".join(lines)
