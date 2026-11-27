"""Health, system status, account view, kill switch, risk config and position-size calculator."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

import apex
from apex.api.deps import ReAuth, SessionDep, StateDep, require_role, verify_reauth
from apex.api.state import AppState
from apex.core.fx import FxConverter
from apex.db.audit import append_audit
from apex.db.models import BarRow, RiskConfigVersion, User
from apex.domain.enums import CloseReason, Role, Side
from apex.domain.models import AccountInfo, ClosedTrade, Position, Quote, SymbolSpec, utcnow
from apex.market.sessions import ALL_TIMEFRAMES
from apex.risk.config import RiskConfig
from apex.risk.sizing import SizingInput, SizingResult, compute_position_size

router = APIRouter(tags=["system"])

Viewer = Annotated[User, Depends(require_role(Role.VIEWER))]
Trader = Annotated[User, Depends(require_role(Role.TRADER))]
Admin = Annotated[User, Depends(require_role(Role.ADMIN))]


# --------------------------------------------------------------------------- health
@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": apex.__version__}


@router.get("/health/ready")
async def ready(state: StateDep, session: SessionDep) -> dict[str, Any]:
    checks: dict[str, str] = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # report, never raise from a health probe
        checks["database"] = f"error: {type(e).__name__}"
    checks["broker"] = "ok" if state.broker.connected else "disconnected"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, checks)
    return {"status": "ready", "checks": checks}


# --------------------------------------------------------------------------- status
async def active_risk_config(session: SessionDep) -> tuple[RiskConfig, int | None]:
    row = (
        await session.execute(select(RiskConfigVersion).where(RiskConfigVersion.active))
    ).scalar_one_or_none()
    if row is None:
        return RiskConfig(), None
    return RiskConfig.model_validate(row.payload), row.id


class SystemStatus(BaseModel):
    version: str
    mode: str
    broker: dict[str, Any]
    trading_enabled: bool
    kill_switch_engaged: bool
    kill_switch_reason: str | None
    kill_switch_at: datetime | None
    risk_config_hash: str
    risk_config_version: int | None
    market_data: dict[str, Any]


@router.get("/system/status", response_model=SystemStatus)
async def system_status(_: Viewer, state: StateDep, session: SessionDep) -> SystemStatus:
    cfg, ver = await active_risk_config(session)
    t = state.trading
    return SystemStatus(
        version=apex.__version__,
        mode=state.broker.mode.value,
        broker={**state.broker.capabilities.model_dump(), "connected": state.broker.connected},
        trading_enabled=t.trading_enabled,
        kill_switch_engaged=t.kill_switch_engaged,
        kill_switch_reason=t.kill_switch_reason,
        kill_switch_at=t.kill_switch_at,
        risk_config_hash=cfg.config_hash(),
        risk_config_version=ver,
        market_data=state.market.describe(),
    )


@router.get("/account", response_model=AccountInfo)
async def account(_: Viewer, state: StateDep) -> AccountInfo:
    try:
        return await state.broker.get_account_info()
    except (LookupError, RuntimeError) as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"account unavailable: {e}") from None


@router.get("/positions", response_model=list[Position])
async def positions(_: Viewer, state: StateDep) -> list[Position]:
    return await state.broker.get_positions()


# --------------------------------------------------------------------------- kill switch
class KillSwitchIn(BaseModel):
    reason: str = Field(min_length=3, max_length=200)
    close_positions: bool = True


class KillSwitchOut(BaseModel):
    engaged: bool
    closed: list[ClosedTrade]
    errors: list[str]


@router.post("/system/kill-switch", response_model=KillSwitchOut)
async def kill_switch(
    body: KillSwitchIn, user: Trader, state: StateDep, session: SessionDep
) -> KillSwitchOut:
    """Stopping must be easy: trader role, no re-auth. Trading is disabled before anything else."""
    state.trading.engage(user.email, body.reason)
    closed: list[ClosedTrade] = []
    errors: list[str] = []
    if body.close_positions:
        try:
            closed = await state.broker.close_all_positions(CloseReason.KILL_SWITCH)
        except Exception as e:  # never let a broker error hide that the switch is engaged
            errors.append(f"{type(e).__name__}: {e}")
    await append_audit(
        session,
        user.email,
        "kill_switch_engaged",
        details={"reason": body.reason, "closed": len(closed), "errors": errors},
    )
    await session.commit()
    return KillSwitchOut(engaged=True, closed=closed, errors=errors)


@router.post("/system/kill-switch/rearm", status_code=204)
async def rearm(body: ReAuth, user: Admin, state: StateDep, session: SessionDep) -> None:
    verify_reauth(user, body, state)
    state.trading.rearm()
    await append_audit(session, user.email, "kill_switch_rearmed")
    await session.commit()


# --------------------------------------------------------------------------- risk config
class RiskConfigOut(BaseModel):
    version: int | None
    config_hash: str
    config: RiskConfig


@router.get("/risk/config", response_model=RiskConfigOut)
async def get_risk_config(_: Viewer, session: SessionDep) -> RiskConfigOut:
    cfg, ver = await active_risk_config(session)
    return RiskConfigOut(version=ver, config_hash=cfg.config_hash(), config=cfg)


class RiskConfigUpdate(BaseModel):
    reauth: ReAuth
    config: RiskConfig


@router.put("/risk/config", response_model=RiskConfigOut)
async def put_risk_config(
    body: RiskConfigUpdate, user: Admin, state: StateDep, session: SessionDep
) -> RiskConfigOut:
    """New immutable version. Human admin only, with password + TOTP. No other write path exists."""
    verify_reauth(user, body.reauth, state)
    before, _ = await active_risk_config(session)
    await session.execute(update(RiskConfigVersion).values(active=False).where(RiskConfigVersion.active))
    row = RiskConfigVersion(
        payload=body.config.model_dump(mode="json"),
        config_hash=body.config.config_hash(),
        created_by=user.email,
        active=True,
    )
    session.add(row)
    await session.flush()
    await append_audit(
        session,
        user.email,
        "risk_config_changed",
        target=f"risk_config:{row.id}",
        details={"before": before.model_dump(mode="json"), "after": body.config.model_dump(mode="json")},
    )
    await session.commit()
    return RiskConfigOut(version=row.id, config_hash=row.config_hash, config=body.config)


# --------------------------------------------------------------------------- calculator
class SizeCalcIn(BaseModel):
    side: Side
    entry: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    risk_pct: Decimal | None = None  # default: active risk config
    quote_to_account_rate: Decimal | None = Field(
        default=None, gt=0, description="Manual USD->account rate for what-if use when no live FX feed exists"
    )


@router.post("/risk/position-size", response_model=SizingResult)
async def position_size(body: SizeCalcIn, _: Viewer, state: StateDep, session: SessionDep) -> SizingResult:
    """What-if calculator using the live account. It never places an order."""
    cfg, _v = await active_risk_config(session)
    info = await state.broker.get_account_info()
    spec = await state.broker.get_symbol_spec("XAUUSD")
    now = utcnow()
    fx: FxConverter = getattr(state.broker, "fx", FxConverter())
    if body.quote_to_account_rate is not None and info.currency != spec.quote_ccy:
        fx = FxConverter(max_age=timedelta(minutes=1))
        r = body.quote_to_account_rate
        fx.update(Quote(symbol=spec.quote_ccy + info.currency, bid=r, ask=r, ts=now))
    inp = SizingInput(
        side=body.side,
        entry=body.entry,
        stop_loss=body.stop_loss,
        risk_pct=body.risk_pct if body.risk_pct is not None else cfg.risk_per_trade_pct,
        equity=info.equity,
        balance=info.balance,
        free_margin=info.free_margin,
        leverage=info.leverage,
        max_volume=cfg.max_volume_lots,
        min_free_margin_ratio=cfg.min_free_margin_ratio,
    )
    return compute_position_size(inp, spec, fx, now)


# --------------------------------------------------------------------------- market
class QuoteOut(BaseModel):
    symbol: str
    bid: Decimal
    ask: Decimal
    spread: Decimal
    ts: datetime
    age_s: float


@router.get("/market/quote", response_model=QuoteOut)
async def market_quote(_: Viewer, state: StateDep, symbol: str = "XAUUSD") -> QuoteOut:
    q = state.market.last.get(symbol)
    if q is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no live quote (market data not connected)")
    return QuoteOut(
        symbol=q.symbol,
        bid=q.bid,
        ask=q.ask,
        spread=q.spread,
        ts=q.ts,
        age_s=round((utcnow() - q.ts).total_seconds(), 1),
    )


class SpecOut(BaseModel):
    source: str
    spec: SymbolSpec
    commission_per_lot_side_at_price: Decimal | None


@router.get("/market/spec", response_model=SpecOut)
async def market_spec(_: Viewer, state: StateDep) -> SpecOut:
    spec = await state.broker.get_symbol_spec("XAUUSD")
    q = state.market.last.get("XAUUSD")
    return SpecOut(
        source=state.market.spec_source,
        spec=spec,
        commission_per_lot_side_at_price=spec.commission_for(Decimal(1), q.mid) if q else None,
    )


class BarOut(BaseModel):
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    spread_avg: Decimal
    spread_max: Decimal
    tick_count: int
    complete: bool


@router.get("/market/bars", response_model=list[BarOut])
async def market_bars(
    _: Viewer,
    state: StateDep,
    session: SessionDep,
    timeframe: str = "5m",
    limit: int = 300,
    symbol: str = "XAUUSD",
) -> list[BarOut]:
    """Completed bars (database + in-memory) followed by the current partial bar. Oldest first."""
    if timeframe not in ALL_TIMEFRAMES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"timeframe must be one of {ALL_TIMEFRAMES}"
        )
    return await collect_bars(state, session, timeframe, max(1, min(limit, 2000)), symbol)


async def collect_bars(
    state: AppState, session: AsyncSession, timeframe: str, limit: int, symbol: str = "XAUUSD"
) -> list[BarOut]:
    """Shared by the bars endpoint and the analysis engine (same merge rules everywhere)."""
    rows = (
        (
            await session.execute(
                select(BarRow)
                .where(BarRow.symbol == symbol, BarRow.timeframe == timeframe)
                .order_by(BarRow.start.desc())
                .limit(limit * 2)
            )
        )
        .scalars()
        .all()
    )
    # Several sources can cover the same period (e.g. Dukascopy history + live broker feed):
    # the live source wins, other sources only fill gaps.
    live = state.market.source
    rows = sorted(rows, key=lambda r: r.source == live)  # live rows last -> they overwrite
    by_start: dict[datetime, BarOut] = {
        r.start: BarOut(
            start=r.start,
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            spread_avg=r.spread_avg,
            spread_max=r.spread_max,
            tick_count=r.tick_count,
            complete=True,
        )
        for r in rows
    }
    feed = state.market
    mem = list(feed.recent.get(timeframe, ()))
    partial = feed.bars.partial(timeframe) if symbol == feed.bars.symbol else None
    live_stored = {r.start for r in rows if r.source == live}
    for b in [*mem, *([partial] if partial else [])]:
        fresh = BarOut(**{k: v for k, v in b.as_dict().items() if k not in ("symbol", "timeframe")})
        prior = by_start.get(b.start)
        if prior is not None and b.start not in live_stored and prior.start <= b.start:
            # The feed started mid-period: the stored (history) bar knows the true open and earlier extremes.
            fresh = fresh.model_copy(
                update={
                    "open": prior.open,
                    "high": max(prior.high, fresh.high),
                    "low": min(prior.low, fresh.low),
                    "tick_count": prior.tick_count + fresh.tick_count,
                }
            )
        by_start[b.start] = fresh
    return [by_start[k] for k in sorted(by_start)][-limit:]


@router.get("/market/quality")
async def market_quality(_: Viewer, state: StateDep, symbol: str = "XAUUSD") -> dict[str, Any]:
    r = state.market.quality.report(symbol)
    return {**r.__dict__, "spread": r.spread, "spread_median": r.spread_median}
