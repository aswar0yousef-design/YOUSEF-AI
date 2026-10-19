"""Secret values inside ordinary files, hidden from the model.

Keys and tokens written into code (a Shopify token in a PHP file, an API key in
config.js) would otherwise reach the model whenever it reads, searches or diffs
that file. The agents see a marker such as [[SECRET_1]] instead. Markers in an
agent's edits are put back to the real value before anything is proposed, so
an edit that keeps the line keeps the key, and an edit that replaces the key
with an environment variable removes it.
"""

from __future__ import annotations

import re

from .files import WorkspaceError

# values that are secrets whatever variable holds them
PATTERNS = [
    re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z]+ )?PRIVATE KEY-----|\Z)"),
    re.compile(r"\bshp(?:at|ss|ca|pa)_[0-9a-fA-F]{32}\b"),                    # Shopify
    re.compile(r"\b(?:ck|cs)_[0-9a-f]{40}\b"),                               # WooCommerce
    re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}"),                        # Stripe
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{24,}"),                  # OpenAI / Anthropic
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                     # AWS
    re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}"),  # GitHub
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),                               # Google
    re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}"),                          # Slack
    re.compile(r"\bEAA[A-Za-z0-9]{60,}"),                                    # Meta (Facebook / Instagram) tokens
]
# quoted values given to a variable whose name says it is secret
ASSIGNED = re.compile(r"(?i)\b((?:db_|wp_|smtp_|mail_|client_)?(?:password|passwd|pwd|secret|api_?key|access_?token|"
                      r"auth_?token|secret_?key|private_?key))\b(['\"]?\s*(?:=>|:|=|,)\s*)(['\"])([^'\"\s]{8,})(\3)")
PLACEHOLDER = re.compile(r"your|example|xxxx|changeme|placeholder|dummy|<|\$\{|\{\{|process\.env|getenv|os\.environ|"
                         r"\[\[SECRET_", re.I)
MARKER = re.compile(r"\[\[SECRET_\d+\]\]")


class SecretMasker:
    def __init__(self):
        self.values: dict[str, str] = {}   # marker -> real value
        self._markers: dict[str, str] = {}  # real value -> marker

    def _marker(self, value: str) -> str:
        if value not in self._markers:
            marker = f"[[SECRET_{len(self._markers) + 1}]]"
            self._markers[value] = marker
            self.values[marker] = value
        return self._markers[value]

    def mask(self, text: str) -> str:
        for pattern in PATTERNS:
            text = pattern.sub(lambda m: self._marker(m.group(0)), text)

        def assigned(m: re.Match) -> str:
            if PLACEHOLDER.search(m.group(4)):
                return m.group(0)
            return m.group(1) + m.group(2) + m.group(3) + self._marker(m.group(4)) + m.group(5)

        return ASSIGNED.sub(assigned, text)

    def unmask(self, text: str) -> str:
        def real(m: re.Match) -> str:
            if m.group(0) not in self.values:
                raise WorkspaceError(f"{m.group(0)} is not a marker from this project; copy markers exactly as shown")
            return self.values[m.group(0)]

        return MARKER.sub(real, text)
