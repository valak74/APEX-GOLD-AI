"""Causal signal generation over a full history (backtest).

The analysis functions are proven causal (truncation-invariance property tests), so we run them once on the
whole series and, at each bar ``i``, use only objects *known by bar i* (swings confirmed ≤ i, pools known ≤ i,
events detected at i, regime/trend series at i, higher-timeframe bars that closed before the bar's time).

The rules mirror ``apex.analysis.opportunities`` (same stop buffer, ≥ 1R liquidity targets,
confluence weights)
so a backtest measures what the live scanner would have proposed.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np

from apex.analysis.indicators import Candles, atr, ema, rsi
from apex.analysis.liquidity import equal_levels, fair_value_gaps, order_blocks, sweeps
from apex.analysis.regime import classify_series
from apex.analysis.structure import analyze_structure, trend_series
from apex.market.sessions import bucket_start

Setup = Literal["liquidity_sweep_reversal", "trend_pullback"]


@dataclass(frozen=True)
class StrategyParams:
    setup: Setup = "liquidity_sweep_reversal"
    stop_buffer_atr: float = 0.2
    min_quality: int = 0  # 0..100 share of confluence weight
    require_htf_alignment: bool = False
    max_hold_bars: int = 48
    sessions: tuple[str, ...] = ("asia", "london", "newyork")  # by UTC hour bands, see SESSION_HOURS
    min_target_r: float = 1.0


SESSION_HOURS = {"asia": range(0, 7), "london": range(7, 13), "newyork": range(13, 21)}


@dataclass(frozen=True)
class Signal:
    idx: int  # detection bar (entry at the next bar's open)
    t: datetime
    direction: Literal["bullish", "bearish"]
    stop: float
    target: float
    quality: int
    regime: str
    regime_multiplier: float
    setup: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


def htf_trend_at(c: Candles, tf: str) -> tuple[list[datetime], list[str]]:
    """Resample to ``tf``, return (bar end times, structure trend after each completed bar)."""
    if not len(c):
        return [], []
    groups: dict[datetime, list[int]] = {}
    for i, t in enumerate(c.t):
        groups.setdefault(bucket_start(t, tf), []).append(i)
    starts = sorted(groups)[:-1]  # last group may be incomplete
    if len(starts) < 10:
        return [], []
    o = np.array([c.o[groups[s][0]] for s in starts])
    h = np.array([c.h[groups[s]].max() for s in starts])
    lo = np.array([c.l[groups[s]].min() for s in starts])
    cl = np.array([c.c[groups[s][-1]] for s in starts])
    hc = Candles(starts, o, h, lo, cl, tf)
    ends = [c.t[groups[s][-1]] for s in starts]  # known after the last lower-tf bar of the group closes
    return ends, trend_series(analyze_structure(hc), len(hc))


def _session_ok(t: datetime, allowed: tuple[str, ...]) -> bool:
    return any(t.hour in SESSION_HOURS[s] for s in allowed)


def generate(c: Candles, p: StrategyParams) -> list[Signal]:
    n = len(c)
    if n < 80:
        return []
    st = analyze_structure(c)
    a, r, e50 = atr(c), rsi(c.c), ema(c.c, 50)
    regimes = classify_series(c, st)
    pools = equal_levels(c, st.swings)
    htf = {tf: htf_trend_at(c, tf) for tf in ("1h", "4h")}

    def htf_bias(t: datetime) -> str:
        score = wsum = 0.0
        for tf, w in (("4h", 2.5), ("1h", 2.0)):
            ends, trends = htf[tf]
            k = bisect_right(ends, t) - 1
            if k >= 0:
                score += w * {"up": 1.0, "down": -1.0}.get(trends[k], 0.0)
                wsum += w
        if wsum == 0:
            return "neutral"
        b = score / wsum
        return "bullish" if b >= 0.33 else "bearish" if b <= -0.33 else "neutral"

    # liquidity candidates, sorted by the bar they become known
    highs = sorted(
        [(s.confirmed_idx, s.price) for s in st.swings if s.kind == "high"]
        + [(q.idx, q.level) for q in pools if q.side == "highs"]
    )
    lows = sorted(
        [(s.confirmed_idx, s.price) for s in st.swings if s.kind == "low"]
        + [(q.idx, q.level) for q in pools if q.side == "lows"]
    )

    def target_for(direction: str, i: int, entry: float, stop: float) -> float:
        risk = abs(entry - stop)
        sgn = 1 if direction == "bullish" else -1
        cands = [
            lv
            for k, lv in (highs if direction == "bullish" else lows)
            if k <= i and (lv - entry) * sgn >= p.min_target_r * risk
        ]
        if not cands:
            return entry + sgn * 2 * risk
        return min(cands, key=lambda lv: (lv - entry) * sgn)

    out: list[Signal] = []
    if p.setup == "liquidity_sweep_reversal":
        for sw in sweeps(c, st, pools):
            i = sw.idx
            if i >= n - 1 or np.isnan(a[i]) or not _session_ok(c.t[i], p.sessions):
                continue
            unit = float(a[i])
            d = sw.direction
            entry = float(c.c[i])
            stop = (
                sw.extreme - p.stop_buffer_atr * unit
                if d == "bullish"
                else sw.extreme + p.stop_buffer_atr * unit
            )
            bias = htf_bias(c.t[i])
            reg = regimes[i]
            rsi_v = r[i]
            shift = any(e.idx == i and e.direction == d for e in st.events)
            confs = [
                (3.0, True),
                (1.0, sw.target == "pool"),
                (2.0, shift),
                (1.0, not np.isnan(rsi_v) and (rsi_v < 35 if d == "bullish" else rsi_v > 65)),
                (1.0, reg.label in ("range", "weak_trend")),
                (2.0, bias == d),
            ]
            q = round(100 * sum(w for w, ok in confs if ok) / sum(w for w, _ in confs))
            if q < p.min_quality or (p.require_htf_alignment and bias != d):
                continue
            out.append(
                Signal(
                    i,
                    c.t[i],
                    d,
                    stop,
                    target_for(d, i, entry, stop),
                    q,
                    reg.label,
                    reg.risk_multiplier,
                    p.setup,
                )
            )
    else:
        zones = sorted(fair_value_gaps(c) + order_blocks(c, st), key=lambda z: z.idx)
        used: set[tuple[int, str]] = set()
        for i in range(80, n - 1):
            if np.isnan(a[i]) or not _session_ok(c.t[i], p.sessions):
                continue
            bias = htf_bias(c.t[i])
            if bias == "neutral":
                continue
            for z in zones:
                if z.idx >= i:
                    break
                if z.direction != bias or (z.idx, z.kind) in used:
                    continue
                if z.invalidated_idx is not None and z.invalidated_idx <= i:
                    continue
                if not (c.l[i] <= z.top and c.h[i] >= z.bottom):
                    continue
                used.add((z.idx, z.kind))
                unit = float(a[i])
                entry = float(c.c[i])
                stop = (
                    z.bottom - p.stop_buffer_atr * unit
                    if bias == "bullish"
                    else z.top + p.stop_buffer_atr * unit
                )
                if (entry - stop) * (1 if bias == "bullish" else -1) <= 0:
                    continue
                reg = regimes[i]
                above = (c.c[i] > e50[i]) if bias == "bullish" else (c.c[i] < e50[i])
                confs = [
                    (3.0, True),
                    (2.0, True),
                    (1.0, bool(above) and not np.isnan(e50[i])),
                    (1.0, reg.label in ("strong_trend", "weak_trend")),
                ]
                q = round(100 * sum(w for w, ok in confs if ok) / sum(w for w, _ in confs))
                if q < p.min_quality:
                    continue
                out.append(
                    Signal(
                        i,
                        c.t[i],
                        bias,
                        stop,
                        target_for(bias, i, entry, stop),
                        q,
                        reg.label,
                        reg.risk_multiplier,
                        p.setup,
                    )
                )
                break
    return sorted(out, key=lambda s: s.idx)
