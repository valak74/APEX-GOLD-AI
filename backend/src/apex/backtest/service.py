"""Loads history from the database and runs reports (used by the API and the CLI)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.analysis.indicators import Candles
from apex.backtest.engine import CostModel
from apex.backtest.runner import full_report
from apex.backtest.signals import StrategyParams
from apex.db.models import BacktestRow, BarRow, EconomicEvent
from apex.domain.models import SymbolSpec, utcnow
from apex.intel.calendar import GOLD_SENSITIVE, LOCKOUT_IMPORTANCE
from apex.research.holdout import ensure_holdout
from apex.risk.config import RiskConfig

SOURCE_PRIORITY = {"dukascopy": 0, "ctrader": 1}  # long clean history first; others after


class BacktestRequest(BaseModel):
    setup: Literal["liquidity_sweep_reversal", "trend_pullback"] = "liquidity_sweep_reversal"
    timeframe: Literal["5m", "15m", "1h"] = "5m"
    start: datetime | None = None
    end: datetime | None = None
    stop_buffer_atr: float = Field(0.2, ge=0.0, le=2.0)
    min_quality: int = Field(0, ge=0, le=100)
    require_htf_alignment: bool = False
    max_hold_bars: int = Field(48, ge=1, le=2000)
    sessions: list[Literal["asia", "london", "newyork"]] = ["asia", "london", "newyork"]
    spread_mult: float = Field(1.0, ge=0.5, le=5.0)
    slippage: float = Field(0.05, ge=0.0, le=5.0)
    swap_per_lot_night: float = Field(0.0, ge=-100.0, le=100.0)
    initial_balance: float = Field(10_000.0, ge=100.0)
    final_holdout: bool = False  # final verdict on the reserved period (admin, recorded)


async def load_history(
    maker: async_sessionmaker[AsyncSession],
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
    max_bars: int = 400_000,
) -> tuple[Candles, np.ndarray, list[str]]:
    q = select(BarRow).where(BarRow.symbol == "XAUUSD", BarRow.timeframe == timeframe)
    if start:
        q = q.where(BarRow.start >= start)
    if end:
        q = q.where(BarRow.start < end)
    async with maker() as s:
        rows = (await s.execute(q.order_by(BarRow.start).limit(max_bars * 2))).scalars().all()
    best: dict[datetime, BarRow] = {}
    for r in rows:  # one bar per period, preferring the long clean history source
        cur = best.get(r.start)
        if cur is None or SOURCE_PRIORITY.get(r.source, 9) < SOURCE_PRIORITY.get(cur.source, 9):
            best[r.start] = r
    ordered = [best[k] for k in sorted(best)][-max_bars:]
    spreads = np.array([float(r.spread_avg) for r in ordered]) if ordered else np.array([])
    return Candles.from_rows(ordered, timeframe), spreads, sorted({r.source for r in ordered})


async def load_news(
    maker: async_sessionmaker[AsyncSession], start: datetime, end: datetime, before: int, after: int
) -> list[tuple[datetime, datetime]]:
    async with maker() as s:
        rows = (
            (
                await s.execute(
                    select(EconomicEvent).where(
                        EconomicEvent.scheduled_at >= start - timedelta(hours=1),
                        EconomicEvent.scheduled_at <= end + timedelta(hours=1),
                    )
                )
            )
            .scalars()
            .all()
        )
    return [
        (e.scheduled_at - timedelta(minutes=before), e.scheduled_at + timedelta(minutes=after))
        for e in rows
        if e.importance in LOCKOUT_IMPORTANCE and e.currency in GOLD_SENSITIVE
    ]


async def prior_trials(maker: async_sessionmaker[AsyncSession], setup: str, timeframe: str) -> int:
    """Parameter variants already tried on this setup/timeframe (6 per completed research run)."""
    async with maker() as s:
        n = (
            await s.execute(
                select(func.count())
                .select_from(BacktestRow)
                .where(
                    BacktestRow.kind == "backtest",
                    BacktestRow.status == "done",
                    BacktestRow.request["setup"].as_string() == setup,
                    BacktestRow.request["timeframe"].as_string() == timeframe,
                )
            )
        ).scalar_one()
    return int(n) * 6


async def run_backtest(
    maker: async_sessionmaker[AsyncSession],
    req: BacktestRequest,
    spec: SymbolSpec,
    cfg: RiskConfig,
    spec_source: str,
    actor: str = "system",
) -> dict[str, Any]:
    hold = await ensure_holdout(maker, actor)
    start, end = req.start, req.end
    if req.final_holdout:
        if hold is None:
            return {"error": "aucune période hors-échantillon réservée (historique trop court)"}
        start, end = hold.start, None
    elif hold is not None:
        end = min(end, hold.start) if end else hold.start
    c, spreads, sources = await load_history(maker, req.timeframe, start, end)
    if len(c) < 200:
        return {"error": f"historique insuffisant : {len(c)} bougies {req.timeframe} (importez Dukascopy)"}
    trials = await prior_trials(maker, req.setup, req.timeframe)
    news = await load_news(
        maker, c.t[0], c.t[-1], cfg.news_lockout_minutes_before, cfg.news_lockout_minutes_after
    )
    params = StrategyParams(
        setup=req.setup,
        stop_buffer_atr=req.stop_buffer_atr,
        min_quality=req.min_quality,
        require_htf_alignment=req.require_htf_alignment,
        max_hold_bars=req.max_hold_bars,
        sessions=tuple(req.sessions),
    )
    costs = CostModel(
        spread_mult=req.spread_mult, slippage=req.slippage, swap_per_lot_night=req.swap_per_lot_night
    )
    report = await asyncio.to_thread(
        full_report, c, spreads, spec, params, cfg, costs, req.initial_balance, news, 4, trials
    )
    report["data"] = report.get("data", {}) | {
        "sources": sources,
        "news_windows": len(news),
        "holdout_start": hold.start if hold else None,
        "final_holdout": req.final_holdout,
    }
    report["spec"] = {
        "source": spec_source,
        "commission": f"{spec.commission_type} {spec.commission_rate}",
        "contract_size": str(spec.contract_size),
    }
    report["risk_config_hash"] = cfg.config_hash()
    out: dict[str, Any] = json.loads(json.dumps(report, default=str))
    return out


async def store(
    maker: async_sessionmaker[AsyncSession], row_id: int, report: dict[str, Any], ok_key: str = "summary"
) -> None:
    async with maker() as s:
        row = await s.get(BacktestRow, row_id)
        if row is None:
            return
        if "error" in report and ok_key not in report:
            row.status, row.error = "error", report["error"]
        else:
            row.status, row.report = "done", report
        row.finished_at = utcnow()
        await s.commit()
