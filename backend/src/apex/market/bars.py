"""Build OHLC bars from bid/ask quotes, for every timeframe at once.

* OHLC on the **mid** price (bid/ask are kept separately as spread statistics);
* ``tick_count`` = number of quote updates. This is NOT traded volume: spot gold has no consolidated volume,
  and we never present tick counts as volume;
* a bar is *closed* when the first quote of a later bucket arrives (no look-ahead: a bar is never
  emitted before its period is over);
* out-of-order quotes (older than the current bar) are rejected and counted, not silently merged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from apex.domain.models import Quote
from apex.market.sessions import ALL_TIMEFRAMES, bucket_start


@dataclass
class Bar:
    symbol: str
    timeframe: str
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    spread_sum: Decimal
    spread_max: Decimal
    tick_count: int
    complete: bool = False

    @property
    def spread_avg(self) -> Decimal:
        return self.spread_sum / self.tick_count if self.tick_count else Decimal(0)

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start": self.start,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "spread_avg": self.spread_avg,
            "spread_max": self.spread_max,
            "tick_count": self.tick_count,
            "complete": self.complete,
        }


@dataclass
class BarBuilder:
    symbol: str
    timeframes: tuple[str, ...] = ALL_TIMEFRAMES
    current: dict[str, Bar] = field(default_factory=dict)
    rejected_out_of_order: int = 0
    last_ts: datetime | None = None

    def update(self, q: Quote) -> list[Bar]:
        """Feed one quote; returns the bars that this quote closed (smallest timeframe first)."""
        if q.symbol != self.symbol:
            return []
        if self.last_ts is not None and q.ts < self.last_ts:
            self.rejected_out_of_order += 1
            return []
        self.last_ts = q.ts
        closed: list[Bar] = []
        mid, spread = q.mid, q.spread
        for tf in self.timeframes:
            start = bucket_start(q.ts, tf)
            bar = self.current.get(tf)
            if bar is None or start > bar.start:
                if bar is not None:
                    bar.complete = True
                    closed.append(bar)
                self.current[tf] = Bar(self.symbol, tf, start, mid, mid, mid, mid, spread, spread, 1)
                continue
            bar.high = max(bar.high, mid)
            bar.low = min(bar.low, mid)
            bar.close = mid
            bar.spread_sum += spread
            bar.spread_max = max(bar.spread_max, spread)
            bar.tick_count += 1
        return closed

    def partial(self, timeframe: str) -> Bar | None:
        return self.current.get(timeframe)
