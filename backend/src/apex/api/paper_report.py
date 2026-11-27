"""PAPER evidence: statistics, comparison with the backtest, live/backtest divergence and the phase 9 gate.

* Statistics on closed autopilot trades (net, in R) with the same confidence intervals as backtests.
* Comparison with the latest completed research backtest of the same setup/timeframe: is the live expectancy
  inside the backtest's 95 % interval?
* Divergence by replay: the backtest signal generator is re-run on the bars recorded LIVE during the paper
  period. Signals are matched to the opportunities the live scanner produced (same direction, signal bar within
  one bar). Missed signals point to downtime or detector differences; live-only signals point to a live/backtest
  mismatch; for matched trades, fill slippage and the R difference measure execution costs not modelled.
* Phase 9 gate (roadmap): ≥ 3 months AND ≥ 100 trades, expectancy inside the backtest interval, zero unexplained
  risk events.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select

from apex.analysis.indicators import Candles
from apex.analysis.stats import wilson
from apex.backtest.metrics import bootstrap_ci
from apex.backtest.signals import StrategyParams, generate
from apex.db.models import BacktestRow, BarRow, OpportunityRow, PaperOrderRow
from apex.domain.models import utcnow
from apex.market.sessions import TIMEFRAMES

GATE_DAYS = 90
GATE_TRADES = 100


def stats(rows: list[PaperOrderRow]) -> dict[str, Any]:
    closed = [r for r in rows if r.status == "closed" and r.r_multiple is not None]
    if not closed:
        return {"trades": 0}
    r = np.array([float(x.r_multiple) for x in closed])  # type: ignore[arg-type]
    net = np.array([float(x.net_pnl or 0) for x in closed])
    lo, hi = bootstrap_ci(r) if len(r) >= 2 else (math.nan, math.nan)
    slip = [float(x.slippage) for x in rows if x.slippage is not None]
    lat = [x.latency_s for x in rows if x.latency_s is not None]
    wins, losses = net[net > 0].sum(), net[net <= 0].sum()
    return {
        "trades": len(closed),
        "net_profit": round(float(net.sum()), 2),
        "currency": closed[0].currency,
        "win_rate": round(float((net > 0).mean()), 4),
        "win_rate_ci": [round(x, 4) for x in wilson(int((net > 0).sum()), len(net))],
        "expectancy_r": round(float(r.mean()), 4),
        "expectancy_r_ci": [
            None if math.isnan(lo) else round(lo, 4),
            None if math.isnan(hi) else round(hi, 4),
        ],
        "profit_factor": round(float(wins / -losses), 3) if losses < 0 else None,
        "avg_slippage": round(float(np.mean(slip)), 4) if slip else None,
        "avg_latency_s": round(float(np.mean(lat)), 1) if lat else None,
        "by_reason": {
            k: sum(1 for x in closed if x.close_reason == k)
            for k in sorted({x.close_reason or "?" for x in closed})
        },
    }


async def report(maker: Any) -> dict[str, Any]:
    async with maker() as s:
        rows = (await s.execute(select(PaperOrderRow).order_by(PaperOrderRow.considered_at))).scalars().all()
        bts = (
            (
                await s.execute(
                    select(BacktestRow)
                    .where(BacktestRow.kind == "backtest", BacktestRow.status == "done")
                    .order_by(BacktestRow.id.desc())
                )
            )
            .scalars()
            .all()
        )
    groups: dict[tuple[str, str], list[PaperOrderRow]] = {}
    for r in rows:
        groups.setdefault((r.setup, r.timeframe), []).append(r)
    out_groups: list[dict[str, Any]] = []
    for (setup, tf), rs in sorted(groups.items()):
        st = stats(rs)
        bt = next(
            (
                b
                for b in bts
                if (b.request or {}).get("setup") == setup
                and (b.request or {}).get("timeframe") == tf
                and not (b.request or {}).get("final_holdout")
            ),
            None,
        )
        comp: dict[str, Any] | None = None
        if bt is not None and bt.report:
            bs = bt.report.get("summary", {})
            ci = bs.get("expectancy_r_ci") or [None, None]
            inside = None
            if st.get("trades", 0) >= 30 and ci[0] is not None:
                inside = ci[0] <= st["expectancy_r"] <= ci[1]
            comp = {
                "backtest_id": bt.id,
                "expectancy_r": bs.get("expectancy_r"),
                "expectancy_r_ci": ci,
                "win_rate": bs.get("win_rate"),
                "validated": (bt.report.get("validation") or {}).get("validated"),
                "paper_inside_ci": inside,
            }
        out_groups.append(
            {
                "setup": setup,
                "timeframe": tf,
                "considered": len(rs),
                "status_counts": {
                    k: sum(1 for x in rs if x.status == k)
                    for k in ("skipped", "rejected", "failed", "open", "closed")
                },
                "stats": st,
                "backtest": comp,
            }
        )
    overall = stats(list(rows))
    first = rows[0].considered_at if rows else None
    days = (utcnow() - first).days if first else 0
    unexplained = [r for r in rows if r.status == "failed"] + [
        r for r in rows if r.close_reason == "stop_out"
    ]
    comparable = [g for g in out_groups if g["backtest"] and g["backtest"]["paper_inside_ci"] is not None]
    gate = [
        {
            "name": "Durée ≥ 3 mois",
            "passed": days >= GATE_DAYS,
            "value": f"{days} j",
            "requirement": f"≥ {GATE_DAYS} j",
            "progress": min(1.0, days / GATE_DAYS),
        },
        {
            "name": "Au moins 100 trades",
            "passed": overall.get("trades", 0) >= GATE_TRADES,
            "value": str(overall.get("trades", 0)),
            "requirement": f"≥ {GATE_TRADES}",
            "progress": min(1.0, overall.get("trades", 0) / GATE_TRADES),
        },
        {
            "name": "Résultats dans l'intervalle du backtest",
            "passed": bool(comparable) and all(g["backtest"]["paper_inside_ci"] for g in comparable),
            "value": f"{sum(1 for g in comparable if g['backtest']['paper_inside_ci'])}/{len(comparable)} groupes comparables",
            "requirement": "tous (≥ 30 trades par groupe)",
            "progress": None,
        },
        {
            "name": "Aucun incident de risque inexpliqué",
            "passed": not unexplained,
            "value": str(len(unexplained)),
            "requirement": "0 (ordre en échec, stop-out)",
            "progress": None,
        },
    ]
    return {
        "overall": overall,
        "groups": out_groups,
        "first_at": first,
        "days": days,
        "gate": gate,
        "gate_passed": all(g["passed"] for g in gate),
    }


async def divergence(maker: Any, setup: str, timeframe: str, since: datetime | None = None) -> dict[str, Any]:
    """Replay the backtest signal generator on the bars recorded live and match with live opportunities."""
    tf_s = TIMEFRAMES.get(timeframe, 300)
    async with maker() as s:
        if since is None:
            since = (
                await s.execute(
                    select(PaperOrderRow.considered_at).order_by(PaperOrderRow.considered_at).limit(1)
                )
            ).scalar_one_or_none()
        if since is None:
            return {"error": "aucune activité PAPER pour l'instant"}
        warm = since - timedelta(seconds=tf_s * 400)  # history the detectors need before the paper period
        bars = (
            (
                await s.execute(
                    select(BarRow)
                    .where(
                        BarRow.symbol == "XAUUSD",
                        BarRow.timeframe == timeframe,
                        BarRow.source == "ctrader",
                        BarRow.start >= warm,
                    )
                    .order_by(BarRow.start)
                )
            )
            .scalars()
            .all()
        )
        opps = (
            (
                await s.execute(
                    select(OpportunityRow).where(
                        OpportunityRow.setup == setup,
                        OpportunityRow.timeframe == timeframe,
                        OpportunityRow.detected_at >= since,
                    )
                )
            )
            .scalars()
            .all()
        )
        orders = {
            r.opportunity_id: r
            for r in (
                await s.execute(
                    select(PaperOrderRow).where(
                        PaperOrderRow.setup == setup, PaperOrderRow.timeframe == timeframe
                    )
                )
            )
            .scalars()
            .all()
        }
    if len(bars) < 100:
        return {"error": f"pas assez de bougies {timeframe} enregistrées en direct ({len(bars)})"}
    c = Candles.from_rows(bars, timeframe)
    sigs = [x for x in generate(c, StrategyParams(setup=setup)) if x.t >= since]  # type: ignore[arg-type]
    tol = timedelta(seconds=tf_s)
    used: set[str] = set()
    matched, bt_only = [], []
    for sg in sigs:
        m = next(
            (
                o
                for o in opps
                if o.id not in used and o.direction == sg.direction and abs(o.detected_at - sg.t) <= tol
            ),
            None,
        )
        if m is None:
            bt_only.append({"t": sg.t, "direction": sg.direction})
            continue
        used.add(m.id)
        po = orders.get(m.id)
        matched.append(
            {
                "t": sg.t,
                "direction": sg.direction,
                "opportunity_id": m.id,
                "backtest_stop": round(sg.stop, 2),
                "live_stop": float(m.payload.get("stop", 0)),
                "paper_status": po.status if po else "non examinée",
                "paper_r": po.r_multiple if po else None,
                "slippage": float(po.slippage) if po and po.slippage is not None else None,
            }
        )
    live_only = [
        {"t": o.detected_at, "direction": o.direction, "opportunity_id": o.id}
        for o in opps
        if o.id not in used
    ]
    n = len(sigs) + len(live_only)
    agreement = len(matched) / n if n else None
    return {
        "setup": setup,
        "timeframe": timeframe,
        "since": since,
        "bars": len(bars),
        "backtest_signals": len(sigs),
        "live_opportunities": len(opps),
        "matched": matched[-200:],
        "backtest_only": bt_only[-200:],
        "live_only": live_only[-200:],
        "agreement": round(agreement, 4) if agreement is not None else None,
        "note": (
            "Signaux vus par le backtest mais pas en direct : coupure du flux ou du serveur, ou différence entre "
            "détecteurs. Signaux vus seulement en direct : écart à expliquer avant toute conclusion."
        ),
    }
