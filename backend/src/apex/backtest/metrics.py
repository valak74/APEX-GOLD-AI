"""Performance metrics, robustness tests and the pre-registered validation gate (docs/06-roadmap.md).

All statistics are computed on NET results (after spread, slippage, commission, modelled swap).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from apex.analysis.stats import wilson
from apex.backtest.engine import BTTrade

MIN_DAYS_RATIO = 20  # Sharpe/Sortino on fewer trading days are noise: reported as None


def _session(t: datetime) -> str:
    h = t.hour
    return "asia" if h < 7 else "london" if h < 13 else "newyork" if h < 21 else "late"


def _max_drawdown(equity: list[float]) -> tuple[float, float]:
    peak, mdd, mdd_pct = -math.inf, 0.0, 0.0
    for x in equity:
        peak = max(peak, x)
        mdd = max(mdd, peak - x)
        if peak > 0:
            mdd_pct = max(mdd_pct, (peak - x) / peak)
    return mdd, mdd_pct


def _streaks(rs: list[float]) -> tuple[int, int]:
    best = worst = cur_w = cur_l = 0
    for r in rs:
        if r > 0:
            cur_w, cur_l = cur_w + 1, 0
        else:
            cur_l, cur_w = cur_l + 1, 0
        best, worst = max(best, cur_w), max(worst, cur_l)
    return best, worst


def bootstrap_ci(
    x: np.ndarray, stat: Any = np.mean, iters: int = 4000, seed: int = 11
) -> tuple[float, float]:
    if len(x) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    vals = [stat(rng.choice(x, len(x), replace=True)) for _ in range(iters)]
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def summarize(
    trades: list[BTTrade], initial: float, equity_points: list[tuple[datetime, float]]
) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    r = np.array([t.r for t in trades])
    net = np.array([t.net for t in trades])
    wins, losses = net[net > 0], net[net <= 0]
    eq = [e for _, e in equity_points] or [initial]
    mdd, mdd_pct = _max_drawdown(eq)
    total = float(net.sum())
    # daily returns for Sharpe / Sortino
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        daily[t.exit_t.strftime("%Y-%m-%d")] += t.net
    dr = np.array(list(daily.values())) / initial
    sharpe = (
        float(dr.mean() / dr.std(ddof=1) * math.sqrt(252))
        if len(dr) >= MIN_DAYS_RATIO and dr.std(ddof=1) > 0
        else None
    )
    downside = dr[dr < 0]
    sortino = (
        float(dr.mean() / downside.std(ddof=1) * math.sqrt(252))
        if len(dr) >= MIN_DAYS_RATIO and len(downside) > 1 and downside.std(ddof=1) > 0
        else None
    )
    lo, hi = bootstrap_ci(r)
    best, worst = _streaks(list(r))
    monthly: dict[str, float] = defaultdict(float)
    for t in trades:
        monthly[t.exit_t.strftime("%Y-%m")] += t.net
    by: dict[str, dict[str, dict[str, float]]] = {}
    for key, fn in (
        ("session", lambda t: _session(t.entry_t)),
        ("regime", lambda t: t.regime),
        ("direction", lambda t: t.direction),
        ("exit", lambda t: t.reason),
    ):
        groups: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            groups[fn(t)].append(t.r)
        by[key] = {
            k: {
                "n": len(v),
                "avg_r": round(float(np.mean(v)), 3),
                "win_rate": round(float(np.mean(np.array(v) > 0)), 3),
            }
            for k, v in groups.items()
        }
    return {
        "trades": n,
        "net_profit": round(total, 2),
        "return_pct": round(total / initial * 100, 2),
        "win_rate": round(float((net > 0).mean()), 4),
        "win_rate_ci": [round(x, 4) for x in wilson(int((net > 0).sum()), n)],
        "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if losses.sum() < 0 else None,
        "expectancy_r": round(float(r.mean()), 4),
        "expectancy_r_ci": [round(lo, 4), round(hi, 4)],
        "avg_win_r": round(float(r[r > 0].mean()), 3) if (r > 0).any() else None,
        "avg_loss_r": round(float(r[r <= 0].mean()), 3) if (r <= 0).any() else None,
        "max_drawdown": round(mdd, 2),
        "max_drawdown_pct": round(mdd_pct * 100, 2),
        "recovery_factor": round(total / mdd, 3) if mdd > 0 else None,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "avg_bars_held": round(float(np.mean([t.bars_held for t in trades])), 1),
        "max_win_streak": best,
        "max_loss_streak": worst,
        "total_costs": round(float(sum(t.costs for t in trades)), 2),
        "monthly": {k: round(v, 2) for k, v in sorted(monthly.items())},
        "by": by,
    }


def monte_carlo(
    trades: list[BTTrade], initial: float, runs: int = 2000, seed: int = 5, ruin_dd_pct: float = 20.0
) -> dict[str, Any]:
    """Reshuffle trade order: distribution of max drawdown and probability of a deep drawdown."""
    if len(trades) < 5:
        return {"runs": 0}
    rng = np.random.default_rng(seed)
    net = np.array([t.net for t in trades])
    dds = []
    for _ in range(runs):
        eq = initial + np.cumsum(rng.permutation(net))
        _, pct = _max_drawdown([initial, *eq.tolist()])
        dds.append(pct * 100)
    arr = np.array(dds)
    return {
        "runs": runs,
        "max_dd_median_pct": round(float(np.median(arr)), 2),
        "max_dd_p95_pct": round(float(np.percentile(arr, 95)), 2),
        "prob_dd_over_pct": ruin_dd_pct,
        "prob_dd_over": round(float((arr >= ruin_dd_pct).mean()), 4),
    }


@dataclass
class GateItem:
    name: str
    passed: bool
    value: str
    requirement: str


@dataclass
class Validation:
    validated: bool
    items: list[GateItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate(
    base: dict[str, Any],
    stressed: dict[str, Any],
    folds: list[dict[str, Any]],
    neighbours: list[dict[str, Any]],
    min_trades: int = 200,
) -> Validation:
    """Pre-registered criteria (roadmap). A strategy is validated only if ALL pass."""
    items: list[GateItem] = []
    n = base.get("trades", 0)
    items.append(GateItem("Échantillon suffisant", n >= min_trades, str(n), f"≥ {min_trades} trades"))
    ci = base.get("expectancy_r_ci", [None, None])
    lo = ci[0] if ci and ci[0] is not None and not math.isnan(ci[0]) else None
    items.append(
        GateItem(
            "Espérance positive (borne basse IC 95 %)",
            lo is not None and lo > 0,
            f"{lo:.3f} R" if lo is not None else "—",
            "> 0 R",
        )
    )
    pf = base.get("profit_factor")
    items.append(GateItem("Profit factor", pf is not None and pf >= 1.2, str(pf), "≥ 1,2"))
    s_exp = stressed.get("expectancy_r")
    items.append(
        GateItem(
            "Résiste à des coûts +50 %",
            s_exp is not None and s_exp > 0,
            f"{s_exp:.3f} R" if s_exp is not None else "—",
            "espérance > 0",
        )
    )
    profits = [f.get("net_profit", 0.0) for f in folds if f.get("trades", 0) > 0]
    pos = sum(p for p in profits if p > 0)
    share = max(profits) / pos if pos > 0 and profits else 1.0
    items.append(
        GateItem(
            "Stabilité walk-forward (aucune période > 50 % des gains)",
            bool(profits)
            and pos > 0
            and share <= 0.5
            and sum(1 for p in profits if p > 0) > len(profits) / 2,
            f"{share * 100:.0f} % · {sum(1 for p in profits if p > 0)}/{len(profits)} périodes positives",
            "≤ 50 % et majorité positive",
        )
    )
    nb = [x.get("expectancy_r") for x in neighbours if x.get("trades", 0) >= 30]
    items.append(
        GateItem(
            "Paramètres voisins aussi rentables",
            bool(nb) and all(e is not None and e > 0 for e in nb),
            ", ".join(f"{e:.2f}" for e in nb if e is not None) or "—",
            "toutes les variantes > 0 R",
        )
    )
    return Validation(all(i.passed for i in items), items)
