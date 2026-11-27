"""Risk service: assembles the engine's snapshot from live state, records trades and decisions, and runs the
drawdown guard (automatic kill switch).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from apex.analysis.indicators import Candles, atr
from apex.analysis.regime import classify
from apex.analysis.structure import analyze_structure
from apex.api.routes.system import active_risk_config, collect_bars
from apex.api.state import AppState
from apex.broker.sim import SimBroker
from apex.core.fx import FxConverter
from apex.core.logging import get_logger
from apex.db.audit import append_audit
from apex.db.models import RiskDecisionRow, TradeRow
from apex.domain.enums import CloseReason, Side
from apex.domain.models import ClosedTrade, utcnow
from apex.intel.calendar import evaluate_lockout, upcoming
from apex.risk.config import RiskConfig
from apex.risk.engine import Proposal, RiskDecision, Snapshot, account_metrics, client_order_id, evaluate
from apex.risk.ledger import TradeRecord, compute_stats

log = get_logger(__name__)


def _jsonable(x: Any) -> Any:
    import json

    return json.loads(json.dumps(x, default=str))


class RiskService:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self._task: asyncio.Task[None] | None = None
        self._writes: set[asyncio.Task[None]] = set()
        b = state.broker
        if isinstance(b, SimBroker):
            b.trade_listeners.append(self._on_trade)

    # ------------------------------------------------------------------ trade journal
    def _on_trade(self, t: ClosedTrade) -> None:
        task = asyncio.get_running_loop().create_task(self.record_trade(t))
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def record_trade(self, t: ClosedTrade) -> None:
        b = self.state.broker
        async with self.state.sessionmaker() as s:
            s.add(
                TradeRow(
                    mode=b.mode.value,
                    account_id=getattr(b, "account_id", "?"),
                    position_id=str(t.position_id),
                    symbol=t.symbol,
                    side=t.side.value,
                    volume=t.volume,
                    open_price=t.open_price,
                    close_price=t.close_price,
                    opened_at=t.opened_at,
                    closed_at=t.closed_at,
                    reason=t.reason.value,
                    gross_pnl=t.gross_pnl.amount,
                    commission=t.commission.amount,
                    net_pnl=t.net_pnl.amount,
                    currency=t.net_pnl.ccy,
                )
            )
            await s.commit()

    async def flush(self) -> None:
        if self._writes:
            await asyncio.gather(*list(self._writes), return_exceptions=True)

    # ------------------------------------------------------------------ snapshot
    async def snapshot(
        self, session: Any, cfg: RiskConfig, now: datetime | None = None, timeframe: str = "5m"
    ) -> Snapshot:
        now = now or utcnow()
        st, b = self.state, self.state.broker
        account = await b.get_account_info()
        positions = await b.get_positions()
        spec = await b.get_symbol_spec("XAUUSD")
        trades = (
            await session.execute(
                select(TradeRow.closed_at, TradeRow.net_pnl).where(TradeRow.mode == b.mode.value)
            )
        ).all()
        initial = Decimal(st.settings.paper_initial_balance)
        ledger = compute_stats([TradeRecord(c, Decimal(p)) for c, p in trades], initial, now)
        q = st.market.quality.report("XAUUSD", now)
        lock = evaluate_lockout(
            await upcoming(session, now, days=2, back_hours=6),
            now,
            cfg.news_lockout_minutes_before,
            cfg.news_lockout_minutes_after,
        )
        bars = await collect_bars(st, session, timeframe, 300)
        if bars and not bars[-1].complete:
            bars = bars[:-1]
        c = Candles.from_rows(bars, timeframe)
        a = atr(c)
        atr_v = Decimal(str(round(float(a[-1]), 4))) if len(c) and a[-1] == a[-1] else None  # NaN-safe
        mult = Decimal(1)
        if len(c) >= 60:
            mult = Decimal(str(classify(c, analyze_structure(c)).risk_multiplier))
        fx: FxConverter = b.fx if isinstance(b, SimBroker) else FxConverter()
        return Snapshot(
            now=now,
            mode=b.mode,
            trading_enabled=st.trading.trading_enabled,
            kill_switch=st.trading.kill_switch_engaged,
            account=account,
            positions=positions,
            quote=st.market.last.get("XAUUSD"),
            spec=spec,
            fx=fx,
            ledger=ledger,
            data_quality_ok=q.tradeable,
            data_quality_issues=q.issues,
            news_lock_reason=lock.reason if lock.locked else None,
            atr=atr_v,
            regime_multiplier=mult,
        )

    # ------------------------------------------------------------------ decisions
    async def evaluate_opportunity(self, opp: dict[str, Any], actor: str) -> RiskDecision:
        async with self.state.sessionmaker() as session:
            cfg, _ = await active_risk_config(session)
            snap = await self.snapshot(session, cfg, timeframe=opp.get("timeframe", "5m"))
            prop = Proposal(
                proposal_id=opp["id"],
                symbol="XAUUSD",
                side=Side.BUY if opp["direction"] == "bullish" else Side.SELL,
                entry=Decimal(str(opp["entry"])),
                stop_loss=Decimal(str(opp["stop"])),
                take_profit=Decimal(str(opp["targets"][0])),
            )
            # an idea is executed at most once: the order id is derived from the proposal id
            submitted = (
                await self.state.broker.get_order_status(client_order_id(prop.proposal_id)) is not None
            )
            if submitted:
                snap = Snapshot(**{**snap.__dict__, "already_submitted": True})
            d = evaluate(prop, snap, cfg)
            payload = _jsonable(asdict(d))
            session.add(
                RiskDecisionRow(
                    proposal_id=d.proposal_id,
                    decided_at=d.decided_at,
                    approved=d.approved,
                    critical=d.critical,
                    rejections=d.rejections,
                    config_hash=d.config_hash,
                    payload=payload,
                )
            )
            await append_audit(
                session,
                actor,
                "risk_evaluated",
                target=f"proposal:{d.proposal_id}",
                details={"approved": d.approved, "rejections": d.rejections},
            )
            await session.commit()
            return d

    async def status(self) -> dict[str, Any]:
        async with self.state.sessionmaker() as session:
            cfg, _ = await active_risk_config(session)
            snap = await self.snapshot(session, cfg)
        m = account_metrics(snap, cfg)
        led = snap.ledger
        blocks = []
        if snap.kill_switch:
            blocks.append("arrêt d'urgence engagé")
        if not snap.trading_enabled:
            blocks.append("trading non activé")
        if snap.news_lock_reason:
            blocks.append(snap.news_lock_reason)
        if not snap.data_quality_ok:
            blocks.append("qualité des données insuffisante")
        if m.daily_loss >= m.daily_cap:
            blocks.append("perte maximale du jour atteinte")
        if m.weekly_loss >= m.weekly_cap:
            blocks.append("perte maximale de la semaine atteinte")
        if m.drawdown >= cfg.max_total_drawdown_pct:
            blocks.append("drawdown maximal atteint")
        if led.consecutive_losses >= cfg.max_consecutive_losses:
            blocks.append("série de pertes maximale atteinte")
        if m.opened_today >= cfg.max_trades_per_day:
            blocks.append("nombre maximal de trades du jour atteint")
        ccy = snap.account.currency
        return {
            "currency": ccy,
            "entry_allowed": not blocks,
            "blocks": blocks,
            "realized_today": led.realized_today,
            "realized_week": led.realized_week,
            "daily_loss": m.daily_loss,
            "daily_cap": m.daily_cap,
            "remaining_daily": m.remaining_daily,
            "weekly_loss": m.weekly_loss,
            "weekly_cap": m.weekly_cap,
            "drawdown": m.drawdown,
            "max_drawdown": cfg.max_total_drawdown_pct,
            "peak_balance": led.peak_balance,
            "trades_today": m.opened_today,
            "max_trades": cfg.max_trades_per_day,
            "consecutive_losses": led.consecutive_losses,
            "max_consecutive_losses": cfg.max_consecutive_losses,
            "open_positions": len(snap.positions),
            "max_open_positions": cfg.max_open_positions,
            "regime_multiplier": snap.regime_multiplier,
        }

    # ------------------------------------------------------------------ guard
    async def guard_once(self) -> bool:
        """Engage the kill switch automatically at the maximum drawdown. Returns True if it fired."""
        st = self.state
        if st.trading.kill_switch_engaged:
            return False
        async with st.sessionmaker() as session:
            cfg, _ = await active_risk_config(session)
            snap = await self.snapshot(session, cfg)
            m = account_metrics(snap, cfg)
            if m.drawdown < cfg.max_total_drawdown_pct:
                return False
            reason = f"drawdown maximal atteint ({m.drawdown * 100:.2f} %)"
            st.trading.engage("risk-guard", reason)
            closed, errors = [], []
            try:
                closed = await st.broker.close_all_positions(CloseReason.KILL_SWITCH)
            except Exception as e:
                errors.append(type(e).__name__)
            await append_audit(
                session,
                "risk-guard",
                "kill_switch_engaged",
                details={"reason": reason, "closed": len(closed), "errors": errors},
            )
            await session.commit()
        log.warning("risk_guard_kill_switch", reason=reason)
        return True

    async def _loop(self, every: float) -> None:
        while True:
            try:
                await self.guard_once()
            except Exception as e:  # the guard must keep running
                log.error("risk_guard_failed", error=type(e).__name__)
            await asyncio.sleep(every)

    def start(self, every: float = 5.0) -> None:
        self._task = asyncio.create_task(self._loop(every))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()
