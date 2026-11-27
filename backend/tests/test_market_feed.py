"""End-to-end: encrypted credentials in DB -> fake cTrader server -> live quotes -> paper broker -> API."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from apex.api.main import create_app
from apex.broker.ctrader.adapter import CTraderCredentials
from apex.broker.ctrader.oauth import OAuthError, _parse_tokens, build_auth_url, exchange_code, extract_code
from apex.broker.ctrader.store import (
    CredentialsMissingError,
    load_ctrader_credentials,
    save_ctrader_account,
)
from apex.core.config import Settings
from apex.db.models import User
from apex.domain.enums import OrderStatus, Role, Side
from apex.domain.models import OrderRequest
from apex.security.crypto import generate_master_key
from apex.security.passwords import hash_password
from tests.fake_ctrader import FakeCTrader

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")
PW = "correct horse battery staple"


# --------------------------------------------------------------------------- OAuth helpers (no network)
def test_extract_code_variants() -> None:
    assert extract_code("http://localhost:8080/callback?code=ABC123&state=x") == "ABC123"
    assert extract_code("  ABC123  ") == "ABC123"
    with pytest.raises(OAuthError, match="refused"):
        extract_code("http://localhost:8080/callback?error=access_denied")
    with pytest.raises(OAuthError):
        extract_code("http://localhost:8080/callback?foo=1")


def test_auth_url_is_encoded() -> None:
    url = build_auth_url("12_ab", "http://localhost:8080/callback")
    assert url.startswith("https://openapi.ctrader.com/apps/auth?client_id=12_ab&redirect_uri=http%3A%2F%2F")
    assert url.endswith("scope=trading")


def test_token_parsing() -> None:
    t = _parse_tokens({"accessToken": "a", "refreshToken": "r", "expiresIn": 2628000, "tokenType": "bearer"})
    assert (t.access_token, t.refresh_token, t.expires_in) == ("a", "r", 2628000)
    assert "a" not in repr(t)
    with pytest.raises(OAuthError, match="ACCESS_DENIED"):
        _parse_tokens({"errorCode": "ACCESS_DENIED", "description": "bad code"})


async def test_exchange_code_sends_expected_params() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        seen["host"] = req.url.host
        return httpx.Response(200, json={"accessToken": "A", "refreshToken": "R", "expiresIn": 10})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        t = await exchange_code("cid", "sec", "CODE", "http://localhost:8080/callback", http=http)
    assert t.access_token == "A"
    assert seen == {
        "grant_type": "authorization_code",
        "code": "CODE",
        "client_id": "cid",
        "client_secret": "sec",
        "redirect_uri": "http://localhost:8080/callback",
        "host": "openapi.ctrader.com",
    }


# --------------------------------------------------------------------------- full stack
@pytest.fixture
async def stack() -> AsyncIterator[tuple[httpx.AsyncClient, Any, FakeCTrader]]:
    fake = FakeCTrader()
    await fake.start()
    mk = generate_master_key()
    base = dict(
        environment="test",
        database_url=DB_URL,
        jwt_secret="t" * 40,
        master_key=mk,
        cookie_secure=False,
        paper_account_currency="EUR",
        log_json=False,
        log_level="WARNING",
    )
    try:
        # 1) link the account (what `apex ctrader-setup` does after OAuth)
        setup_app = create_app(Settings(**base))  # type: ignore[arg-type]
        async with setup_app.router.lifespan_context(setup_app):
            maker = setup_app.state.apex.sessionmaker
            async with maker() as s:
                await s.execute(
                    text(
                        "TRUNCATE core.users, core.audit_log, core.broker_accounts, core.credentials, "
                        "core.risk_config RESTART IDENTITY CASCADE"
                    )
                )
                s.add(User(email="v@x.io", password_hash=hash_password(PW), role=Role.VIEWER))
                await s.commit()
            async with maker() as s:
                creds = CTraderCredentials("cid", "csecret", "atoken", "rtoken", 777)
                acc = await save_ctrader_account(
                    s,
                    mk,
                    actor="test",
                    broker="fake-ctrader",
                    ctid_account_id=777,
                    currency="EUR",
                    creds=creds,
                )
        # 2) run the API with the cTrader feed enabled, pointed at the fake server
        app = create_app(
            Settings(
                **base,
                market_data_source="ctrader",
                ctrader_broker_account_id=acc.id,  # type: ignore[arg-type]
                ctrader_host="127.0.0.1",
                ctrader_port=fake.port,
                ctrader_use_tls=False,
            )
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                await c.post("/auth/login", json={"email": "v@x.io", "password": PW})
                yield c, app.state.apex, fake
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")
    finally:
        await fake.stop()


async def wait_for(cond: Any, limit_s: float = 5.0) -> None:
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


async def test_live_quotes_reach_paper_broker_and_api(stack: Any) -> None:
    c, state, fake = stack
    await wait_for(lambda: state.market.describe()["state"] == "connected")
    st = (await c.get("/system/status")).json()["market_data"]
    assert (
        st["source"] == "ctrader"
        and st["broker"] == "FakeBroker"
        and st["spec_source"] == "ctrader:FakeBroker"
    )

    # real spec replaced the placeholder: 30 USD/million commission, margin capped at 1:20
    spec = (await c.get("/market/spec")).json()
    assert spec["spec"]["commission_type"] == "per_million_notional" and spec["spec"]["margin_rate"] == "0.05"

    assert (await c.get("/market/quote")).status_code == 503  # nothing received yet
    now_ms = int(time.time() * 1000)
    await fake.push_spot(1, 105000, 105020, ts_ms=now_ms)  # EURUSD for conversion
    await fake.push_spot(41, 400010000, 400030000, ts_ms=now_ms)
    await wait_for(lambda: "XAUUSD" in state.market.last)
    q = (await c.get("/market/quote")).json()
    assert D(q["bid"]) == D("4000.10") and D(q["spread"]) == D("0.20") and q["age_s"] < 5

    # the paper broker now fills on real prices, with the broker's real commission model
    res = await state.broker.place_order(
        OrderRequest(symbol="XAUUSD", side=Side.BUY, volume=D("0.10"), stop_loss=D(3990))
    )
    assert res.status is OrderStatus.FILLED and res.fill_price == D("4000.30")
    # 30/million * 0.1 * 100 * 4000.30 = 1.20009 USD -> EUR at 1/1.05 (conservative)
    assert res.commission.ccy == "EUR"
    assert abs(res.commission.amount - D("1.20009") / D("1.05")) < D("0.0001")

    # sizing calculator uses the live EURUSD rate: no manual rate needed any more
    r = (
        await c.post("/risk/position-size", json={"side": "buy", "entry": "4000", "stop_loss": "3995"})
    ).json()
    assert r["approved"], r


async def test_missing_credentials_reported_not_crashing(stack: Any) -> None:
    _c, state, _ = stack
    async with state.sessionmaker() as s:
        with pytest.raises(CredentialsMissingError):
            await load_ctrader_credentials(s, state.settings.master_key.get_secret_value(), 999)


def test_tls_cannot_be_disabled_outside_tests() -> None:
    with pytest.raises(ValueError, match="TLS"):
        Settings(environment="dev", ctrader_use_tls=False)


def test_empty_env_values_mean_unset() -> None:
    s = Settings(ctrader_broker_account_id="", ctrader_host="")  # type: ignore[arg-type]
    assert s.ctrader_broker_account_id is None and s.ctrader_host is None
