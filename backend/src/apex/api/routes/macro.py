"""Fundamental context: latest point-in-time values, sync status, manual refresh."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from apex.api.deps import SessionDep, StateDep, require_role
from apex.db.models import EconomicEvent, MacroObservation, User
from apex.domain.enums import Role
from apex.domain.models import utcnow
from apex.intel.macro import SERIES, SyncReport, load_context
from apex.research.costs import spread_profile

router = APIRouter(prefix="/macro", tags=["macro"])
Viewer = Annotated[User, Depends(require_role(Role.VIEWER))]
Admin = Annotated[User, Depends(require_role(Role.ADMIN))]


@router.get("/context")
async def context(_: Viewer, state: StateDep) -> dict[str, Any]:
    ctx = await load_context(state.sessionmaker)
    snap = ctx.snapshot(utcnow())
    snap["fred_configured"] = state.settings.fred_api_key is not None
    return snap


@router.get("/status")
async def macro_status(_: Viewer, state: StateDep, session: SessionDep) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(
                MacroObservation.series,
                func.count(),
                func.min(MacroObservation.obs_date),
                func.max(MacroObservation.obs_date),
            ).group_by(MacroObservation.series)
        )
    ).all()
    ev = (
        await session.execute(
            select(
                EconomicEvent.source,
                func.count(),
                func.min(EconomicEvent.scheduled_at),
                func.max(EconomicEvent.scheduled_at),
            ).group_by(EconomicEvent.source)
        )
    ).all()
    svc = state.macro
    return {
        "fred_configured": state.settings.fred_api_key is not None,
        "series": [
            {"key": k, "label": SERIES[k].label if k in SERIES else k, "count": n, "first": a, "last": b}
            for k, n, a, b in rows
        ],
        "events": [{"source": src, "count": n, "first": a, "last": b} for src, n, a, b in ev],
        "last_sync": svc.last.to_dict() if svc and svc.last else None,
        "running": bool(svc and svc.running),
    }


@router.post("/sync")
async def sync(_: Admin, state: StateDep) -> dict[str, Any]:
    if state.macro is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "service indisponible")
    if state.macro.running:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "synchronisation déjà en cours")
    rep: SyncReport = await state.macro.sync_now()
    return rep.to_dict()


@router.get("/costs")
async def costs(_: Viewer, state: StateDep) -> dict[str, Any]:
    return await spread_profile(state.sessionmaker)
