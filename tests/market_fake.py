"""A fake Binance market-data endpoint and Twelve Data API for the trading tests."""

from __future__ import annotations

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TWELVE_KEY = "td-test-key"
DAY = 86400
START = 1_760_000_000 - 1_760_000_000 % DAY


def series(n: int, start: float, step: float, wave: float = 0.0) -> list[float]:
    """A trending series with a gentle wave, so indicators have something to say."""
    return [round(start + step * i + wave * math.sin(i / 3), 6) for i in range(n)]


class FakeMarket:
    def __init__(self):
        self.requests: list[tuple[str, dict, dict]] = []
        # closes per Binance symbol / Twelve symbol, oldest first
        self.closes = {
            "BTCUSDT": series(300, 40000, 100, 600),      # up-trend, last ~69,900
            "ETHUSDT": series(300, 4000, -5, 60),          # down-trend
            "SOLUSDT": [150.0] * 300,
            "AAPL": series(300, 180, 0.2, 2),
        }
        self.ticker_override: dict[str, float] = {}
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                outer.requests.append((url.path, q, dict(self.headers)))
                if url.path.startswith("/binance/"):
                    return self._binance(url.path[len("/binance"):], q)
                if url.path.startswith("/twelve/"):
                    return self._twelve(url.path[len("/twelve"):], q)
                self._send(404, {"error": "unknown"})

            def _binance(self, path, q):
                closes = outer.closes.get(q.get("symbol", ""))
                if closes is None:
                    return self._send(400, {"code": -1121, "msg": "Invalid symbol."})
                if path == "/api/v3/klines":
                    limit = int(q.get("limit", 500))
                    rows = []
                    for i, c in enumerate(closes[-limit:]):
                        t = (START + i * DAY) * 1000
                        o = closes[max(len(closes) - limit + i - 1, 0)]
                        rows.append([t, str(o), str(max(o, c) * 1.01), str(min(o, c) * 0.99), str(c), "1000",
                                     t + DAY * 1000 - 1, "0", 10, "0", "0", "0"])
                    return self._send(200, rows)
                if path == "/api/v3/ticker/24hr":
                    last = outer.ticker_override.get(q["symbol"], closes[-1])
                    return self._send(200, {"symbol": q["symbol"], "lastPrice": str(last),
                                            "priceChangePercent": f"{(last / closes[-2] - 1) * 100:.3f}",
                                            "highPrice": str(last * 1.02), "lowPrice": str(last * 0.97),
                                            "quoteVolume": "123456789", "closeTime": (START + 299 * DAY) * 1000})
                return self._send(404, {"code": -1, "msg": "unknown path"})

            def _twelve(self, path, q):
                if self.headers.get("Authorization") != f"apikey {TWELVE_KEY}":
                    return self._send(200, {"code": 401, "message": "**apikey** parameter is incorrect", "status": "error"})
                closes = outer.closes.get(q.get("symbol", ""))
                if closes is None:
                    return self._send(200, {"code": 404, "message": "**symbol** not found", "status": "error"})
                if path == "/time_series":
                    n = int(q.get("outputsize", 30))
                    values = [{"datetime": f"2026-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}", "open": str(c), "high": str(c * 1.01),
                               "low": str(c * 0.99), "close": str(c), "volume": "5000"}
                              for i, c in enumerate(closes[-n:])]
                    return self._send(200, {"meta": {"symbol": q["symbol"], "currency": "USD", "exchange": "NASDAQ"},
                                            "values": list(reversed(values)), "status": "ok"})
                if path == "/quote":
                    last = outer.ticker_override.get(q["symbol"], closes[-1])
                    return self._send(200, {"symbol": q["symbol"], "name": "Apple Inc", "exchange": "NASDAQ",
                                            "currency": "USD", "close": str(last), "high": str(last), "low": str(last),
                                            "percent_change": "0.5", "is_market_open": False, "timestamp": START})
                return self._send(200, {"code": 404, "message": "unknown", "status": "error"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.urls = {"binance_url": base + "/binance/api/v3", "twelve_url": base + "/twelve"}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def last(self, symbol: str) -> float:
        return self.closes[symbol][-1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
