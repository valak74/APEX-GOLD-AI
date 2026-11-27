from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apex.domain.models import Quote
from apex.market.bars import BarBuilder
from apex.market.quality import DataQualityMonitor
from apex.market.sessions import bucket_start, is_market_open, trading_day_start


def u(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def q(t: datetime, mid: str = "4000.00", spread: str = "0.20", sym: str = "XAUUSD") -> Quote:
    m, s = D(mid), D(spread)
    return Quote(symbol=sym, bid=m - s / 2, ask=m + s / 2, ts=t)


# --------------------------------------------------------------------------- sessions
@pytest.mark.parametrize(
    ("ts", "open_"),
    [
        (u(2026, 11, 27, 15, 0), True),  # Fri 10:00 NY (EST, UTC-5)
        (u(2026, 11, 27, 22, 1), False),  # Fri 17:01 NY -> weekly close
        (u(2026, 11, 28, 12, 0), False),  # Saturday
        (u(2026, 11, 29, 22, 59), False),  # Sun 17:59 NY
        (u(2026, 11, 29, 23, 0), True),  # Sun 18:00 NY -> weekly open
        (u(2026, 11, 30, 22, 30), False),  # Mon 17:30 NY -> daily break
        (u(2026, 7, 1, 21, 30), False),  # Wed 17:30 NY in summer (EDT, UTC-4) -> break
        (u(2026, 7, 1, 22, 0), True),  # Wed 18:00 EDT -> reopened
    ],
)
def test_market_hours_with_dst(ts: datetime, open_: bool) -> None:
    assert is_market_open(ts) is open_


def test_trading_day_and_week_boundaries() -> None:
    assert trading_day_start(u(2026, 11, 30, 10, 0)) == u(2026, 11, 29, 23, 0)  # Mon morning -> Sun 18:00 NY
    assert trading_day_start(u(2026, 7, 1, 23, 0)) == u(2026, 7, 1, 22, 0)  # summer
    assert bucket_start(u(2026, 12, 2, 12, 0), "1w") == u(2026, 11, 29, 23, 0)  # Wed -> Sunday open
    assert bucket_start(u(2026, 11, 29, 23, 30), "1w") == u(2026, 11, 29, 23, 0)
    assert bucket_start(u(2026, 11, 27, 14, 7, 31), "5m") == u(2026, 11, 27, 14, 5)
    assert bucket_start(u(2026, 11, 27, 14, 7, 31), "4h") == u(2026, 11, 27, 12, 0)
    with pytest.raises(ValueError):
        bucket_start(datetime(2026, 1, 1), "1m")  # naive refused
    with pytest.raises(ValueError):
        bucket_start(u(2026, 1, 1), "2m")


# --------------------------------------------------------------------------- bars
def test_bars_ohlc_spread_and_close_on_next_bucket() -> None:
    b = BarBuilder("XAUUSD", timeframes=("1m", "5m"))
    t0 = u(2026, 11, 27, 14, 0)
    assert b.update(q(t0, "4000.00", "0.20")) == []
    b.update(q(t0 + timedelta(seconds=10), "4001.00", "0.40"))
    b.update(q(t0 + timedelta(seconds=20), "3999.50", "0.10"))
    b.update(q(t0 + timedelta(seconds=59), "4000.50", "0.30"))
    closed = b.update(q(t0 + timedelta(seconds=60), "4002.00"))
    assert len(closed) == 1
    bar = closed[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (
        D("4000.00"),
        D("4001.00"),
        D("3999.50"),
        D("4000.50"),
    )
    assert (
        bar.tick_count == 4 and bar.spread_max == D("0.40") and bar.spread_avg == D("0.25") and bar.complete
    )
    five = b.partial("5m")
    assert five is not None and five.tick_count == 5 and five.high == D("4002.00") and not five.complete


def test_out_of_order_quotes_rejected() -> None:
    b = BarBuilder("XAUUSD", timeframes=("1m",))
    t0 = u(2026, 11, 27, 14, 0, 30)
    b.update(q(t0))
    assert b.update(q(t0 - timedelta(seconds=1), "5000")) == []
    assert b.rejected_out_of_order == 1
    bar = b.partial("1m")
    assert bar is not None and bar.high == D("4000.00")


def test_other_symbol_ignored() -> None:
    b = BarBuilder("XAUUSD")
    assert b.update(q(u(2026, 11, 27, 14), sym="EURUSD")) == [] and b.partial("1m") is None


@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 7200), st.integers(-500, 500)), min_size=1, max_size=200))
def test_property_bars_consistent(points: list[tuple[int, int]]) -> None:
    b = BarBuilder("XAUUSD", timeframes=("1m", "15m"))
    t0 = u(2026, 11, 27, 14)
    bars = []
    for dt, dp in sorted(points):
        bars += b.update(q(t0 + timedelta(seconds=dt), str(D(4000) + D(dp) / 100)))
    for bar in bars + [x for x in (b.partial("1m"), b.partial("15m")) if x]:
        assert bar.low <= min(bar.open, bar.close) and bar.high >= max(bar.open, bar.close)
        assert bar.start == bucket_start(bar.start, bar.timeframe)
    assert sum(x.tick_count for x in bars if x.timeframe == "1m") + (
        p.tick_count if (p := b.partial("1m")) else 0
    ) == len(points)


# --------------------------------------------------------------------------- data quality
OPEN = u(2026, 11, 27, 15, 0)  # Friday 10:00 NY


def warm(m: DataQualityMonitor, n: int = 40) -> datetime:
    t = OPEN
    for i in range(n):
        t = OPEN + timedelta(seconds=i)
        m.observe(q(t, str(D(4000) + D(i % 5) / 10), "0.20" if i % 2 else "0.22"), now=t)
    return t


def test_quality_clean_feed_is_tradeable() -> None:
    m = DataQualityMonitor()
    t = warm(m)
    r = m.report("XAUUSD", now=t + timedelta(seconds=1))
    assert r.score == 1.0 and r.tradeable and r.issues == []


def test_quality_no_data_warming_and_stale() -> None:
    m = DataQualityMonitor()
    assert m.report("XAUUSD", now=OPEN).issues == ["no_data"]
    m.observe(q(OPEN), now=OPEN)
    r = m.report("XAUUSD", now=OPEN)
    assert "warming_up" in r.issues and not r.tradeable
    t = warm(m)
    r = m.report("XAUUSD", now=t + timedelta(seconds=30))
    assert "stale" in r.issues and r.score == 0.0 and not r.tradeable


def test_quality_spread_spike_and_jump_block_trading() -> None:
    m = DataQualityMonitor()
    t = warm(m) + timedelta(seconds=1)
    assert "spread_spike" in m.observe(q(t, "4000.2", "3.00"), now=t)
    t2 = t + timedelta(seconds=1)
    assert "price_jump" in m.observe(q(t2, "4030.00", "0.20"), now=t2)
    r = m.report("XAUUSD", now=t2)
    assert not r.tradeable and "recent_spread_spike" in r.issues and "recent_price_jump" in r.issues
    # flags expire after the window
    later = t2 + timedelta(minutes=3)
    for i in range(5):
        m.observe(q(later + timedelta(seconds=i), "4030.1", "0.20"), now=later + timedelta(seconds=i))
    assert m.report("XAUUSD", now=later + timedelta(seconds=5)).tradeable


def test_quality_duplicates_out_of_order_future_and_closed_market() -> None:
    m = DataQualityMonitor()
    t = warm(m)
    last = q(t, str(D(4000) + D(39 % 5) / 10), "0.20" if 39 % 2 else "0.22")
    assert m.observe(last, now=t) == ["duplicate"]
    assert "out_of_order" in m.observe(q(t - timedelta(seconds=5)), now=t)
    assert "future_timestamp" in m.observe(q(t + timedelta(seconds=30)), now=t)
    sat = u(2026, 11, 28, 12)
    r = m.report("XAUUSD", now=sat)
    assert not r.market_open and not r.tradeable and "market_closed" in r.issues
