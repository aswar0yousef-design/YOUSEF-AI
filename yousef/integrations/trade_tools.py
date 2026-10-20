"""Tools the trading analyst calls: live prices and indicators, the paper portfolio, and proposals.

Proposals are PAPER trades and alerts the user approves. There is no tool,
and no code path, that sends an order to a real exchange or broker.
"""

from __future__ import annotations

from ..models.base import ProviderError
from ..trading.indicators import analyze, fmt_pct, fmt_price
from ..trading.market import INTERVALS, MarketError
from ..trading.paper import CONDITION_AR, CONDITIONS, PaperError, alert_value, describe_alert

OBSERVATION_LIMIT = 7000
MAX_PROPOSALS = 10
MAX_MOVE = 0.03          # a paper trade whose price moved more than 3% since the proposal waits for a new look
ACCOUNT = "المحفظة التجريبية"
KIND_AR = {"crypto": "عملة رقمية", "stock": "سهم", "forex": "فوركس"}

TOOL_HELP = """{"tool": "price", "symbol": "BTC"}                                       live price and change (crypto: BTC, ETH, SOL...; US stocks: AAPL, TSLA...; forex and gold: EUR/USD, XAU/USD)
{"tool": "analyze", "symbol": "BTC", "interval": "1d"}                  indicators on the chart (interval: 15m, 1h, 4h, 1d, 1w): trend, moving averages, RSI, MACD, Bollinger, ATR, support and resistance, last closes
{"tool": "compare", "symbols": ["BTC", "ETH", "SOL"], "interval": "1d"}  side by side: change, RSI, trend (up to 6 symbols)
{"tool": "portfolio"}                                                   the paper-trading portfolio: cash, positions with live value and profit or loss, recent trades
{"tool": "list_alerts"}                                                 the user's price and RSI alerts
{"tool": "propose_trade", "symbol": "BTC", "side": "buy", "amount": 500, "stop_loss": 61000, "take_profit": 72000, "reason": "..."}   propose a PAPER buy for an amount in US dollars (fee included); for a sell: "side": "sell" with "qty" or "all": true
{"tool": "propose_alert", "symbol": "BTC", "condition": "price_above", "value": 70000, "interval": "1d", "note": "..."}   propose an alert (condition: price_above, price_below, rsi_above, rsi_below; interval only for RSI)
{"tool": "propose_cancel_alert", "id": 3}                               propose removing an active alert
{"done": "your answer for the user"}                                    finish"""


def _clip(text: str) -> str:
    return text if len(text) <= OBSERVATION_LIMIT else text[:OBSERVATION_LIMIT] + "\n... (cut)"


def _rsi_word(value) -> str:
    if value is None:
        return ""
    if value >= 70:
        return " (overbought)"
    if value <= 30:
        return " (oversold)"
    return ""


class TradeToolbox:
    def __init__(self, market, book, settings=None):
        self.market = market  # trading.market.Market
        self.book = book      # trading.paper.PaperBook
        self.settings = settings
        self.trace: list[dict] = []
        self.proposals: list[dict] = []

    def _trading(self) -> dict:
        s = self.settings.get().get("trading", {}) if self.settings else {}
        return {"fee_pct": float(s.get("fee_pct", 0.1)), "max_position_pct": float(s.get("max_position_pct", 25))}

    def help(self) -> str:
        return TOOL_HELP

    def run(self, call: dict) -> str:
        tool = str(call.get("tool") or "")
        target = str(call.get("symbol") or call.get("symbols") or call.get("id") or "")
        try:
            handler = getattr(self, f"tool_{tool}", None)
            if handler is None:
                raise MarketError(f'unknown tool "{tool}". Available tools:\n{TOOL_HELP}')
            out = handler(call)
            self.trace.append({"tool": tool, "target": target[:120], "ok": True})
            return _clip(out)
        except (ProviderError, ValueError, TypeError) as e:
            self.trace.append({"tool": tool, "target": target[:120], "ok": False, "note": str(e)[:200]})
            return f"Error: {e}"

    # ---- reading ------------------------------------------------------------------------
    @staticmethod
    def _interval(call: dict) -> str:
        interval = str(call.get("interval") or "1d").lower()
        if interval not in INTERVALS:
            raise MarketError(f"interval must be one of {', '.join(INTERVALS)}")
        return interval

    def tool_price(self, call: dict) -> str:
        q = self.market.quote(str(call.get("symbol") or ""), call.get("market"))
        inst = q["instrument"]
        span = "24h" if inst.kind == "crypto" else "today"
        line = (f"{inst.display} ({inst.kind}): {fmt_price(q['price'])} {inst.quote} | {span} {fmt_pct(q['change_pct'])} | "
                f"high {fmt_price(q['high'])} | low {fmt_price(q['low'])}")
        if q.get("name"):
            line += f"\n{q['name']} on {q.get('exchange') or '?'}, market {'open' if q.get('open') else 'closed'}"
        return line

    def tool_analyze(self, call: dict) -> str:
        interval = self._interval(call)
        inst, candles = self.market.candles(str(call.get("symbol") or ""), interval, 300, call.get("market"))
        a = analyze(candles)
        p = fmt_price
        lines = [f"{inst.display} {interval} chart, {a['bars']} candles, prices in {inst.quote}",
                 f"Last close {p(a['last'])} | change: 1 bar {fmt_pct(a['change_1'])}, 7 bars {fmt_pct(a['change_7'])}, "
                 f"30 bars {fmt_pct(a['change_30'])}",
                 f"Range of the last {min(a['bars'], 365)} bars: high {p(a['high'])}, low {p(a['low'])}",
                 f"Trend: {a['trend']} | SMA20 {p(a['sma20'])} | SMA50 {p(a['sma50'])} | SMA200 {p(a['sma200'])} | EMA20 {p(a['ema20'])}"]
        if a["ma_cross"]:
            lines.append(f"SMA50 crossed {'above' if a['ma_cross'] == 'up' else 'below'} SMA200 in the last 20 bars "
                         f"({'golden' if a['ma_cross'] == 'up' else 'death'} cross)")
        lines.append(f"RSI14 {a['rsi']:.1f}{_rsi_word(a['rsi'])}" if a["rsi"] is not None else "RSI14 - (too few bars)")
        if a["macd"]:
            m = a["macd"]
            cross = f", crossed {'above' if m['cross'] == 'up' else 'below'} its signal recently" if m["cross"] else ""
            lines.append(f"MACD {p(m['line'])} vs signal {p(m['signal'])}, histogram {p(m['hist'])}{cross}")
        if a["bollinger"]:
            b = a["bollinger"]
            lines.append(f"Bollinger(20,2): upper {p(b['upper'])}, middle {p(b['middle'])}, lower {p(b['lower'])}; "
                         f"price at {b['pct_b'] * 100:.0f}% of the band, band width {b['width_pct']:.1f}%")
        if a["atr"] is not None:
            lines.append(f"ATR14 {p(a['atr'])} ({a['atr_pct']:.2f}% of price) — typical move per bar")
        if a["volume_ratio"] is not None and inst.kind != "forex":
            lines.append(f"Volume of the last bar: {a['volume_ratio']:.2f}x its 20-bar average")
        lines.append("Support (nearest first): " + (", ".join(p(x) for x in a["support"]) or "none below in range"))
        lines.append("Resistance (nearest first): " + (", ".join(p(x) for x in a["resistance"]) or "none above in range"))
        lines.append("Last closes (oldest first): " + ", ".join(p(x) for x in a["closes"]))
        return "\n".join(lines)

    def tool_compare(self, call: dict) -> str:
        symbols = call.get("symbols") or []
        if isinstance(symbols, str):
            symbols = [s for s in symbols.replace("،", ",").split(",") if s.strip()]
        if not isinstance(symbols, list) or not 2 <= len(symbols) <= 6:
            raise MarketError("symbols must be a list of 2 to 6 symbols")
        interval = self._interval(call)
        rows = ["symbol | last | 1 bar | 7 bars | 30 bars | RSI14 | trend | ATR %"]
        for s in symbols:
            try:
                inst, candles = self.market.candles(str(s), interval, 300)
                a = analyze(candles)
                r = "-" if a["rsi"] is None else f"{a['rsi']:.1f}"
                atr_pct = "-" if a["atr_pct"] is None else f"{a['atr_pct']:.2f}"
                rows.append(f"{inst.display} | {fmt_price(a['last'])} | {fmt_pct(a['change_1'])} | "
                            f"{fmt_pct(a['change_7'])} | {fmt_pct(a['change_30'])} | {r} | {a['trend']} | {atr_pct}")
            except (ProviderError, ValueError) as e:
                rows.append(f"{s} | error: {e}")
        return f"{interval} candles\n" + "\n".join(rows)

    def _prices(self) -> dict[str, float]:
        out = {}
        for p in self.book.positions():
            try:
                out[p["ref"]] = self.market.quote(p["ref"], p["kind"])["price"]
            except (ProviderError, ValueError):
                pass
        return out

    def tool_portfolio(self, call: dict) -> str:
        v = self.book.valuation(self._prices())
        lines = [f"Paper portfolio (US dollars, practice only): value {v['value']:,.2f} | cash {v['cash']:,.2f} | "
                 f"since start {v['pnl']:+,.2f} ({v['pnl_pct']:+.2f}%) from {v['start_cash']:,.2f}"]
        if v["positions"]:
            lines.append("position | qty | avg price | now | value | P/L")
            for p in v["positions"]:
                pnl = "-" if p["pnl"] is None else f"{p['pnl']:+,.2f} ({p['pnl_pct']:+.2f}%)"
                lines.append(f"{p['display']} | {p['qty']:.8g} | {fmt_price(p['avg_price'])} | {fmt_price(p['price'])} | "
                             f"{p['value']:,.2f} | {pnl}")
        else:
            lines.append("No open positions.")
        trades = self.book.trades(8)
        if trades:
            lines.append("Recent trades: " + "; ".join(
                f"#{t['id']} {t['side']} {t['qty']:.6g} {t['display']} @ {fmt_price(t['price'])}"
                + (f" P/L {t['pnl']:+,.2f}" if t["pnl"] is not None else "") for t in trades))
        return "\n".join(lines)

    def tool_list_alerts(self, call: dict) -> str:
        alerts = self.book.alerts(("active", "triggered"), limit=30)
        if not alerts:
            return "No alerts."
        return "\n".join(f"#{a['id']} [{a['status']}] {describe_alert(a, fmt_price)}"
                         + (f" — fired at {fmt_price(a['triggered_value'])}" if a["triggered_value"] is not None else "")
                         + (f" — {a['note']}" if a["note"] else "") for a in alerts)

    # ---- proposals ----------------------------------------------------------------------
    def _check_room(self) -> None:
        if len(self.proposals) >= MAX_PROPOSALS:
            raise PaperError(f"at most {MAX_PROPOSALS} proposals per task")

    @staticmethod
    def _number(call: dict, key: str, required: bool = False) -> float | None:
        value = call.get(key)
        if value in (None, "", 0, "0"):
            if required:
                raise PaperError(f'"{key}" is required')
            return None
        try:
            return float(str(value).replace(",", ""))
        except ValueError:
            raise PaperError(f'"{key}" must be a number') from None

    def tool_propose_trade(self, call: dict) -> str:
        self._check_room()
        side = str(call.get("side") or "").lower()
        if side not in ("buy", "sell"):
            raise PaperError('side must be "buy" or "sell"')
        inst = self.market.resolve(str(call.get("symbol") or ""), call.get("market"))
        if not inst.usd:
            raise PaperError(f"the paper portfolio is in US dollars; {inst.display} is priced in {inst.quote}. "
                             "Use a USD or USDT pair.")
        q = self.market.quote(inst.ref, inst.kind, fresh=True)
        inst, price = q["instrument"], q["price"]
        if not inst.usd:  # a stock listed in another currency
            raise PaperError(f"the paper portfolio is in US dollars; {inst.display} is priced in {inst.quote}.")
        cfg = self._trading()
        fee_rate = cfg["fee_pct"] / 100
        v = self.book.valuation(self._prices())
        warnings = []
        rows = [{"label": "النوع", "value": "تداول تجريبي — لا أموال حقيقية"},
                {"label": "الأصل", "value": f"{inst.display} ({KIND_AR[inst.kind]})"},
                {"label": "السعر الآن", "value": f"{fmt_price(price)} {inst.quote}"}]
        payload = {"title": f"{'شراء' if side == 'buy' else 'بيع'} {inst.display}", "account": ACCOUNT,
                   "ref": inst.ref, "market": inst.kind, "display": inst.display, "side": side, "price": price,
                   "reason": str(call.get("reason") or "")[:300]}
        if side == "buy":
            amount = self._number(call, "amount", required=True)
            if amount < 1:
                raise PaperError("amount must be at least 1 US dollar")
            if amount > v["cash"] + 1e-6:
                raise PaperError(f"not enough paper cash: {v['cash']:,.2f} available")
            stop, target = self._number(call, "stop_loss"), self._number(call, "take_profit")
            if stop is not None and stop >= price:
                raise PaperError(f"stop_loss must be below the current price {fmt_price(price)} for a buy")
            if target is not None and target <= price:
                raise PaperError(f"take_profit must be above the current price {fmt_price(price)} for a buy")
            fee = amount * fee_rate
            qty = (amount - fee) / price
            rows += [{"label": "المبلغ", "value": f"{amount:,.2f}$ (الرسوم {fee:,.2f}$ = {cfg['fee_pct']:g}%)"},
                     {"label": "الكمية التقريبية", "value": f"{qty:.8g}"},
                     {"label": "النقد بعد الصفقة", "value": f"{v['cash'] - amount:,.2f}$ من {v['cash']:,.2f}$"}]
            if stop is not None:
                rows.append({"label": "وقف الخسارة", "value": f"{fmt_price(stop)} ({(stop / price - 1) * 100:+.1f}%، خسارة نحو {amount * (1 - stop / price):,.2f}$)"})
            else:
                warnings.append("بلا وقف خسارة: حدّد مستوى تخرج عنده إن سار السعر عكسك")
            if target is not None:
                rows.append({"label": "جني الأرباح", "value": f"{fmt_price(target)} ({(target / price - 1) * 100:+.1f}%)"})
            share = (amount + (self.book.position(inst.ref) or {}).get("cost", 0)) / v["value"] * 100 if v["value"] else 100
            if share > cfg["max_position_pct"]:
                warnings.append(f"هذا المركز سيكون {share:.0f}% من المحفظة؛ الحد المقترح {cfg['max_position_pct']:g}%")
            if stop and target and (target - price) < (price - stop):
                warnings.append("الربح المتوقع أقل من الخسارة المحتملة (نسبة العائد إلى المخاطرة أقل من 1)")
            payload.update(amount=round(amount, 2), qty=qty, stop_loss=stop, take_profit=target)
            out = f"Proposed a PAPER buy of {amount:,.2f}$ of {inst.display} at about {fmt_price(price)}."
        else:
            pos = self.book.position(inst.ref)
            if not pos:
                raise PaperError(f"there is no paper position in {inst.display}")
            qty = pos["qty"] if call.get("all") in (True, "true", 1) else self._number(call, "qty", required=True)
            if qty > pos["qty"] * (1 + 1e-9):
                raise PaperError(f"qty is more than the position ({pos['qty']:.8g})")
            gross = qty * price
            fee = gross * fee_rate
            cost = pos["cost"] * qty / pos["qty"]
            pnl = gross - fee - cost
            rows += [{"label": "الكمية", "value": f"{qty:.8g} من {pos['qty']:.8g}"},
                     {"label": "المتحصّل", "value": f"{gross - fee:,.2f}$ بعد الرسوم {fee:,.2f}$"},
                     {"label": "الربح/الخسارة", "value": f"{pnl:+,.2f}$ ({(pnl / cost * 100) if cost else 0:+.2f}%)"}]
            payload.update(qty=qty, all=qty >= pos["qty"])
            out = f"Proposed a PAPER sell of {qty:.8g} {inst.display} at about {fmt_price(price)} (P/L about {pnl:+,.2f}$)."
        if payload["reason"]:
            rows.append({"label": "السبب", "value": payload["reason"]})
        payload.update(rows=rows, warnings=warnings)
        self.proposals.append({"kind": f"trade_{side}", "payload": payload})
        return out + " It happens only if the user approves, at the price at that moment. It is practice, not a real order."

    def tool_propose_alert(self, call: dict) -> str:
        self._check_room()
        condition = str(call.get("condition") or "")
        if condition not in CONDITIONS:
            raise PaperError(f"condition must be one of {', '.join(CONDITIONS)}")
        value = self._number(call, "value", required=True)
        interval = self._interval(call)
        q = self.market.quote(str(call.get("symbol") or ""), call.get("market"))
        inst = q["instrument"]
        if condition == "price_above" and value <= q["price"]:
            raise PaperError(f"the price is already {fmt_price(q['price'])}, above {fmt_price(value)}")
        if condition == "price_below" and value >= q["price"]:
            raise PaperError(f"the price is already {fmt_price(q['price'])}, below {fmt_price(value)}")
        if condition.startswith("rsi") and not 0 < value < 100:
            raise PaperError("RSI alerts take a value between 0 and 100")
        note = str(call.get("note") or "")[:200]
        rows = [{"label": "الأصل", "value": inst.display},
                {"label": "الشرط", "value": f"{CONDITION_AR[condition]} {alert_value(condition, value, fmt_price)}"
                                           + (f" على إطار {interval}" if condition.startswith("rsi") else "")},
                {"label": "السعر الآن", "value": f"{fmt_price(q['price'])} {inst.quote}"},
                {"label": "كيف يعمل", "value": "يفحصه يوسف كل دقيقة ما دام شغّالًا، ويظهر في اللوحة عند تحققه. لا يبيع ولا يشتري."}]
        if note:
            rows.append({"label": "ملاحظة", "value": note})
        self.proposals.append({"kind": "trade_alert", "payload": {
            "title": f"تنبيه {inst.display}", "account": ACCOUNT, "ref": inst.ref, "market": inst.kind,
            "display": inst.display, "condition": condition, "value": value, "interval": interval, "note": note,
            "rows": rows, "warnings": []}})
        return f"Proposed an alert: {inst.display} {condition} {value:g}. It is added only if the user approves."

    def tool_propose_cancel_alert(self, call: dict) -> str:
        self._check_room()
        try:
            alert_id = int(call.get("id"))
        except (TypeError, ValueError):
            raise PaperError("id must be an alert number from list_alerts") from None
        alert = self.book.alert(alert_id)
        if not alert or alert["status"] != "active":
            raise PaperError(f"no active alert #{alert_id}")
        self.proposals.append({"kind": "trade_cancel_alert", "payload": {
            "title": f"إلغاء تنبيه {alert['display']}", "account": ACCOUNT, "alert_id": alert_id,
            "rows": [{"label": "التنبيه", "value": describe_alert(alert, fmt_price)}], "warnings": []}})
        return f"Proposed removing alert #{alert_id}."

    def action_list(self) -> list[dict]:
        return list(self.proposals)


def execute(action: dict, market, book, settings, task_id: int | None = None) -> tuple[str, str]:
    """Carry out one approved paper action -> (status, note). Status: done, conflict or failed."""
    p = action["payload"]
    kind = action["kind"]
    try:
        if kind in ("trade_buy", "trade_sell"):
            q = market.quote(p["ref"], p["market"], fresh=True)
            inst, price = q["instrument"], q["price"]
            moved = price / p["price"] - 1 if p.get("price") else 0
            if abs(moved) > MAX_MOVE:
                return "conflict", (f"تحرك السعر {moved * 100:+.1f}% منذ الاقتراح (من {fmt_price(p['price'])} إلى "
                                    f"{fmt_price(price)})؛ اطلب تحليلًا جديدًا قبل الصفقة")
            fee_rate = float((settings.get().get("trading") or {}).get("fee_pct", 0.1)) / 100
            if kind == "trade_buy":
                t = book.buy(inst, p["amount"], price, fee_rate, task_id, action["id"], p.get("reason") or "")
                note = f"اشتُري {t['qty']:.8g} {inst.display} بسعر {fmt_price(price)} (الرسوم {t['fee']:,.2f}$)"
                extra = []
                for cond, level, label in (("price_below", p.get("stop_loss"), "وقف الخسارة"),
                                           ("price_above", p.get("take_profit"), "جني الأرباح")):
                    if level:
                        try:
                            book.add_alert(inst, cond, level, note=f"{label} لصفقة #{t['id']}", task_id=task_id)
                            extra.append(f"تنبيه {label} عند {fmt_price(level)}")
                        except PaperError:
                            pass
                if extra:
                    note += "؛ و" + " و".join(extra)
            else:
                t = book.sell(inst, None if p.get("all") else p["qty"], price, fee_rate, task_id, action["id"],
                              p.get("reason") or "")
                note = (f"بيع {t['qty']:.8g} {inst.display} بسعر {fmt_price(price)}: "
                        f"{'ربح' if (t['pnl'] or 0) >= 0 else 'خسارة'} {abs(t['pnl'] or 0):,.2f}$")
            return "done", note + f" — النقد التجريبي الآن {t['cash_after']:,.2f}$"
        if kind == "trade_alert":
            inst = market.resolve(p["ref"], p["market"])
            a = book.add_alert(inst, p["condition"], p["value"], p.get("interval") or "1d", p.get("note") or "", task_id)
            return "done", f"أُضيف التنبيه #{a['id']}: {describe_alert(a, fmt_price)}"
        if kind == "trade_cancel_alert":
            if book.cancel_alert(int(p["alert_id"])):
                return "done", f"أُلغي التنبيه #{p['alert_id']}"
            return "failed", "التنبيه لم يعد فعّالًا"
        return "failed", f"إجراء غير معروف: {kind}"
    except (ProviderError, ValueError, KeyError) as e:
        return "failed", str(e)[:500]

