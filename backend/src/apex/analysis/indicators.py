"""Candles container and causal technical indicators.

Every indicator value at index ``i`` depends only on bars ``0..i`` (causal — no look-ahead).
Warm-up values are ``NaN``. Indicators are context, never standalone buy/sell signals.

No VWAP / volume profile: spot gold has no consolidated traded volume (tick counts are not volume).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import numpy.typing as npt

F = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Candles:
    t: list[datetime]  # bar start (UTC)
    o: F
    h: F
    l: F  # noqa: E741
    c: F
    timeframe: str = "?"

    def __len__(self) -> int:
        return len(self.t)

    @classmethod
    def from_rows(cls, rows: Sequence[Any], timeframe: str = "?") -> Candles:
        """From objects with ``start, open, high, low, close`` (BarRow, Bar, BarOut)."""
        return cls(
            t=[r.start for r in rows],
            o=np.array([float(r.open) for r in rows]),
            h=np.array([float(r.high) for r in rows]),
            l=np.array([float(r.low) for r in rows]),
            c=np.array([float(r.close) for r in rows]),
            timeframe=timeframe,
        )

    def head(self, n: int) -> Candles:
        return Candles(self.t[:n], self.o[:n], self.h[:n], self.l[:n], self.c[:n], self.timeframe)


def sma(x: F, n: int) -> F:
    out = np.full_like(x, np.nan)
    if len(x) >= n:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1 :] = (cs[n:] - cs[:-n]) / n
    return out


def ema(x: F, n: int) -> F:
    """EMA seeded with the SMA of the first ``n`` values."""
    out = np.full_like(x, np.nan)
    if len(x) < n:
        return out
    k = 2.0 / (n + 1)
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def wilder(x: F, n: int) -> F:
    """Wilder's smoothing (RMA), seeded with the SMA of the first ``n`` valid values."""
    out = np.full_like(x, np.nan)
    valid = np.where(~np.isnan(x))[0]
    if len(valid) < n:
        return out
    s = valid[0]
    out[s + n - 1] = x[s : s + n].mean()
    for i in range(s + n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def true_range(c: Candles) -> F:
    prev = np.roll(c.c, 1)
    tr = np.maximum(c.h - c.l, np.maximum(np.abs(c.h - prev), np.abs(c.l - prev)))
    if len(tr):
        tr[0] = c.h[0] - c.l[0]
    return tr


def atr(c: Candles, n: int = 14) -> F:
    return wilder(true_range(c), n)


def rsi(x: F, n: int = 14) -> F:
    d = np.diff(x, prepend=np.nan)
    gain, loss = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    gain[0] = loss[0] = np.nan
    ag, al = wilder(gain, n), wilder(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = ag / al
        out = 100 - 100 / (1 + rs)
    out[(al == 0) & ~np.isnan(ag)] = 100.0
    return out


def macd(x: F, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[F, F, F]:
    line = ema(x, fast) - ema(x, slow)
    sig = np.full_like(x, np.nan)
    valid = np.where(~np.isnan(line))[0]
    if len(valid):
        sig[valid[0] :] = ema(line[valid[0] :], signal)
    return line, sig, line - sig


def bollinger(x: F, n: int = 20, k: float = 2.0) -> tuple[F, F, F]:
    mid = sma(x, n)
    sd = np.full_like(x, np.nan)
    for i in range(n - 1, len(x)):
        sd[i] = x[i - n + 1 : i + 1].std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def adx(c: Candles, n: int = 14) -> tuple[F, F, F]:
    """Returns (ADX, +DI, -DI). ADX measures trend strength irrespective of direction."""
    up = np.diff(c.h, prepend=np.nan)
    dn = -np.diff(c.l, prepend=np.nan)
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    plus_dm[0] = minus_dm[0] = np.nan
    tr = true_range(c)
    tr[0] = np.nan
    atr_ = wilder(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * wilder(plus_dm, n) / atr_
        mdi = 100 * wilder(minus_dm, n) / atr_
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    return wilder(dx, n), pdi, mdi


def efficiency_ratio(x: F, n: int = 20) -> F:
    """Kaufman efficiency ratio: |net move| / path length over ``n`` bars (1 = straight line, 0 = noise)."""
    out = np.full_like(x, np.nan)
    path = np.abs(np.diff(x, prepend=np.nan))
    for i in range(n, len(x)):
        p = path[i - n + 1 : i + 1].sum()
        out[i] = abs(x[i] - x[i - n]) / p if p > 0 else 0.0
    return out


def percentile_rank(x: F, lookback: int = 200) -> F:
    """Rank of x[i] among the previous ``lookback`` valid values (0..1), causal."""
    out = np.full_like(x, np.nan)
    for i in range(len(x)):
        if np.isnan(x[i]):
            continue
        w = x[max(0, i - lookback) : i]
        w = w[~np.isnan(w)]
        if len(w) >= 20:
            out[i] = float((w < x[i]).mean())
    return out
