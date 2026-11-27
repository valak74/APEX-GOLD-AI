"""Market analysis (MTF context, chart overlays, pattern statistics) and economic calendar endpoints.

All analysis is computed from the same bars the chart shows (``collect_bars``) and runs in a worker thread so
it can never stall the quote pipeline.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from apex.analysis.engine import analyze_tf, mtf_context
from apex.analysis.indicators import Candles
from apex.analysis.levels import reference_levels
from apex.analysis.liquidity import equal_levels, fair_value_gaps, order_blocks, sweeps
from apex.analysis.stats import event_study
from apex.analysis.structure import analyze_structure
from apex.api.deps import ReAuth, SessionDep, StateDep, require_role, verify_reauth
from apex.api.routes.system import active_risk_config, collect_bars
from apex.db.audit import append_audit
from apex.db.models import User
from apex.domain.enums import Role
from apex.domain.models import utcnow
from apex.intel.calendar import EventIn, evaluate_lockout, import_events, upcoming

router = APIRouter(tags=["analysis"])
Viewer = Annotated[User, Depends(require_role(Role.VIEWER))]
Admin = Annotated[User, Depends(require_role(Role.ADMIN))]

MTF_FRAMES = {"1d": 200, "4h": 300, "1h": 300, "15m": 300, "5m": 400}
LEVELS_TF, LEVELS_BARS = "15m", 2000  # ~3 weeks of 15m bars: previous week + sessions


async def _candles(state: Any, session: Any, tf: str, limit: int) -> Candles:
    return Candles.from_rows(await collect_bars(state, session, tf, limit), tf)


@router.get("/analysis/context")
async def analysis_context(_: Viewer, state: StateDep, session: SessionDep) -> dict[str, Any]:
    frames = {tf: await _candles(state, session, tf, n) for tf, n in MTF_FRAMES.items()}
    intraday = await _candles(state, session, LEVELS_TF, LEVELS_BARS)
    ctx = await asyncio.to_thread(mtf_context, frames, intraday, utcnow())
    return asdict(ctx)


@router.get("/analysis/overlays")
async def analysis_overlays(
    _: Viewer, state: StateDep, session: SessionDep, timeframe: str = "5m"
) -> dict[str, Any]:
    """What the chart draws: structure events, active zones, liquidity pools, reference levels."""
    if timeframe not in MTF_FRAMES and timeframe != "1m":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unsupported timeframe")
    c = await _candles(state, session, timeframe, 400)
    intraday = await _candles(state, session, LEVELS_TF, LEVELS_BARS)

    def compute() -> dict[str, Any]:
        a = analyze_tf(c, max_items=8)
        lv = [asdict(x) for x in reference_levels(intraday, utcnow())] if len(intraday) else []
        return {
            "timeframe": timeframe,
            "events": a.events,
            "fvgs": a.active_fvgs,
            "order_blocks": a.active_order_blocks,
            "pools": a.liquidity_pools,
            "sweeps": a.recent_sweeps,
            "levels": lv,
            "regime": a.regime,
        }

    return await asyncio.to_thread(compute)


Pattern = Literal["bos", "choch", "fvg", "sweep", "order_block"]


@router.get("/analysis/stats")
async def analysis_stats(
    _: Viewer,
    state: StateDep,
    session: SessionDep,
    pattern: Pattern = "sweep",
    timeframe: str = "15m",
    bars: int = 2000,
) -> dict[str, Any]:
    """Historical behaviour after each detected pattern (descriptive: no costs, no execution model)."""
    c = await _candles(state, session, timeframe, max(100, min(bars, 2000)))

    def compute() -> dict[str, Any]:
        st = analyze_structure(c)
        if pattern in ("bos", "choch"):
            ev = [(e.idx, e.direction) for e in st.events if e.kind.lower() == pattern]
        elif pattern == "fvg":
            ev = [(z.idx, z.direction) for z in fair_value_gaps(c)]
        elif pattern == "order_block":
            ev = [(z.idx, z.direction) for z in order_blocks(c, st)]
        else:
            ev = [(s.idx, s.direction) for s in sweeps(c, st, equal_levels(c, st.swings))]
        out = asdict(event_study(c, ev, f"{pattern}@{timeframe}"))
        out["bars_analyzed"] = len(c)
        return out

    return await asyncio.to_thread(compute)


# --------------------------------------------------------------------------- calendar
class EventOut(BaseModel):
    event_code: str
    title: str
    currency: str
    importance: str
    scheduled_at: datetime
    previous: str | None
    forecast: str | None
    actual: str | None


@router.get("/calendar/upcoming", response_model=list[EventOut])
async def calendar_upcoming(_: Viewer, session: SessionDep, days: int = 7) -> list[EventOut]:
    rows = await upcoming(session, utcnow(), days=max(1, min(days, 60)))
    return [EventOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/calendar/lockout")
async def calendar_lockout(_: Viewer, session: SessionDep) -> dict[str, Any]:
    cfg, _v = await active_risk_config(session)
    now = utcnow()
    lock = evaluate_lockout(
        await upcoming(session, now, days=14, back_hours=6),
        now,
        cfg.news_lockout_minutes_before,
        cfg.news_lockout_minutes_after,
    )
    return {
        **asdict(lock),
        "before_min": cfg.news_lockout_minutes_before,
        "after_min": cfg.news_lockout_minutes_after,
    }


class CalendarImport(BaseModel):
    reauth: ReAuth
    source: str = "manual"
    events: list[EventIn]


@router.post("/calendar/import")
async def calendar_import(
    body: CalendarImport, user: Admin, state: StateDep, session: SessionDep
) -> dict[str, int]:
    verify_reauth(user, body.reauth, state)
    n = await import_events(session, body.events, body.source)
    await append_audit(session, user.email, "calendar_imported", details={"source": body.source, "events": n})
    await session.commit()
    return {"imported": n}
