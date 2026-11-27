"""Risk engine endpoints: live risk status, evaluating an opportunity, decision history."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from apex.api.deps import SessionDep, StateDep, require_role
from apex.db.models import OpportunityRow, RiskDecisionRow, User
from apex.domain.enums import Role

router = APIRouter(prefix="/risk", tags=["risk"])
Viewer = Annotated[User, Depends(require_role(Role.VIEWER))]
Trader = Annotated[User, Depends(require_role(Role.TRADER))]


@router.get("/status")
async def risk_status(_: Viewer, state: StateDep) -> dict[str, Any]:
    out: dict[str, Any] = await state.risk.status()
    return out


@router.post("/evaluate/{opportunity_id}")
async def evaluate_opportunity(
    opportunity_id: str, user: Trader, state: StateDep, session: SessionDep
) -> dict[str, Any]:
    """Ask the risk engine whether this opportunity could be traded now. Places no order."""
    row = await session.get(OpportunityRow, opportunity_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    if row.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, f"opportunity is {row.status}")
    d = await state.risk.evaluate_opportunity(row.payload, user.email)
    return asdict(d)


@router.get("/decisions")
async def decisions(_: Viewer, session: SessionDep, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(RiskDecisionRow).order_by(RiskDecisionRow.decided_at.desc()).limit(max(1, min(limit, 200)))
        )
    ).scalars()
    return [r.payload for r in rows]
