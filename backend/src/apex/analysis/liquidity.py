"""Liquidity concepts, defined objectively and causally.

* **Fair Value Gap (FVG)** — 3-bar imbalance confirmed at bar ``i``: bullish if ``low[i] > high[i-2]``
  (gap = [high[i-2], low[i]]), bearish if ``high[i] < low[i-2]``. Kept only if the gap ≥ ``min_atr`` × ATR.
  *Mitigated* when price later trades into the gap; *invalidated* (filled) when a close goes through it.
* **Equal highs / lows (liquidity pools)** — ≥ 2 confirmed swing highs (lows) within ``tol_atr`` × ATR of each
  other; level = the extreme of the group. Known when the second swing is confirmed. Resting stops are assumed
  beyond them (the reason they are called liquidity).
* **Liquidity sweep** — a bar trades beyond a pool / confirmed swing (by ≥ ``min_pierce_atr`` × ATR)
  and *closes back inside*. Bearish sweep of highs, bullish sweep of lows.
* **Displacement** — a bar whose body ≥ ``disp_atr`` × ATR.
* **Order block** — the last opposite-coloured candle before a displacement that produced a structure break
  (BOS/CHoCH) in the same direction; zone = that candle's high–low.
  Invalidated by a close beyond the far side.
* **Premium / discount** — position of the last close in the current dealing range (last confirmed swing low →
  swing high): > 0.5 premium, < 0.5 discount, equilibrium at 0.5.

Scores are 0–1 heuristics (size or pierce depth in ATR), NOT probabilities. Their predictive value is measured
by ``apex.analysis.stats`` on history before any strategy may rely on them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np

from apex.analysis.indicators import Candles, atr
from apex.analysis.structure import Structure, Swing

Dir = Literal["bullish", "bearish"]


@dataclass
class Zone:
    kind: Literal["FVG", "OB"]
    direction: Dir
    idx: int  # detection (confirmation) bar
    t: datetime
    top: float
    bottom: float
    score: float
    method: str
    invalidation: str
    mitigated_idx: int | None = None
    invalidated_idx: int | None = None

    @property
    def active(self) -> bool:
        return self.invalidated_idx is None


@dataclass
class Pool:
    side: Literal["highs", "lows"]
    level: float
    swing_idxs: list[int]
    idx: int  # known from (confirmation of the last swing)
    t: datetime
    swept_idx: int | None = None


@dataclass
class Sweep:
    direction: Dir  # bullish = lows swept then closed back above
    idx: int
    t: datetime
    level: float
    extreme: float
    score: float
    target: str  # "pool" | "swing"
    method: str
    invalidation_level: float
    invalidation: str


def _unit(a: np.ndarray, c: Candles, i: int) -> float:
    v = float(a[i])
    return v if not np.isnan(v) and v > 0 else max(float(c.h[i] - c.l[i]), 1e-9)


def fair_value_gaps(c: Candles, min_atr: float = 0.25, atr_n: int = 14) -> list[Zone]:
    a = atr(c, atr_n)
    out: list[Zone] = []
    for i in range(2, len(c)):
        u = _unit(a, c, i)
        if c.l[i] > c.h[i - 2] and (c.l[i] - c.h[i - 2]) >= min_atr * u:
            out.append(
                Zone(
                    "FVG",
                    "bullish",
                    i,
                    c.t[i],
                    float(c.l[i]),
                    float(c.h[i - 2]),
                    round(min(1.0, (c.l[i] - c.h[i - 2]) / u), 3),
                    f"3-bar gap ≥ {min_atr} ATR",
                    "close below the gap bottom",
                )
            )
        elif c.h[i] < c.l[i - 2] and (c.l[i - 2] - c.h[i]) >= min_atr * u:
            out.append(
                Zone(
                    "FVG",
                    "bearish",
                    i,
                    c.t[i],
                    float(c.l[i - 2]),
                    float(c.h[i]),
                    round(min(1.0, (c.l[i - 2] - c.h[i]) / u), 3),
                    f"3-bar gap ≥ {min_atr} ATR",
                    "close above the gap top",
                )
            )
    _track_zones(c, out)
    return out


def _track_zones(c: Candles, zones: list[Zone]) -> None:
    for z in zones:
        for j in range(z.idx + 1, len(c)):
            if z.mitigated_idx is None and c.l[j] <= z.top and c.h[j] >= z.bottom:
                z.mitigated_idx = j
            if (z.direction == "bullish" and c.c[j] < z.bottom) or (
                z.direction == "bearish" and c.c[j] > z.top
            ):
                z.invalidated_idx = j
                break


def equal_levels(c: Candles, swings: list[Swing], tol_atr: float = 0.1, atr_n: int = 14) -> list[Pool]:
    a = atr(c, atr_n)
    pools: list[Pool] = []
    for side, kind in (("highs", "high"), ("lows", "low")):
        group: list[Swing] = []
        for s in (x for x in swings if x.kind == kind):
            u = _unit(a, c, s.confirmed_idx)
            if group and abs(s.price - group[-1].price) <= tol_atr * u:
                group.append(s)
                lvl = max(x.price for x in group) if side == "highs" else min(x.price for x in group)
                pool = Pool(side, lvl, [x.idx for x in group], s.confirmed_idx, c.t[s.confirmed_idx])  # type: ignore[arg-type]
                if pools and pools[-1].side == side and pools[-1].swing_idxs == pool.swing_idxs[:-1]:
                    pools[-1] = pool  # same pool grew
                else:
                    pools.append(pool)
            else:
                group = [s]
    pools.sort(key=lambda p: p.idx)
    return pools


def sweeps(
    c: Candles, st: Structure, pools: list[Pool], min_pierce_atr: float = 0.05, atr_n: int = 14
) -> list[Sweep]:
    a = atr(c, atr_n)
    out: list[Sweep] = []
    targets: list[tuple[int, str, float, str]] = []  # known_from, side, level, target
    targets += [(p.idx, p.side, p.level, "pool") for p in pools]
    targets += [
        (s.confirmed_idx, "highs" if s.kind == "high" else "lows", s.price, "swing") for s in st.swings
    ]
    used: set[tuple[str, float]] = set()
    for i in range(len(c)):
        u = _unit(a, c, i)
        for known, side, lvl, tgt in targets:
            if known >= i or (side, lvl) in used:
                continue
            if side == "highs" and c.h[i] - lvl >= min_pierce_atr * u and c.c[i] < lvl:
                used.add((side, lvl))
                out.append(
                    Sweep(
                        "bearish",
                        i,
                        c.t[i],
                        lvl,
                        float(c.h[i]),
                        round(min(1.0, (c.h[i] - lvl) / u), 3),
                        tgt,
                        "wick above, close back below",
                        float(c.h[i]),
                        "close above the sweep high",
                    )
                )
            elif side == "lows" and lvl - c.l[i] >= min_pierce_atr * u and c.c[i] > lvl:
                used.add((side, lvl))
                out.append(
                    Sweep(
                        "bullish",
                        i,
                        c.t[i],
                        lvl,
                        float(c.l[i]),
                        round(min(1.0, (lvl - c.l[i]) / u), 3),
                        tgt,
                        "wick below, close back above",
                        float(c.l[i]),
                        "close below the sweep low",
                    )
                )
        for p in pools:
            if (
                p.swept_idx is None
                and p.idx < i
                and ((p.side == "highs" and c.h[i] > p.level) or (p.side == "lows" and c.l[i] < p.level))
            ):
                p.swept_idx = i
    # pools swept or broken are consumed; sweeps of the same level are de-duplicated (first one wins)
    return out


def order_blocks(c: Candles, st: Structure, disp_atr: float = 1.0, atr_n: int = 14) -> list[Zone]:
    a = atr(c, atr_n)
    out: list[Zone] = []
    for ev in st.events:
        i = ev.idx
        body = abs(c.c[i] - c.o[i])
        if body < disp_atr * _unit(a, c, i):
            continue  # break without displacement: no order block
        bullish = ev.direction == "bullish"
        for j in range(i - 1, max(-1, i - 20), -1):
            opposite = c.c[j] < c.o[j] if bullish else c.c[j] > c.o[j]
            if opposite:
                out.append(
                    Zone(
                        "OB",
                        ev.direction,
                        i,
                        c.t[i],
                        float(c.h[j]),
                        float(c.l[j]),
                        round(min(1.0, body / (2 * _unit(a, c, i))), 3),
                        f"last opposite candle before {ev.kind} with displacement ≥ {disp_atr} ATR",
                        "close below the block" if bullish else "close above the block",
                    )
                )
                break
    _track_zones(c, out)
    return out


def premium_discount(c: Candles, st: Structure) -> dict[str, float | str] | None:
    if st.last_high is None or st.last_low is None or st.last_high.price <= st.last_low.price:
        return None
    hi, lo, close = st.last_high.price, st.last_low.price, float(c.c[-1])
    pos = (close - lo) / (hi - lo)
    zone = "premium" if pos > 0.55 else "discount" if pos < 0.45 else "equilibrium"
    return {
        "range_high": hi,
        "range_low": lo,
        "equilibrium": (hi + lo) / 2,
        "position": round(pos, 3),
        "zone": zone,
    }
