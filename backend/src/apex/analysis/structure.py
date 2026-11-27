"""Objective market structure: swings, HH/HL/LH/LL, Break of Structure (BOS), Change of Character (CHoCH).

Definitions (all parameters explicit, all events causal):
* **Swing high** at bar ``i``: ``high[i]`` is strictly greater than the highs of the ``k`` bars on each side.
  It is only *known* at bar ``i + k`` (``confirmed_idx``) — never used before that.
* Swing labels compare with the previous confirmed swing of the same kind: HH/LH for highs, HL/LL for lows.
* **Trend state**: ``up`` after a bullish BOS/CHoCH, ``down`` after a bearish one, ``none`` initially.
* **BOS**: a bar *closes* beyond the most recent confirmed swing high (bullish) / low (bearish) in the
  direction of the current trend (or from ``none``).
* **CHoCH**: the same close-beyond event *against* the current trend — first sign the structure changed.
  Each swing level can be broken only once.
* Score (0–1, a heuristic, not a probability): break distance beyond the level in ATR, capped at 1 ATR.
* Invalidation: a bullish break is invalidated by a close back below the broken level's origin swing low
  (the swing low preceding the break); bearish symmetric. Recorded when it happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np

from apex.analysis.indicators import Candles, atr

Dir = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class Swing:
    kind: Literal["high", "low"]
    idx: int
    confirmed_idx: int
    price: float
    t: datetime
    label: str = ""  # HH, LH, HL, LL or "" for the first of its kind


@dataclass
class StructureEvent:
    kind: Literal["BOS", "CHoCH"]
    direction: Dir
    idx: int  # bar whose close broke the level (= detection time)
    t: datetime
    level: float  # broken swing price
    swing_idx: int
    score: float
    method: str
    invalidation_level: float | None
    invalidation: str
    invalidated_idx: int | None = None


@dataclass
class Structure:
    swings: list[Swing] = field(default_factory=list)
    events: list[StructureEvent] = field(default_factory=list)
    trend: Literal["up", "down", "none"] = "none"
    last_high: Swing | None = None
    last_low: Swing | None = None


def find_swings(c: Candles, k: int = 3) -> list[Swing]:
    out: list[Swing] = []
    prev = {"high": None, "low": None}  # type: dict[str, float | None]
    for i in range(k, len(c) - k):
        h, lo = c.h[i], c.l[i]
        win_h = np.concatenate([c.h[i - k : i], c.h[i + 1 : i + k + 1]])
        win_l = np.concatenate([c.l[i - k : i], c.l[i + 1 : i + k + 1]])
        if h > win_h.max():
            p = prev["high"]
            out.append(
                Swing("high", i, i + k, float(h), c.t[i], "" if p is None else ("HH" if h > p else "LH"))
            )
            prev["high"] = float(h)
        if lo < win_l.min():
            p = prev["low"]
            out.append(
                Swing("low", i, i + k, float(lo), c.t[i], "" if p is None else ("HL" if lo > p else "LL"))
            )
            prev["low"] = float(lo)
    return out


def analyze_structure(c: Candles, k: int = 3, atr_n: int = 14) -> Structure:
    """Walk bars in time order, using swings only once confirmed."""
    st = Structure(swings=find_swings(c, k))
    a = atr(c, atr_n)
    by_conf: dict[int, list[Swing]] = {}
    for s in st.swings:
        by_conf.setdefault(s.confirmed_idx, []).append(s)
    broken: set[int] = set()
    for i in range(len(c)):
        for s in by_conf.get(i, []):
            if s.kind == "high":
                st.last_high = s
            else:
                st.last_low = s
        close = float(c.c[i])
        unit = float(a[i]) if not np.isnan(a[i]) and a[i] > 0 else max(float(c.h[i] - c.l[i]), 1e-9)
        for ev in st.events:  # invalidation tracking
            if ev.invalidated_idx is None and ev.invalidation_level is not None:
                if (ev.direction == "bullish" and close < ev.invalidation_level) or (
                    ev.direction == "bearish" and close > ev.invalidation_level
                ):
                    ev.invalidated_idx = i
        hi, lo = st.last_high, st.last_low
        if hi is not None and hi.idx not in broken and i > hi.confirmed_idx - 1 and close > hi.price:
            kind: Literal["BOS", "CHoCH"] = "CHoCH" if st.trend == "down" else "BOS"
            broken.add(hi.idx)
            st.events.append(
                StructureEvent(
                    kind,
                    "bullish",
                    i,
                    c.t[i],
                    hi.price,
                    hi.idx,
                    round(min(1.0, (close - hi.price) / unit), 3),
                    f"close above confirmed swing high (k={k})",
                    lo.price if lo else None,
                    "close below the swing low preceding the break",
                )
            )
            st.trend = "up"
        elif lo is not None and lo.idx not in broken and close < lo.price:
            kind2: Literal["BOS", "CHoCH"] = "CHoCH" if st.trend == "up" else "BOS"
            broken.add(lo.idx)
            st.events.append(
                StructureEvent(
                    kind2,
                    "bearish",
                    i,
                    c.t[i],
                    lo.price,
                    lo.idx,
                    round(min(1.0, (lo.price - close) / unit), 3),
                    f"close below confirmed swing low (k={k})",
                    hi.price if hi else None,
                    "close above the swing high preceding the break",
                )
            )
            st.trend = "down"
    return st
