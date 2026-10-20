import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from yousef.app import build_app
from yousef.integrations.trade_tools import TradeToolbox
from yousef.server import make_server
from yousef.trading import indicators as ind
from yousef.trading.market import MarketError, resolve
from yousef.trading.paper import PaperError

from helpers import FakeProvider, XorCipher, factory_from, wait_for
from market_fake import TWELVE_KEY, FakeMarket


class IndicatorTest(unittest.TestCase):
    def test_series(self):
        self.assertEqual(ind.sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])
        self.assertEqual(ind.ema([2, 4, 6, 8], 3), [None, None, 4.0, 6.0])  # seed 4, then 8*0.5 + 4*0.5
        r = ind.rsi([1, 2, 1, 2, 3], 2)
        self.assertEqual(r[:2], [None, None])
        self.assertAlmostEqual(r[2], 50.0)
        self.assertAlmostEqual(r[3], 75.0)
        self.assertAlmostEqual(r[4], 87.5)
        self.assertEqual(ind.rsi(list(range(1, 30)))[-1], 100.0)
        self.assertEqual(ind.rsi(list(range(30, 1, -1)))[-1], 0.0)
        closes = [float(x % 7 + x / 10) for x in range(60)]
        line, sig, hist = ind.macd(closes)
        e12, e26 = ind.ema(closes, 12), ind.ema(closes, 26)
        self.assertAlmostEqual(line[-1], e12[-1] - e26[-1])
        self.assertAlmostEqual(hist[-1], line[-1] - sig[-1])
        self.assertIsNone(sig[30])     # 26 + 9 - 2 bars before the signal starts
        self.assertIsNotNone(sig[33])
        up, mid, low = ind.bollinger([5.0] * 25)
        self.assertEqual((up[-1], mid[-1], low[-1]), (5.0, 5.0, 5.0))
        candles = [{"t": i, "o": 10, "h": 11, "l": 9, "c": 10, "v": 1} for i in range(20)]
        self.assertAlmostEqual(ind.atr(candles)[-1], 2.0)

    def test_analyze_and_format(self):
        closes = [100 + i for i in range(220)]
        candles = [{"t": i, "o": c - 0.5, "h": c + 1, "l": c - 1, "c": c, "v": 10 if i < 219 else 30}
                   for i, c in enumerate(closes)]
        a = ind.analyze(candles)
        self.assertEqual((a["trend"], a["last"], a["rsi"]), ("up", 319, 100.0))
        self.assertAlmostEqual(a["sma20"], sum(closes[-20:]) / 20)
        self.assertAlmostEqual(a["volume_ratio"], 30 / 11)  # 19 tens and one 30 over 20 bars
        self.assertEqual(ind.fmt_price(69123.456), "69,123.46")
        self.assertEqual(ind.fmt_price(0.000012345), "0.00001234")
        self.assertEqual(ind.fmt_price(1.2345), "1.2345")
        self.assertEqual(ind.fmt_pct(-1.234), "-1.23%")


class ResolveTest(unittest.TestCase):
    def test_symbols(self):
        cases = {"btc": ("BTCUSDT", "crypto", "BTC/USDT"), "بيتكوين": ("BTCUSDT", "crypto", "BTC/USDT"),
                 "BTC/USD": ("BTCUSDT", "crypto", "BTC/USDT"), "ethbtc": ("ETHBTC", "crypto", "ETH/BTC"),
                 "aapl": ("AAPL", "stock", "AAPL"), "EURUSD": ("EUR/USD", "forex", "EUR/USD"),
                 "الذهب": ("XAU/USD", "forex", "XAU/USD"), "sol-usdt": ("SOLUSDT", "crypto", "SOL/USDT")}
        for raw, (ref, kind, display) in cases.items():
            inst = resolve(raw)
            self.assertEqual((inst.ref, inst.kind, inst.display), (ref, kind, display), raw)
        self.assertFalse(resolve("ethbtc").usd)
        self.assertEqual(resolve("OP", "stock").kind, "stock")
        for bad in ("", "BTC;rm", "a" * 30):
            with self.assertRaises(MarketError):
                resolve(bad)


class TradingBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.fake = FakeMarket()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.close()
        self.fake.close()
        self.dir.cleanup()

    def make_app(self, script=None, twelve=False):
        provider = FakeProvider("ollama", responder=script or Script([]), models=["qwen2.5:7b"], is_local=True)
        self.app = build_app(Path(self.dir.name) / "data", factory=factory_from({"ollama": provider}),
                             cipher=XorCipher(), market_urls=self.fake.urls)
        if twelve:
            self.app.keystore.set("twelvedata", TWELVE_KEY)
        return self.app


class MarketTest(TradingBase):
    def test_crypto_and_cache(self):
        self.make_app()
        inst, candles = self.app.market.candles("btc", "1d", 120)
        self.assertEqual((inst.display, len(candles), candles[-1]["c"]), ("BTC/USDT", 120, self.fake.last("BTCUSDT")))
        self.assertLess(candles[0]["t"], candles[-1]["t"])
        self.app.market.candles("BTC", "1d", 120)
        self.assertEqual(len(self.fake.requests), 1)  # cached for a minute
        q = self.app.market.quote("BTC")
        self.assertEqual(q["price"], self.fake.last("BTCUSDT"))
        with self.assertRaisesRegex(MarketError, "غير موجود في Binance"):
            self.app.market.quote("XYZUSDT", "crypto")

    def test_stocks_need_key_sent_in_header(self):
        self.make_app()
        with self.assertRaisesRegex(MarketError, "Twelve Data"):
            self.app.market.candles("AAPL")
        self.app.keystore.set("twelvedata", TWELVE_KEY)
        inst, candles = self.app.market.candles("AAPL", "1d", 50)
        self.assertEqual((inst.kind, inst.quote, len(candles), candles[-1]["c"]), ("stock", "USD", 50, self.fake.last("AAPL")))
        path, query, headers = self.fake.requests[-1]
        self.assertEqual((path, query["interval"]), ("/twelve/time_series", "1day"))
        self.assertNotIn("apikey", query)  # never in the URL, where logs could keep it
        self.assertEqual(headers["Authorization"], f"apikey {TWELVE_KEY}")
        with self.assertRaisesRegex(MarketError, "لا يعرف هذا الرمز"):
            self.app.market.quote("ZZZZ", "stock")
        self.app.keystore.set("twelvedata", "wrong-key-0123456789")
        with self.assertRaisesRegex(MarketError, "غير صحيح"):
            self.app.market.quote("MSFT", "stock")


class PaperTest(TradingBase):
    def test_book(self):
        self.make_app()
        book, btc, eth = self.app.paper, resolve("BTC"), resolve("ethbtc")
        self.assertEqual(book.account()["cash"], 10000)
        t = book.buy(btc, 1000, 50000, 0.001)
        self.assertAlmostEqual(t["qty"], 999 / 50000)
        self.assertAlmostEqual(t["cash_after"], 9000)
        book.buy(btc, 1000, 40000, 0.001)
        pos = book.position("BTCUSDT")
        self.assertAlmostEqual(pos["cost"], 2000)
        v = book.valuation({"BTCUSDT": 45000})
        self.assertAlmostEqual(v["positions"][0]["value"], pos["qty"] * 45000)
        self.assertAlmostEqual(v["value"], 8000 + pos["qty"] * 45000)
        s = book.sell(btc, pos["qty"] / 2, 60000, 0.001)
        self.assertAlmostEqual(s["pnl"], pos["qty"] / 2 * 60000 * 0.999 - 1000)
        book.sell(btc, None, 60000, 0.001)
        self.assertIsNone(book.position("BTCUSDT"))
        with self.assertRaisesRegex(PaperError, "لا يكفي"):
            book.buy(btc, 1e9, 1, 0)
        with self.assertRaisesRegex(PaperError, "بالدولار"):
            book.buy(eth, 100, 0.05, 0)
        self.assertEqual(len(book.trades()), 4)
        book.reset(5000)
        self.assertEqual((book.account()["cash"], book.trades()), (5000, []))

    def test_alerts_fire_once(self):
        self.make_app()
        last = self.fake.last("BTCUSDT")
        book = self.app.paper
        a1 = book.add_alert(resolve("BTC"), "price_above", last - 10)
        book.add_alert(resolve("BTC"), "price_below", last - 1000)
        book.add_alert(resolve("BTC"), "rsi_above", 1, "1d")
        fired = self.app.alerts.check()
        self.assertEqual(sorted(a["condition"] for a in fired), ["price_above", "rsi_above"])
        self.assertEqual(self.app.alerts.check(), [])
        self.assertEqual(book.alert(a1["id"])["triggered_value"], last)
        self.assertEqual(len(book.unseen()), 2)
        book.mark_seen()
        self.assertEqual(book.unseen(), [])


class ToolboxTest(TradingBase):
    def setUp(self):
        super().setUp()
        self.make_app()
        self.tb = TradeToolbox(self.app.market, self.app.paper, self.app.settings)

    def test_reading(self):
        out = self.tb.run({"tool": "analyze", "symbol": "BTC", "interval": "1d"})
        self.assertIn("BTC/USDT 1d chart, 300 candles", out)
        self.assertIn("Trend: up", out)
        for part in ("RSI14", "MACD", "Bollinger", "ATR14", "Support", "Resistance"):
            self.assertIn(part, out)
        self.assertIn("ETH/USDT | ", self.tb.run({"tool": "compare", "symbols": ["BTC", "ETH", "SOL"]}))
        self.assertIn("No open positions", self.tb.run({"tool": "portfolio"}))
        self.assertIn("Error: interval", self.tb.run({"tool": "analyze", "symbol": "BTC", "interval": "2d"}))
        self.assertIn("Twelve Data", self.tb.run({"tool": "price", "symbol": "AAPL"}))

    def test_proposals(self):
        last = self.fake.last("BTCUSDT")
        self.assertIn("stop_loss must be below", self.tb.run(
            {"tool": "propose_trade", "symbol": "BTC", "side": "buy", "amount": 500, "stop_loss": last * 2}))
        self.assertIn("US dollars", self.tb.run({"tool": "propose_trade", "symbol": "ETHBTC", "side": "buy", "amount": 5}))
        self.assertIn("not enough paper cash", self.tb.run({"tool": "propose_trade", "symbol": "BTC", "side": "buy",
                                                             "amount": 20000}))
        self.tb.run({"tool": "propose_trade", "symbol": "BTC", "side": "buy", "amount": 3000, "reason": "اتجاه صاعد"})
        p = self.tb.proposals[0]["payload"]
        self.assertEqual((self.tb.proposals[0]["kind"], p["ref"], p["amount"], p["price"]), ("trade_buy", "BTCUSDT", 3000, last))
        self.assertTrue(any("وقف خسارة" in w for w in p["warnings"]))
        self.assertTrue(any("30% من المحفظة" in w for w in p["warnings"]))
        self.assertEqual(p["rows"][0]["value"], "تداول تجريبي — لا أموال حقيقية")
        self.assertIn("no paper position", self.tb.run({"tool": "propose_trade", "symbol": "SOL", "side": "sell", "all": True}))
        self.assertIn("already", self.tb.run({"tool": "propose_alert", "symbol": "BTC", "condition": "price_above",
                                              "value": last / 2}))
        self.tb.run({"tool": "propose_alert", "symbol": "BTC", "condition": "rsi_below", "value": 30, "interval": "4h"})
        self.assertEqual(self.tb.proposals[1]["payload"]["interval"], "4h")
        self.assertEqual(self.app.paper.trades(), [])  # nothing happened yet


class Script:
    def __init__(self, turns, agent="trading_analyst"):
        self.turns = [json.dumps(t, ensure_ascii=False) for t in turns]
        self.agent = agent
        self.planner_calls, self.systems = [], []

    def __call__(self, messages, json_mode):
        system = messages[0]["content"]
        if system.startswith("You are the planner"):
            self.planner_calls.append(system)
            return json.dumps({"steps": [{"agent": self.agent, "instruction": "حلل واقترح"}]})
        if json_mode and "PAPER-trading portfolio" in system:
            self.systems.append(system)
            return self.turns.pop(0) if self.turns else json.dumps({"done": "تم"})
        return "تم"


class FlowTest(TradingBase):
    def start(self, done="البيتكوين في اتجاه صاعد فوق متوسط 50."):
        last = self.fake.last("BTCUSDT")
        self.script = Script([
            {"tool": "analyze", "symbol": "BTC", "interval": "1d"},
            {"tool": "propose_trade", "symbol": "BTC", "side": "buy", "amount": 1000, "stop_loss": round(last * 0.95),
             "take_profit": round(last * 1.1), "reason": "اتجاه صاعد"},
            {"tool": "propose_alert", "symbol": "BTC", "condition": "price_below", "value": round(last * 0.9)},
            {"done": done}])
        self.make_app(self.script)
        tid = self.app.orchestrator.submit("حلل البيتكوين")  # short, but it must reach the planner
        wait_for(lambda: self.app.memory.get_task(tid)["status"] in ("waiting", "done", "failed"))
        return tid, last

    def test_approve(self):
        tid, last = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.assertEqual((step["agent"], step["status"]), ("trading_analyst", "awaiting_approval"))
        self.assertEqual([a["kind"] for a in step["actions"]], ["trade_buy", "trade_alert"])
        self.assertIn('"trading_analyst"', self.script.planner_calls[0])
        self.assertIn("Twelve Data key the user has not added", self.script.systems[0])
        self.app.orchestrator.decide(step["id"], True)
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        task = self.app.memory.get_task(tid)
        self.assertEqual([a["status"] for a in task["steps"][0]["actions"]], ["done", "done"])
        trade = self.app.paper.trades()[0]
        self.assertEqual((trade["side"], trade["price"], trade["total"]), ("buy", last, 1000))
        alerts = self.app.paper.alerts(("active",))
        self.assertEqual(sorted(a["condition"] for a in alerts), ["price_above", "price_below", "price_below"])
        self.assertIn("ليس نصيحة مالية", task["steps"][0]["output"])  # added when the model forgot it
        self.assertIn("**التداول التجريبي:**", task["steps"][0]["output"])

    def test_price_moved(self):
        tid, last = self.start()
        step = self.app.memory.get_task(tid)["steps"][0]
        self.fake.ticker_override["BTCUSDT"] = last * 1.05
        self.app.orchestrator.decide(step["id"], True, exclude=[step["actions"][1]["id"]])
        wait_for(lambda: self.app.memory.get_task(tid)["status"] == "done")
        acts = self.app.memory.get_task(tid)["steps"][0]["actions"]
        self.assertEqual([a["status"] for a in acts], ["conflict", "rejected"])
        self.assertIn("تحرك السعر +5.0%", acts[0]["result"])
        self.assertEqual(self.app.paper.trades(), [])


class ApiTest(TradingBase):
    def setUp(self):
        super().setUp()
        self.make_app()
        self.server = make_server(self.app, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"X-Yousef-Client": "1", "Content-Type": "application/json"}
        conn.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
        resp = conn.getresponse()
        out = json.loads(resp.read() or b"{}")
        conn.close()
        return resp.status, out

    def test_endpoints(self):
        btc = resolve("BTC")
        self.app.paper.buy(btc, 2000, 50000, 0.001)
        alert = self.app.paper.add_alert(btc, "price_above", 1)
        status, data = self.call("GET", "/api/trading?prices=1")
        self.assertEqual(status, 200)
        pos = data["portfolio"]["positions"][0]
        self.assertEqual((pos["display"], pos["price"]), ("BTC/USDT", self.fake.last("BTCUSDT")))
        self.assertGreater(data["portfolio"]["pnl"], 0)
        self.assertEqual(data["alerts"][0]["text"], "BTC/USDT: السعر فوق 1")
        self.assertFalse(data["stocks"])
        self.app.alerts.check()
        self.assertEqual(len(self.call("GET", "/api/trading/alerts")[1]["alerts"]), 1)
        self.call("POST", "/api/trading/alerts/seen", {})
        self.assertEqual(self.call("GET", "/api/trading/alerts")[1]["alerts"], [])
        self.assertEqual(self.call("DELETE", f"/api/trading/alerts/{alert['id']}")[1]["alerts"], [])
        self.assertEqual(self.call("POST", "/api/trading/reset", {"cash": 50})[0], 400)
        status, data = self.call("POST", "/api/trading/reset", {"cash": 2500})
        self.assertEqual((status, data["portfolio"]["cash"], data["portfolio"]["positions"]), (200, 2500, []))


if __name__ == "__main__":
    unittest.main()
