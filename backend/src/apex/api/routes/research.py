"""Research: reserved holdout and edge diagnostics."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from apex.api.deps import SessionDep, StateDep, require_role
from apex.backtest.service import store
from apex.core.logging import get_logger
from apex.db.models import BacktestRow, User
from apex.domain.enums import Role
from apex.research.holdout import get_holdout, holdout_consultations
from apex.research.service import DiagnosticRequest, run_diagnostic

router = APIRouter(prefix="/research", tags=["research"])
Viewer = Annotated[User, Depends(require_role(Role.VIEWER))]
Researcher = Annotated[User, Depends(require_role(Role.RESEARCHER))]
log = get_logger(__name__)
_running: set[asyncio.Task[None]] = set()


@router.get("/holdout")
async def holdout(_: Viewer, state: StateDep) -> dict[str, Any]:
    h = await get_holdout(state.sessionmaker)
    return {
        "start": h.start if h else None,
        "created_at": h.created_at if h else None,
        "note": h.note if h else None,
        "consultations": await holdout_consultations(state.sessionmaker),
    }


@router.post("/diagnostics", status_code=202)
async def start_diagnostic(
    body: DiagnosticRequest, user: Researcher, state: StateDep, session: SessionDep
) -> dict[str, Any]:
    if any(not t.done() for t in _running):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "un diagnostic est déjà en cours")
    spec = await state.broker.get_symbol_spec("XAUUSD")
    row = BacktestRow(
        kind="diagnostic", created_by=user.email, status="running", request=body.model_dump(mode="json")
    )
    session.add(row)
    await session.commit()
    rid, actor = row.id, user.email

    async def job() -> None:
        try:
            rep = await run_diagnostic(state.sessionmaker, body, spec, state.market.spec_source, actor)
        except Exception as e:  # recorded on the run, never crashes the API
            log.error("diagnostic_failed", id=rid, error=type(e).__name__)
            rep = {"error": f"échec : {type(e).__name__}"}
        await store(state.sessionmaker, rid, rep, ok_key="findings")

    task = asyncio.create_task(job())
    _running.add(task)
    task.add_done_callback(_running.discard)
    return {"id": rid, "status": "running"}


@router.get("/diagnostics")
async def list_diagnostics(_: Viewer, session: SessionDep, limit: int = 30) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(BacktestRow)
            .where(BacktestRow.kind == "diagnostic")
            .order_by(BacktestRow.id.desc())
            .limit(max(1, min(limit, 200)))
        )
    ).scalars()
    out = []
    for r in rows:
        rep = r.report or {}
        out.append(
            {
                "id": r.id,
                "created_at": r.created_at,
                "created_by": r.created_by,
                "status": r.status,
                "error": r.error,
                "request": r.request,
                "tests_total": rep.get("tests_total"),
                "retained_total": rep.get("retained_total"),
                "edges_total": rep.get("edges_total"),
                "data": rep.get("data"),
            }
        )
    return out


@router.get("/diagnostics/{diag_id}")
async def get_diagnostic(diag_id: int, _: Viewer, session: SessionDep) -> dict[str, Any]:
    r = await session.get(BacktestRow, diag_id)
    if r is None or r.kind != "diagnostic":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "diagnostic not found")
    return {
        "id": r.id,
        "status": r.status,
        "error": r.error,
        "request": r.request,
        "report": r.report,
        "created_at": r.created_at,
        "finished_at": r.finished_at,
    }
