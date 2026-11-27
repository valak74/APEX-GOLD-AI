"""Cost calibration: the broker's real spreads (recorded live from cTrader) versus the historical source
(Dukascopy) used by backtests, hour by hour. The ratio rescales historical spreads so that backtests pay what
the account actually pays."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.domain.models import utcnow

LIVE = "ctrader"
HIST = "dukascopy"
MIN_LIVE_BARS = 2000  # ≈ one trading week of 5m bars before the ratio is trusted

_Q = text(
    """
    SELECT extract(hour from start AT TIME ZONE 'UTC')::int AS h,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY spread_avg) AS med,
           count(*) AS n
    FROM market.bars
    WHERE symbol = 'XAUUSD' AND timeframe = '5m' AND source = :src AND start >= :since
    GROUP BY 1 ORDER BY 1
    """
)


async def spread_profile(maker: async_sessionmaker[AsyncSession], days: int = 90) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    async with maker() as s:
        live = {
            int(r.h): (float(r.med), int(r.n)) for r in await s.execute(_Q, {"src": LIVE, "since": since})
        }
        hist = {
            int(r.h): (float(r.med), int(r.n))
            for r in await s.execute(_Q, {"src": HIST, "since": since - timedelta(days=3 * 365)})
        }
    hours = [
        {
            "hour": h,
            "live": round(live[h][0], 3) if h in live else None,
            "hist": round(hist[h][0], 3) if h in hist else None,
        }
        for h in range(24)
    ]
    n_live = sum(n for _, n in live.values())
    pairs = [(live[h][0], hist[h][0]) for h in range(24) if h in live and h in hist and hist[h][0] > 0]
    ratio = None
    if n_live >= MIN_LIVE_BARS and len(pairs) >= 12:
        weights = [live[h][1] for h in range(24) if h in live and h in hist and hist[h][0] > 0]
        ratio = round(sum(w * a / b for w, (a, b) in zip(weights, pairs, strict=True)) / sum(weights), 3)
    return {
        "hours": hours,
        "live_bars": n_live,
        "hist_bars": sum(n for _, n in hist.values()),
        "ratio": ratio,
        "min_live_bars": MIN_LIVE_BARS,
        "note": (
            f"spread IC Markets ≈ {ratio:.2f} × spread Dukascopy : appliqué automatiquement aux backtests et diagnostics"
            if ratio is not None
            else f"pas encore assez de prix enregistrés en direct ({n_live}/{MIN_LIVE_BARS} bougies 5m) : spreads Dukascopy utilisés tels quels"
        ),
    }
