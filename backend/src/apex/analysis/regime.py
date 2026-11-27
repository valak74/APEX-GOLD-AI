"""Deterministic market-regime classification for one timeframe (at the last bar, causal).

Inputs: ADX(14) with ±DI, Kaufman efficiency ratio (20), ATR(14) percentile rank over 200 bars, structure
trend,
20-bar Donchian breakout. Rules are explicit and documented below; thresholds are parameters, to be
calibrated
on history (phase 7). A later ML regime model must beat these rules out-of-sample before replacing them.

* ``strong_trend``  ADX ≥ 30 and ER ≥ 0.35, DI direction not contradicted by structure (undetermined
  allowed)
* ``range``         ER < 0.20 with ADX < 30 (price goes nowhere; ADX alone is noisy in oscillations),
                    or ADX < 20 and ER < 0.25
* ``weak_trend``    ADX ≥ 20 otherwise (or strong ADX but structure disagrees)
* ``breakout``      close beyond the previous 20-bar high/low while ATR rank is rising above 0.6
* ``transition``    anything else
Volatility overlay: ``high`` if ATR rank ≥ 0.85, ``low`` if ≤ 0.15, else ``normal``.

``risk_multiplier`` (≤ 1, never > 1) is a *suggestion* the risk engine may apply to reduce size; it can
never
raise risk above the configured limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from apex.analysis.indicators import Candles, adx, atr, efficiency_ratio, percentile_rank
from apex.analysis.structure import Structure

Label = Literal["strong_trend", "weak_trend", "range", "breakout", "transition", "insufficient_data"]


@dataclass
class Regime:
    label: Label
    direction: Literal["bullish", "bearish", "neutral"]
    volatility: Literal["low", "normal", "high", "unknown"]
    risk_multiplier: float
    metrics: dict[str, float | None] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    suitable: list[str] = field(default_factory=list)


def _f(x: float) -> float | None:
    return None if np.isnan(x) else round(float(x), 4)


def classify(c: Candles, st: Structure, min_bars: int = 60) -> Regime:
    if len(c) < min_bars:
        return Regime(
            "insufficient_data", "neutral", "unknown", 0.0, reasons=[f"moins de {min_bars} bougies"]
        )
    a, pdi, mdi = adx(c)
    er = efficiency_ratio(c.c, 20)
    at = atr(c)
    rank = percentile_rank(at, 200)
    i = len(c) - 1
    m = {
        "adx": _f(a[i]),
        "plus_di": _f(pdi[i]),
        "minus_di": _f(mdi[i]),
        "efficiency": _f(er[i]),
        "atr": _f(at[i]),
        "atr_rank": _f(rank[i]),
    }
    if any(v is None for k, v in m.items() if k != "atr_rank"):
        return Regime(
            "insufficient_data",
            "neutral",
            "unknown",
            0.0,
            m,
            ["indicateurs en cours de calcul (historique trop court)"],
        )

    adx_v, er_v = float(a[i]), float(er[i])
    di_dir = "bullish" if pdi[i] > mdi[i] else "bearish"
    st_dir = {"up": "bullish", "down": "bearish"}.get(st.trend, "neutral")
    r = float(rank[i]) if not np.isnan(rank[i]) else None
    vol: Literal["low", "normal", "high", "unknown"] = (
        "unknown" if r is None else "high" if r >= 0.85 else "low" if r <= 0.15 else "normal"
    )
    reasons: list[str] = []
    hi20, lo20 = float(c.h[i - 20 : i].max()), float(c.l[i - 20 : i].min())
    rising = r is not None and not np.isnan(rank[i - 3]) and r >= 0.6 and r > float(rank[i - 3])
    label: Label
    direction: Literal["bullish", "bearish", "neutral"]
    if (c.c[i] > hi20 or c.c[i] < lo20) and rising:
        label, direction = "breakout", "bullish" if c.c[i] > hi20 else "bearish"
        reasons.append("clôture hors du range des 20 dernières bougies avec volatilité en hausse")
    elif adx_v >= 30 and er_v >= 0.35 and st_dir in (di_dir, "neutral"):
        label, direction = "strong_trend", di_dir  # type: ignore[assignment]
        agree = (
            "la structure confirme"
            if st_dir == di_dir
            else "structure pas encore formée (aucun repli confirmé)"
        )
        pressure = "acheteuse" if di_dir == "bullish" else "vendeuse"
        reasons.append(f"ADX {adx_v:.0f} ≥ 30, efficacité {er_v:.2f}, pression {pressure} ; {agree}")
    elif (er_v < 0.20 and adx_v < 30) or (adx_v < 20 and er_v < 0.25):
        label, direction = "range", "neutral"
        reasons.append(f"efficacité {er_v:.2f} : le prix oscille sans progresser (ADX {adx_v:.0f})")
    elif adx_v >= 20:
        label, direction = "weak_trend", di_dir  # type: ignore[assignment]
        if adx_v < 30:
            reasons.append(f"ADX {adx_v:.0f} entre 20 et 30, efficacité {er_v:.2f}")
        elif st_dir not in (di_dir, "neutral"):
            reasons.append("ADX élevé mais la structure de marché va dans l'autre sens")
        else:
            reasons.append(f"ADX {adx_v:.0f} mais efficacité modérée {er_v:.2f}")
    else:
        label, direction = "transition", "neutral"
        reasons.append("ni tendance ni range nets")

    mult = {"strong_trend": 1.0, "weak_trend": 0.75, "range": 0.75, "breakout": 0.5, "transition": 0.5}[label]
    if vol == "high":
        mult = min(mult, 0.5)
        reasons.append("volatilité élevée : réduction de taille suggérée")
    suitable = {
        "strong_trend": ["trend_pullback", "htf_trend_ltf_entry"],
        "weak_trend": ["trend_pullback", "liquidity_sweep_reversal"],
        "range": ["range_mean_reversion", "liquidity_sweep_reversal"],
        "breakout": ["breakout_confirmation"],
        "transition": [],
    }[label]
    return Regime(label, direction, vol, mult, m, reasons, suitable)
