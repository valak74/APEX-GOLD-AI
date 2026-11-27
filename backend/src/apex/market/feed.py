"""Market data feed supervisor for the API process.

Starts the configured source (cTrader demo account) in the background. Every quote goes through
``on_quote``: data-quality monitor -> bar builder -> recorder (ticks + completed bars) -> paper broker.
The paper broker's placeholder XAUUSD specification is replaced by the broker's real one.
A failing source never blocks the API: it is reported in the status and retried.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.broker.ctrader import proto
from apex.broker.ctrader.adapter import CTraderAdapter
from apex.broker.ctrader.client import CTraderClient
from apex.broker.ctrader.store import CredentialsMissingError, load_ctrader_credentials, token_saver
from apex.broker.sim import SimBroker
from apex.core.config import Settings
from apex.core.logging import get_logger
from apex.domain.models import Quote, SymbolSpec, utcnow
from apex.market.bars import Bar, BarBuilder
from apex.market.quality import DataQualityMonitor
from apex.market.recorder import MarketRecorder

log = get_logger(__name__)


class MarketFeed:
    def __init__(self) -> None:
        self.source = "none"
        self.state = "disabled"  # disabled | not_configured | connecting | connected | reconnecting | error
        self.error: str | None = None
        self.adapter: CTraderAdapter | None = None
        self.last: dict[str, Quote] = {}
        self.spec_source = "placeholder"
        self._maker: async_sessionmaker[AsyncSession] | None = None
        self.broker_name: str | None = None
        self._task: asyncio.Task[None] | None = None
        self.quality = DataQualityMonitor()
        self.bars = BarBuilder("XAUUSD")
        self.recent: dict[str, deque[Bar]] = {}
        self.recorder: MarketRecorder | None = None
        self.paper: Any = None

    async def on_quote(self, q: Quote) -> None:
        self.quality.observe(q)
        if q.symbol == "XAUUSD":
            for bar in self.bars.update(q):
                self.recent.setdefault(bar.timeframe, deque(maxlen=1000)).append(bar)
                if self.recorder:
                    self.recorder.add_bar(bar)
        if self.recorder:
            self.recorder.add_quote(q)
        self.last[q.symbol] = q
        if isinstance(self.paper, SimBroker):
            await self.paper.on_quote(q)

    async def warm_up(self, maker: async_sessionmaker[AsyncSession], now: datetime | None = None) -> int:
        """Rebuild the in-progress bars (current trading day and below) from stored ticks.

        Without this, a restart mid-session would make the day's bar "open" at the first live quote.
        Uses history (e.g. Dukascopy) up to the live source's first tick, then the live source.
        The current week bar (1w) is only warmed from the current day — documented limitation.
        """
        from sqlalchemy import func, select

        from apex.db.models import Tick
        from apex.market.dukascopy import stream_ticks
        from apex.market.sessions import trading_day_start

        now = now or utcnow()
        start = trading_day_start(now)
        async with maker() as session:
            rows = (
                await session.execute(
                    select(Tick.source, func.count(), func.min(Tick.ts))
                    .where(Tick.symbol == "XAUUSD", Tick.ts >= start, Tick.ts <= now)
                    .group_by(Tick.source)
                )
            ).all()
        if not rows:
            return 0
        info = {src: (cnt, first) for src, cnt, first in rows}
        # Plan: history (best other source) until the live source's first tick, then the live source.
        plan: list[tuple[str, datetime, datetime]] = []
        live_first = info[self.source][1] if self.source in info else now
        others = {k: v for k, v in info.items() if k != self.source and v[1] < live_first}
        if others:
            best = max(others, key=lambda k: others[k][0])
            plan.append((best, start, live_first))
        if self.source in info:
            plan.append((self.source, live_first, now))
        n = 0
        for src, a, b in plan:
            async for q in stream_ticks(maker, src, "XAUUSD", a, b + timedelta(microseconds=1)):
                for bar in self.bars.update(q):
                    self.recent.setdefault(bar.timeframe, deque(maxlen=1000)).append(bar)
                n += 1
        log.info("bars_warmed_up", plan=[p[0] for p in plan], ticks=n, since=start.isoformat())
        return n

    async def start(self, settings: Settings, maker: async_sessionmaker[AsyncSession], paper: Any) -> None:
        self.source = settings.market_data_source
        self.paper = paper
        if self.source == "none":
            return
        acc_id = settings.ctrader_broker_account_id
        mk = settings.master_key.get_secret_value()
        if acc_id is None or not mk:
            self.state, self.error = (
                "not_configured",
                "APEX_CTRADER_BROKER_ACCOUNT_ID / APEX_MASTER_KEY missing",
            )
            return
        try:
            async with maker() as session:
                creds = await load_ctrader_credentials(session, mk, acc_id)
        except CredentialsMissingError as e:
            self.state, self.error = "not_configured", str(e)
            return
        tls = settings.ctrader_use_tls
        self.adapter = CTraderAdapter(
            creds,
            host=settings.ctrader_host or proto.DEMO_HOST,
            port=settings.ctrader_port,
            client_factory=lambda h, p: CTraderClient(h, p, use_tls=tls),
            max_gold_leverage=settings.ctrader_max_gold_leverage,
            extra_currencies=(settings.paper_account_currency,),
            on_tokens_refreshed=token_saver(maker, mk, acc_id),
        )

        try:
            await self.warm_up(maker)
        except Exception as e:  # warm-up is best effort; never block the feed
            log.warning("bars_warm_up_failed", error=type(e).__name__)
        self._maker = maker
        self.recorder = MarketRecorder(maker, source=self.source)
        self.recorder.start()
        self.adapter.add_quote_listener(self.on_quote)
        self._task = asyncio.create_task(self._run(paper))

    async def _run(self, paper: Any) -> None:
        assert self.adapter is not None
        backoff = 2.0
        self.state = "connecting"
        while True:
            try:
                await self.adapter.connect()
                break
            except Exception as e:  # report and retry; never crash the API
                self.state, self.error = "error", f"{type(e).__name__}: {e}"
                log.warning("market_feed_connect_failed", error=type(e).__name__, retry_in=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120.0)
        self.state, self.error = "connected", None
        self.broker_name = self.adapter.broker_name
        spec: SymbolSpec = self.adapter.specs["XAUUSD"]
        if isinstance(paper, SimBroker):
            paper.set_symbol_spec(spec)
            self.spec_source = f"ctrader:{self.adapter.broker_name or 'broker'}"
        if self._maker is not None:
            from apex.market.specs import save_spec

            try:  # remembered for offline tools (CLI backtests, research): real commission, not a placeholder
                await save_spec(self._maker, spec, f"ctrader:{self.adapter.broker_name or 'broker'}")
            except Exception as e:  # never block the feed on this
                log.warning("spec_save_failed", error=type(e).__name__)
        log.info("market_feed_connected", source=self.source, broker=self.broker_name)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self.adapter:
            await self.adapter.disconnect()
        if self.recorder:
            await self.recorder.stop()

    def describe(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        state = self.state
        if state == "connected" and self.adapter is not None and not self.adapter.connected:
            state = "reconnecting"
        q = self.last.get("XAUUSD")
        return {
            "source": self.source,
            "state": state,
            "error": self.error,
            "broker": self.broker_name,
            "spec_source": self.spec_source,
            "last_quote_age_s": round((now - q.ts).total_seconds(), 1) if q else None,
            "recorder": self.recorder.describe() if self.recorder else None,
        }
