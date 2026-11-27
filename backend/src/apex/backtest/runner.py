"""Full backtest report: base run, +50 % cost stress, walk-forward (out-of-sample) optimisation, parameter
sensitivity, Monte Carlo and the pre-registered validation gate.

Walk-forward: history is cut into ``folds`` consecutive periods. For each period k ≥ 1, the parameter set with
the best expectancy on periods < k (≥ 30 trades) is selected and its trades in period k are kept: the
concatenation of those period-k trades is the out-of-sample (OOS) result — parameters never see the data they
are judged on. Each variant is simulated once on the full history and its trades are partitioned by period
(signals are causal; account-dependent sizing makes this a close, documented approximation of separate runs).
"""

from __future__ import annotations

import itertools
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any

import numpy as np

from apex.analysis.indicators import Candles
from apex.backtest.engine import BTTrade, CostModel, run
from apex.backtest.metrics import deflated_sharpe, monte_carlo, summarize, validate
from apex.backtest.signals import StrategyParams
from apex.domain.models import SymbolSpec
from apex.risk.config import RiskConfig

STOP_BUFFERS: tuple[float, ...] = (0.1, 0.2, 0.3)
MIN_QUALITIES: tuple[int, ...] = (0, 50)


def _fold_edges(start: datetime, end: datetime, k: int) -> list[datetime]:
    span = (end - start) / k
    return [start + span * i for i in range(k + 1)]


def _in(t: BTTrade, a: datetime, b: datetime) -> bool:
    return a <= t.entry_t < b


def _downsample(points: list[tuple[datetime, float]], max_points: int = 800) -> list[list[Any]]:
    if len(points) <= max_points:
        return [[t, round(v, 2)] for t, v in points]
    step = len(points) / max_points
    return [[points[int(i * step)][0], round(points[int(i * step)][1], 2)] for i in range(max_points)] + [
        [points[-1][0], round(points[-1][1], 2)]
    ]


def full_report(
    c: Candles,
    spreads: np.ndarray,
    spec: SymbolSpec,
    params: StrategyParams,
    cfg: RiskConfig,
    costs: CostModel | None = None,
    initial: float = 10_000.0,
    news: list[tuple[datetime, datetime]] | None = None,
    folds: int = 4,
    prior_trials: int = 0,
) -> dict[str, Any]:
    """``prior_trials``: parameter variants already tried on this setup in earlier runs (selection bias)."""
    costs = costs or CostModel()
    if len(c) < 200:
        return {"error": "historique insuffisant (moins de 200 bougies)", "bars": len(c)}
    base = run(c, spreads, spec, params, cfg, costs, initial, news)
    base_sum = summarize(base.trades, initial, base.equity)
    stress = run(
        c,
        spreads,
        spec,
        params,
        cfg,
        replace(
            costs,
            spread_mult=costs.spread_mult * 1.5,
            slippage=costs.slippage * 1.5,
            commission_mult=costs.commission_mult * 1.5,
        ),
        initial,
        news,
    )
    stress_sum = summarize(stress.trades, initial, stress.equity)

    edges = _fold_edges(c.t[0], c.t[-1], folds)
    edges[-1] = c.t[-1].replace(year=c.t[-1].year + 1)  # include the last bar
    fold_sums = [
        summarize([t for t in base.trades if _in(t, edges[k], edges[k + 1])], initial, [])
        | {"from": edges[k], "to": min(edges[k + 1], c.t[-1])}
        for k in range(folds)
    ]

    variants: dict[tuple[float, int], list[BTTrade]] = {}
    for sb, mq in itertools.product(STOP_BUFFERS, MIN_QUALITIES):
        p = replace(params, stop_buffer_atr=sb, min_quality=mq)
        variants[(sb, mq)] = (
            base.trades if p == params else run(c, spreads, spec, p, cfg, costs, initial, news).trades
        )
    oos: list[BTTrade] = []
    chosen: list[dict[str, Any]] = []
    for k in range(1, folds):
        best, best_exp = None, -1e9
        for key, trades in variants.items():
            train = [t for t in trades if t.entry_t < edges[k]]
            if len(train) >= 30:
                e = float(np.mean([t.r for t in train]))
                if e > best_exp:
                    best, best_exp = key, e
        if best is None:
            chosen.append({"period": k, "params": None, "reason": "moins de 30 trades d'entraînement"})
            continue
        test = [t for t in variants[best] if _in(t, edges[k], edges[k + 1])]
        oos += test
        chosen.append(
            {
                "period": k,
                "params": {"stop_buffer_atr": best[0], "min_quality": best[1]},
                "train_expectancy_r": round(best_exp, 3),
                "test_trades": len(test),
            }
        )
    oos_eq: list[tuple[datetime, float]] = []
    bal = initial
    for t in sorted(oos, key=lambda x: x.exit_t):
        bal += t.net
        oos_eq.append((t.exit_t, bal))
    oos_sum = summarize(sorted(oos, key=lambda x: x.exit_t), initial, [(c.t[0], initial), *oos_eq])

    sens = []
    for key, trades in sorted(variants.items()):
        s = summarize(trades, initial, [])
        sens.append(
            {
                "stop_buffer_atr": key[0],
                "min_quality": key[1],
                "trades": s.get("trades", 0),
                "expectancy_r": s.get("expectancy_r"),
                "profit_factor": s.get("profit_factor"),
            }
        )
    near = [
        x
        for x in sens
        if (x["stop_buffer_atr"], x["min_quality"]) != (params.stop_buffer_atr, params.min_quality)
        and abs(x["stop_buffer_atr"] - params.stop_buffer_atr) <= 0.1001
        and x["min_quality"] == params.min_quality
    ]

    judged = sorted(oos, key=lambda x: x.exit_t) if oos else base.trades
    srs = [
        float(np.mean([t.r for t in v]) / np.std([t.r for t in v], ddof=1))
        for v in variants.values()
        if len(v) >= 10 and np.std([t.r for t in v], ddof=1) > 0
    ]
    dsr = deflated_sharpe(
        np.array([t.r for t in judged]),
        prior_trials + len(variants),
        float(np.var(srs, ddof=1)) if len(srs) >= 2 else 0.0,
    )
    gate = validate(oos_sum if oos_sum.get("trades") else base_sum, stress_sum, fold_sums, near, dsr=dsr)
    dd_series: list[list[Any]] = []
    peak = -1e18
    for ts, v in base.equity:
        peak = max(peak, v)
        dd_series.append([ts, round((v - peak) / peak * 100, 3) if peak > 0 else 0.0])
    return {
        "data": {"bars": len(c), "timeframe": c.timeframe, "start": c.t[0], "end": c.t[-1]},
        "params": asdict(params) | {"sessions": list(params.sessions)},
        "costs": asdict(costs),
        "assumptions": {
            "spread": "spread moyen enregistré de chaque bougie",
            "swap": "non modélisé (0)"
            if costs.swap_per_lot_night == 0
            else f"{costs.swap_per_lot_night} USD/lot/nuit",
            "fills": "ouverture suivante, stop prioritaire si stop et objectif dans la même bougie",
            "currency": "USD",
        },
        "signals": base.signals,
        "rejected_by_risk_engine": base.rejected,
        "summary": base_sum,
        "stress_costs_150": stress_sum,
        "folds": fold_sums,
        "walk_forward": {"periods": chosen, "oos": oos_sum},
        "sensitivity": sens,
        "monte_carlo": monte_carlo(base.trades, initial),
        "validation": gate.to_dict(),
        "deflated_sharpe": dsr,
        "equity": _downsample(base.equity),
        "drawdown": _downsample([(ts, v) for ts, v in dd_series]),
        "trades": [asdict(t) for t in base.trades[-1000:]],
    }
