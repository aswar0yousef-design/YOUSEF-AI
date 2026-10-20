"""Technical indicators in plain Python, on candles oldest first.

A candle is {"t": epoch seconds, "o", "h", "l", "c", "v"}. Every series
function returns a list as long as its input, with None where there is not
enough history yet, so values line up with the candles.
"""

from __future__ import annotations

import math
from statistics import pstdev


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def ema(values: list[float | None], n: int) -> list[float | None]:
    """Seeded with the simple average of the first n values (None values at the start are skipped)."""
    out: list[float | None] = [None] * len(values)
    start = next((i for i, v in enumerate(values) if v is not None), len(values))
    if len(values) - start < n:
        return out
    k = 2 / (n + 1)
    prev = sum(values[start:start + n]) / n
    out[start + n - 1] = prev
    for i in range(start + n, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI, as charting sites show it."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_gain, avg_loss = gains / n, losses / n

    def value() -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        return 100 - 100 / (1 + avg_gain / avg_loss)

    out[n] = value()
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(d, 0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-d, 0)) / n
        out[i] = value()
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """-> (macd line, signal line, histogram)."""
    f, s = ema(closes, fast), ema(closes, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    sig = ema(line, signal)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def bollinger(closes: list[float], n: int = 20, k: float = 2.0):
    """-> (upper, middle, lower)."""
    mid = sma(closes, n)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        sd = pstdev(closes[i - n + 1:i + 1])
        upper[i], lower[i] = mid[i] + k * sd, mid[i] - k * sd
    return upper, mid, lower


def atr(candles: list[dict], n: int = 14) -> list[float | None]:
    """Wilder's average true range."""
    out: list[float | None] = [None] * len(candles)
    if len(candles) <= n:
        return out
    trs = [candles[0]["h"] - candles[0]["l"]]
    for prev, c in zip(candles, candles[1:]):
        trs.append(max(c["h"] - c["l"], abs(c["h"] - prev["c"]), abs(c["l"] - prev["c"])))
    value = sum(trs[1:n + 1]) / n
    out[n] = value
    for i in range(n + 1, len(candles)):
        value = (value * (n - 1) + trs[i]) / n
        out[i] = value
    return out


def swing_levels(candles: list[dict], window: int = 3, lookback: int = 120) -> tuple[list[float], list[float]]:
    """Recent swing highs and lows (a bar higher/lower than `window` bars on each side)."""
    recent = candles[-lookback:]
    highs, lows = [], []
    for i in range(window, len(recent) - window):
        around = recent[i - window:i + window + 1]
        if recent[i]["h"] == max(c["h"] for c in around):
            highs.append(recent[i]["h"])
        if recent[i]["l"] == min(c["l"] for c in around):
            lows.append(recent[i]["l"])
    return highs, lows


def _cluster(levels: list[float], tolerance: float) -> list[float]:
    """Merge levels closer than `tolerance` (a fraction of price) into their average."""
    out: list[list[float]] = []
    for level in sorted(levels):
        if out and abs(level - out[-1][-1]) <= tolerance * level:
            out[-1].append(level)
        else:
            out.append([level])
    return [sum(g) / len(g) for g in out]


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return (a / b - 1) * 100


def _last(series: list) -> float | None:
    return series[-1] if series else None


def _crossed(a: list, b: list, bars: int) -> str | None:
    """'up' if a crossed above b within the last `bars` bars, 'down' if below, else None."""
    for i in range(len(a) - 1, max(len(a) - 1 - bars, 0), -1):
        if None in (a[i], b[i], a[i - 1], b[i - 1]):
            return None
        if a[i - 1] <= b[i - 1] and a[i] > b[i]:
            return "up"
        if a[i - 1] >= b[i - 1] and a[i] < b[i]:
            return "down"
    return None


def analyze(candles: list[dict]) -> dict:
    """Everything the analyst reads about a chart, as numbers (None where history is too short)."""
    if len(candles) < 2:
        raise ValueError("not enough candles to analyse")
    closes = [c["c"] for c in candles]
    last = closes[-1]
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    e20 = ema(closes, 20)
    r = rsi(closes)
    m_line, m_sig, m_hist = macd(closes)
    bb_up, bb_mid, bb_low = bollinger(closes)
    a = atr(candles)
    vols = [c.get("v") or 0 for c in candles]
    vol_avg = sma(vols, 20)

    def back(series, bars):
        return series[-1 - bars] if len(series) > bars else None

    trend = "sideways"
    if s50[-1] is not None:
        slope = _pct(s50[-1], back(s50, 10))
        if last > s50[-1] and (slope or 0) > 0.5:
            trend = "up"
        elif last < s50[-1] and (slope or 0) < -0.5:
            trend = "down"
    elif s20[-1] is not None:
        trend = "up" if last > s20[-1] * 1.01 else "down" if last < s20[-1] * 0.99 else "sideways"

    highs, lows = swing_levels(candles)
    support = sorted((x for x in _cluster(lows, 0.01) if x < last), reverse=True)[:3]
    resistance = sorted(x for x in _cluster(highs, 0.01) if x > last)[:3]
    window = candles[-min(len(candles), 365):]
    bb = None
    if bb_up[-1] is not None:
        width = bb_up[-1] - bb_low[-1]
        bb = {"upper": bb_up[-1], "middle": bb_mid[-1], "lower": bb_low[-1],
              "pct_b": (last - bb_low[-1]) / width if width else 0.5,
              "width_pct": width / bb_mid[-1] * 100 if bb_mid[-1] else None}
    return {
        "last": last,
        "time": candles[-1]["t"],
        "bars": len(candles),
        "change_1": _pct(last, back(closes, 1)),
        "change_7": _pct(last, back(closes, 7)),
        "change_30": _pct(last, back(closes, 30)),
        "high": max(c["h"] for c in window),
        "low": min(c["l"] for c in window),
        "sma20": s20[-1], "sma50": s50[-1], "sma200": s200[-1], "ema20": e20[-1],
        "rsi": r[-1],
        "macd": None if m_sig[-1] is None else {"line": m_line[-1], "signal": m_sig[-1], "hist": m_hist[-1],
                                               "cross": _crossed(m_line, m_sig, 3)},
        "bollinger": bb,
        "atr": a[-1],
        "atr_pct": a[-1] / last * 100 if a[-1] is not None and last else None,
        "volume_ratio": vols[-1] / vol_avg[-1] if vol_avg[-1] else None,
        "trend": trend,
        "ma_cross": _crossed(s50, s200, 20),   # golden ("up") or death ("down") cross in the last 20 bars
        "support": support,
        "resistance": resistance,
        "closes": closes[-10:],
    }


def fmt_price(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".") if ax < 10 else f"{x:,.2f}"
    if ax == 0:
        return "0"
    digits = min(10, 3 - int(math.floor(math.log10(ax))))
    return f"{x:.{digits}f}"


def fmt_pct(x: float | None) -> str:
    return "-" if x is None else f"{x:+.2f}%"
