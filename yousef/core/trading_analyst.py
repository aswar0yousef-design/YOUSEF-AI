"""The trading analyst: live prices and indicators, a paper portfolio and alerts. Never real orders."""

from __future__ import annotations

from ..integrations.trade_tools import TradeToolbox
from ..models import Router
from .multitool import MultiToolbox
from .toolloop import LoopResult, run_tool_loop

TRADE_MAX_CALLS = 12
DISCLAIMER = "هذا تحليل تعليمي وليس نصيحة مالية؛ القرار والصفقات الحقيقية لك وحدك."

TRADE_PROMPT = """You are analysing markets for the user with tools, and you manage their PAPER-trading portfolio (practice money in US dollars).
{stocks}

Reply with exactly ONE JSON object per turn, using one of these forms:
{tools}

Rules:
- Read before you judge: use price or analyze for every symbol you talk about (use the timeframe the user asks for; 1d by default, 4h or 1h for short-term). Every number must come from the tools; never invent prices or levels.
- Explain the picture plainly: trend, momentum (RSI, MACD), volatility (ATR, Bollinger), the nearest support and resistance, and what would change the view. Give scenarios (if it breaks X..., if it falls below Y...), never certainties or promises.
- Paper trades and alerts are proposals the user approves; they use practice money only. Propose a paper trade only when the user asks for one or asks you to practise; size it sensibly (a stop loss, and usually no more than a quarter of the portfolio in one position).
- You cannot place real orders, connect to exchanges or brokers, or move money, and you must not tell the user to put real money in. If they ask for a real trade, give the analysis and the levels, and say the real order is theirs to place in their own account.
- When you are done, reply with "done": a clear answer in the user's language, ending with this line: "{disclaimer}"
- You have at most {limit} tool calls."""

STOCKS_ON = "Crypto prices come from Binance; US stocks, forex and gold from Twelve Data (free plan: about 8 requests a minute, so do not compare too many stocks at once)."
STOCKS_OFF = ("Crypto prices come from Binance. Stocks, forex and gold need a free Twelve Data key the user has not added "
              "yet: for those, say they can add it in Settings, then Services.")


def build_toolbox(market, book, settings) -> MultiToolbox:
    return MultiToolbox(TradeToolbox(market, book, settings))


def run_trading_analyst(router: Router, toolbox: MultiToolbox, system: str, task_text: str,
                        max_calls: int = TRADE_MAX_CALLS) -> LoopResult:
    trade = toolbox.get(TradeToolbox)
    system = system + "\n\n" + TRADE_PROMPT.format(
        stocks=STOCKS_ON if trade.market.stocks_ready else STOCKS_OFF, tools=toolbox.help(), limit=max_calls,
        disclaimer=DISCLAIMER)
    res = run_tool_loop(router, toolbox, system, task_text, task_type="analysis", max_calls=max_calls,
                        name="trading_analyst")
    if res.text and DISCLAIMER not in res.text and "ليس نصيحة مالية" not in res.text \
            and "not financial advice" not in res.text.lower():
        res.text = res.text.rstrip() + "\n\n" + DISCLAIMER
    return res
