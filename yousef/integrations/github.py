"""GitHub REST API: just what the coder needs to open a pull request."""

from __future__ import annotations

from ..models.base import ProviderError, http_json

API = "https://api.github.com"


class GitHub:
    def __init__(self, token: str, base_url: str = API):
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "YOUSEF-AI",
        }

    def default_branch(self, owner: str, repo: str) -> str:
        data = http_json("GET", f"{self.base_url}/repos/{owner}/{repo}", headers=self._headers(), timeout=20) or {}
        return data.get("default_branch") or "main"

    def open_pull_request(self, owner: str, repo: str, head: str, title: str, body: str = "",
                          base: str | None = None) -> dict:
        base = base or self.default_branch(owner, repo)
        if head == base:
            raise ProviderError(f"الفرع الحالي {head} هو نفسه الفرع الأساسي؛ أنشئ فرعًا جديدًا للتعديلات أولًا")
        data = http_json("POST", f"{self.base_url}/repos/{owner}/{repo}/pulls", headers=self._headers(),
                         body={"title": title, "head": head, "base": base, "body": body}, timeout=30) or {}
        return {"number": data.get("number"), "url": data.get("html_url"), "base": base, "head": head}
