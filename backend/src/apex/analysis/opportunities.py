"""Opportunity detection: turns the analysis into explicit, explainable trade *ideas*.

An opportunity is NOT a trade. It is a setup with a complete plan (entry, stop, targets) and the evidence
behind
it. Whether it may be traded is decided later by the deterministic risk engine (and, before that, by the
setup's measured historical edge). Every opportunity carries:
* ``confluences`` — each criterion checked, ✓ or ✗, with its weight and a French explanation;
* ``warnings``  — conflicts (higher-timeframe disagreement, high volatility, low R:R…);
* ``blockers``  — conditions that forbid trading it right now (news lockout, bad data quality);
* ``evidence``  — what the chart must draw (levels, zones, the triggering bar);
* ``quality``   — share of confluence weight satisfied (0–100). A **heuristic score, not a probability**.

Setups (causal: only bars up to the last *completed* one are used):
* ``liquidity_sweep_reversal`` — liquidity taken beyond a pool/swing, close back inside (spec strategy B).
* ``trend_pullback``           — higher-timeframe trend, pullback into an active FVG / order block in the
                                 discount (bullish) or premium (bearish) half of the range (strategies A/J).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import numpy as np

from apex.analysis.engine import MTFContext
from apex.analysis.indicators import Candles, atr, ema, rsi
from apex.analysis.liquidity import equal_levels, fair_value_gaps, order_blocks, premium_discount, sweeps
from apex.analysis.stats import event_study
from apex.analysis.structure import analyze_structure

Dir = Literal["bullish", "bearish"]
SETUP_FR = {
    "liquidity_sweep_reversal": "Retournement après balayage de liquidité",
    "trend_pullback": "Repli dans la tendance de fond",
}
MIN_RR = 1.5
STOP_BUFFER_ATR = 0.2
RECENT_BARS = 3


@dataclass
class Confluence:
    key: str
    label: str
    ok: bool
    weight: float
    detail: str


@dataclass
class Opportunity:
    id: str
    setup: str
    setup_label: str
    direction: Dir
    timeframe: str
    detected_at: datetime
    entry: float
    stop: float
    targets: list[float]
    risk_reward: float
    quality: int
    confluences: list[Confluence]
    warnings: list[str]
    blockers: list[str]
    invalidation: str
    evidence: dict[str, Any]
    stats: dict[str, Any] | None = None
    status: str = "active"
    narrative: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _id(setup: str, tf: str, t: datetime, direction: str) -> str:
    return hashlib.sha1(f"{setup}|{tf}|{t.isoformat()}|{direction}".encode()).hexdigest()[:16]  # noqa: S324


def _targets(direction: Dir, entry: float, stop: float, candidates: list[float]) -> list[float]:
    """Liquidity levels at least 1R away (closer ones are not worth the risk); else a measured 2R target."""
    risk = abs(entry - stop)
    s = 1 if direction == "bullish" else -1
    levels = sorted(
        {round(x, 2) for x in candidates if (x - entry) * s >= 1.0 * risk}, key=lambda x: (x - entry) * s
    )
    out: list[float] = []
    for lv in levels:  # a second target must be meaningfully further (≥ 0.5R beyond the first)
        if not out or abs(lv - out[-1]) >= 0.5 * risk:
            out.append(lv)
        if len(out) == 2:
            break
    if not out:
        out = [round(entry + s * 2 * risk, 2)]  # no liquidity far enough: measured 2R target
    return out


def _context_checks(direction: Dir, ctx: MTFContext | None, pd_zone: str | None) -> list[Confluence]:
    out: list[Confluence] = []
    if ctx is not None:
        want = "bullish" if direction == "bullish" else "bearish"
        agree = ctx.bias == want
        label = {"bullish": "haussier", "bearish": "baissier", "neutral": "neutre"}[ctx.bias]
        out.append(
            Confluence(
                "htf_bias",
                "Biais multi-unités de temps",
                agree,
                2.0,
                f"biais {label} ({ctx.bias_score:+.2f})"
                + ("" if ctx.htf_available else ", fond 1d/4h indisponible"),
            )
        )
    if pd_zone is not None:
        good = (direction == "bullish" and pd_zone == "discount") or (
            direction == "bearish" and pd_zone == "premium"
        )
        out.append(
            Confluence(
                "pd_zone",
                "Zone de prix favorable",
                good,
                1.0,
                f"prix en zone {pd_zone}"
                + (
                    " — achat bon marché"
                    if good and direction == "bullish"
                    else " — vente chère"
                    if good
                    else " — zone défavorable"
                ),
            )
        )
    return out


def _finish(
    setup: str,
    direction: Dir,
    c: Candles,
    i: int,
    entry: float,
    stop: float,
    targets: list[float],
    confs: list[Confluence],
    warnings: list[str],
    invalidation: str,
    evidence: dict[str, Any],
    event_t: datetime,
) -> Opportunity:
    risk = abs(entry - stop)
    rr = round(abs(targets[0] - entry) / risk, 2) if risk > 0 else 0.0
    total = sum(x.weight for x in confs) or 1.0
    quality = round(100 * sum(x.weight for x in confs if x.ok) / total)
    if rr < MIN_RR:
        warnings.append(f"rapport rendement/risque faible ({rr:.2f} < {MIN_RR})")
    return Opportunity(
        id=_id(setup, c.timeframe, event_t, direction),  # one idea per triggering event, however often seen
        setup=setup,
        setup_label=SETUP_FR[setup],
        direction=direction,
        timeframe=c.timeframe,
        detected_at=c.t[i],
        entry=round(entry, 2),
        stop=round(stop, 2),
        targets=targets,
        risk_reward=rr,
        quality=quality,
        confluences=confs,
        warnings=warnings,
        blockers=[],
        invalidation=invalidation,
        evidence=evidence,
    )


def detect(c: Candles, ctx: MTFContext | None = None, with_stats: bool = True) -> list[Opportunity]:
    """Scan the last completed bars of one timeframe. ``c`` must contain completed bars only."""
    if len(c) < 60:
        return []
    st = analyze_structure(c)
    a, r, e50 = atr(c), rsi(c.c), ema(c.c, 50)
    i = len(c) - 1
    unit = float(a[i]) if not np.isnan(a[i]) else float(c.h[i] - c.l[i])
    pd = premium_discount(c, st)
    pd_zone = str(pd["zone"]) if pd else None
    pools = equal_levels(c, st.swings)
    highs = [s.price for s in st.swings if s.kind == "high"] + [p.level for p in pools if p.side == "highs"]
    lows = [s.price for s in st.swings if s.kind == "low"] + [p.level for p in pools if p.side == "lows"]
    regime_label = None
    if ctx is not None and c.timeframe in ctx.timeframes:
        regime_label = ctx.timeframes[c.timeframe].regime.get("label")
    out: list[Opportunity] = []

    # ---------------------------------------------------------------- liquidity sweep reversal
    for sw in sweeps(c, st, pools):
        if sw.idx < i - RECENT_BARS + 1:
            continue
        d: Dir = sw.direction
        entry = float(c.c[i])
        stop = sw.extreme - STOP_BUFFER_ATR * unit if d == "bullish" else sw.extreme + STOP_BUFFER_ATR * unit
        if (entry - stop) * (1 if d == "bullish" else -1) <= 0:
            continue  # price already beyond the sweep extreme: idea invalid
        later = [e for e in st.events if e.idx >= sw.idx and e.direction == d]
        pool_label = (
            ("égaux " + ("bas" if d == "bullish" else "hauts")) if sw.target == "pool" else "point pivot"
        )
        rsi_v = float(r[sw.idx]) if not np.isnan(r[sw.idx]) else None
        confs = [
            Confluence(
                "sweep",
                "Balayage de liquidité",
                True,
                3.0,
                f"mèche {'sous' if d == 'bullish' else 'au-dessus de'} {sw.level:.2f} "
                f"({pool_label}),"
                f" clôture revenue à l'intérieur",
            ),
            Confluence(
                "pool",
                "Liquidité « égaux hauts/bas »",
                sw.target == "pool",
                1.0,
                "plusieurs sommets/creux au même niveau : stops accumulés"
                if sw.target == "pool"
                else "simple point pivot (moins de stops accumulés)",
            ),
            Confluence(
                "structure_shift",
                "Changement de structure confirmé",
                bool(later),
                2.0,
                f"{later[-1].kind} {'haussier' if d == 'bullish' else 'baissier'} à {later[-1].level:.2f}"
                if later
                else "pas encore de BOS/CHoCH dans le sens du retournement",
            ),
            Confluence(
                "rsi",
                "RSI en zone extrême au balayage",
                rsi_v is not None and (rsi_v < 35 if d == "bullish" else rsi_v > 65),
                1.0,
                f"RSI {rsi_v:.0f}" if rsi_v is not None else "RSI indisponible",
            ),
            Confluence(
                "regime",
                "Régime adapté (range / tendance faible)",
                regime_label in ("range", "weak_trend"),
                1.0,
                f"régime : {regime_label or 'inconnu'}",
            ),
            *_context_checks(d, ctx, pd_zone),
        ]
        warnings = []
        if ctx is not None and ctx.htf_conflict:
            warnings.append("l'unité d'exécution va contre le fond")
        targets = _targets(d, entry, stop, highs if d == "bullish" else lows)
        evidence = {
            "sweep": {"level": sw.level, "extreme": sw.extreme, "t": sw.t},
            "structure_events": [asdict(e) for e in later[-2:]],
            "trigger_bar": sw.t,  # the sweep bar itself
        }
        out.append(
            _finish(
                "liquidity_sweep_reversal",
                d,
                c,
                i,
                entry,
                stop,
                targets,
                confs,
                warnings,
                (
                    "clôture sous le bas du balayage"
                    if d == "bullish"
                    else "clôture au-dessus du haut du balayage"
                )
                + f" ({sw.invalidation_level:.2f})",
                evidence,
                sw.t,
            )
        )
        if with_stats:
            hist = [(s.idx, s.direction) for s in sweeps(c, st, equal_levels(c, st.swings)) if s.idx < sw.idx]
            out[-1].stats = _stats(c, hist, "balayages de liquidité", out[-1], unit)

    # ---------------------------------------------------------------- trend pullback into FVG / OB
    if ctx is not None and ctx.bias in ("bullish", "bearish") and ctx.htf_available:
        d2: Dir = ctx.bias  # type: ignore[assignment]
        zones = [
            z
            for z in fair_value_gaps(c) + order_blocks(c, st)
            if z.active and z.direction == d2 and z.idx < i
        ]
        lo3, hi3 = (
            float(c.l[i - RECENT_BARS + 1 : i + 1].min()),
            float(c.h[i - RECENT_BARS + 1 : i + 1].max()),
        )
        touched = [z for z in zones if lo3 <= z.top and hi3 >= z.bottom]
        if touched:
            z = touched[-1]
            entry = float(c.c[i])
            stop = z.bottom - STOP_BUFFER_ATR * unit if d2 == "bullish" else z.top + STOP_BUFFER_ATR * unit
            if (entry - stop) * (1 if d2 == "bullish" else -1) > 0:
                trend_ok = st.trend in ("up" if d2 == "bullish" else "down", "none")
                above = (c.c[i] > e50[i]) if d2 == "bullish" else (c.c[i] < e50[i])
                confs = [
                    Confluence(
                        "htf_trend",
                        "Tendance de fond",
                        True,
                        3.0,
                        f"biais {'haussier' if d2 == 'bullish' else 'baissier'} {ctx.bias_score:+.2f}",
                    ),
                    Confluence(
                        "zone",
                        f"Repli dans un {'Fair Value Gap' if z.kind == 'FVG' else 'order block'}",
                        True,
                        2.0,
                        f"zone {z.bottom:.2f}–{z.top:.2f} touchée",
                    ),
                    Confluence(
                        "exec_structure",
                        "Structure d'exécution non contraire",
                        trend_ok,
                        1.5,
                        f"structure {st.trend}",
                    ),
                    Confluence(
                        "ema50",
                        "Prix du bon côté de l'EMA 50",
                        bool(above) if not np.isnan(e50[i]) else False,
                        1.0,
                        f"EMA50 {e50[i]:.2f}" if not np.isnan(e50[i]) else "EMA50 indisponible",
                    ),
                    Confluence(
                        "regime",
                        "Régime de tendance",
                        regime_label in ("strong_trend", "weak_trend"),
                        1.0,
                        f"régime : {regime_label or 'inconnu'}",
                    ),
                    *[x for x in _context_checks(d2, ctx, pd_zone) if x.key == "pd_zone"],
                ]
                warnings = []
                targets = _targets(d2, entry, stop, highs if d2 == "bullish" else lows)
                evidence = {
                    "zone": {"kind": z.kind, "top": z.top, "bottom": z.bottom, "t": z.t},
                    "trigger_bar": c.t[i],
                }
                out.append(
                    _finish(
                        "trend_pullback",
                        d2,
                        c,
                        i,
                        entry,
                        stop,
                        targets,
                        confs,
                        warnings,
                        (
                            "clôture sous le bas de la zone"
                            if d2 == "bullish"
                            else "clôture au-dessus du haut de la zone"
                        )
                        + f" ({z.bottom if d2 == 'bullish' else z.top:.2f})",
                        evidence,
                        z.t,
                    )
                )
                if with_stats:
                    hist = [
                        (x.idx, x.direction)
                        for x in fair_value_gaps(c)
                        if x.idx < z.idx and x.direction == d2
                    ]
                    out[-1].stats = _stats(c, hist, "Fair Value Gaps dans ce sens", out[-1], unit)
    return out


def _stats(
    c: Candles, hist: Sequence[tuple[int, str]], label: str, opp: Opportunity, unit: float
) -> dict[str, Any]:
    """How often did price reach this opportunity's first target before its stop after the same pattern?"""
    if unit <= 0:
        return {"label": label, "n": 0}
    stop_atr = abs(opp.entry - opp.stop) / unit
    tgt_atr = abs(opp.targets[0] - opp.entry) / unit
    s = event_study(c, hist, label, horizons=(5, 10), target_atr=tgt_atr, stop_atr=stop_atr, max_bars=30)
    return {
        "label": label,
        "n": s.n,
        "hit_rate": s.hit_rate,
        "hit_rate_ci": s.hit_rate_ci,
        "small_sample": s.small_sample,
        "stop_atr": round(stop_atr, 2),
        "target_atr": round(tgt_atr, 2),
        "breakeven_rate": round(1 / (1 + opp.risk_reward), 3) if opp.risk_reward > 0 else None,
    }


def opportunity_from_payload(p: dict[str, Any]) -> Opportunity:
    """Rebuild an Opportunity from its stored JSON payload (for re-explaining)."""
    data = dict(p)
    data["detected_at"] = datetime.fromisoformat(str(data["detected_at"]))
    data["confluences"] = [Confluence(**x) for x in data.get("confluences", [])]
    keys = Opportunity.__dataclass_fields__.keys()
    return Opportunity(**{k: v for k, v in data.items() if k in keys})
