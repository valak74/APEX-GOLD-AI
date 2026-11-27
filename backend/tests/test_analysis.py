from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apex.analysis.engine import analyze_tf, mtf_context
from apex.analysis.indicators import Candles, adx, atr, bollinger, ema, macd, rsi, sma
from apex.analysis.levels import reference_levels
from apex.analysis.liquidity import equal_levels, fair_value_gaps, order_blocks, premium_discount, sweeps
from apex.analysis.regime import classify
from apex.analysis.stats import event_study, wilson
from apex.analysis.structure import analyze_structure, find_swings

T0 = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)  # Monday


def mk(ohlc: list[tuple[float, float, float, float]], tf: str = "5m", step: int = 300) -> Candles:
    a = np.array(ohlc, dtype=float)
    return Candles(
        [T0 + timedelta(seconds=step * i) for i in range(len(a))], a[:, 0], a[:, 1], a[:, 2], a[:, 3], tf
    )


def from_close(closes: list[float], spread: float = 0.5, tf: str = "5m") -> Candles:
    rows, prev = [], closes[0]
    for x in closes:
        o = (prev + x) / 2  # open between previous and current close: neighbouring extremes are distinct
        rows.append((o, max(o, x) + spread, min(o, x) - spread, x))
        prev = x
    return mk(rows, tf)


def random_walk(n: int, seed: int, drift: float = 0.0, vol: float = 1.0) -> Candles:
    rng = np.random.default_rng(seed)
    return from_close(list(4000 + np.cumsum(rng.normal(drift, vol, n))))


# --------------------------------------------------------------------------- indicators
def test_sma_ema_known_values() -> None:
    x = np.array([1.0, 2, 3, 4, 5])
    assert np.allclose(sma(x, 3)[2:], [2, 3, 4]) and np.isnan(sma(x, 3)[:2]).all()
    e = ema(x, 3)
    assert e[2] == 2.0 and e[3] == pytest.approx(3.0) and e[4] == pytest.approx(4.0)


def test_rsi_bounds_and_extremes() -> None:
    up = rsi(np.arange(1.0, 40.0))
    assert up[-1] == 100.0
    down = rsi(np.arange(40.0, 1.0, -1))
    assert down[-1] == pytest.approx(0.0)
    r = rsi(random_walk(300, 1).c)
    v = r[~np.isnan(r)]
    assert ((v >= 0) & (v <= 100)).all()


def test_atr_constant_range() -> None:
    c = mk([(10, 11, 9, 10)] * 30)
    assert atr(c)[-1] == pytest.approx(2.0)


def test_macd_bollinger_adx_shapes() -> None:
    c = random_walk(300, 2, drift=0.6, vol=0.5)
    line, sig, hist = macd(c.c)
    assert np.allclose(hist[~np.isnan(hist)], (line - sig)[~np.isnan(hist)])
    lo, mid, hi = bollinger(c.c)
    ok = ~np.isnan(mid)
    assert (lo[ok] <= mid[ok]).all() and (mid[ok] <= hi[ok]).all()
    a, pdi, mdi = adx(c)
    assert a[-1] > 20 and pdi[-1] > mdi[-1]  # steady up-drift -> trending, +DI dominant


@settings(max_examples=40, deadline=None)
@given(st.integers(0, 10_000), st.integers(60, 250))
def test_property_indicators_are_causal(seed: int, cut: int) -> None:
    c = random_walk(260, seed)
    head = c.head(cut)
    for f in (lambda x: atr(x), lambda x: rsi(x.c), lambda x: ema(x.c, 20), lambda x: adx(x)[0]):
        full, part = f(c)[:cut], f(head)
        assert np.allclose(full, part, equal_nan=True)


# --------------------------------------------------------------------------- structure
def zigzag() -> Candles:
    # up-leg, pullback, higher high (bullish BOS), then a sell-off below the last higher low (CHoCH)
    closes = [
        100,
        102,
        104,
        106,
        108,
        106,
        104,
        103,
        105,
        107,
        109,
        111,
        113,
        111,
        109,
        107,
        105,
        103,
        101,
        99,
        97,
        95,
    ]
    return from_close([float(x) for x in closes], spread=0.2)


def test_swings_labels_and_confirmation() -> None:
    c = zigzag()
    sw = find_swings(c, k=2)
    highs = [s for s in sw if s.kind == "high"]
    lows = [s for s in sw if s.kind == "low"]
    assert [round(s.price, 1) for s in highs] == [108.2, 113.2] and highs[1].label == "HH"
    assert next(round(s.price, 1) for s in lows) == 102.8
    assert all(s.confirmed_idx == s.idx + 2 for s in sw)


def test_bos_then_choch() -> None:
    st_ = analyze_structure(zigzag(), k=2)
    kinds = [(e.kind, e.direction) for e in st_.events]
    assert kinds[0] == ("BOS", "bullish") and ("CHoCH", "bearish") in kinds
    assert st_.trend == "down"
    bos = st_.events[0]
    assert bos.level == pytest.approx(108.2) and 0 <= bos.score <= 1 and bos.invalidation


@settings(max_examples=40, deadline=None)
@given(st.integers(0, 10_000), st.integers(40, 199))
def test_property_structure_events_have_no_lookahead(seed: int, cut: int) -> None:
    c = random_walk(200, seed)
    full = [(e.kind, e.direction, e.idx, e.level) for e in analyze_structure(c).events if e.idx < cut]
    part = [(e.kind, e.direction, e.idx, e.level) for e in analyze_structure(c.head(cut)).events]
    assert full == part


@settings(max_examples=40, deadline=None)
@given(st.integers(0, 10_000), st.integers(40, 199))
def test_property_fvg_and_sweeps_have_no_lookahead(seed: int, cut: int) -> None:
    c = random_walk(200, seed, vol=2.0)
    f_full = [(z.direction, z.idx, z.top, z.bottom) for z in fair_value_gaps(c) if z.idx < cut]
    f_part = [(z.direction, z.idx, z.top, z.bottom) for z in fair_value_gaps(c.head(cut))]
    assert f_full == f_part
    s_full_st, s_part_st = analyze_structure(c), analyze_structure(c.head(cut))
    sw_full = [
        (s.direction, s.idx, s.level)
        for s in sweeps(c, s_full_st, equal_levels(c, s_full_st.swings))
        if s.idx < cut
    ]
    h = c.head(cut)
    sw_part = [(s.direction, s.idx, s.level) for s in sweeps(h, s_part_st, equal_levels(h, s_part_st.swings))]
    assert sw_full == sw_part


# --------------------------------------------------------------------------- liquidity
def test_fvg_detection_mitigation_and_invalidation() -> None:
    base = [(100, 101, 99, 100)] * 15
    c = mk(
        [
            *base,
            (100, 101, 99.5, 100.8),
            (101, 106, 100.9, 105.5),
            (105.5, 107, 103, 106.5),
            (106, 106.5, 102.5, 103),
            (103, 103.5, 100, 100.5),
        ]
    )
    zones = fair_value_gaps(c, min_atr=0.25)
    z = next(x for x in zones if x.direction == "bullish")
    assert (z.bottom, z.top) == (101.0, 103.0) and z.idx == 17
    assert z.mitigated_idx == 18 and z.invalidated_idx == 19  # close 100.5 < gap bottom 101


def test_equal_highs_pool_and_bearish_sweep() -> None:
    rows = [(100, 101, 99, 100)] * 15
    rows += [(100, 105, 99.5, 104), (104, 104.5, 101, 102), (102, 103, 100, 101), (101, 102, 100, 101)]
    rows += [
        (101, 105.05, 100.5, 104.5),
        (104.5, 104.6, 101, 102),
        (102, 103, 100.5, 101),
        (101, 102, 100, 101),
    ]
    rows += [(101, 106.0, 100.8, 104.0)]  # pierces 105.05 then closes back below -> sweep of equal highs
    c = mk(rows)
    st_ = analyze_structure(c, k=2)
    pools = equal_levels(c, st_.swings, tol_atr=0.1)
    assert pools and pools[0].side == "highs" and pools[0].level == pytest.approx(105.05)
    sw = sweeps(c, st_, pools)
    assert (
        sw
        and sw[-1].direction == "bearish"
        and sw[-1].idx == len(c) - 1
        and sw[-1].target in ("pool", "swing")
    )


def test_order_block_requires_displacement() -> None:
    st_ = analyze_structure(zigzag(), k=2)
    weak = order_blocks(zigzag(), st_, disp_atr=50.0)
    assert weak == []
    obs = order_blocks(zigzag(), st_, disp_atr=0.1)
    assert obs and all(o.top >= o.bottom for o in obs)


def test_premium_discount() -> None:
    c = zigzag()
    pd = premium_discount(c, analyze_structure(c, k=2))
    assert pd is not None and pd["zone"] == "discount" and pd["position"] < 0.45


# --------------------------------------------------------------------------- regime
def test_regime_trend_vs_range() -> None:
    trend = random_walk(400, 3, drift=0.6, vol=0.5)
    r = classify(trend, analyze_structure(trend))
    assert r.label in ("strong_trend", "breakout") and r.direction == "bullish" and 0 < r.risk_multiplier <= 1
    x = np.arange(400)
    rng_ = from_close(list(4000 + 3 * np.sin(x / 3.0)), spread=0.3)
    r2 = classify(rng_, analyze_structure(rng_))
    assert r2.label == "range" and r2.direction == "neutral"
    short = random_walk(30, 1)
    assert classify(short, analyze_structure(short)).label == "insufficient_data"


def test_risk_multiplier_never_exceeds_one() -> None:
    for seed in range(20):
        c = random_walk(300, seed, drift=float(seed % 3 - 1), vol=1 + seed % 4)
        assert 0 <= classify(c, analyze_structure(c)).risk_multiplier <= 1


# --------------------------------------------------------------------------- stats
def test_wilson_interval() -> None:
    lo, hi = wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)


def test_event_study_known_outcomes() -> None:
    c = from_close([4000 + i * 2.0 for i in range(80)], spread=0.1)  # steady rise
    ev = [(30, "bullish"), (40, "bearish")]
    s = event_study(c, ev, "test", horizons=(5,), target_atr=1.0, stop_atr=1.0)
    assert s.n == 2 and s.hit_rate == 0.5 and s.small_sample
    assert s.horizons[5]["positive_share"] == 0.5


# --------------------------------------------------------------------------- levels & MTF
def test_reference_levels_prev_day_and_sessions() -> None:
    # 2 trading days of 1h bars starting Sun 23:00 UTC (18:00 NY)
    start = datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
    rows = [(4000 + i, 4001 + i, 3999 + i, 4000.5 + i) for i in range(48)]
    a = np.array(rows, dtype=float)
    c = Candles([start + timedelta(hours=i) for i in range(48)], a[:, 0], a[:, 1], a[:, 2], a[:, 3], "1h")
    now = start + timedelta(hours=47, minutes=30)  # Tue 17:30 NY (daily break) -> day 3 has started
    lv = {x.name: x for x in reference_levels(c, now)}
    # trading days start 17:00 NY = 22:00 UTC; bars from Sun 23:00 UTC: day 1 = bars 0..22, day 2 = 23..46
    assert lv["prev_day_high"].price == 4001 + 46 and lv["prev_day_low"].price == 3999 + 23
    assert "london_high" in lv and "asia_low" in lv


def test_mtf_context_bias_and_conflict() -> None:
    up = random_walk(300, 5, drift=0.8, vol=0.5)
    down = random_walk(300, 6, drift=-0.8, vol=0.5)
    frames = {
        "1d": Candles(up.t, up.o, up.h, up.l, up.c, "1d"),
        "4h": Candles(up.t, up.o, up.h, up.l, up.c, "4h"),
        "5m": Candles(down.t, down.o, down.h, down.l, down.c, "5m"),
    }
    ctx = mtf_context(frames, None, up.t[-1])
    assert ctx.bias == "bullish" and ctx.htf_conflict and ctx.alignment == "mixed" and ctx.htf_available
    short = mtf_context({"5m": up}, None, up.t[-1])
    assert not short.htf_available and any("1d/4h" in x for x in short.summary)
    one = analyze_tf(up)
    assert one.effective_trend == "up" and one.indicators["atr"] is not None


@settings(max_examples=25, deadline=None)
@given(st.integers(0, 10_000), st.integers(80, 299))
def test_property_regime_series_matches_live_classifier(seed: int, cut: int) -> None:
    from apex.analysis.regime import classify_series

    c = random_walk(300, seed, drift=float(seed % 3 - 1) * 0.3)
    series = classify_series(c, analyze_structure(c))
    h = c.head(cut)
    live = classify(h, analyze_structure(h))
    assert (series[cut - 1].label, series[cut - 1].direction, series[cut - 1].risk_multiplier) == (
        live.label,
        live.direction,
        live.risk_multiplier,
    )


@settings(max_examples=60, deadline=None)
@given(st.integers(0, 10_000), st.floats(0.5, 3.0))
def test_property_fast_sweeps_equal_reference(seed: int, vol: float) -> None:
    from apex.analysis.liquidity import sweeps_reference

    c = random_walk(400, seed, vol=vol)
    s = analyze_structure(c)
    fast = sweeps(c, s, equal_levels(c, s.swings))
    ref = sweeps_reference(c, s, equal_levels(c, s.swings))
    key = lambda x: (x.idx, x.direction, x.level, x.target, x.score)  # noqa: E731
    assert sorted(map(key, fast)) == sorted(map(key, ref))
