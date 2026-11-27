"""PAPER autopilot: status, switch and selection, journal, evidence report, divergence, alerts."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from apex.api.autopilot import AutopilotConfig
from apex.api.deps import SessionDep, StateDep, require_role
from apex.api.paper_report import divergence, report
from apex.api.paper_state import save_trading_state
from apex.db.audit import append_audit
from apex.db.models import PaperOrderRow, User
from apex.domain.enums import Role, TradingMode

router = APIRouter(prefix="/paper", tags=["paper"])
Viewer = Annotated[User, Depends(require_role(Role.VIEWER))]
Trader = Annotated[User, Depends(require_role(Role.TRADER))]


class AutopilotIn(BaseModel):
    enabled: bool
    config: AutopilotConfig | None = None


def _status(state: Any) -> dict[str, Any]:
    ap = state.autopilot
    t = state.trading
    return {
        "mode": state.broker.mode.value,
        "available": state.broker.mode is TradingMode.PAPER,
        "enabled": t.autopilot_enabled,
        "trading_enabled": t.trading_enabled,
        "kill_switch": t.kill_switch_engaged,
        "config": AutopilotConfig.model_validate(t.autopilot_config or {}).model_dump(),
        "last_cycle": ap.last_cycle if ap else None,
        "last_error": ap.last_error if ap else None,
        "alerts": state.notifier.status() if state.notifier else {"configured": False, "recent": []},
    }


@router.get("/status")
async def paper_status(_: Viewer, state: StateDep) -> dict[str, Any]:
    return _status(state)


@router.post("/autopilot")
async def set_autopilot(
    body: AutopilotIn, user: Trader, state: StateDep, session: SessionDep
) -> dict[str, Any]:
    """Switching OFF is always allowed. Switching ON: PAPER only, kill switch released, trading enabled."""
    t = state.trading
    if body.enabled:
        if state.broker.mode is not TradingMode.PAPER:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "le pilote automatique ne fonctionne qu'en mode PAPER"
            )
        if t.kill_switch_engaged:
            raise HTTPException(status.HTTP_409_CONFLICT, "arrêt d'urgence engagé : réarmement admin requis")
        if not t.trading_enabled:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "activez d'abord le trading (admin, mot de passe + code)"
            )
    if body.config is not None:
        t.autopilot_config = body.config.model_dump()
    t.autopilot_enabled = body.enabled
    await append_audit(
        session,
        user.email,
        "autopilot_enabled" if body.enabled else "autopilot_disabled",
        details={"config": t.autopilot_config},
    )
    await session.commit()
    await save_trading_state(state)
    if state.notifier:
        state.notifier.notify(
            f"{'▶️ Pilote PAPER activé' if body.enabled else '⏸ Pilote PAPER arrêté'} par {user.email}"
        )
    return _status(state)


@router.get("/journal")
async def journal(
    _: Viewer,
    session: SessionDep,
    limit: int = 100,
    status_: Annotated[str | None, Query(alias="status")] = None,
) -> list[dict[str, Any]]:
    q = select(PaperOrderRow).order_by(PaperOrderRow.considered_at.desc()).limit(max(1, min(limit, 1000)))
    if status_:
        q = q.where(PaperOrderRow.status == status_)
    rows = (await session.execute(q)).scalars().all()
    cols = [c.name for c in PaperOrderRow.__table__.columns]
    return [{c: getattr(r, c) for c in cols} for r in rows]


@router.get("/report")
async def paper_report(_: Viewer, state: StateDep) -> dict[str, Any]:
    return await report(state.sessionmaker)


@router.get("/divergence")
async def paper_divergence(
    _: Viewer, state: StateDep, setup: str = "liquidity_sweep_reversal", timeframe: str = "5m"
) -> dict[str, Any]:
    if setup not in ("liquidity_sweep_reversal", "trend_pullback") or timeframe not in ("5m", "15m"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "stratégie ou unité de temps inconnue")
    return await divergence(state.sessionmaker, setup, timeframe)


@router.post("/alerts/test")
async def test_alert(user: Trader, state: StateDep) -> dict[str, Any]:
    n = state.notifier
    if n is None or not n.configured:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Telegram non configuré (APEX_TELEGRAM_BOT_TOKEN, APEX_TELEGRAM_CHAT_ID)",
        )
    ok = await n.send(f"🔔 Test d'alerte APEX GOLD demandé par {user.email}")
    return {"sent": ok, "error": n.last_error}
