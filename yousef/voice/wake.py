"""Understanding short spoken phrases from a transcript.

- Wake word: "يوسف" / "يا يوسف" / "Yousef" near the start of the sentence.
  The rest of the sentence is the command ("يوسف، اكتب منشورًا…").
- Replies to an approval question: "وافق / نعم / نفّذ" or "ارفض / لا / ألغِ".
- Noise: phrases Whisper is known to invent from silence or music.
Whisper itself does the listening, so no separate wake-word model is needed.
"""

from __future__ import annotations

import re

from ..memory import normalize

DEFAULT_WAKE_WORDS = ["يوسف", "يا يوسف", "Yousef", "Youssef", "Yusuf"]

_SPLIT = re.compile(r"[\s،,.!؟?؛;:\-—…\"'«»()\[\]]+")
_LEADING_FILLERS = {"يا", "هاي", "هلا", "اوكي", "اوك", "hey", "hi", "ok", "okay", "ya", "oh"}

_APPROVE = {"وافق", "موافق", "نعم", "ايوه", "ايوا", "اي", "نفذ", "نفذها", "نفذه", "تمام", "اوكي", "اكيد", "اعتمد",
            "yes", "yeah", "yep", "approve", "approved", "ok", "okay", "sure", "confirm", "go"}
_REJECT = {"ارفض", "رفض", "لا", "لاء", "الغ", "الغي", "الغيها", "توقف", "وقف", "بلاش", "لا تنفذ",
           "no", "nope", "reject", "cancel", "stop", "dont"}

_NOISE = [
    "ترجمه نانسي قنقر", "نانسي قنقر", "اشتركوا في القناه", "اشترك في القناه", "شكرا للمشاهده",
    "شكرا على المشاهده", "شكرا لكم على المشاهده", "موسيقى", "thanks for watching", "thank you for watching",
    "subtitles by", "please subscribe", "[music]", "(music)",
]


def _tokens_with_original(text: str) -> list[tuple[str, str]]:
    """(original, normalized) pairs for each word."""
    out = []
    for word in text.split():
        norm = _SPLIT.sub("", normalize(word)).replace("'", "")
        if norm:
            out.append((word, norm))
    return out


def _close(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 1:
        return False
    # Levenshtein distance <= 1
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long_)):
        if long_[:i] + long_[i + 1:] == short:
            return True
    return False


def _command(tokens: list[tuple[str, str]]) -> str:
    # drop punctuation left between the wake word and the command, keep the rest as spoken
    return " ".join(t[0] for t in tokens).strip().lstrip("،,.!؟?؛;:-—… ").strip()


def split_wake(text: str, wake_words: list[str]) -> tuple[bool, str]:
    """Return (woke, command). The wake phrase must be among the first words."""
    tokens = _tokens_with_original(text or "")
    phrases = []
    for phrase in wake_words:
        parts = [p for p in (_SPLIT.sub(" ", normalize(phrase)).split()) if p]
        if parts:
            phrases.append(parts)
    phrases.sort(key=len, reverse=True)  # prefer "يا يوسف" over "يوسف"
    for start in range(min(3, len(tokens))):
        # only filler words may come before the wake phrase
        if any(t[1] not in _LEADING_FILLERS for t in tokens[:start]):
            break
        for parts in phrases:
            window = tokens[start:start + len(parts)]
            if len(window) == len(parts) and all(_close(w[1], p) for w, p in zip(window, parts)):
                return True, _command(tokens[start + len(parts):])
            # merged "يايوسف"
            if len(parts) == 1 and tokens[start][1].startswith("يا") and _close(tokens[start][1][2:], parts[0]):
                return True, _command(tokens[start + 1:])
    return False, (text or "").strip()


def classify_reply(text: str) -> str | None:
    """'approve', 'reject' or None for a short spoken answer."""
    tokens = [t[1] for t in _tokens_with_original(text or "")]
    if not tokens or len(tokens) > 6:
        return None
    for tok in tokens:
        if tok in _REJECT:
            return "reject"
        if tok in _APPROVE:
            return "approve"
    return None


def is_noise(text: str) -> bool:
    norm = " ".join(_SPLIT.sub(" ", normalize(text or "")).split())
    if not norm:
        return True
    return any(n in norm for n in _NOISE) and len(norm.split()) <= 8
