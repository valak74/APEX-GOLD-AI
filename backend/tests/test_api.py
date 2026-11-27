"""API integration tests against a real PostgreSQL database (migrated to head).

Set APEX_TEST_DATABASE_URL (default: local apex_test). Tests are skipped if the database is unreachable.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from decimal import Decimal as D

import httpx
import pyotp
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from apex.api.main import create_app
from apex.core.config import Settings
from apex.db.audit import verify_chain
from apex.db.models import Credential, User
from apex.domain.enums import Role, Side
from apex.domain.models import OrderRequest, Quote, utcnow
from apex.security.crypto import generate_master_key
from apex.security.passwords import hash_password

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")
PW = "correct horse battery staple"
pytestmark = pytest.mark.db


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        environment="test",
        database_url=DB_URL,
        jwt_secret="t" * 40,
        master_key=generate_master_key(),
        cookie_secure=False,
        login_rate_limit_per_minute=5,
        paper_account_currency="EUR",
        log_json=False,
        log_level="WARNING",
    )
    app = create_app(settings)
    try:
        async with app.router.lifespan_context(app):
            state = app.state.apex
            async with state.sessionmaker() as s:
                await s.execute(
                    text(
                        "TRUNCATE core.users, core.audit_log, core.broker_accounts, core.credentials, "
                        "core.risk_config RESTART IDENTITY CASCADE"
                    )
                )
                for email, role in [
                    ("viewer@x.io", Role.VIEWER),
                    ("trader@x.io", Role.TRADER),
                    ("admin@x.io", Role.ADMIN),
                    ("ops@apex.local", Role.VIEWER),
                ]:
                    s.add(User(email=email, password_hash=hash_password(PW), role=role))
                await s.commit()
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                c.app = app  # type: ignore[attr-defined]
                yield c
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":  # CI: a missing database is a failure, not a skip
            raise
        pytest.skip(f"database unavailable: {e}")


async def login(c: httpx.AsyncClient, email: str, totp: str | None = None) -> httpx.Response:
    r = await c.post("/auth/login", json={"email": email, "password": PW, "totp": totp})
    return r


async def enable_mfa(c: httpx.AsyncClient) -> str:
    setup = (await c.post("/auth/mfa/setup")).json()
    secret = pyotp.parse_uri(setup["otpauth_uri"]).secret
    r = await c.post(
        "/auth/mfa/confirm", json={"setup_token": setup["setup_token"], "code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 200 and r.json()["mfa_enabled"]
    return secret


def reauth(secret: str) -> dict[str, str]:
    return {"password": PW, "totp": pyotp.TOTP(secret).now()}


async def test_health_and_auth_required(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).json()["status"] == "ok"
    assert (await client.get("/health/ready")).status_code == 200
    assert (await client.get("/system/status")).status_code == 401
    assert (await client.get("/account")).status_code == 401


async def test_login_flow_and_status(client: httpx.AsyncClient) -> None:
    assert (
        await client.post("/auth/login", json={"email": "viewer@x.io", "password": "wrong-password"})
    ).status_code == 401
    r = await login(client, "viewer@x.io")
    assert r.status_code == 200 and "apex_session" in r.cookies
    assert r.headers["x-frame-options"] == "DENY"
    me = (await client.get("/auth/me")).json()
    assert me == {"id": 1, "email": "viewer@x.io", "role": "viewer", "mfa_enabled": False}
    st = (await client.get("/system/status")).json()
    assert st["mode"] == "paper" and st["trading_enabled"] is False and st["kill_switch_engaged"] is False
    acc = (await client.get("/account")).json()
    assert acc["currency"] == "EUR" and D(acc["balance"]["amount"]) == D(10000)


async def test_login_rate_limited(client: httpx.AsyncClient) -> None:
    codes = [
        (await client.post("/auth/login", json={"email": "x@x.io", "password": "nope-nope-nope"})).status_code
        for _ in range(7)
    ]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]


async def test_viewer_cannot_engage_kill_switch(client: httpx.AsyncClient) -> None:
    await login(client, "viewer@x.io")
    assert (await client.post("/system/kill-switch", json={"reason": "test"})).status_code == 403


async def test_kill_switch_closes_positions_and_rearm_requires_admin_mfa(client: httpx.AsyncClient) -> None:
    broker = client.app.state.apex.broker  # type: ignore[attr-defined]
    now = utcnow()
    await broker.on_quote(Quote(symbol="EURUSD", bid=D("1.05"), ask=D("1.0502"), ts=now))
    await broker.on_quote(Quote(symbol="XAUUSD", bid=D("4000"), ask=D("4000.2"), ts=now))
    res = await broker.place_order(
        OrderRequest(symbol="XAUUSD", side=Side.BUY, volume=D("0.05"), stop_loss=D(3990))
    )
    assert res.status.value == "filled"

    await login(client, "trader@x.io")
    r = await client.post("/system/kill-switch", json={"reason": "manual test stop"})
    body = r.json()
    assert r.status_code == 200 and body["engaged"] and len(body["closed"]) == 1
    assert body["closed"][0]["reason"] == "kill_switch"
    assert (await client.get("/positions")).json() == []
    st = (await client.get("/system/status")).json()
    assert st["kill_switch_engaged"] and not st["trading_enabled"]

    # trader cannot re-arm
    assert (
        await client.post("/system/kill-switch/rearm", json={"password": PW, "totp": "000000"})
    ).status_code == 403
    # admin without MFA cannot re-arm
    await login(client, "admin@x.io")
    assert (
        await client.post("/system/kill-switch/rearm", json={"password": PW, "totp": "000000"})
    ).status_code == 403
    secret = await enable_mfa(client)
    # login now requires TOTP
    assert (await login(client, "admin@x.io")).status_code == 401
    assert (await login(client, "admin@x.io", pyotp.TOTP(secret).now())).status_code == 200
    bad = {"password": "wrong password!!", "totp": pyotp.TOTP(secret).now()}
    assert (await client.post("/system/kill-switch/rearm", json=bad)).status_code == 403
    assert (await client.post("/system/kill-switch/rearm", json=reauth(secret))).status_code == 204
    st = (await client.get("/system/status")).json()
    assert not st["kill_switch_engaged"] and not st["trading_enabled"]  # re-arm never auto-enables trading

    async with client.app.state.apex.sessionmaker() as s:  # type: ignore[attr-defined]
        assert await verify_chain(s)
        actions = (await s.execute(text("SELECT action FROM core.audit_log ORDER BY id"))).scalars().all()
        assert "kill_switch_engaged" in actions and "kill_switch_rearmed" in actions


async def test_risk_config_versioning(client: httpx.AsyncClient) -> None:
    await login(client, "admin@x.io")
    default = (await client.get("/risk/config")).json()
    assert default["version"] is None and default["config"]["risk_per_trade_pct"] == "0.0025"
    secret = await enable_mfa(client)
    await login(client, "admin@x.io", pyotp.TOTP(secret).now())
    bad = {"reauth": reauth(secret), "config": {"risk_per_trade_pct": "0.05"}}  # above absolute 2 % cap
    assert (await client.put("/risk/config", json=bad)).status_code == 422
    bad2 = {"reauth": reauth(secret), "config": {"max_daily_loss_pct": "0.001"}}  # daily < per-trade
    assert (await client.put("/risk/config", json=bad2)).status_code == 422
    ok = {"reauth": reauth(secret), "config": {"risk_per_trade_pct": "0.005"}}
    r = await client.put("/risk/config", json=ok)
    assert r.status_code == 200 and r.json()["version"] == 1
    assert (await client.put("/risk/config", json=ok)).json()["version"] == 2
    got = (await client.get("/risk/config")).json()
    assert got["version"] == 2 and got["config"]["risk_per_trade_pct"] == "0.005"
    assert got["config_hash"] == (await client.get("/system/status")).json()["risk_config_hash"]


async def test_trader_cannot_change_risk_config(client: httpx.AsyncClient) -> None:
    await login(client, "trader@x.io")
    r = await client.put("/risk/config", json={"reauth": {"password": PW, "totp": "1"}, "config": {}})
    assert r.status_code == 403


async def test_credentials_are_write_only_and_encrypted(client: httpx.AsyncClient) -> None:
    await login(client, "admin@x.io")
    secret = await enable_mfa(client)
    await login(client, "admin@x.io", pyotp.TOTP(secret).now())
    acc = {
        "broker": "icmarkets-ctrader",
        "platform": "ctrader",
        "account_ref": "demo-123",
        "mode_allowed": "paper",
        "currency": "EUR",
    }
    r = await client.post("/broker-accounts", json={"reauth": reauth(secret), "account": acc})
    assert r.status_code == 201
    aid = r.json()["id"]
    r = await client.put(
        f"/broker-accounts/{aid}/credentials",
        json={"reauth": reauth(secret), "kind": "client_secret", "value": "SUPER-SECRET-VALUE"},
    )
    assert r.status_code == 204
    listing = await client.get("/broker-accounts")
    assert "SUPER-SECRET-VALUE" not in listing.text
    assert listing.json()[0]["credentials_present"] == ["client_secret"]
    async with client.app.state.apex.sessionmaker() as s:  # type: ignore[attr-defined]
        cred = (await s.execute(select(Credential))).scalar_one()
        assert b"SUPER-SECRET-VALUE" not in cred.ciphertext
        log = (await s.execute(text("SELECT details::text FROM core.audit_log"))).scalars().all()
        assert not any("SUPER-SECRET-VALUE" in d for d in log)


async def test_audit_log_is_append_only(client: httpx.AsyncClient) -> None:
    await login(client, "viewer@x.io")
    async with client.app.state.apex.sessionmaker() as s:  # type: ignore[attr-defined]
        with pytest.raises(DBAPIError, match="append-only"):
            await s.execute(text("UPDATE core.audit_log SET actor = 'evil'"))
        await s.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            await s.execute(text("DELETE FROM core.audit_log"))


async def test_position_size_calculator_eur(client: httpx.AsyncClient) -> None:
    await login(client, "viewer@x.io")
    body = {"side": "buy", "entry": "4000", "stop_loss": "3995", "quote_to_account_rate": "0.95"}
    r = (await client.post("/risk/position-size", json=body)).json()
    # 10k EUR * 0.25 % = 25 EUR; loss/lot = (500 + 7) * 0.95 = 481.65 EUR -> 0.05 lot
    assert r["approved"] and D(r["volume"]) == D("0.05")
    assert r["risk_amount"]["ccy"] == "EUR" and D(r["risk_amount"]["amount"]) <= 25
    r2 = (await client.post("/risk/position-size", json={**body, "stop_loss": "4001"})).json()
    assert not r2["approved"] and r2["rejections"] == ["invalid_stop_side"]


async def test_login_accepts_local_domain_and_is_case_insensitive(client: httpx.AsyncClient) -> None:
    r = await client.post("/auth/login", json={"email": "OPS@apex.local", "password": PW})
    assert r.status_code == 200 and r.json()["email"] == "ops@apex.local"
