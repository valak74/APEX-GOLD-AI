"""Persists ticks and completed bars in batches, off the quote path.

Quotes are appended to in-memory buffers (O(1)); a background task flushes them every ``flush_every``
seconds with idempotent inserts (``ON CONFLICT DO NOTHING`` for ticks, upsert for bars). If the database
is unavailable, buffers are kept up to ``max_buffer`` rows (oldest dropped, counted) — recording failures
never block the feed or trading safety checks.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.core.logging import get_logger
from apex.db.models import BarRow, Tick
from apex.domain.models import Quote, utcnow
from apex.market.bars import Bar

log = get_logger(__name__)


class MarketRecorder:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        source: str,
        flush_every: float = 1.0,
        max_buffer: int = 200_000,
        persist_timeframes: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"),
    ) -> None:
        self.maker, self.source, self.flush_every = maker, source, flush_every
        self.persist_timeframes = set(persist_timeframes)
        self._ticks: deque[dict[str, Any]] = deque(maxlen=max_buffer)
        self._bars: deque[dict[str, Any]] = deque(maxlen=max_buffer)
        self.dropped = 0
        self.written_ticks = 0
        self.written_bars = 0
        self.last_error: str | None = None
        self._task: asyncio.Task[None] | None = None

    def add_quote(self, q: Quote) -> None:
        if len(self._ticks) == self._ticks.maxlen:
            self.dropped += 1
        self._ticks.append(
            {
                "source": self.source,
                "symbol": q.symbol,
                "ts": q.ts,
                "bid": q.bid,
                "ask": q.ask,
                "recv_ts": utcnow(),
            }
        )

    def add_bar(self, bar: Bar) -> None:
        if bar.timeframe not in self.persist_timeframes or not bar.complete:
            return
        self._bars.append(
            {
                "source": self.source,
                "symbol": bar.symbol,
                "timeframe": bar.timeframe,
                "start": bar.start,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "spread_avg": bar.spread_avg,
                "spread_max": bar.spread_max,
                "tick_count": bar.tick_count,
            }
        )

    async def flush(self) -> None:
        ticks, bars = list(self._ticks), list(self._bars)
        if not ticks and not bars:
            return
        try:
            async with self.maker() as session:
                if ticks:
                    await session.execute(insert(Tick).on_conflict_do_nothing(), ticks)
                if bars:
                    stmt = insert(BarRow)
                    cols = ("open", "high", "low", "close", "spread_avg", "spread_max", "tick_count")
                    await session.execute(
                        stmt.on_conflict_do_update(
                            index_elements=["source", "symbol", "timeframe", "start"],
                            set_={c: stmt.excluded[c] for c in cols},
                        ),
                        bars,
                    )
                await session.commit()
        except Exception as e:  # keep buffering; never crash the feed
            self.last_error = f"{type(e).__name__}"
            log.warning(
                "market_recorder_flush_failed", error=self.last_error, buffered=len(ticks) + len(bars)
            )
            return
        for _ in ticks:
            self._ticks.popleft()
        for _ in bars:
            self._bars.popleft()
        self.written_ticks += len(ticks)
        self.written_bars += len(bars)
        self.last_error = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.flush_every)
            await self.flush()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()

    def describe(self) -> dict[str, Any]:
        return {
            "written_ticks": self.written_ticks,
            "written_bars": self.written_bars,
            "buffered": len(self._ticks) + len(self._bars),
            "dropped": self.dropped,
            "last_error": self.last_error,
        }
