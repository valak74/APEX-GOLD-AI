"""Runs edge diagnostics on research data (everything before the reserved holdout)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.backtest.service import load_history
from apex.db.models import BacktestRow
from apex.domain.models import SymbolSpec
from apex.research.diagnostics import diagnose
from apex.research.holdout import ensure_holdout

TF = Literal["5m", "15m", "1h"]


def _all_tf() -> list[TF]:
    return ["5m", "15m", "1h"]


class DiagnosticRequest(BaseModel):
    timeframes: list[TF] = Field(default_factory=_all_tf, min_length=1)
    start: datetime | None = None
    slippage: float = Field(0.05, ge=0.0, le=5.0)


async def run_diagnostic(
    maker: async_sessionmaker[AsyncSession],
    req: DiagnosticRequest,
    spec: SymbolSpec,
    spec_source: str,
    actor: str = "system",
) -> dict[str, Any]:
    hold = await ensure_holdout(maker, actor)
    c, spreads, sources = await load_history(maker, "5m", req.start, hold.start if hold else None)
    if len(c) < 1000:
        return {"error": f"historique insuffisant : {len(c)} bougies 5m de recherche (importez Dukascopy)"}
    async with maker() as s:
        previous = (
            await s.execute(
                select(func.count())
                .select_from(BacktestRow)
                .where(BacktestRow.kind == "diagnostic", BacktestRow.status == "done")
            )
        ).scalar_one()
    rep = await asyncio.to_thread(diagnose, c, spreads, spec, tuple(req.timeframes), req.slippage)
    rep["data"] = {
        "bars_5m": len(c),
        "start": c.t[0],
        "end": c.t[-1],
        "sources": sources,
        "holdout_start": hold.start if hold else None,
        "holdout_note": hold.note if hold else "aucune période réservée : historique de moins de 60 jours",
        "previous_diagnostics": int(previous),
    }
    rep["spec"] = {"source": spec_source, "commission": f"{spec.commission_type} {spec.commission_rate}"}
    out: dict[str, Any] = json.loads(json.dumps(rep, default=str))
    return out
