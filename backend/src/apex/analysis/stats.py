"""Event studies: how did price behave after a detected pattern, historically?

For each event (bar index + direction), measured *after* its detection bar (no look-ahead into the event
itself):
* forward signed return at horizons ``h`` bars, in ATR units at detection (positive = moved in the
  expected way);
* first-touch outcome: did price reach +``target_atr`` × ATR before −``stop_atr`` × ATR within
  ``max_bars``
  (bar high/low; if both inside the same bar the stop is assumed first — conservative)?
Reported with 95 % confidence intervals (Wilson for rates, bootstrap for means). Small samples are
flagged.
These are descriptive statistics, not trading results: no costs, no execution model (see phase 7
backtester).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from apex.analysis.indicators import Candles, atr

MIN_SAMPLE = 30


@dataclass
class EventStats:
    pattern: str
    n: int
    horizons: dict[int, dict[str, float | None]]
    hit_rate: float | None
    hit_rate_ci: tuple[float, float] | None
    unresolved: int
    target_atr: float
    stop_atr: float
    small_sample: bool


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_mean_ci(x: np.ndarray, iters: int = 2000, seed: int = 7) -> tuple[float, float]:
    if len(x) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(iters, len(x)), replace=True).mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def event_study(
    c: Candles,
    events: Sequence[tuple[int, str]],
    pattern: str,
    horizons: Sequence[int] = (1, 3, 5, 10, 20),
    target_atr: float = 1.0,
    stop_atr: float = 1.0,
    max_bars: int = 20,
) -> EventStats:
    a = atr(c)
    n_bars = len(c)
    rets: dict[int, list[float]] = {h: [] for h in horizons}
    wins = losses = unresolved = 0
    used = 0
    for idx, direction in events:
        unit = float(a[idx]) if idx < n_bars else float("nan")
        if np.isnan(unit) or unit <= 0:
            continue
        used += 1
        s = 1.0 if direction == "bullish" else -1.0
        entry = float(c.c[idx])
        for h in horizons:
            if idx + h < n_bars:
                rets[h].append(s * (float(c.c[idx + h]) - entry) / unit)
        tgt, stp = entry + s * target_atr * unit, entry - s * stop_atr * unit
        outcome = None
        for j in range(idx + 1, min(n_bars, idx + 1 + max_bars)):
            hit_stop = c.l[j] <= stp if s > 0 else c.h[j] >= stp
            hit_tgt = c.h[j] >= tgt if s > 0 else c.l[j] <= tgt
            if hit_stop:
                outcome = False
                break
            if hit_tgt:
                outcome = True
                break
        if outcome is True:
            wins += 1
        elif outcome is False:
            losses += 1
        else:
            unresolved += 1
    out_h: dict[int, dict[str, float | None]] = {}
    for h, xs in rets.items():
        arr = np.array(xs)
        lo, hi = bootstrap_mean_ci(arr) if len(arr) >= 2 else (float("nan"), float("nan"))
        out_h[h] = {
            "n": float(len(arr)),
            "mean_atr": round(float(arr.mean()), 4) if len(arr) else None,
            "median_atr": round(float(np.median(arr)), 4) if len(arr) else None,
            "mean_ci_low": None if math.isnan(lo) else round(lo, 4),
            "mean_ci_high": None if math.isnan(hi) else round(hi, 4),
            "positive_share": round(float((arr > 0).mean()), 4) if len(arr) else None,
        }
    resolved = wins + losses
    return EventStats(
        pattern=pattern,
        n=used,
        horizons=out_h,
        hit_rate=round(wins / resolved, 4) if resolved else None,
        hit_rate_ci=tuple(round(v, 4) for v in wilson(wins, resolved)) if resolved else None,  # type: ignore[arg-type]
        unresolved=unresolved,
        target_atr=target_atr,
        stop_atr=stop_atr,
        small_sample=used < MIN_SAMPLE,
    )
