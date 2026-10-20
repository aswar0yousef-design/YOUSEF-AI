"""Checks the user's price and RSI alerts while Yousef runs, and records the ones that fire.

An alert only informs: it never trades. The dashboard shows the fired alerts.
"""

from __future__ import annotations

import logging
import threading

from ..models.base import ProviderError
from .indicators import rsi

log = logging.getLogger("yousef.alerts")
CHECK_EVERY = 60  # seconds; prices are cached for a minute anyway


class AlertWatcher:
    def __init__(self, book, market, every: float = CHECK_EVERY):
        self.book = book      # paper.PaperBook
        self.market = market  # market.Market
        self.every = every
        self.listeners: list = []  # callables(alert) when an alert fires
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> list[dict]:
        """One pass over the active alerts -> the alerts that fired now."""
        fired = []
        prices: dict[str, float | None] = {}
        rsis: dict[tuple, float | None] = {}
        for alert in self.book.alerts(("active",), limit=200):
            try:
                if alert["condition"].startswith("price"):
                    if alert["ref"] not in prices:
                        prices[alert["ref"]] = self.market.quote(alert["ref"], alert["kind"])["price"]
                    current = prices[alert["ref"]]
                else:
                    key = (alert["ref"], alert["interval"])
                    if key not in rsis:
                        _, candles = self.market.candles(alert["ref"], alert["interval"], 120, alert["kind"])
                        rsis[key] = rsi([c["c"] for c in candles])[-1]
                    current = rsis[key]
            except (ProviderError, ValueError) as e:
                log.warning("alert %s: %s", alert["id"], e)
                continue
            if current is None:
                continue
            hit = current >= alert["value"] if alert["condition"].endswith("above") else current <= alert["value"]
            if hit and self.book.trigger_alert(alert["id"], current):
                done = self.book.alert(alert["id"])
                fired.append(done)
                for listener in self.listeners:
                    try:
                        listener(done)
                    except Exception:  # a listener must not stop the others
                        log.exception("alert listener failed")
        return fired

    def start(self) -> None:
        if self._thread:
            return

        def loop():
            while not self._stop.wait(self.every):
                try:
                    self.check()
                except Exception:
                    log.exception("alert check failed")

        self._thread = threading.Thread(target=loop, name="yousef-alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
