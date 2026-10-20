"""The paper-trading book: a practice portfolio in US dollars, its trades and the user's alerts.

Nothing here touches a real exchange or broker. Trades happen only after the
user approves them, at the market price at that moment, with a fee so the
results are not rosier than real trading.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_CASH = 10_000.0
CONDITIONS = ("price_above", "price_below", "rsi_above", "rsi_below")
ALERT_STATUSES = ("active", "triggered", "cancelled")
MAX_ALERTS = 50
DUST = 1e-9

SCHEMA = """
CREATE TABLE IF NOT EXISTS account(
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash REAL NOT NULL,
    start_cash REAL NOT NULL,
    started_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS positions(
    ref TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    display TEXT NOT NULL,
    qty REAL NOT NULL,
    cost REAL NOT NULL,          -- what the position cost, fees included
    opened_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trades(
    id INTEGER PRIMARY KEY,
    ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    display TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    total REAL NOT NULL,         -- cash out for a buy, cash in for a sell (after the fee)
    pnl REAL,                    -- realised profit or loss of a sell
    cash_after REAL NOT NULL,
    task_id INTEGER,
    action_id INTEGER,
    note TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts(
    id INTEGER PRIMARY KEY,
    ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    display TEXT NOT NULL,
    condition TEXT NOT NULL,
    value REAL NOT NULL,
    interval TEXT NOT NULL DEFAULT '1d',
    note TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    triggered_at REAL,
    triggered_value REAL,
    seen INTEGER NOT NULL DEFAULT 0,
    task_id INTEGER
);
CREATE INDEX IF NOT EXISTS alerts_status ON alerts(status);
"""


class PaperError(ValueError):
    pass


class PaperBook:
    def __init__(self, path: Path, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- account ----------------------------------------------------------------------
    def account(self) -> dict:
        with self._lock:
            row = self._db.execute("SELECT * FROM account WHERE id = 1").fetchone()
            if row is None:
                now = self.clock()
                self._db.execute("INSERT INTO account(id, cash, start_cash, started_at) VALUES (1, ?, ?, ?)",
                                 (DEFAULT_CASH, DEFAULT_CASH, now))
                self._db.commit()
                return {"cash": DEFAULT_CASH, "start_cash": DEFAULT_CASH, "started_at": now}
        return {"cash": row["cash"], "start_cash": row["start_cash"], "started_at": row["started_at"]}

    def reset(self, cash: float = DEFAULT_CASH) -> dict:
        """Start again: new cash, no positions, no trades. Alerts stay."""
        cash = float(cash)
        if not 100 <= cash <= 10_000_000:
            raise PaperError("الرصيد التجريبي بين 100 و10,000,000 دولار")
        with self._lock:
            self._db.execute("DELETE FROM positions")
            self._db.execute("DELETE FROM trades")
            self._db.execute("INSERT OR REPLACE INTO account(id, cash, start_cash, started_at) VALUES (1, ?, ?, ?)",
                             (cash, cash, self.clock()))
            self._db.commit()
        return self.account()

    def positions(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM positions ORDER BY opened_at").fetchall()
        return [dict(r) for r in rows]

    def position(self, ref: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM positions WHERE ref = ?", (ref,)).fetchone()
        return dict(row) if row else None

    def trades(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ---- trading ----------------------------------------------------------------------
    def buy(self, inst, amount: float, price: float, fee_rate: float, task_id: int | None = None,
            action_id: int | None = None, note: str = "") -> dict:
        """Spend `amount` dollars (fee included) at `price`."""
        if not inst.usd:
            raise PaperError(f"المحفظة التجريبية بالدولار، و{inst.display} مسعّر بـ {inst.quote}")
        amount, price = float(amount), float(price)
        if amount <= 0 or price <= 0:
            raise PaperError("المبلغ والسعر يجب أن يكونا أكبر من صفر")
        fee = amount * fee_rate
        qty = (amount - fee) / price
        now = self.clock()
        with self._lock:
            cash = self.account()["cash"]
            if amount > cash + 1e-6:
                raise PaperError(f"النقد التجريبي لا يكفي: المتاح {cash:,.2f}$ والمطلوب {amount:,.2f}$")
            cash -= amount
            pos = self.position(inst.ref)
            if pos:
                self._db.execute("UPDATE positions SET qty = ?, cost = ?, updated_at = ? WHERE ref = ?",
                                 (pos["qty"] + qty, pos["cost"] + amount, now, inst.ref))
            else:
                self._db.execute("INSERT INTO positions(ref, kind, display, qty, cost, opened_at, updated_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (inst.ref, inst.kind, inst.display, qty, amount, now, now))
            trade = self._record(inst, "buy", qty, price, fee, amount, None, cash, task_id, action_id, note, now)
        return trade

    def sell(self, inst, qty: float | None, price: float, fee_rate: float, task_id: int | None = None,
             action_id: int | None = None, note: str = "") -> dict:
        """Sell `qty` (None = the whole position) at `price`."""
        price = float(price)
        now = self.clock()
        with self._lock:
            pos = self.position(inst.ref)
            if not pos:
                raise PaperError(f"لا يوجد مركز في {inst.display} في المحفظة التجريبية")
            qty = pos["qty"] if qty is None else float(qty)
            if qty <= 0:
                raise PaperError("الكمية يجب أن تكون أكبر من صفر")
            if qty > pos["qty"] * (1 + 1e-9):
                raise PaperError(f"الكمية أكبر من المركز ({pos['qty']:.8g})")
            qty = min(qty, pos["qty"])
            gross = qty * price
            fee = gross * fee_rate
            total = gross - fee
            share = qty / pos["qty"]
            cost = pos["cost"] * share
            cash = self.account()["cash"] + total
            left = pos["qty"] - qty
            if left * price < 0.01 or left < DUST:
                self._db.execute("DELETE FROM positions WHERE ref = ?", (inst.ref,))
            else:
                self._db.execute("UPDATE positions SET qty = ?, cost = ?, updated_at = ? WHERE ref = ?",
                                 (left, pos["cost"] - cost, now, inst.ref))
            trade = self._record(inst, "sell", qty, price, fee, total, total - cost, cash, task_id, action_id, note, now)
        return trade

    def _record(self, inst, side, qty, price, fee, total, pnl, cash, task_id, action_id, note, now) -> dict:
        self._db.execute("UPDATE account SET cash = ? WHERE id = 1", (cash,))
        cur = self._db.execute(
            "INSERT INTO trades(ref, kind, display, side, qty, price, fee, total, pnl, cash_after, task_id, action_id, "
            "note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (inst.ref, inst.kind, inst.display, side, qty, price, fee, total, pnl, cash, task_id, action_id,
             note[:300] or None, now))
        self._db.commit()
        row = self._db.execute("SELECT * FROM trades WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def valuation(self, prices: dict[str, float]) -> dict:
        """Portfolio value with live prices (ref -> price); positions without a price count at cost."""
        acct = self.account()
        rows, value = [], acct["cash"]
        for p in self.positions():
            price = prices.get(p["ref"])
            now_value = p["qty"] * price if price is not None else p["cost"]
            value += now_value
            rows.append(dict(p, price=price, value=now_value, avg_price=p["cost"] / p["qty"] if p["qty"] else None,
                             pnl=now_value - p["cost"] if price is not None else None,
                             pnl_pct=(now_value / p["cost"] - 1) * 100 if price is not None and p["cost"] else None))
        return {"cash": acct["cash"], "start_cash": acct["start_cash"], "started_at": acct["started_at"],
                "value": value, "pnl": value - acct["start_cash"],
                "pnl_pct": (value / acct["start_cash"] - 1) * 100 if acct["start_cash"] else 0.0, "positions": rows}

    # ---- alerts -----------------------------------------------------------------------
    def add_alert(self, inst, condition: str, value: float, interval: str = "1d", note: str = "",
                  task_id: int | None = None) -> dict:
        if condition not in CONDITIONS:
            raise PaperError(f"condition must be one of {', '.join(CONDITIONS)}")
        value = float(value)
        if condition.startswith("rsi") and not 0 < value < 100:
            raise PaperError("RSI بين 0 و100")
        if condition.startswith("price") and value <= 0:
            raise PaperError("السعر يجب أن يكون أكبر من صفر")
        with self._lock:
            active = self._db.execute("SELECT COUNT(*) FROM alerts WHERE status = 'active'").fetchone()[0]
            if active >= MAX_ALERTS:
                raise PaperError(f"وصلت إلى {MAX_ALERTS} تنبيهًا فعّالًا؛ احذف بعضها")
            cur = self._db.execute(
                "INSERT INTO alerts(ref, kind, display, condition, value, interval, note, status, created_at, task_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (inst.ref, inst.kind, inst.display, condition, value, interval, note[:200] or None, self.clock(), task_id))
            self._db.commit()
            return self.alert(int(cur.lastrowid))

    def alert(self, alert_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return dict(row) if row else None

    def alerts(self, statuses: tuple = ("active", "triggered"), limit: int = 50) -> list[dict]:
        marks = ", ".join("?" for _ in statuses)
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM alerts WHERE status IN ({marks}) "
                                    "ORDER BY status = 'active' DESC, COALESCE(triggered_at, created_at) DESC LIMIT ?",
                                    (*statuses, limit)).fetchall()
        return [dict(r) for r in rows]

    def cancel_alert(self, alert_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE alerts SET status = 'cancelled' WHERE id = ? AND status = 'active'",
                                   (alert_id,))
            self._db.commit()
            return cur.rowcount == 1

    def delete_alert(self, alert_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            self._db.commit()
            return cur.rowcount == 1

    def trigger_alert(self, alert_id: int, value: float) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE alerts SET status = 'triggered', triggered_at = ?, triggered_value = ? "
                                   "WHERE id = ? AND status = 'active'", (self.clock(), value, alert_id))
            self._db.commit()
            return cur.rowcount == 1

    def unseen(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM alerts WHERE status = 'triggered' AND seen = 0 "
                                    "ORDER BY triggered_at").fetchall()
        return [dict(r) for r in rows]

    def mark_seen(self) -> None:
        with self._lock:
            self._db.execute("UPDATE alerts SET seen = 1 WHERE status = 'triggered'")
            self._db.commit()


CONDITION_AR = {"price_above": "السعر فوق", "price_below": "السعر تحت", "rsi_above": "مؤشر RSI فوق",
                "rsi_below": "مؤشر RSI تحت"}


def alert_value(condition: str, value, fmt=lambda x: f"{x:,.6g}") -> str:
    if value is None:
        return "-"
    return f"{value:.1f}".removesuffix(".0") if condition.startswith("rsi") else fmt(value)


def describe_alert(alert: dict, fmt=lambda x: f"{x:,.6g}") -> str:
    text = f"{alert['display']}: {CONDITION_AR.get(alert['condition'], alert['condition'])} " \
           f"{alert_value(alert['condition'], alert['value'], fmt)}"
    if alert["condition"].startswith("rsi"):
        text += f" على إطار {alert['interval']}"
    return text
