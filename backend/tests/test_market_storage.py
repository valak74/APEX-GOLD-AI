"""Dukascopy decoding/import, tick & bar persistence, bars/quality API (real PostgreSQL)."""

from __future__ import annotations

import lzma
import os
import struct
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from apex.api.main import create_app
from apex.core.config import Settings
from apex.db.models import BarRow, Tick, User
from apex.domain.enums import Role
from apex.domain.models import Quote
from apex.market.dukascopy import InvalidFileError, decode_hour, hour_url, import_ticks, rebuild_bars
from apex.market.recorder import MarketRecorder
from apex.security.passwords import hash_password

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")
PW = "correct horse battery staple"
H = datetime(2026, 11, 26, 14, tzinfo=UTC)  # Thursday 09:00 NY


def bi5(records: list[tuple[int, int, int]]) -> bytes:
    raw = b"".join(struct.pack(">IIIff", ms, ask, bid, 1.0, 1.0) for ms, ask, bid in records)
    return lzma.compress(raw, format=lzma.FORMAT_ALONE)


# --------------------------------------------------------------------------- decoding (no DB)
def test_url_uses_zero_based_month() -> None:
    assert hour_url("XAUUSD", datetime(2026, 1, 5, 7, tzinfo=UTC)).endswith(
        "/XAUUSD/2026/00/05/07h_ticks.bi5"
    )


def test_decode_valid_file() -> None:
    qs = decode_hour("XAUUSD", H, bi5([(0, 4000300, 4000100), (1500, 4000400, 4000200)]))
    assert [(x.bid, x.ask) for x in qs] == [(D("4000.1"), D("4000.3")), (D("4000.2"), D("4000.4"))]
    assert qs[1].ts == H + timedelta(milliseconds=1500)
    assert decode_hour("XAUUSD", H, b"") == []


@pytest.mark.parametrize(
    ("records", "why"),
    [
        ([(0, 40003, 40001)], "plausible"),  # wrong divisor -> 40.003 USD gold
        ([(0, 4000100, 4000300)], "spread"),  # ask < bid
        ([(0, 4100000, 4000000)], "spread"),  # 100 USD spread (> 1 %)
        ([(500, 4000300, 4000100), (100, 4000300, 4000100)], "order"),  # time going backwards
        ([(3_600_000, 4000300, 4000100)], "outside"),
    ],
)
def test_decode_rejects_whole_file(records: list[tuple[int, int, int]], why: str) -> None:
    with pytest.raises(InvalidFileError, match=why):
        decode_hour("XAUUSD", H, bi5(records))


def test_decode_rejects_garbage() -> None:
    with pytest.raises(InvalidFileError):
        decode_hour("XAUUSD", H, b"not lzma at all")


# --------------------------------------------------------------------------- DB-backed
@pytest.fixture
async def app_state() -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    s = Settings(
        environment="test",
        database_url=DB_URL,
        jwt_secret="t" * 40,
        cookie_secure=False,  # type: ignore[arg-type]
        log_json=False,
        log_level="WARNING",
    )
    app = create_app(s)
    try:
        async with app.router.lifespan_context(app):
            st = app.state.apex
            async with st.sessionmaker() as ses:
                await ses.execute(
                    text("TRUNCATE core.users, core.audit_log, market.ticks, market.bars RESTART IDENTITY")
                )
                ses.add(User(email="v@x.io", password_hash=hash_password(PW), role=Role.VIEWER))
                await ses.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await c.post("/auth/login", json={"email": "v@x.io", "password": PW})
                yield c, st
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")


async def test_import_and_rebuild_bars(app_state: Any) -> None:
    _, st = app_state
    files = {
        hour_url("XAUUSD", H): bi5(
            [(0, 4000300, 4000100), (30_000, 4001300, 4001100), (61_000, 3999300, 3999100)]
        ),
        hour_url("XAUUSD", H + timedelta(hours=1)): bi5([(0, 4002300, 4002100)]),
        hour_url("XAUUSD", H + timedelta(hours=2)): bi5([(0, 40003, 40001)]),  # corrupt -> rejected
    }

    def handler(req: httpx.Request) -> httpx.Response:
        body = files.get(str(req.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rep = await import_ticks(st.sessionmaker, "XAUUSD", H, H + timedelta(hours=4), http=http)
        assert (rep.hours_total, rep.hours_imported, rep.ticks) == (4, 2, 4)
        assert rep.hours_empty == 1 and len(rep.rejected) == 1 and "plausible" in rep.rejected[0]
        again = await import_ticks(st.sessionmaker, "XAUUSD", H, H + timedelta(hours=4), http=http)
        assert again.hours_skipped_existing == 2 and again.ticks == 0  # resumable, idempotent

    n = await rebuild_bars(st.sessionmaker, "dukascopy", "XAUUSD", H, H + timedelta(hours=4))
    assert n > 0
    async with st.sessionmaker() as s:
        first = (
            (await s.execute(select(BarRow).where(BarRow.timeframe == "1m").order_by(BarRow.start)))
            .scalars()
            .first()
        )
        assert first is not None and first.start == H and first.tick_count == 2
        assert (first.open, first.high, first.close) == (D("4000.2"), D("4001.2"), D("4001.2"))
        assert await s.scalar(select(func.count()).select_from(Tick)) == 4


async def test_recorder_flush_idempotent(app_state: Any) -> None:
    _, st = app_state
    rec = MarketRecorder(st.sessionmaker, source="test")
    q = Quote(symbol="XAUUSD", bid=D("4000.1"), ask=D("4000.3"), ts=H)
    rec.add_quote(q)
    rec.add_quote(q)  # duplicate tick -> stored once
    await rec.flush()
    assert rec.written_ticks == 2 and rec.describe()["buffered"] == 0
    async with st.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Tick)) == 1


async def test_bars_and_quality_endpoints_from_live_quotes(app_state: Any) -> None:
    c, st = app_state
    now = datetime.now(UTC).replace(microsecond=0)
    t0 = now - timedelta(minutes=3)
    for i in range(0, 150):
        ts = t0 + timedelta(seconds=i)
        mid = D("4000") + D(i % 7) / 10
        await st.market.on_quote(Quote(symbol="XAUUSD", bid=mid - D("0.1"), ask=mid + D("0.1"), ts=ts))
    bars = (await c.get("/market/bars", params={"timeframe": "1m"})).json()
    assert len(bars) >= 3 and bars[-1]["complete"] is False and all(b["complete"] for b in bars[:-1])
    assert [b["start"] for b in bars] == sorted(b["start"] for b in bars)
    assert (await c.get("/market/bars", params={"timeframe": "2m"})).status_code == 422
    qual = (await c.get("/market/quality")).json()
    assert qual["counters"]["quotes"] == 150 and qual["symbol"] == "XAUUSD"
