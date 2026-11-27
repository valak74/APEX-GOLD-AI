"""Single-timeframe analysis snapshot and multi-timeframe (MTF) context.

MTF roles (spec §6): higher = 1d/4h (macro bias, regime), intermediate = 1h/15m (structure, liquidity),
execution = 5m/1m (timing). The aggregated bias weights higher timeframes more. ``htf_conflict`` flags
when the
execution timeframe's structure points against the higher-timeframe bias — strategies must not trade
against it
unless explicitly validated to do so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from apex.analysis.indicators import Candles, atr, ema, rsi
from apex.analysis.levels import reference_levels
from apex.analysis.liquidity import (
    current_pools,
    equal_levels,
    fair_value_gaps,
    order_blocks,
    premium_discount,
    sweeps,
)
from apex.analysis.regime import classify
from apex.analysis.structure import analyze_structure

ROLES = {
    "1d": "higher",
    "4h": "higher",
    "1h": "intermediate",
    "15m": "intermediate",
    "5m": "execution",
    "1m": "execution",
}
WEIGHTS = {"1d": 3.0, "4h": 2.5, "1h": 2.0, "15m": 1.5, "5m": 1.0, "1m": 0.5}


def _num(x: float) -> float | None:
    return None if np.isnan(x) else round(float(x), 2)


@dataclass
class TFAnalysis:
    timeframe: str
    role: str
    bars: int
    last_close: float | None
    trend: str  # market-structure trend: up / down / none
    effective_trend: str  # structure trend, or the regime direction when structure is still undetermined
    regime: dict[str, Any]
    indicators: dict[str, float | None]
    last_event: dict[str, Any] | None
    premium_discount: dict[str, Any] | None
    active_fvgs: list[dict[str, Any]] = field(default_factory=list)
    active_order_blocks: list[dict[str, Any]] = field(default_factory=list)
    liquidity_pools: list[dict[str, Any]] = field(default_factory=list)
    recent_sweeps: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    swings: list[dict[str, Any]] = field(default_factory=list)


def analyze_tf(c: Candles, k: int = 3, max_items: int = 6) -> TFAnalysis:
    tf = c.timeframe
    if len(c) == 0:
        return TFAnalysis(
            tf,
            ROLES.get(tf, "?"),
            0,
            None,
            "none",
            "none",
            {"label": "insufficient_data", "direction": "neutral"},
            {},
            None,
            None,
        )
    st = analyze_structure(c, k)
    reg = classify(c, st)
    fvgs = [z for z in fair_value_gaps(c) if z.active]
    obs = [z for z in order_blocks(c, st) if z.active]
    pools = [p for p in current_pools(equal_levels(c, st.swings)) if p.swept_idx is None]
    sw = sweeps(c, st, equal_levels(c, st.swings))
    a, r = atr(c), rsi(c.c)
    e50, e200 = ema(c.c, 50), ema(c.c, 200)
    i = len(c) - 1

    def ev(e: Any) -> dict[str, Any]:
        d = asdict(e)
        d.pop("idx", None)
        return d

    return TFAnalysis(
        timeframe=tf,
        role=ROLES.get(tf, "?"),
        bars=len(c),
        last_close=round(float(c.c[i]), 2),
        trend=st.trend,
        effective_trend=st.trend
        if st.trend != "none"
        else {"bullish": "up", "bearish": "down"}.get(
            reg.direction if reg.label in ("strong_trend", "weak_trend", "breakout") else "", "none"
        ),
        regime=asdict(reg),
        indicators={"atr": _num(a[i]), "rsi": _num(r[i]), "ema50": _num(e50[i]), "ema200": _num(e200[i])},
        last_event=ev(st.events[-1]) if st.events else None,
        premium_discount=premium_discount(c, st),
        active_fvgs=[ev(z) for z in fvgs[-max_items:]],
        active_order_blocks=[ev(z) for z in obs[-max_items:]],
        liquidity_pools=[
            {"side": p.side, "level": p.level, "touches": len(p.swing_idxs), "t": p.t}
            for p in pools[-max_items:]
        ],
        recent_sweeps=[ev(s) for s in sw[-max_items:]],
        events=[ev(e) for e in st.events[-max_items * 2 :]],
        swings=[
            {"kind": s.kind, "price": s.price, "t": s.t, "label": s.label}
            for s in st.swings[-max_items * 2 :]
        ],
    )


@dataclass
class MTFContext:
    generated_at: datetime
    bias: str
    bias_score: float  # -1 (fully bearish) .. +1 (fully bullish)
    alignment: str
    htf_conflict: bool
    htf_available: bool  # False when 1d/4h lack data: the bias is then short-term only
    summary: list[str]
    timeframes: dict[str, TFAnalysis]
    levels: list[dict[str, Any]]


def mtf_context(frames: dict[str, Candles], intraday: Candles | None, now: datetime) -> MTFContext:
    tfs = {tf: analyze_tf(c) for tf, c in frames.items()}
    score = wsum = 0.0
    for tf, a in tfs.items():
        if a.regime.get("label") == "insufficient_data":
            continue
        w = WEIGHTS.get(tf, 1.0)
        s = {"up": 1.0, "down": -1.0}.get(a.effective_trend, 0.0)
        score += w * s
        wsum += w
    bias_score = round(score / wsum, 3) if wsum else 0.0
    bias = "bullish" if bias_score >= 0.33 else "bearish" if bias_score <= -0.33 else "neutral"
    higher = [a.effective_trend for a in tfs.values() if a.role == "higher" and a.effective_trend != "none"]
    exec_ = [a.effective_trend for a in tfs.values() if a.role == "execution" and a.effective_trend != "none"]
    htf_dir = higher[0] if higher and all(h == higher[0] for h in higher) else None
    htf_conflict = bool(htf_dir and exec_ and any(e != htf_dir for e in exec_))
    trends = {a.effective_trend for a in tfs.values() if a.effective_trend != "none"}
    alignment = "aligned" if len(trends) == 1 else "mixed" if trends else "undetermined"
    htf_available = any(
        a.role == "higher" and a.regime.get("label") != "insufficient_data" for a in tfs.values()
    )
    summary = []
    for tf in ("1d", "4h", "1h", "15m", "5m"):
        if tf in tfs:
            a = tfs[tf]
            summary.append(f"{tf}: trend {a.effective_trend}, regime {a.regime.get('label')}")
    if htf_conflict:
        summary.append("execution timeframe structure is against the higher-timeframe bias")
    if not htf_available:
        summary.append("higher timeframes (1d/4h) lack data: bias reflects short-term timeframes only")
    levels = [asdict(lv) for lv in reference_levels(intraday, now)] if intraday is not None else []
    return MTFContext(now, bias, bias_score, alignment, htf_conflict, htf_available, summary, tfs, levels)
