"""PAPER autopilot end-to-end (real DB, simulated broker, fixed clock): decisions, fills, exits, journal,
restart continuity, kill switch, report, alerts."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pyotp
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from apex.api.main import create_app
from apex.api.notifier import Notifier
from apex.core.config import Settings
from apex.db.models import BarRow, OpportunityRow, PaperOrderRow, PaperPositionRow, TradeRow, User
from apex.domain.enums import Role
from apex.domain.models import Quote
from apex.market.quality import DataQualityMonitor
from apex.security.crypto import generate_master_key
from apex.security.passwords import hash_password

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")
PW = "correct horse battery staple"
T = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)  # Tuesday 10:00 New York: market open, no rollover nearby
MASTER = generate_master_key()


def settings() -> Settings:
    return Settings(
        environment="test",
        database_url=DB_URL,
        jwt_secret="t" * 40,
        master_key=MASTER,  # type: ignore[arg-type]
        cookie_secure=False,
        log_json=False,
        log_level="WARNING",
        scanner_enabled=False,
        risk_guard_enabled=False,
        paper_account_currency="USD",
    )


def opp(oid: str, detected: datetime, setup: str = "liquidity_sweep_reversal") -> OpportunityRow:
    payload = {
        "id": oid,
        "direction": "bullish",
        "timeframe": "5m",
        "entry": 4000.2,
        "stop": 3995.2,
        "targets": [4012.2],
    }
    return OpportunityRow(
        id=oid,
        detected_at=detected,
        setup=setup,
        direction="bullish",
        timeframe="5m",
        status="active",
        quality=70,
        payload=payload,
    )


async def seed(st: Any) -> None:
    async with st.sessionmaker() as s:
        await s.execute(
            text(
                "TRUNCATE core.users, core.audit_log, market.bars, market.ticks, intel.economic_events, "
                "trading.opportunities, trading.risk_decisions RESTART IDENTITY"
            )
        )
        s.add(User(email="adm@x.io", password_hash=hash_password(PW), role=Role.ADMIN))
        start = T - timedelta(minutes=5 * 101)
        for i in range(100):  # calm 5m bars: ATR ≈ 4
            o = D(4000) + D(i % 3)
            s.add(
                BarRow(
                    source="hist",
                    symbol="XAUUSD",
                    timeframe="5m",
                    start=start + timedelta(minutes=5 * i),
                    open=o,
                    high=o + 2,
                    low=o - 2,
                    close=o + 1,
                    spread_avg=D("0.2"),
                    spread_max=D("0.3"),
                    tick_count=10,
                )
            )
        s.add(opp("fresh-1", T - timedelta(minutes=5)))  # signal bar closed at T
        s.add(opp("old-1", T - timedelta(minutes=30)))  # closed 25 minutes ago: too old
        s.add(opp("other-setup", T - timedelta(minutes=5), setup="trend_pullback"))
        await s.commit()


async def quotes(st: Any, at: datetime, bid: str = "4000.00", n: int = 40) -> None:
    st.broker._clock = lambda: at
    for i in range(n):  # warm the data-quality monitor
        await st.market.on_quote(
            Quote(symbol="XAUUSD", bid=D(bid), ask=D(bid) + D("0.2"), ts=at - timedelta(seconds=n - i))
        )
    await st.market.on_quote(Quote(symbol="XAUUSD", bid=D(bid), ask=D(bid) + D("0.2"), ts=at))


async def enable(c: httpx.AsyncClient) -> None:
    setup = (await c.post("/auth/mfa/setup")).json()
    secret = pyotp.parse_uri(setup["otpauth_uri"]).secret
    await c.post(
        "/auth/mfa/confirm", json={"setup_token": setup["setup_token"], "code": pyotp.TOTP(secret).now()}
    )
    await c.post("/auth/login", json={"email": "adm@x.io", "password": PW, "totp": pyotp.TOTP(secret).now()})
    r = await c.post("/system/trading/enable", json={"password": PW, "totp": pyotp.TOTP(secret).now()})
    assert r.status_code == 204, r.text


@pytest.fixture
async def env() -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    app = create_app(settings())
    try:
        async with app.router.lifespan_context(app):
            st = app.state.apex
            await seed(st)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await c.post("/auth/login", json={"email": "adm@x.io", "password": PW})
                yield c, st
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")


async def journal(st: Any) -> dict[str, PaperOrderRow]:
    async with st.sessionmaker() as s:
        return {r.opportunity_id: r for r in (await s.execute(select(PaperOrderRow))).scalars()}


async def test_autopilot_requires_paper_trading_enabled_and_is_off_by_default(env: Any) -> None:
    c, st = env
    await quotes(st, T + timedelta(seconds=10))
    await st.autopilot.cycle(T + timedelta(seconds=10))
    assert await journal(st) == {}  # off by default: nothing considered
    r = await c.post("/paper/autopilot", json={"enabled": True})
    assert r.status_code == 409 and "activez d'abord le trading" in r.json()["detail"]


async def test_opens_journals_and_closes_at_stop(env: Any) -> None:
    c, st = env
    await enable(c)
    r = await c.post(
        "/paper/autopilot",
        json={"enabled": True, "config": {"setups": ["liquidity_sweep_reversal"], "timeframes": ["5m"]}},
    )
    assert r.status_code == 200 and r.json()["enabled"] is True
    now = T + timedelta(seconds=10)
    await quotes(st, now)
    await st.autopilot.cycle(now)
    j = await journal(st)
    assert j["fresh-1"].status == "open" and j["fresh-1"].fill_price == D("4000.20")
    assert j["fresh-1"].slippage == 0 and j["fresh-1"].latency_s == pytest.approx(10)
    assert j["fresh-1"].risk_amount is not None and j["fresh-1"].risk_amount <= D("25")  # 0.25 % of 10 000
    assert j["old-1"].status == "skipped" and "idée trop ancienne" in j["old-1"].reasons
    assert j["other-setup"].status == "skipped" and "stratégie non sélectionnée" in j["other-setup"].reasons
    assert len(await st.broker.get_positions()) == 1
    await st.autopilot.cycle(now + timedelta(seconds=5))  # each idea is considered once
    assert len(await journal(st)) == 3
    await st.paper_store.flush()
    async with st.sessionmaker() as s:
        assert len((await s.execute(select(PaperPositionRow))).scalars().all()) == 1  # saved for restarts
    # price falls through the stop
    await quotes(st, now + timedelta(minutes=3), bid="3995.00", n=1)
    await st.autopilot.flush()
    await st.paper_store.flush()
    await st.risk.flush()
    rec = (await journal(st))["fresh-1"]
    assert rec.status == "closed" and rec.close_reason == "stop_loss"
    assert rec.r_multiple is not None and -1.2 < rec.r_multiple < -0.9
    async with st.sessionmaker() as s:
        assert (await s.execute(select(PaperPositionRow))).scalars().all() == []
        assert len((await s.execute(select(TradeRow))).scalars().all()) == 1
    assert any("PAPER" in m["text"] for m in st.notifier.status()["recent"])
    rep = (await c.get("/paper/report")).json()
    assert rep["overall"]["trades"] == 1 and rep["gate_passed"] is False
    assert {g["name"] for g in rep["gate"]} >= {"Durée ≥ 3 mois", "Au moins 100 trades"}
    rows = (await c.get("/paper/journal")).json()
    assert {r["opportunity_id"] for r in rows} == {"fresh-1", "old-1", "other-setup"}


async def test_time_exit_and_refusal_when_price_ran_away(env: Any) -> None:
    c, st = env
    await enable(c)
    await c.post("/paper/autopilot", json={"enabled": True})
    now = T + timedelta(seconds=10)
    await quotes(st, now, bid="4006.00")  # price already 6 $ above the idea's entry: reward/risk collapses
    await st.autopilot.cycle(now)
    j = await journal(st)
    assert j["fresh-1"].status == "rejected" and "risk_reward" in j["fresh-1"].reasons
    # a fresh idea at a normal price opens, then is closed by the time limit
    async with st.sessionmaker() as s:
        s.add(opp("fresh-2", T))
        await s.commit()
    later = T + timedelta(minutes=5, seconds=10)
    st.market.quality = DataQualityMonitor()  # the test jumps 5 minutes without quotes: start a clean window
    await quotes(st, later)
    await st.autopilot.cycle(later)
    assert (await journal(st))["fresh-2"].status == "open"
    async with st.sessionmaker() as s:
        row = (
            await s.execute(select(PaperOrderRow).where(PaperOrderRow.opportunity_id == "fresh-2"))
        ).scalar_one()
        row.max_hold_until = later
        await s.commit()
    await quotes(st, later + timedelta(seconds=30), n=1)
    await st.autopilot.cycle(later + timedelta(seconds=30))
    await st.autopilot.flush()
    assert (await journal(st))["fresh-2"].close_reason == "time_exit"


async def test_kill_switch_stops_autopilot_and_is_alerted(env: Any) -> None:
    c, st = env
    await enable(c)
    await c.post("/paper/autopilot", json={"enabled": True})
    r = await c.post("/system/kill-switch", json={"reason": "test", "close_positions": True})
    assert r.status_code == 200
    assert st.trading.autopilot_enabled is False
    await quotes(st, T + timedelta(seconds=10))
    await st.autopilot.cycle(T + timedelta(seconds=10))
    assert await journal(st) == {}
    assert any("Arrêt d'urgence" in m["text"] for m in st.notifier.status()["recent"])
    assert (await c.post("/paper/autopilot", json={"enabled": True})).status_code == 409


async def test_paper_account_and_switches_survive_restart() -> None:
    app = create_app(settings())
    try:
        async with app.router.lifespan_context(app):
            st = app.state.apex
            await seed(st)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await c.post("/auth/login", json={"email": "adm@x.io", "password": PW})
                await enable(c)
                await c.post("/paper/autopilot", json={"enabled": True})
            now = T + timedelta(seconds=10)
            await quotes(st, now)
            await st.autopilot.cycle(now)
            [pos] = await st.broker.get_positions()
            balance = (await st.broker.get_account_info()).balance.amount
            assert balance < D(10000)  # opening commission paid
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")
    app2 = create_app(settings())
    async with app2.router.lifespan_context(app2):
        st2 = app2.state.apex
        [pos2] = await st2.broker.get_positions()
        assert pos2.position_id == pos.position_id and pos2.stop_loss == pos.stop_loss
        assert (await st2.broker.get_account_info()).balance.amount == balance
        assert st2.trading.trading_enabled and st2.trading.autopilot_enabled  # PAPER: run continues
        assert await st2.broker.get_order_status(pos2_coid(st2)) is not None  # idempotency kept


def pos2_coid(st: Any) -> str:
    return next(iter(st.broker._orders))


async def test_notifier_sends_and_never_raises() -> None:
    sent: list[dict[str, Any]] = []

    def ok(req: httpx.Request) -> httpx.Response:
        import json

        sent.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as http:
        n = Notifier("tok", "42", http)
        assert await n.send("bonjour") and sent == [
            {"chat_id": "42", "text": "bonjour", "disable_web_page_preview": True}
        ]

    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("blocked")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        n = Notifier("tok", "42", http)
        assert await n.send("x") is False and n.failed == 1 and n.last_error == "ConnectError"
    off = Notifier(None, None)
    off.notify("kept locally")
    assert not off.configured and off.status()["recent"][0]["text"] == "kept locally"
