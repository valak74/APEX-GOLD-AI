from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from apex.analysis.indicators import Candles
from apex.backtest.engine import CostModel, run
from apex.backtest.metrics import monte_carlo, summarize, validate
from apex.backtest.runner import full_report
from apex.backtest.signals import Signal, StrategyParams, generate
from apex.broker.factory import PAPER_XAUUSD_SPEC
from apex.risk.config import RiskConfig

T0 = datetime(2026, 11, 24, 13, 0, tzinfo=UTC)  # Tuesday 08:00 New York
CFG = RiskConfig()
SPEC = PAPER_XAUUSD_SPEC  # 100 oz, 3.5 USD/lot/side


def bars(rows: list[tuple[float, float, float, float]], start: datetime = T0) -> Candles:
    a = np.array(rows, dtype=float)
    return Candles(
        [start + timedelta(minutes=5 * i) for i in range(len(a))], a[:, 0], a[:, 1], a[:, 2], a[:, 3], "5m"
    )


FLAT = [(4000.0, 4002.0, 3998.0, 4000.0)] * 30  # ATR ≈ 4


def sig(i: int, direction: str = "bullish", stop: float = 3995.0, target: float = 4010.0) -> Signal:
    return Signal(i, T0 + timedelta(minutes=5 * i), direction, stop, target, 80, "range", 1.0, "test")  # type: ignore[arg-type]


def spreads(n: int, s: float = 0.2) -> np.ndarray:
    return np.full(n, s)


# --------------------------------------------------------------------------- execution model
def test_long_target_fill_costs_and_r() -> None:
    rows = [*FLAT, (4000, 4003, 3999, 4002), (4002, 4011, 4001, 4009)]  # bar 30 = entry, 31 hits target
    c = bars(rows)
    r = run(
        c,
        spreads(len(c)),
        SPEC,
        StrategyParams(max_hold_bars=10),
        CFG,
        CostModel(slippage=0.05),
        signals=[sig(29)],
    )
    [t] = r.trades
    assert t.entry == pytest.approx(4000 + 0.1 + 0.05)  # next open + half spread + slippage
    assert t.reason == "target" and t.exit == 4010.0
    vol = t.volume
    assert t.gross == pytest.approx((4010 - t.entry) * vol * 100, abs=0.01)
    assert t.costs == pytest.approx(2 * 3.5 * vol, abs=0.01)
    assert t.net == pytest.approx(t.gross - t.costs, abs=0.01) and t.r == pytest.approx(
        t.net / t.risk_amount, abs=1e-3
    )
    assert t.risk_amount <= 25  # 0.25 % of 10 000, from the live risk engine


def test_stop_first_when_both_hit_in_same_bar() -> None:
    rows = [*FLAT, (4000, 4001, 3999, 4000), (4000, 4012, 3990, 4005)]  # bar 31 touches both
    c = bars(rows)
    [t] = run(c, spreads(len(c)), SPEC, StrategyParams(), CFG, signals=[sig(29)]).trades
    assert t.reason == "stop" and t.exit == pytest.approx(3995 - 0.05)


def test_gap_through_stop_fills_at_open() -> None:
    rows = [*FLAT, (4000, 4001, 3999, 4000), (3985, 3986, 3980, 3984)]  # opens far below the stop
    c = bars(rows)
    [t] = run(c, spreads(len(c)), SPEC, StrategyParams(), CFG, signals=[sig(29)]).trades
    assert t.reason == "stop_gap" and t.exit < 3995 - 5


def test_short_side_and_time_exit() -> None:
    rows = [*FLAT] + [(4000, 4001, 3999, 4000)] * 6
    c = bars(rows)
    [t] = run(
        c,
        spreads(len(c)),
        SPEC,
        StrategyParams(max_hold_bars=3),
        CFG,
        signals=[sig(29, "bearish", stop=4005, target=3990)],
    ).trades
    assert t.direction == "bearish" and t.reason == "time" and t.bars_held == 3
    assert t.entry == pytest.approx(4000 - 0.1 - 0.05)


def test_risk_engine_rejections_are_counted() -> None:
    c = bars([*FLAT, (4000, 4001, 3999, 4000), (4000, 4001, 3999, 4000)])
    r = run(c, spreads(len(c)), SPEC, StrategyParams(), CFG, signals=[sig(29, target=4002)])  # R:R ~0.4
    assert r.trades == [] and r.rejected.get("risk_reward") == 1


def test_one_position_at_a_time() -> None:
    rows = [*FLAT] + [(4000, 4001, 3999, 4000)] * 10
    c = bars(rows)
    r = run(
        c, spreads(len(c)), SPEC, StrategyParams(max_hold_bars=5), CFG, signals=[sig(29), sig(30), sig(31)]
    )
    assert len(r.trades) == 1  # the next signals arrive while a position is open


# --------------------------------------------------------------------------- metrics & gate
def test_summary_and_monte_carlo() -> None:
    rows = [*FLAT, (4000, 4003, 3999, 4002), (4002, 4011, 4001, 4009)]
    c = bars(rows)
    r = run(c, spreads(len(c)), SPEC, StrategyParams(), CFG, signals=[sig(29)])
    s = summarize(r.trades, 10_000, r.equity)
    assert s["trades"] == 1 and s["win_rate"] == 1.0 and s["max_drawdown"] == 0
    assert monte_carlo(r.trades, 10_000) == {"runs": 0}  # too few trades to resample


def test_gate_requires_everything() -> None:
    good = {"trades": 400, "expectancy_r_ci": [0.05, 0.3], "profit_factor": 1.5}
    folds = [{"trades": 50, "net_profit": p} for p in (100, 120, 90, 110)]
    nb = [{"trades": 100, "expectancy_r": 0.1}, {"trades": 90, "expectancy_r": 0.12}]
    assert validate(good, {"expectancy_r": 0.05}, folds, nb).validated
    assert not validate(good | {"trades": 120}, {"expectancy_r": 0.05}, folds, nb).validated
    assert not validate(good, {"expectancy_r": -0.01}, folds, nb).validated  # fails under higher costs
    lumpy = [{"trades": 50, "net_profit": p} for p in (900, -50, 10, -30)]
    assert not validate(good, {"expectancy_r": 0.05}, lumpy, nb).validated
    assert not validate(
        good, {"expectancy_r": 0.05}, folds, [*nb, {"trades": 80, "expectancy_r": -0.2}]
    ).validated


# --------------------------------------------------------------------------- sanity on pure noise
def noise(n: int, seed: int) -> tuple[Candles, np.ndarray]:
    rng = np.random.default_rng(seed)
    cl = 4000 + np.cumsum(rng.normal(0, 1.5, n))
    o = np.r_[cl[0], cl[:-1]]
    h = np.maximum(o, cl) + np.abs(rng.normal(0, 0.8, n))
    lo = np.minimum(o, cl) - np.abs(rng.normal(0, 0.8, n))
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    t = [start + timedelta(minutes=5 * i) for i in range(n)]
    return Candles(t, o, h, lo, cl, "5m"), np.full(n, 0.25)


def test_signals_are_causal_on_history() -> None:
    c, _ = noise(1500, 3)
    # the last bar of a truncated history is skipped on purpose (no next bar to enter on)
    full = [(s.idx, s.direction, round(s.stop, 4)) for s in generate(c, StrategyParams()) if s.idx < 999]
    part = [(s.idx, s.direction, round(s.stop, 4)) for s in generate(c.head(1000), StrategyParams())]
    assert full == part


@pytest.mark.parametrize("setup", ["liquidity_sweep_reversal", "trend_pullback"])
def test_no_edge_is_validated_on_random_walk(setup: str) -> None:
    c, sp = noise(6000, 21)
    rep = full_report(c, sp, SPEC, StrategyParams(setup=setup), CFG)  # type: ignore[arg-type]
    assert rep["validation"]["validated"] is False  # pure noise must never pass the gate
    assert set(rep) >= {
        "summary",
        "stress_costs_150",
        "folds",
        "walk_forward",
        "sensitivity",
        "monte_carlo",
        "equity",
        "drawdown",
        "trades",
        "assumptions",
    }
    if rep["summary"].get("trades", 0) > 30:
        assert rep["summary"]["expectancy_r"] < 0.3  # costs on noise: no material positive expectancy
