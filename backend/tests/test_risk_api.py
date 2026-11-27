"""Risk service end-to-end (real DB): snapshot, evaluation, decisions, trade journal, drawdown guard."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pyotp
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from apex.api.main import create_app
from apex.core.config import Settings
from apex.db.models import BarRow, OpportunityRow, RiskDecisionRow, TradeRow, User
from apex.domain.enums import Role, Side
from apex.domain.models import OrderRequest, Quote
from apex.market.sessions import bucket_start, is_market_open
from apex.security.crypto import generate_master_key
from apex.security.passwords import hash_password

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")
PW = "correct horse battery staple"


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
                        "TRUNCATE core.users, core.audit_log, market.bars, market.ticks, "
                        "intel.economic_events, trading.opportunities, trading.trades, "
                        "trading.risk_decisions RESTART IDENTITY"
                    )
                )
                ses.add(User(email="adm@x.io", password_hash=hash_password(PW), role=Role.ADMIN))
                start = bucket_start(datetime.now(UTC), "5m") - timedelta(minutes=5 * 101)
                for i in range(100):  # calm 5m bars: ATR ≈ 4 $
                    o = D(4000) + D(i % 3)
                    ses.add(
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
                ses.add(
                    OpportunityRow(
                        id="opp-test-1",
                        detected_at=datetime.now(UTC),
                        setup="liquidity_sweep_reversal",
                        direction="bullish",
                        timeframe="5m",
                        status="active",
                        quality=70,
                        payload={
                            "id": "opp-test-1",
                            "direction": "bullish",
                            "timeframe": "5m",
                            "entry": 4000.2,
                            "stop": 3995.2,
                            "targets": [4012.2],
                        },
                    )
                )
                await ses.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await c.post("/auth/login", json={"email": "adm@x.io", "password": PW})
                yield c, st
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")


async def live_market(st: Any, n: int = 40) -> None:
    now = datetime.now(UTC)
    for i in range(n):  # warm the data-quality monitor with clean quotes
        ts = now - timedelta(seconds=n - i)
        await st.market.on_quote(
            Quote(symbol="XAUUSD", bid=D("4000.00") + D(i % 3) / 10, ask=D("4000.20") + D(i % 3) / 10, ts=ts)
        )
    await st.market.on_quote(Quote(symbol="XAUUSD", bid=D("4000.00"), ask=D("4000.20"), ts=now))


async def enable_trading(c: httpx.AsyncClient) -> str:
    setup = (await c.post("/auth/mfa/setup")).json()
    secret = pyotp.parse_uri(setup["otpauth_uri"]).secret
    await c.post(
        "/auth/mfa/confirm", json={"setup_token": setup["setup_token"], "code": pyotp.TOTP(secret).now()}
    )
    await c.post("/auth/login", json={"email": "adm@x.io", "password": PW, "totp": pyotp.TOTP(secret).now()})
    r = await c.post("/system/trading/enable", json={"password": PW, "totp": pyotp.TOTP(secret).now()})
    assert r.status_code == 204, r.text
    return secret


async def test_evaluation_refuses_until_trading_enabled_then_approves(env: Any) -> None:
    c, st = env
    await live_market(st)
    d = (await c.post("/risk/evaluate/opp-test-1")).json()
    assert not d["approved"] and "trading_enabled" in d["rejections"]
    labels = {ch["code"]: ch for ch in d["checks"]}
    assert labels["kill_switch"]["passed"] and labels["spread"]["passed"]

    await enable_trading(c)
    d = (await c.post("/risk/evaluate/opp-test-1")).json()
    if not is_market_open(datetime.now(UTC)):  # e.g. tests running at the weekend
        assert "market_open" in d["rejections"]
        return
    blocking = set(d["rejections"]) - {"rollover", "weekly_close"}  # time-of-day dependent
    assert not blocking, d["rejections"]
    if d["approved"]:
        assert d["order"]["stop_loss"] == "3995.2" and D(d["sizing"]["risk_amount"]["amount"]) <= 25
    async with st.sessionmaker() as s:
        assert (
            await s.scalar(select(func.count()).select_from(RiskDecisionRow)) == 2
        )  # every decision is stored


async def test_kill_switch_disable_and_reenable_rules(env: Any) -> None:
    c, st = env
    secret = await enable_trading(c)
    assert st.trading.trading_enabled
    assert (await c.post("/system/trading/disable")).status_code == 204 and not st.trading.trading_enabled
    await c.post("/system/kill-switch", json={"reason": "test stop"})
    r = await c.post("/system/trading/enable", json={"password": PW, "totp": pyotp.TOTP(secret).now()})
    assert r.status_code == 409  # cannot enable while the kill switch is engaged


async def test_trade_journal_feeds_risk_status(env: Any) -> None:
    c, st = env
    await live_market(st)
    b = st.broker
    res = await b.place_order(
        OrderRequest(symbol="XAUUSD", side=Side.BUY, volume=D("0.10"), stop_loss=D(3990))
    )
    assert res.status.value == "filled"
    await b.on_quote(
        Quote(symbol="XAUUSD", bid=D("3989.50"), ask=D("3989.70"), ts=datetime.now(UTC))
    )  # stop hit
    await st.risk.flush()
    async with st.sessionmaker() as s:
        row = (await s.execute(select(TradeRow))).scalar_one()
        assert row.reason == "stop_loss" and row.net_pnl < 0 and row.mode == "paper"
    stt = (await c.get("/risk/status")).json()
    assert D(stt["realized_today"]) == row.net_pnl and stt["consecutive_losses"] == 1
    # this test trade (placed directly, bypassing the engine) lost more than the daily cap: budget clamps at 0
    assert D(stt["remaining_daily"]) == max(D(0), D(stt["daily_cap"]) - D(stt["daily_loss"])) == 0
    assert not stt["entry_allowed"] and "perte maximale du jour atteinte" in stt["blocks"]


async def test_guard_engages_kill_switch_at_max_drawdown(env: Any) -> None:
    c, st = env
    async with st.sessionmaker() as s:
        s.add(
            TradeRow(
                mode="paper",
                account_id="paper-1",
                position_id="x",
                symbol="XAUUSD",
                side="buy",
                volume=D(1),
                open_price=D(4000),
                close_price=D(3990),
                opened_at=datetime.now(UTC) - timedelta(days=2),
                closed_at=datetime.now(UTC) - timedelta(days=2),
                reason="stop_loss",
                gross_pnl=D(-900),
                commission=D(0),
                net_pnl=D(-900),
                currency="USD",
            )
        )
        await s.commit()
    # the paper account itself still shows 10 000: make equity match the journal (9 % drawdown)
    st.broker._balance = st.broker._balance.model_copy(update={"amount": D(9100)})
    assert await st.risk.guard_once() is True
    assert st.trading.kill_switch_engaged and "drawdown" in (st.trading.kill_switch_reason or "")
    assert await st.risk.guard_once() is False  # does not fire twice
    stt = (await c.get("/risk/status")).json()
    assert not stt["entry_allowed"] and "drawdown maximal atteint" in stt["blocks"]
