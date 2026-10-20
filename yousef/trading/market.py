"""Live market data for the trading analyst. Read-only: nothing here can place an order.

- Crypto: Binance's public market-data endpoint (data-api.binance.vision), no key.
- Stocks, forex and gold: Twelve Data with the user's free key (8 requests a
  minute, 800 a day), sent in the Authorization header, never in the URL.

Results are cached for a minute so the analyst, the alerts and the dashboard
do not spend the user's request allowance on the same numbers.
"""

from __future__ import annotations

import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

from ..models.base import ProviderError, http_json

BINANCE_URL = "https://data-api.binance.vision/api/v3"
TWELVE_URL = "https://api.twelvedata.com"
TWELVE_KEY = "twelvedata"
CACHE_SECONDS = 60
MAX_CANDLES = 500
INTERVALS = ("15m", "1h", "4h", "1d", "1w")
TWELVE_INTERVAL = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1day", "1w": "1week"}

CRYPTO = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "TRX", "TON", "AVAX", "DOT", "LINK", "POL", "MATIC",
          "LTC", "SHIB", "UNI", "ATOM", "XLM", "NEAR", "APT", "ARB", "OP", "SUI", "PEPE", "FET", "INJ", "FIL", "ETC",
          "BCH", "HBAR", "ICP", "RENDER", "TIA", "SEI", "WLD", "AAVE", "ALGO", "VET", "SAND", "MANA", "CRV", "MKR",
          "BONK", "WIF", "JUP", "ENA", "TAO", "ONDO", "KAS", "XMR", "ZEC", "TRUMP"}
CRYPTO_QUOTES = ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "EUR", "TRY")
FIAT = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "OMR", "SAR", "AED", "KWD", "QAR", "BHD", "EGP",
        "TRY", "CNY", "INR", "XAU", "XAG"}
USD_LIKE = {"USD", "USDT", "USDC", "FDUSD"}
NAMES = {
    "بيتكوين": "BTC", "بتكوين": "BTC", "البيتكوين": "BTC", "البتكوين": "BTC", "ايثريوم": "ETH", "إيثريوم": "ETH",
    "الايثريوم": "ETH", "الإيثريوم": "ETH", "ايثيريوم": "ETH", "إيثيريوم": "ETH", "سولانا": "SOL", "ريبل": "XRP",
    "دوج": "DOGE", "دوجكوين": "DOGE", "دوج كوين": "DOGE", "بينانس": "BNB", "كاردانو": "ADA", "تون": "TON",
    "الذهب": "XAU/USD", "ذهب": "XAU/USD", "الفضة": "XAG/USD", "فضة": "XAG/USD",
    "أبل": "AAPL", "ابل": "AAPL", "آبل": "AAPL", "تسلا": "TSLA", "انفيديا": "NVDA", "إنفيديا": "NVDA",
    "مايكروسوفت": "MSFT", "أمازون": "AMZN", "امازون": "AMZN", "جوجل": "GOOGL", "قوقل": "GOOGL", "ميتا": "META",
    "bitcoin": "BTC", "ethereum": "ETH", "gold": "XAU/USD", "silver": "XAG/USD",
}
SYMBOL = re.compile(r"[A-Z0-9.:/\-]{1,24}")


class MarketError(ProviderError):
    pass


@dataclass(frozen=True)
class Instrument:
    ref: str      # what to store and resolve again: BTCUSDT, AAPL, EUR/USD
    kind: str     # crypto | stock | forex
    source: str   # binance | twelve
    display: str  # BTC/USDT, AAPL, EUR/USD
    quote: str    # the currency prices are in (USDT, USD...)

    @property
    def usd(self) -> bool:
        return self.quote in USD_LIKE

    def public(self) -> dict:
        return {"ref": self.ref, "kind": self.kind, "display": self.display, "quote": self.quote}


def resolve(symbol: str, market: str | None = None) -> Instrument:
    """A symbol as people write it ("btc", "بيتكوين", "BTC/USD", "aapl", "EURUSD", "الذهب") -> Instrument."""
    raw = str(symbol or "").strip()
    raw = NAMES.get(raw, NAMES.get(raw.lower(), raw))
    s = raw.upper().replace(" ", "")
    if not s or not SYMBOL.fullmatch(s):
        raise MarketError(f'invalid symbol "{symbol}"')
    market = (market or "").lower() or None
    if market not in (None, "crypto", "stock", "forex"):
        raise MarketError("market must be crypto, stock or forex")
    pair = re.split(r"[/\-]", s) if ("/" in s or "-" in s) else None
    if pair and len(pair) == 2 and all(pair):
        base, quote = pair
        if market == "crypto" or (market is None and (base in CRYPTO or quote in ("USDT", "USDC", "FDUSD"))):
            quote = "USDT" if quote == "USD" else quote
            return Instrument(base + quote, "crypto", "binance", f"{base}/{quote}", quote)
        return Instrument(f"{base}/{quote}", "forex", "twelve", f"{base}/{quote}", quote)
    if market in (None, "crypto"):
        for q in CRYPTO_QUOTES:
            if s.endswith(q) and len(s) > len(q) and (s[:-len(q)] in CRYPTO or market == "crypto"):
                return Instrument(s, "crypto", "binance", f"{s[:-len(q)]}/{q}", q)
        if s in CRYPTO or market == "crypto":
            return Instrument(s + "USDT", "crypto", "binance", f"{s}/USDT", "USDT")
    if market in (None, "forex") and len(s) == 6 and s[:3] in FIAT and s[3:] in FIAT:
        return Instrument(f"{s[:3]}/{s[3:]}", "forex", "twelve", f"{s[:3]}/{s[3:]}", s[3:])
    if market == "forex":
        raise MarketError(f'"{symbol}" is not a currency pair like EUR/USD')
    return Instrument(s, "stock", "twelve", s, "USD")


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Market:
    def __init__(self, keystore, binance_url: str = BINANCE_URL, twelve_url: str = TWELVE_URL, clock=time.time):
        self.keystore = keystore
        self.binance_url = binance_url.rstrip("/")
        self.twelve_url = twelve_url.rstrip("/")
        self.clock = clock
        self._cache: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    resolve = staticmethod(resolve)

    @property
    def stocks_ready(self) -> bool:
        return bool(self.keystore.get(TWELVE_KEY))

    # ---- public ---------------------------------------------------------------------
    def candles(self, symbol: str, interval: str = "1d", limit: int = 200, market: str | None = None,
                fresh: bool = False) -> tuple[Instrument, list[dict]]:
        inst = resolve(symbol, market)
        if interval not in INTERVALS:
            raise MarketError(f"interval must be one of {', '.join(INTERVALS)}")
        limit = max(10, min(int(limit or 200), MAX_CANDLES))
        key = ("candles", inst.ref, interval, limit)
        cached = None if fresh else self._cached(key)
        if cached is not None:
            return cached
        if inst.source == "binance":
            rows = self._binance("/klines", {"symbol": inst.ref, "interval": interval, "limit": limit})
            candles = [{"t": int(r[0]) // 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                        "c": float(r[4]), "v": float(r[5])} for r in rows or []]
        else:
            data = self._twelve("/time_series", {"symbol": inst.ref, "interval": TWELVE_INTERVAL[interval],
                                                 "outputsize": limit})
            meta = data.get("meta") or {}
            if meta.get("currency") and inst.kind == "stock":
                inst = Instrument(inst.ref, inst.kind, inst.source, inst.display, str(meta["currency"]).upper())
            candles = []
            for v in reversed(data.get("values") or []):  # newest first in the response
                c = _f(v.get("close"))
                if c is None:
                    continue
                candles.append({"t": _epoch(v.get("datetime")), "o": _f(v.get("open")) or c, "h": _f(v.get("high")) or c,
                                "l": _f(v.get("low")) or c, "c": c, "v": _f(v.get("volume")) or 0.0})
        if not candles:
            raise MarketError(f"no price data for {inst.display}")
        self._store(key, (inst, candles))
        return inst, candles

    def quote(self, symbol: str, market: str | None = None, fresh: bool = False) -> dict:
        """-> {"instrument", "price", "change_pct", "high", "low", "volume", "time"} (24h for crypto, the day for stocks)."""
        inst = resolve(symbol, market)
        key = ("quote", inst.ref)
        cached = None if fresh else self._cached(key)
        if cached is not None:
            return cached
        if inst.source == "binance":
            d = self._binance("/ticker/24hr", {"symbol": inst.ref}) or {}
            out = {"instrument": inst, "price": _f(d.get("lastPrice")), "change_pct": _f(d.get("priceChangePercent")),
                   "high": _f(d.get("highPrice")), "low": _f(d.get("lowPrice")), "volume": _f(d.get("quoteVolume")),
                   "time": int(d.get("closeTime") or 0) // 1000 or int(self.clock())}
        else:
            d = self._twelve("/quote", {"symbol": inst.ref})
            if d.get("currency") and inst.kind == "stock":
                inst = Instrument(inst.ref, inst.kind, inst.source, inst.display, str(d["currency"]).upper())
            out = {"instrument": inst, "price": _f(d.get("close")), "change_pct": _f(d.get("percent_change")),
                   "high": _f(d.get("high")), "low": _f(d.get("low")), "volume": _f(d.get("volume")),
                   "time": int(d.get("timestamp") or 0) or int(self.clock()),
                   "name": d.get("name") or "", "exchange": d.get("exchange") or "",
                   "open": bool(d.get("is_market_open"))}
        if out["price"] is None:
            raise MarketError(f"no price for {inst.display}")
        self._store(key, out)
        return out

    # ---- sources --------------------------------------------------------------------
    def _binance(self, path: str, params: dict):
        url = f"{self.binance_url}{path}?{urllib.parse.urlencode(params)}"
        try:
            return http_json("GET", url, timeout=20)
        except ProviderError as e:
            text = str(e)
            if "-1121" in text or "Invalid symbol" in text:
                raise MarketError(f"{params.get('symbol')} غير موجود في Binance (جرّب زوجًا مثل BTCUSDT)") from None
            if "HTTP 451" in text or "HTTP 403" in text:
                raise MarketError("Binance رفض الطلب من هذه الشبكة") from None
            raise MarketError(f"Binance: {text}") from None

    def _twelve(self, path: str, params: dict) -> dict:
        key = self.keystore.get(TWELVE_KEY)
        if not key:
            raise MarketError("لأسعار الأسهم والفوركس والذهب أضف مفتاح Twelve Data المجاني من الإعدادات ← الخدمات "
                              "(twelvedata.com). أسعار العملات الرقمية تعمل بدون مفتاح.")
        url = f"{self.twelve_url}{path}?{urllib.parse.urlencode(params)}"
        try:
            data = http_json("GET", url, headers={"Authorization": f"apikey {key}"}, timeout=20) or {}
        except ProviderError as e:
            raise MarketError(_twelve_problem(str(e))) from None
        if isinstance(data, dict) and data.get("status") == "error":
            raise MarketError(_twelve_problem(f"{data.get('code')}: {data.get('message')}"))
        return data if isinstance(data, dict) else {}

    # ---- cache ----------------------------------------------------------------------
    def _cached(self, key):
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > self.clock():
                return hit[1]
        return None

    def _store(self, key, value) -> None:
        with self._lock:
            if len(self._cache) > 300:
                now = self.clock()
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            self._cache[key] = (self.clock() + CACHE_SECONDS, value)


def _twelve_problem(text: str) -> str:
    low = text.lower()
    if "401" in low or "apikey" in low and "invalid" in low:
        return "مفتاح Twelve Data غير صحيح؛ تحقق منه في الإعدادات ← الخدمات"
    if "429" in low or "run out of api credits" in low:
        return "انتهى رصيد طلبات Twelve Data المجاني لهذه الدقيقة أو لهذا اليوم (8 في الدقيقة، 800 في اليوم)؛ حاول بعد قليل"
    if "404" in low or "not found" in low or "symbol" in low and ("invalid" in low or "missing" in low):
        return "Twelve Data لا يعرف هذا الرمز (الخطة المجانية تغطي الأسهم الأمريكية والفوركس)"
    if "plan" in low or "grow" in low or "pro" in low and "available" in low:
        return "هذا الرمز غير متاح في خطة Twelve Data المجانية"
    return f"Twelve Data: {text[:200]}"


def _epoch(text) -> int:
    try:
        dt = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())
