"""PAPER autopilot: turns the scanner's opportunities into simulated orders, under the risk engine.

Rules
-----
* PAPER only (simulated money on real prices). The loop refuses any other mode.
* Off by default; switched on explicitly by a trader, and stopped by the kill switch like everything else.
* Only setups/timeframes the operator selected, only fresh ideas (signal bar closed less than
  ``freshness_bars`` bars ago), each idea at most once (journal + deterministic order id).
* Every order is sized and approved by the risk engine at the CURRENT executable price (if price has run away,
  the reward/risk check refuses it). Every decision — skipped, refused, failed, opened — is journalled with why.
* Position management mirrors the backtester: stop and target on the order, time exit after ``max_hold_bars``
  bars, flat before the Friday close.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select

from apex.api.routes.system import active_risk_config
from apex.broker.sim import SimBroker
from apex.core.logging import get_logger
from apex.db.models import OpportunityRow, PaperOrderRow
from apex.domain.enums import CloseReason, OrderStatus, Side, TradingMode
from apex.domain.models import ClosedTrade, utcnow
from apex.market.sessions import NY, ROLLOVER, TIMEFRAMES, is_market_open

log = get_logger(__name__)

SETUP_FR = {"liquidity_sweep_reversal": "Balayage de liquidité", "trend_pullback": "Repli dans la tendance"}
REASON_FR = {
    "stop_loss": "stop",
    "take_profit": "objectif",
    "time_exit": "durée maximale",
    "weekly_close": "fermeture du vendredi",
    "kill_switch": "arrêt d'urgence",
    "stop_out": "stop-out",
    "manual": "manuelle",
}


class AutopilotConfig(BaseModel):
    setups: list[Literal["liquidity_sweep_reversal", "trend_pullback"]] = Field(
        default_factory=lambda: ["liquidity_sweep_reversal", "trend_pullback"]  # type: ignore[arg-type]
    )
    timeframes: list[Literal["5m", "15m"]] = Field(default_factory=lambda: ["5m", "15m"])  # type: ignore[arg-type]
    min_quality: int = Field(0, ge=0, le=100)
    max_hold_bars: int = Field(48, ge=1, le=500)
    freshness_bars: int = Field(1, ge=1, le=3)


class AutoTrader:
    def __init__(self, state: Any) -> None:
        self.state = state
        self._task: asyncio.Task[None] | None = None
        self._writes: set[asyncio.Task[None]] = set()
        self.last_cycle: datetime | None = None
        self.last_error: str | None = None
        self._kill_seen = state.trading.kill_switch_engaged
        self._feed_alerted = False
        self._feed_down_since: datetime | None = None
        self._summary_day: Any = None
        b = state.broker
        if isinstance(b, SimBroker):
            b.trade_listeners.append(self._on_trade)

    @property
    def config(self) -> AutopilotConfig:
        return AutopilotConfig.model_validate(self.state.trading.autopilot_config or {})

    def _alert(self, text: str) -> None:
        if self.state.notifier is not None:
            self.state.notifier.notify(text)

    # ------------------------------------------------------------------ main cycle
    async def cycle(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        self.last_cycle = now
        st = self.state
        b = st.broker
        self._watch(now)
        if not isinstance(b, SimBroker) or b.mode is not TradingMode.PAPER:
            return  # never anything but simulated money
        await self.manage(now)
        if not st.trading.autopilot_enabled:
            return
        cfg = self.config
        async with st.sessionmaker() as s:
            seen = set((await s.execute(select(PaperOrderRow.opportunity_id))).scalars())
            rows = (
                (
                    await s.execute(
                        select(OpportunityRow)
                        .where(
                            OpportunityRow.status == "active",
                            OpportunityRow.detected_at >= now - timedelta(days=2),
                        )
                        .order_by(OpportunityRow.detected_at)
                    )
                )
                .scalars()
                .all()
            )
        for row in rows:
            if row.id not in seen and st.trading.autopilot_enabled:
                await self.consider(row, cfg, now)

    async def consider(self, row: OpportunityRow, cfg: AutopilotConfig, now: datetime) -> PaperOrderRow:
        st = self.state
        p = row.payload
        tf_s = TIMEFRAMES.get(row.timeframe, 300)
        bar_close = row.detected_at + timedelta(seconds=tf_s)
        rec = PaperOrderRow(
            opportunity_id=row.id,
            considered_at=now,
            setup=row.setup,
            timeframe=row.timeframe,
            direction=row.direction,
            quality=int(row.quality),
            signal_at=row.detected_at,
            status="skipped",
            reasons=[],
            intended_entry=Decimal(str(p["entry"])),
            stop=Decimal(str(p["stop"])),
            target=Decimal(str(p["targets"][0])),
        )
        skip: list[str] = []
        if row.setup not in cfg.setups:
            skip.append("stratégie non sélectionnée")
        if row.timeframe not in cfg.timeframes:
            skip.append("unité de temps non sélectionnée")
        if row.quality < cfg.min_quality:
            skip.append(f"qualité {row.quality} < {cfg.min_quality}")
        if now > bar_close + timedelta(seconds=tf_s * cfg.freshness_bars):
            skip.append("idée trop ancienne")
        q = st.market.last.get("XAUUSD")
        if q is None:
            skip.append("aucun prix en direct")
        if skip:
            rec.reasons = skip
            return await self._save(rec)
        assert q is not None
        side = Side.BUY if row.direction == "bullish" else Side.SELL
        price = q.ask if side is Side.BUY else q.bid
        d = await st.risk.evaluate_opportunity({**p, "entry": float(price)}, "autopilot", now=now)
        if not d.approved or d.order is None:
            rec.status, rec.reasons = "rejected", list(d.rejections)
            return await self._save(rec)
        try:
            res = await st.broker.place_order(d.order)
        except Exception as e:  # recorded, never raised into the loop
            rec.status, rec.reasons = "failed", [type(e).__name__]
            return await self._save(rec)
        rec.client_order_id = d.order.client_order_id
        if res.status is not OrderStatus.FILLED or res.fill_price is None:
            rec.status, rec.reasons = "failed", [res.reject_reason or res.status.value]
            return await self._save(rec)
        rec.status = "open"
        rec.position_id = str(res.position_id)
        rec.volume, rec.fill_price = res.filled_volume, res.fill_price
        rec.slippage = (res.fill_price - rec.intended_entry) * side.sign
        rec.latency_s = (res.ts - bar_close).total_seconds()
        if d.sizing is not None and d.sizing.risk_amount is not None:
            rec.risk_amount, rec.currency = d.sizing.risk_amount.amount, d.sizing.risk_amount.ccy
        rec.opened_at = res.ts
        rec.max_hold_until = bar_close + timedelta(seconds=tf_s * cfg.max_hold_bars)
        saved = await self._save(rec)
        self._alert(
            f"📈 PAPER · {'Achat' if side is Side.BUY else 'Vente'} {res.filled_volume} lot XAU/USD à {res.fill_price}\n"
            f"{SETUP_FR.get(row.setup, row.setup)} ({row.timeframe}) · stop {rec.stop} · objectif {rec.target}\n"
            f"Risque {rec.risk_amount} {rec.currency}"
        )
        return saved

    async def _save(self, rec: PaperOrderRow) -> PaperOrderRow:
        async with self.state.sessionmaker() as s:
            s.add(rec)
            await s.commit()
            await s.refresh(rec)
        log.info("autopilot_decision", opportunity=rec.opportunity_id, status=rec.status, reasons=rec.reasons)
        return rec

    # ------------------------------------------------------------------ position management
    async def manage(self, now: datetime) -> None:
        st = self.state
        async with st.sessionmaker() as s:
            open_rows = (
                (await s.execute(select(PaperOrderRow).where(PaperOrderRow.status == "open"))).scalars().all()
            )
            if not open_rows:
                return
            cfg, _ = await active_risk_config(s)
        live = {str(p.position_id) for p in await st.broker.get_positions()}
        ny = now.astimezone(NY)
        to_close = (datetime.combine(ny.date(), ROLLOVER, tzinfo=NY) - ny).total_seconds() / 60
        friday = ny.weekday() == 4 and 0 <= to_close <= cfg.weekly_close_buffer_minutes
        for r in open_rows:
            if r.position_id not in live:
                continue
            reason = None
            if r.max_hold_until is not None and now >= r.max_hold_until:
                reason = CloseReason.TIME_EXIT
            elif friday:
                reason = CloseReason.WEEKLY_CLOSE
            if reason is not None and is_market_open(now):
                try:
                    await st.broker.close_position(UUID(r.position_id), reason=reason)
                except Exception as e:
                    log.error("autopilot_close_failed", position=r.position_id, error=type(e).__name__)

    def _on_trade(self, t: ClosedTrade) -> None:
        task = asyncio.get_running_loop().create_task(self._record_close(t))
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def _record_close(self, t: ClosedTrade) -> None:
        async with self.state.sessionmaker() as s:
            rec = (
                await s.execute(select(PaperOrderRow).where(PaperOrderRow.position_id == str(t.position_id)))
            ).scalar_one_or_none()
            if rec is None:
                return
            rec.status, rec.closed_at = "closed", t.closed_at
            rec.close_price, rec.close_reason = t.close_price, t.reason.value
            rec.net_pnl = (rec.net_pnl or Decimal(0)) + t.net_pnl.amount
            if rec.risk_amount:
                rec.r_multiple = round(float(rec.net_pnl / rec.risk_amount), 3)
            await s.commit()
        icon = "✅" if t.net_pnl.amount > 0 else "❌"
        r_txt = f" · {rec.r_multiple:+.2f} R" if rec.r_multiple is not None else ""
        self._alert(
            f"{icon} PAPER · position fermée ({REASON_FR.get(t.reason.value, t.reason.value)})\n"
            f"{t.net_pnl.amount:+.2f} {t.net_pnl.ccy}{r_txt}"
        )

    async def flush(self) -> None:
        if self._writes:
            await asyncio.gather(*list(self._writes), return_exceptions=True)

    # ------------------------------------------------------------------ watchdog alerts
    def _watch(self, now: datetime) -> None:
        st = self.state
        t = st.trading
        if t.kill_switch_engaged and not self._kill_seen:
            self._alert(f"🛑 Arrêt d'urgence engagé par {t.kill_switch_by} : {t.kill_switch_reason}")
        self._kill_seen = t.kill_switch_engaged
        if st.settings.market_data_source != "none" and is_market_open(now):
            q = st.market.last.get("XAUUSD")
            down = st.market.state != "connected" or q is None or (now - q.ts).total_seconds() > 60
            if down:
                self._feed_down_since = self._feed_down_since or now
                if not self._feed_alerted and (now - self._feed_down_since).total_seconds() > 120:
                    self._alert(
                        "⚠️ Flux de prix interrompu depuis plus de 2 minutes : aucune nouvelle position possible."
                    )
                    self._feed_alerted = True
            else:
                if self._feed_alerted:
                    self._alert("✅ Flux de prix rétabli.")
                self._feed_down_since, self._feed_alerted = None, False

    async def daily_summary(self, now: datetime) -> str | None:
        """Once per trading day, shortly after the 17:00 New York rollover."""
        ny = now.astimezone(NY)
        if ny.time() < ROLLOVER or ny.weekday() >= 5 or self._summary_day == ny.date():
            return None
        self._summary_day = ny.date()
        since = datetime.combine(ny.date() - timedelta(days=1), ROLLOVER, tzinfo=NY)
        async with self.state.sessionmaker() as s:
            rows = (
                (
                    await s.execute(
                        select(PaperOrderRow).where(
                            PaperOrderRow.closed_at >= since, PaperOrderRow.status == "closed"
                        )
                    )
                )
                .scalars()
                .all()
            )
            n_seen = len(
                (await s.execute(select(PaperOrderRow.id).where(PaperOrderRow.considered_at >= since))).all()
            )
        net = sum((r.net_pnl or Decimal(0) for r in rows), Decimal(0))
        rs = [r.r_multiple for r in rows if r.r_multiple is not None]
        text = (
            f"📊 Bilan PAPER du {ny:%d/%m} : {len(rows)} trade(s) clôturé(s), {net:+.2f} "
            f"{rows[0].currency if rows else ''} ({sum(rs):+.2f} R) · {n_seen} idée(s) examinée(s)"
        )
        self._alert(text)
        return text

    # ------------------------------------------------------------------ loop
    async def _loop(self) -> None:
        while True:
            try:
                await self.cycle()
                await self.daily_summary(utcnow())
                self.last_error = None
            except Exception as e:  # the loop must survive anything
                self.last_error = type(e).__name__
                log.error("autopilot_cycle_failed", error=self.last_error)
            await asyncio.sleep(self.state.settings.autopilot_interval_s)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()
