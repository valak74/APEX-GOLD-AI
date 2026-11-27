"""Backtests: start a run (background), list runs, read a report."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from apex.api.deps import SessionDep, StateDep, require_role
from apex.api.routes.system import active_risk_config
from apex.backtest.service import BacktestRequest, run_backtest, store
from apex.core.logging import get_logger
from apex.db.models import BacktestRow, User
from apex.domain.enums import Role

router = APIRouter(prefix="/backtests", tags=["backtests"])
Viewer = Annotated[User, Depends(require_role(Role.VIEWER))]
Researcher = Annotated[User, Depends(require_role(Role.RESEARCHER))]
log = get_logger(__name__)
_running: set[asyncio.Task[None]] = set()


@router.post("", status_code=202)
async def start_backtest(
    body: BacktestRequest, user: Researcher, state: StateDep, session: SessionDep
) -> dict[str, Any]:
    if sum(1 for t in _running if not t.done()) >= 2:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "deux backtests sont déjà en cours")
    cfg, _ = await active_risk_config(session)
    spec = await state.broker.get_symbol_spec("XAUUSD")
    row = BacktestRow(created_by=user.email, status="running", request=body.model_dump(mode="json"))
    session.add(row)
    await session.commit()
    rid = row.id

    async def job() -> None:
        try:
            report = await run_backtest(state.sessionmaker, body, spec, cfg, state.market.spec_source)
        except Exception as e:  # report the failure on the run, never crash the API
            log.error("backtest_failed", id=rid, error=type(e).__name__)
            report = {"error": f"échec : {type(e).__name__}"}
        await store(state.sessionmaker, rid, report)

    task = asyncio.create_task(job())
    _running.add(task)
    task.add_done_callback(_running.discard)
    return {"id": rid, "status": "running"}


@router.get("")
async def list_backtests(_: Viewer, session: SessionDep, limit: int = 30) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(BacktestRow).order_by(BacktestRow.id.desc()).limit(max(1, min(limit, 200)))
        )
    ).scalars()
    out = []
    for r in rows:
        rep = r.report or {}
        s = rep.get("summary", {})
        out.append(
            {
                "id": r.id,
                "created_at": r.created_at,
                "created_by": r.created_by,
                "status": r.status,
                "error": r.error,
                "request": r.request,
                "trades": s.get("trades"),
                "expectancy_r": s.get("expectancy_r"),
                "profit_factor": s.get("profit_factor"),
                "validated": (rep.get("validation") or {}).get("validated"),
                "data": rep.get("data"),
            }
        )
    return out


@router.get("/{bt_id}")
async def get_backtest(bt_id: int, _: Viewer, session: SessionDep) -> dict[str, Any]:
    r = await session.get(BacktestRow, bt_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "backtest not found")
    return {
        "id": r.id,
        "status": r.status,
        "error": r.error,
        "request": r.request,
        "report": r.report,
        "created_at": r.created_at,
        "finished_at": r.finished_at,
    }
