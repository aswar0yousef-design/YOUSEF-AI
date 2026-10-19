"""Pull a JSON value out of a model reply (tolerates code fences and extra text)."""

from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_decoder = json.JSONDecoder()


def extract_json(text: str):
    raw = (text or "").strip()
    # 1. the whole reply is JSON (also protects file contents that contain ``` fences)
    try:
        return json.loads(raw)
    except ValueError:
        pass
    # 2. a ```json fenced block
    for match in _FENCE.finditer(raw):
        try:
            return json.loads(match.group(1))
        except ValueError:
            continue
    # 3. the first complete {...} object, ignoring text around it
    start = raw.find("{")
    while start != -1:
        try:
            value, _ = _decoder.raw_decode(raw, start)
            return value
        except ValueError:
            start = raw.find("{", start + 1)
    raise ValueError("no JSON object found")
