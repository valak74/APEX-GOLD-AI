"""Gold trading calendar and bar bucketing.

Spot gold (XAU/USD) trades ~24/5 in New York-time terms:
* weekly open  : Sunday 18:00 America/New_York
* daily break  : 17:00–18:00 America/New_York (rollover; spreads widen, broker quotes may stop)
* weekly close : Friday 17:00 America/New_York
The *trading day* runs 18:00 NY → 17:00 NY next day and is labelled by the calendar date of its close
(standard FX/metals convention). Daily and weekly bars use these boundaries; intraday bars are plain
UTC-aligned buckets. All inputs/outputs are timezone-aware UTC. DST is handled by zoneinfo.

Broker holidays are not modelled here (reported by brokers per symbol; to be added from cTrader schedules).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ROLLOVER = time(17, 0)
REOPEN = time(18, 0)

TIMEFRAMES: dict[str, int] = {  # seconds, intraday only
    "1s": 1,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
}
ALL_TIMEFRAMES = (*TIMEFRAMES, "1d", "1w")


def _check(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(UTC)


def is_market_open(ts: datetime) -> bool:
    ny = _check(ts).astimezone(NY)
    wd, t = ny.weekday(), ny.time()  # Mon=0 .. Sun=6
    if wd == 5:  # Saturday
        return False
    if wd == 6:  # Sunday: opens 18:00
        return t >= REOPEN
    if wd == 4 and t >= ROLLOVER:  # Friday after close
        return False
    return not (ROLLOVER <= t < REOPEN)  # daily break


def trading_day_start(ts: datetime) -> datetime:
    """Start (18:00 NY, as UTC) of the trading day containing ``ts``; break minutes belong to the next day."""
    ny = _check(ts).astimezone(NY)
    day = ny.date() if ny.time() >= ROLLOVER else ny.date() - timedelta(days=1)
    return datetime.combine(day, REOPEN, tzinfo=NY).astimezone(UTC)


def bucket_start(ts: datetime, timeframe: str) -> datetime:
    ts = _check(ts)
    if timeframe in TIMEFRAMES:
        step = TIMEFRAMES[timeframe]
        epoch = int(ts.timestamp())
        return datetime.fromtimestamp(epoch - epoch % step, tz=UTC)
    if timeframe == "1d":
        return trading_day_start(ts)
    if timeframe == "1w":
        start = trading_day_start(ts).astimezone(NY)
        # trading weeks start on Sunday 18:00 NY
        days_back = (start.weekday() + 1) % 7  # Sunday -> 0
        return datetime.combine(start.date() - timedelta(days=days_back), REOPEN, tzinfo=NY).astimezone(UTC)
    raise ValueError(f"unknown timeframe {timeframe!r}")
