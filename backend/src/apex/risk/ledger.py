"""Realised-trade ledger statistics used by the risk engine (pure functions).

Periods follow the gold trading calendar: the day starts at the 17:00 New York rollover,
the week at the Sunday
rollover. Drawdown is measured from the peak of the *realised* balance curve to the current *equity*
(conservative: open losses count, open gains never raise the peak).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from apex.market.sessions import bucket_start, trading_day_start


@dataclass(frozen=True)
class TradeRecord:
    closed_at: datetime
    net_pnl: Decimal  # account currency, after commissions/swap


@dataclass(frozen=True)
class LedgerStats:
    day_start: datetime
    week_start: datetime
    realized_today: Decimal
    realized_week: Decimal
    trades_today: int
    consecutive_losses: int
    peak_balance: Decimal
    day_start_balance: Decimal
    week_start_balance: Decimal


def compute_stats(trades: list[TradeRecord], initial_balance: Decimal, now: datetime) -> LedgerStats:
    trades = sorted(trades, key=lambda t: t.closed_at)
    day0, week0 = trading_day_start(now), bucket_start(now, "1w")
    bal = initial_balance
    peak = initial_balance
    day_bal = week_bal = None
    for t in trades:
        if day_bal is None and t.closed_at >= day0:
            day_bal = bal
        if week_bal is None and t.closed_at >= week0:
            week_bal = bal
        bal += t.net_pnl
        peak = max(peak, bal)
    streak = 0
    for t in reversed(trades):
        if t.net_pnl < 0:
            streak += 1
        else:
            break
    return LedgerStats(
        day_start=day0,
        week_start=week0,
        realized_today=sum((t.net_pnl for t in trades if t.closed_at >= day0), Decimal(0)),
        realized_week=sum((t.net_pnl for t in trades if t.closed_at >= week0), Decimal(0)),
        trades_today=sum(1 for t in trades if t.closed_at >= day0),
        consecutive_losses=streak,
        peak_balance=peak,
        day_start_balance=bal if day_bal is None else day_bal,
        week_start_balance=bal if week_bal is None else week_bal,
    )
