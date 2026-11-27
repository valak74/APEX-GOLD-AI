"""Edge diagnostics: statistics, exit rules, false-positive control and power on a planted edge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from apex.analysis.indicators import Candles, atr
from apex.backtest.metrics import deflated_sharpe
from apex.backtest.signals import Signal
from apex.broker.factory import PAPER_XAUUSD_SPEC
from apex.market.sessions import is_market_open
from apex.research.diagnostics import (
    benjamini_hochberg,
    detect_components,
    diagnose,
    resample,
    simulate_exit,
    thin,
)
from apex.research.holdout import holdout_start_for

T0 = datetime(2026, 11, 24, 13, 0, tzinfo=UTC)


def bars(rows: list[tuple[float, float, float, float]]) -> Candles:
    a = np.array(rows, dtype=float)
    return Candles(
        [T0 + timedelta(minutes=5 * i) for i in range(len(a))], a[:, 0], a[:, 1], a[:, 2], a[:, 3], "5m"
    )


FLAT = [(4000.0, 4001.0, 3999.0, 4000.0)] * 20


def run_rule(rows: list[tuple[float, float, float, float]], rule: str) -> float:
    c = bars([*FLAT, *rows])
    sig = Signal(19, c.t[19], "bullish", 3995.0, 4010.0, 80, "range", 1.0, "t")
    sp = np.full(len(c), 0.0)  # no spread
    r = simulate_exit(c, sp, atr(c), sig, rule, 30, 0.0, 0.0)  # no slippage, no commission
    assert r is not None
    return r


def test_exit_rules_in_r() -> None:
    up_to_2r = [(4000, 4001, 3999, 4000), (4000, 4006, 3999, 4005), (4005, 4011, 4004, 4010)]
    assert run_rule(up_to_2r, "fixed_1r") == pytest.approx(1.0)  # risk = 5 → target 4005
    assert run_rule(up_to_2r, "fixed_2r") == pytest.approx(2.0)
    assert run_rule(up_to_2r, "liquidity") == pytest.approx(2.0, abs=0.01)  # 4010 (0.01 minimum spread)
    up_then_back = [(4000, 4001, 3999, 4000), (4000, 4005.5, 3999, 4005), (4005, 4005, 3990, 3992)]
    assert run_rule(up_then_back, "be_2r") == pytest.approx(0.0)  # stop moved to entry after +1R
    assert run_rule(up_then_back, "partial_1r_2r") == pytest.approx(0.5)  # half banked at +1R
    assert run_rule(up_then_back, "fixed_2r") == pytest.approx(-1.0, abs=0.01)  # original stop kept
    stop_and_target = [(4000, 4012, 3994, 4000)]  # both in the same bar: stop first
    assert run_rule(stop_and_target, "fixed_1r") == pytest.approx(-1.0)


def test_costs_are_charged_in_r() -> None:
    c = bars([*FLAT, (4000, 4001, 3999, 4000), (4000, 4006, 3999, 4005)])
    sig = Signal(19, c.t[19], "bullish", 3995.0, 4010.0, 80, "range", 1.0, "t")
    r = simulate_exit(c, np.full(len(c), 0.2), atr(c), sig, "fixed_1r", 30, 0.05, 0.07)
    risk = 4000 + 0.1 + 0.05 - 3995  # entry at ask + slippage
    assert r == pytest.approx(1.0 - 0.07 / risk)  # target re-based on the fill; commission costs R


def test_benjamini_hochberg_and_thinning() -> None:
    assert benjamini_hochberg([0.001, 0.2, 0.03, 0.5], q=0.1) == [True, False, True, False]
    assert benjamini_hochberg([0.04] * 3 + [0.9] * 97, q=0.1) == [False] * 100
    assert thin([1, 2, 5, 9, 10, 30], 5) == [1, 9, 30]


def test_deflated_sharpe_penalises_many_trials() -> None:
    r = np.random.default_rng(1).normal(0.15, 1.0, 400)
    one, many = deflated_sharpe(r, 1, 0.01), deflated_sharpe(r, 200, 0.01)
    assert one["dsr"] > many["dsr"]
    assert deflated_sharpe(np.random.default_rng(2).normal(0, 1, 400), 50, 0.01)["dsr"] < 0.95


def test_holdout_is_last_six_months_or_quarter_of_short_history() -> None:
    a = datetime(2023, 1, 1, tzinfo=UTC)
    assert holdout_start_for(a, a + timedelta(days=30)) is None  # too short: nothing reserved
    s = holdout_start_for(a, datetime(2026, 1, 1, tzinfo=UTC))
    assert s is not None and timedelta(days=181) <= datetime(2026, 1, 1, tzinfo=UTC) - s <= timedelta(
        days=183
    )
    short = holdout_start_for(a, a + timedelta(days=400))
    assert short is not None and (a + timedelta(days=400)) - short <= timedelta(days=101)


def walk(n: int, seed: int) -> tuple[Candles, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    t, ts = datetime(2025, 1, 6, tzinfo=UTC), []
    while len(ts) < n:
        if is_market_open(t):
            ts.append(t)
        t += timedelta(minutes=5)
    inc = rng.normal(0, 1.5, n)
    return Candles(ts, *_ohlc(inc, seed), "5m"), inc, np.array(ts)


def _ohlc(inc: np.ndarray, seed: int) -> tuple[np.ndarray, ...]:
    r2 = np.random.default_rng(seed + 100)
    cl = 2600 + np.cumsum(inc)
    o = np.r_[cl[0], cl[:-1]]
    return (
        o,
        np.maximum(o, cl) + np.abs(r2.normal(0, 0.8, len(cl))),
        np.minimum(o, cl) - np.abs(r2.normal(0, 0.8, len(cl))),
        cl,
    )


def test_resample_keeps_complete_groups() -> None:
    c, _, _ = walk(600, 1)
    h, sp = resample(c, np.full(len(c), 0.2), "1h")
    assert len(h) >= 45 and h.h[0] == c.h[:12].max() and h.c[0] == c.c[11] and sp[0] == pytest.approx(0.2)


def test_random_walk_yields_no_retained_finding() -> None:
    c, _, _ = walk(24000, 5)
    rep = diagnose(c, np.full(len(c), 0.25), PAPER_XAUUSD_SPEC, timeframes=("5m", "15m"))
    assert rep["tests_total"] > 200
    assert [r for r in rep["components"] if r["retained"]] == []
    assert not any(r.get("retained") and r["expectancy_r"] > 0 for r in rep["exits"])
    assert rep["costs"][0]["cost_pct_atr"] > rep["costs"][1]["cost_pct_atr"]  # costs weigh less on 15m


def test_planted_edge_is_found() -> None:
    c, inc, ts = walk(24000, 8)
    comps, _ = detect_components(c)
    a = atr(c)
    for e in comps["sweep_swing"]:  # after each sweep: +0.8 ATR drift in its direction over 12 bars
        if e.idx + 13 < len(c) and not np.isnan(a[e.idx]):
            inc[e.idx + 1 : e.idx + 13] += (1 if e.direction == "bullish" else -1) * 0.8 * a[e.idx] / 12
    planted = Candles(list(ts), *_ohlc(inc, 8), "5m")
    rep = diagnose(planted, np.full(len(c), 0.25), PAPER_XAUUSD_SPEC, timeframes=("5m",))
    hits = [
        r
        for r in rep["components"]
        if r["retained"] and r["component"] == "sweep_swing" and r["condition_key"] == "tout"
    ]
    assert hits and all(r["mean_excess_atr"] > 0 for r in hits)
    assert any("Balayage d'un sommet/creux" in f for f in rep["findings"])
