"""Key reference levels from intraday bars: previous day / week high-low, session ranges, opening range.

Sessions (UTC-independent, defined in local exchange time, DST-safe):
* Asia   : 18:00–03:00 New York (Tokyo/Sydney/Singapore hours)
* London : 08:00–12:00 London  (morning session; liquidity build-up)
* New York: 08:00–12:00 New York (overlap + US data releases)
* Opening range: first 30 minutes of the New York session.
Trading days/weeks follow ``apex.market.sessions`` (17:00 New York rollover).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from apex.analysis.indicators import Candles
from apex.market.sessions import NY, bucket_start, trading_day_start

LONDON = ZoneInfo("Europe/London")
SESSIONS = {
    "asia": (NY, time(18, 0), time(3, 0)),
    "london": (LONDON, time(8, 0), time(12, 0)),
    "newyork": (NY, time(8, 0), time(12, 0)),
    "ny_opening_range": (NY, time(8, 0), time(8, 30)),
}


@dataclass
class Level:
    name: str
    price: float
    kind: str  # "high" | "low"
    period_start: datetime
    complete: bool = True  # False while the period (e.g. a session) is still in progress


def last_session_window(now: datetime, tz: ZoneInfo, a: time, b: time) -> tuple[datetime, datetime]:
    """Most recent occurrence of a local-time session that has started at ``now``."""
    local_day = now.astimezone(tz).date()
    best: tuple[datetime, datetime] | None = None
    for d in range(0, 4):
        day = local_day - timedelta(days=d)
        start = datetime.combine(day, a, tzinfo=tz)
        end_day = day if a < b else day + timedelta(days=1)
        end = datetime.combine(end_day, b, tzinfo=tz)
        if start <= now and (best is None or start > best[0]):
            best = (start, end)
    assert best is not None
    return best


def reference_levels(c: Candles, now: datetime) -> list[Level]:
    """Levels known at ``now`` from completed periods (no look-ahead)."""
    out: list[Level] = []
    if not len(c):
        return out
    today = trading_day_start(now)
    yday = trading_day_start(today - timedelta(minutes=1))
    week = bucket_start(now, "1w")
    pweek = bucket_start(week - timedelta(minutes=1), "1w")

    def hl(mask: list[bool], name: str, start: datetime, complete: bool = True) -> None:
        idx = [i for i, m in enumerate(mask) if m]
        if idx:
            out.append(Level(f"{name}_high", float(c.h[idx].max()), "high", start, complete))
            out.append(Level(f"{name}_low", float(c.l[idx].min()), "low", start, complete))

    hl([yday <= t < today for t in c.t], "prev_day", yday)
    hl([pweek <= t < week for t in c.t], "prev_week", pweek)
    for name, (tz, a, b) in SESSIONS.items():
        start, end = last_session_window(now, tz, a, b)
        hl([start <= t < min(end, now) for t in c.t], name, start, complete=end <= now)
    return out
