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
from datetime import datetime
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
