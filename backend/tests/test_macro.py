"""Fundamental data: parsing, point-in-time availability, sync with mocked FRED/CFTC, macro conditions."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from apex.core.config import Settings
from apex.db.models import EconomicEvent, MacroObservation
from apex.db.session import make_engine, make_sessionmaker
from apex.intel.macro import (
    RELEASES,
    SERIES,
    MacroContext,
    lag_next_business_day,
    lag_next_monday,
    load_context,
    parse_cot,
    parse_fred_observations,
    release_events,
    sync_macro,
)

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")


# --------------------------------------------------------------------------- availability (no look-ahead)
def test_publication_lags_are_conservative() -> None:
    fri = date(2026, 11, 20)
    assert lag_next_business_day(fri) == datetime(2026, 11, 23, 22, 0, tzinfo=UTC)  # Monday 17:00 New York
    assert lag_next_business_day(date(2026, 11, 24)) == datetime(2026, 11, 25, 22, 0, tzinfo=UTC)
    assert lag_next_monday(date(2026, 11, 24)).date() == date(2026, 11, 30)  # weekly: following Monday
    assert lag_next_monday(date(2026, 11, 30)).date() == date(2026, 12, 7)  # never the same day


def test_parsing() -> None:
    obs = parse_fred_observations(
        {"observations": [{"date": "2026-11-20", "value": "1.85"}, {"date": "2026-11-23", "value": "."}]},
        SERIES["REAL_10Y"],
    )
    assert (
        len(obs) == 1
        and obs[0]["value"] == D("1.85")
        and obs[0]["available_at"] > datetime(2026, 11, 21, tzinfo=UTC)
    )
    cot = parse_cot(
        [
            {
                "report_date_as_yyyy_mm_dd": "2026-11-17T00:00:00.000",
                "m_money_positions_long_all": "180000",
                "m_money_positions_short_all": "30000",
                "open_interest_all": "500000",
            },
            {"report_date_as_yyyy_mm_dd": "bad"},
        ]
    )
    assert [(r["series"], r["value"]) for r in cot] == [("COT_MM_NET", D(150000)), ("COT_OI", D(500000))]
    nfp = next(r for r in RELEASES if r.code == "US_NFP")
    [ev] = release_events(["2026-12-04"], nfp)
    assert (
        ev.scheduled_at == datetime(2026, 12, 4, 13, 30, tzinfo=UTC) and ev.importance == "high"
    )  # 08:30 NY
    fomc = next(r for r in RELEASES if r.code == "FOMC_DECISION")
    assert release_events(["2026-07-29"], fomc)[0].scheduled_at == datetime(
        2026, 7, 29, 18, 0, tzinfo=UTC
    )  # EDT


def _obs(series: str, d: date, v: float) -> MacroObservation:
    return MacroObservation(
        series=series, obs_date=d, value=D(str(v)), available_at=SERIES[series].lag(d), source="t"
    )


def test_context_only_sees_published_values() -> None:
    start = date(2026, 10, 1)
    rows = [
        _obs("REAL_10Y", start + timedelta(days=i), 1.0 + 0.02 * i)
        for i in range(40)
        if (start + timedelta(days=i)).weekday() < 5
    ]
    ctx = MacroContext(rows)
    t = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)  # Tuesday morning: Monday's value is NOT published yet
    # Monday 19 Oct is published Tuesday 17:00 New York: at Tuesday 15:00 UTC the last known value is Fri 16
    assert ctx.value("REAL_10Y", t) == pytest.approx(1.0 + 0.02 * 15)
    assert ctx.value("REAL_10Y", datetime(2026, 10, 20, 21, 1, tzinfo=UTC)) == pytest.approx(1.0 + 0.02 * 18)
    assert ctx.labels(t)["taux réels (5 j)"] == "en hausse"
    assert ctx.value("REAL_10Y", datetime(2026, 10, 1, 12, tzinfo=UTC)) is None  # nothing known yet


def test_cot_percentile_and_event_day() -> None:
    rows = [_obs("COT_MM_NET", date(2024, 1, 2) + timedelta(weeks=i), float(i)) for i in range(100)]
    ev = EconomicEvent(
        source="t",
        event_code="US_CPI",
        title="CPI",
        country="US",
        currency="USD",
        importance="high",
        scheduled_at=datetime(2026, 1, 13, 13, 30, tzinfo=UTC),
    )
    ctx = MacroContext(rows, [ev])
    late = datetime(2026, 6, 1, tzinfo=UTC)
    assert (
        ctx.percentile("COT_MM_NET", late) == 1.0
        and ctx.labels(late)["spéculateurs (COT)"] == "très acheteurs"
    )
    assert ctx.event_day(datetime(2026, 1, 13, 9, 0, tzinfo=UTC))  # same trading day (opened 12 Jan 17:00 NY)
    assert ctx.event_day(
        datetime(2026, 1, 12, 23, 0, tzinfo=UTC)
    )  # 18:00 NY on the 12th = the 13th's session
    assert not ctx.event_day(datetime(2026, 1, 13, 23, 0, tzinfo=UTC))  # next session


# --------------------------------------------------------------------------- sync against mocked APIs
def _handler(req: httpx.Request) -> httpx.Response:
    path, q = req.url.path, dict(req.url.params)
    if "publicreporting.cftc.gov" in str(req.url):
        return httpx.Response(
            200,
            json=[
                {
                    "report_date_as_yyyy_mm_dd": "2026-11-17T00:00:00.000",
                    "m_money_positions_long_all": "1000",
                    "m_money_positions_short_all": "400",
                    "open_interest_all": "5000",
                }
            ],
        )
    if path.endswith("/series/observations"):
        assert q["api_key"] == "k"
        return httpx.Response(200, json={"observations": [{"date": "2026-11-20", "value": "1.5"}]})
    if path.endswith("/releases"):
        return httpx.Response(
            200,
            json={
                "releases": [
                    {"id": 50, "name": "Employment Situation"},
                    {"id": 10, "name": "Consumer Price Index"},
                ]
            },
        )
    if path.endswith("/release/dates"):
        return httpx.Response(
            200, json={"release_dates": [{"release_id": int(q["release_id"]), "date": "2026-12-04"}]}
        )
    return httpx.Response(404)


@pytest.fixture
async def maker() -> AsyncIterator[Any]:
    engine = make_engine(DB_URL)
    mk = make_sessionmaker(engine)
    try:
        async with mk() as s:
            await s.execute(text("TRUNCATE intel.macro_observations, intel.economic_events RESTART IDENTITY"))
            await s.commit()
        yield mk
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")
    finally:
        await engine.dispose()


async def test_sync_stores_series_events_and_reports_missing(maker: Any) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http:
        rep = await sync_macro(maker, "k", date(2026, 1, 1), http)
    assert rep.observations["REAL_10Y"] == 1 and rep.observations["COT"] == 2
    assert rep.events == {"US_NFP": 1, "US_CPI": 1}
    assert any("FOMC Press Release" in e for e in rep.errors)  # unknown names are reported, never invented
    async with maker() as s:
        n = (await s.execute(select(MacroObservation))).scalars().all()
        ev = (await s.execute(select(EconomicEvent))).scalars().all()
    assert len(n) == 8 and {e.event_code for e in ev} == {"US_NFP", "US_CPI"}
    ctx = await load_context(maker)
    assert ctx.value("COT_MM_NET", datetime(2026, 11, 30, 23, tzinfo=UTC)) == 600
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http:  # idempotent
        await sync_macro(maker, "k", date(2026, 1, 1), http)
    async with maker() as s:
        assert len((await s.execute(select(MacroObservation))).scalars().all()) == 8


async def test_sync_without_key_still_fetches_cot(maker: Any) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http:
        rep = await sync_macro(maker, None, date(2026, 1, 1), http)
    assert rep.observations == {"COT": 2} and any("APEX_FRED_API_KEY" in e for e in rep.errors)


async def test_network_failure_is_reported_not_raised(maker: Any) -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        rep = await sync_macro(maker, "k", date(2026, 1, 1), http)
    assert rep.observations == {} and len(rep.errors) >= 7


def test_settings_have_no_key_by_default() -> None:
    assert Settings(environment="test").fred_api_key is None
