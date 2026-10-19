"""Which steps must wait for the user's approval.

The planner flags risky steps itself; this check is a second safety net so a
step whose instruction is an outward action (publish, send, pay, buy, delete,
trade, deploy) never runs unapproved just because a small model forgot to flag
it. It looks for the action verb as a command (at the start, or after "then" /
"and"), so "write a post to publish later" stays a writing step, while
"publish the post" waits for approval.
"""

from __future__ import annotations

import re

_EN_VERBS = r"(publish|post|send|e-?mail|pay|purchase|buy|delete|remove|trade|sell|transfer|deploy|order)"
_EN = re.compile(rf"(^|\b(then|and|now)\s+){_EN_VERBS}\b", re.IGNORECASE)

_AR_VERBS = r"(انشر|أرسل|ارسل|ادفع|اشتر|احذف|امسح|بِع|بع|حوّل|حول\s+مبلغ|نفّذ\s+صفقة|نفذ\s+صفقة|اطلب\s+شراء)"
# optional attached object pronoun: انشره، أرسلها، احذفهم
_AR = re.compile(rf"(^|(ثم|بعدها|بعد\s+ذلك)\s+|\sو){_AR_VERBS}(ه|ها|هم|هن|وه|وها)?(\s|$|[.،,؛!])")


def requires_approval(instruction: str, planner_flag: bool) -> bool:
    if planner_flag:
        return True
    text = (instruction or "").strip()
    return bool(_EN.search(text) or _AR.search(text))
