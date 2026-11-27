"""Background opportunity scanner.

Every ``scanner_interval_s`` seconds: build the MTF context from the bars the chart shows, run the setup
detectors on the last *completed* bars of the execution timeframes, attach blockers (news lockout,
data quality, kill switch), persist new opportunities, optionally ask the LLM for a written explanation,
and follow active
opportunities until their first target, their stop, or expiry (paper tracking — no order is ever sent).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from apex.analysis.engine import mtf_context
from apex.analysis.indicators import Candles
from apex.analysis.opportunities import Opportunity, detect
from apex.api.routes.system import active_risk_config, collect_bars
from apex.api.state import AppState
from apex.core.logging import get_logger
from apex.db.models import OpportunityRow
from apex.domain.models import utcnow
from apex.intel.calendar import evaluate_lockout, upcoming
from apex.intel.explainer import Explainer
from apex.market.sessions import TIMEFRAMES

log = get_logger(__name__)

MTF_FRAMES = {"1d": 200, "4h": 300, "1h": 300, "15m": 300, "5m": 400}
SCAN_TIMEFRAMES = ("5m", "15m")
EXPIRY_BARS = 12


class OpportunityScanner:
    def __init__(self, state: AppState, explainer: Explainer | None = None) -> None:
        self.state = state
        self.explainer = explainer
        self._task: asyncio.Task[None] | None = None
        self._explaining: set[asyncio.Task[Any]] = set()
        self.last_scan: datetime | None = None
        self.last_error: str | None = None

    async def _candles(self, session: Any, tf: str, limit: int, completed_only: bool) -> Candles:
        bars = await collect_bars(self.state, session, tf, limit)
        if completed_only and bars and not bars[-1].complete:
            bars = bars[:-1]
        return Candles.from_rows(bars, tf)

    async def _blockers(self, session: Any, now: datetime) -> list[str]:
        out: list[str] = []
        if self.state.trading.kill_switch_engaged:
            out.append("arrêt d'urgence engagé")
        cfg, _ = await active_risk_config(session)
        lock = evaluate_lockout(
            await upcoming(session, now, days=2, back_hours=6),
            now,
            cfg.news_lockout_minutes_before,
            cfg.news_lockout_minutes_after,
        )
        if lock.locked:
            out.append(lock.reason or "annonce économique majeure")
        q = self.state.market.quality.report("XAUUSD", now)
        if not q.tradeable:
            out.append("qualité des données insuffisante (" + ", ".join(q.issues) + ")")
        return out

    async def scan_once(self, now: datetime | None = None) -> list[Opportunity]:
        now = now or utcnow()
        new: list[Opportunity] = []
        async with self.state.sessionmaker() as session:
            frames = {
                tf: await self._candles(session, tf, n, completed_only=True) for tf, n in MTF_FRAMES.items()
            }
            ctx = await asyncio.to_thread(mtf_context, frames, None, now)
            blockers = await self._blockers(session, now)
            cfg, _ = await active_risk_config(session)
            spec = await self.state.broker.get_symbol_spec("XAUUSD")
            q = self.state.market.quality.report("XAUUSD", now)
            spread = float(q.spread_median) if q.spread_median is not None else 0.3
            px = frames["5m"].c[-1] if len(frames["5m"]) else 4000.0
            comm = (
                float(spec.commission_for(Decimal(1), Decimal(str(round(float(px), 2)))))
                * 2
                / float(spec.contract_size)
            )
            found: list[Opportunity] = []
            for tf in SCAN_TIMEFRAMES:
                found += await asyncio.to_thread(
                    detect, frames[tf], ctx, True, spread + comm, float(cfg.min_risk_reward)
                )
            for opp in found:
                if await session.get(OpportunityRow, opp.id) is not None:
                    continue
                opp.blockers = list(blockers)
                session.add(
                    OpportunityRow(
                        id=opp.id,
                        detected_at=opp.detected_at,
                        setup=opp.setup,
                        direction=opp.direction,
                        timeframe=opp.timeframe,
                        status=opp.status,
                        quality=opp.quality,
                        payload=_json(opp.to_dict()),
                    )
                )
                new.append(opp)
            await session.commit()
        for opp in new:
            log.info(
                "opportunity_detected",
                id=opp.id,
                setup=opp.setup,
                direction=opp.direction,
                tf=opp.timeframe,
                quality=opp.quality,
                blocked=bool(opp.blockers),
            )
            if self.explainer is not None:
                t = asyncio.create_task(self.explain(opp))
                self._explaining.add(t)
                t.add_done_callback(self._explaining.discard)
        await self.track(now)
        self.last_scan = now
        return new

    async def explain(self, opp: Opportunity) -> dict[str, Any] | None:
        if self.explainer is None:
            return None
        n = await self.explainer.explain(opp)
        if n is None:
            return None
        async with self.state.sessionmaker() as session:
            row = await session.get(OpportunityRow, opp.id)
            if row is not None:
                row.narrative = n.model_dump()
                await session.commit()
        return n.model_dump()

    async def track(self, now: datetime) -> None:
        """Follow active ideas: target reached, stop hit (invalidated) or expired — paper tracking only."""
        q = self.state.market.last.get("XAUUSD")
        async with self.state.sessionmaker() as session:
            rows = (
                await session.execute(select(OpportunityRow).where(OpportunityRow.status == "active"))
            ).scalars()
            for row in rows:
                p = row.payload
                status = None
                if q is not None and q.ts >= row.detected_at:
                    s = 1 if row.direction == "bullish" else -1
                    px = float(q.bid if s > 0 else q.ask)  # price at which a position would be closed
                    if (px - float(p["stop"])) * s <= 0:
                        status = "invalidated"
                    elif (px - float(p["targets"][0])) * s >= 0:
                        status = "target_reached"
                tf_s = TIMEFRAMES.get(row.timeframe, 300)
                if status is None and now - row.detected_at > timedelta(seconds=tf_s * EXPIRY_BARS):
                    status = "expired"
                if status:
                    row.status = status
                    row.payload = {**p, "status": status, "closed_at": now.isoformat()}
            await session.commit()

    async def _loop(self) -> None:
        while True:
            try:
                await self.scan_once()
                self.last_error = None
            except Exception as e:  # never let the scanner crash the API
                self.last_error = type(e).__name__
                log.warning("scanner_failed", error=self.last_error)
            await asyncio.sleep(self.state.settings.scanner_interval_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        for t in [self._task, *self._explaining]:
            if t:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t


def _json(d: dict[str, Any]) -> dict[str, Any]:
    import json

    out: dict[str, Any] = json.loads(json.dumps(d, default=str))
    return out
