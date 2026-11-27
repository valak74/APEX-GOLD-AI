"""Backtest API end-to-end (real DB): history loading with source priority, background run, list, detail."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from apex.api.main import create_app
from apex.backtest.service import load_history
from apex.core.config import Settings
from apex.db.models import BarRow, User
from apex.domain.enums import Role
from apex.security.crypto import generate_master_key
from apex.security.passwords import hash_password

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")
PW = "correct horse battery staple"
START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def _bar(source: str, i: int, o: float, h: float, lo: float, c: float) -> BarRow:
    return BarRow(
        source=source,
        symbol="XAUUSD",
        timeframe="5m",
        start=START + timedelta(minutes=5 * i),
        open=D(f"{o:.2f}"),
        high=D(f"{h:.2f}"),
        low=D(f"{lo:.2f}"),
        close=D(f"{c:.2f}"),
        spread_avg=D("0.25"),
        spread_max=D("0.4"),
        tick_count=20,
    )


@pytest.fixture
async def env() -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    s = Settings(
        environment="test",
        database_url=DB_URL,
        jwt_secret="t" * 40,
        master_key=generate_master_key(),  # type: ignore[arg-type]
        cookie_secure=False,
        log_json=False,
        log_level="WARNING",
        scanner_enabled=False,
        risk_guard_enabled=False,
        paper_account_currency="USD",
    )
    app = create_app(s)
    try:
        async with app.router.lifespan_context(app):
            st = app.state.apex
            async with st.sessionmaker() as ses:
                await ses.execute(
                    text(
                        "TRUNCATE core.users, core.audit_log, market.bars, intel.economic_events, "
                        "research.backtests, research.holdout RESTART IDENTITY"
                    )
                )
                ses.add(User(email="adm@x.io", password_hash=hash_password(PW), role=Role.ADMIN))
                ses.add(User(email="view@x.io", password_hash=hash_password(PW), role=Role.VIEWER))
                rng = np.random.default_rng(4)
                cl = 4000 + np.cumsum(rng.normal(0, 1.5, 1500))
                for i in range(1500):
                    o = cl[i - 1] if i else cl[0]
                    h = max(o, cl[i]) + abs(rng.normal(0, 0.8))
                    lo = min(o, cl[i]) - abs(rng.normal(0, 0.8))
                    ses.add(_bar("dukascopy", i, o, h, lo, cl[i]))
                    if i >= 1400:  # overlapping live bars with a different price: history must win
                        ses.add(_bar("ctrader", i, o + 50, h + 50, lo + 50, cl[i] + 50))
                await ses.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                yield c, st
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")


async def test_history_prefers_long_history_source_on_overlap(env: Any) -> None:
    _, st = env
    c, spreads, sources = await load_history(st.sessionmaker, "5m", None, None)
    assert len(c) == 1500 and sources == ["dukascopy"]
    assert abs(c.c[-1] - c.c[-2]) < 20  # no +50 jump from the overlapping live source
    assert spreads[0] == pytest.approx(0.25)


async def wait_done(c: httpx.AsyncClient, bt_id: int) -> dict[str, Any]:
    for _ in range(300):
        r = (await c.get(f"/backtests/{bt_id}")).json()
        if r["status"] != "running":
            return r  # type: ignore[no-any-return]
        await asyncio.sleep(0.1)
    raise AssertionError("backtest did not finish")


async def test_run_list_and_detail(env: Any) -> None:
    c, _ = env
    await c.post("/auth/login", json={"email": "adm@x.io", "password": PW})
    r = await c.post("/backtests", json={"setup": "liquidity_sweep_reversal", "timeframe": "5m"})
    assert r.status_code == 202, r.text
    d = await wait_done(c, r.json()["id"])
    assert d["status"] == "done", d
    rep = d["report"]
    assert rep["data"]["bars"] == 1500 and rep["data"]["sources"] == ["dukascopy"]
    assert rep["validation"]["validated"] is False  # a random walk never passes the gate
    assert {"summary", "walk_forward", "sensitivity", "equity", "drawdown", "trades"} <= set(rep)
    [row] = (await c.get("/backtests")).json()
    assert row["id"] == d["id"] and row["validated"] is False and row["request"]["timeframe"] == "5m"


async def test_insufficient_history_is_reported_as_error(env: Any) -> None:
    c, _ = env
    await c.post("/auth/login", json={"email": "adm@x.io", "password": PW})
    r = await c.post("/backtests", json={"timeframe": "15m"})  # no 15m bars stored
    d = await wait_done(c, r.json()["id"])
    assert d["status"] == "error" and "insuffisant" in d["error"]


async def test_viewer_cannot_start_and_bad_input_rejected(env: Any) -> None:
    c, _ = env
    await c.post("/auth/login", json={"email": "view@x.io", "password": PW})
    assert (await c.post("/backtests", json={})).status_code == 403
    assert (await c.get("/backtests")).status_code == 200
    assert (await c.get("/backtests/999")).status_code == 404
    await c.post("/auth/login", json={"email": "adm@x.io", "password": PW})
    assert (await c.post("/backtests", json={"stop_buffer_atr": 9})).status_code == 422


async def test_holdout_is_reserved_and_only_admin_can_consult_it(env: Any) -> None:
    c, st = env
    async with st.sessionmaker() as ses:  # one bar 200 days later: history spans > 60 days
        ses.add(_bar("dukascopy", 200 * 288, 4000, 4001, 3999, 4000))
        await ses.commit()
    await c.post("/auth/login", json={"email": "adm@x.io", "password": PW})
    d = await wait_done(c, (await c.post("/backtests", json={})).json()["id"])
    assert d["status"] == "done"
    rep = d["report"]
    assert (
        rep["data"]["bars"] == 1500 and rep["data"]["holdout_start"] is not None
    )  # the late bar is held out
    assert any(i["name"].startswith("Sharpe dégonflé") for i in rep["validation"]["items"])
    h = (await c.get("/research/holdout")).json()
    assert h["start"] is not None and h["consultations"] == 0
    # the reserved period never moves, even when more history arrives
    async with st.sessionmaker() as ses:
        ses.add(_bar("dukascopy", 400 * 288, 4000, 4001, 3999, 4000))
        await ses.commit()
    await wait_done(c, (await c.post("/backtests", json={"timeframe": "5m"})).json()["id"])
    assert (await c.get("/research/holdout")).json()["start"] == h["start"]
    # final verdict: admin only, and every consultation is counted
    f = await wait_done(c, (await c.post("/backtests", json={"final_holdout": True})).json()["id"])
    assert f["status"] == "error" and "insuffisant" in f["error"]  # only 2 bars in the holdout here
    assert (await c.get("/research/holdout")).json()["consultations"] == 1
    await c.post("/auth/login", json={"email": "view@x.io", "password": PW})
    assert (await c.post("/backtests", json={"final_holdout": True})).status_code == 403


async def test_diagnostic_endpoints(env: Any) -> None:
    c, _ = env
    await c.post("/auth/login", json={"email": "adm@x.io", "password": PW})
    r = await c.post("/research/diagnostics", json={"timeframes": ["5m"]})
    assert r.status_code == 202, r.text
    for _ in range(600):
        d = (await c.get(f"/research/diagnostics/{r.json()['id']}")).json()
        if d["status"] != "running":
            break
        await asyncio.sleep(0.1)
    assert d["status"] == "done", d
    rep = d["report"]
    assert rep["findings"] and rep["costs"][0]["timeframe"] == "5m" and rep["data"]["bars_5m"] == 1500
    [row] = (await c.get("/research/diagnostics")).json()
    assert row["id"] == d["id"] and row["tests_total"] == rep["tests_total"]
    assert (await c.get("/backtests")).json() == []  # diagnostics are not listed as backtests
    assert (await c.get(f"/backtests/{d['id']}")).status_code == 404
    assert (await c.post("/research/diagnostics", json={"timeframes": []})).status_code == 422
