from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pyotp
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from apex.api.main import create_app
from apex.core.config import Settings
from apex.db.models import BarRow, User
from apex.domain.enums import Role
from apex.intel.calendar import evaluate_lockout
from apex.market.sessions import bucket_start
from apex.security.crypto import generate_master_key
from apex.security.passwords import hash_password

DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")
PW = "correct horse battery staple"
STEP = {"1d": 86400, "4h": 14400, "1h": 3600, "15m": 900, "5m": 300}


def ev(title: str, at: datetime, importance: str = "high", ccy: str = "USD") -> Any:
    return SimpleNamespace(title=title, scheduled_at=at, importance=importance, currency=ccy)


# --------------------------------------------------------------------------- lockout (pure)
def test_lockout_window_and_next_event() -> None:
    now = datetime(2026, 12, 4, 13, 20, tzinfo=UTC)
    nfp = ev("Non-Farm Payrolls", datetime(2026, 12, 4, 13, 30, tzinfo=UTC))
    lk = evaluate_lockout([nfp], now, 15, 15)
    assert lk.locked and "avant" in (lk.reason or "") and lk.until == nfp.scheduled_at + timedelta(minutes=15)
    after = evaluate_lockout([nfp], now + timedelta(minutes=20), 15, 15)
    assert after.locked and "après" in (after.reason or "")
    free = evaluate_lockout([nfp], now + timedelta(minutes=26), 15, 15)
    assert not free.locked
    early = evaluate_lockout([nfp], now - timedelta(hours=2), 15, 15)
    assert not early.locked and early.next_event_title == "Non-Farm Payrolls"


def test_lockout_ignores_low_importance_and_unrelated_currency() -> None:
    now = datetime(2026, 12, 4, 13, 30, tzinfo=UTC)
    assert not evaluate_lockout([ev("x", now, "medium")], now, 15, 15).locked
    assert not evaluate_lockout([ev("x", now, "high", "NZD")], now, 15, 15).locked
    assert evaluate_lockout([ev("x", now, "high", "CNY")], now, 15, 15).locked


# --------------------------------------------------------------------------- API
@pytest.fixture
async def client() -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    s = Settings(
        environment="test",
        database_url=DB_URL,
        jwt_secret="t" * 40,
        master_key=generate_master_key(),  # type: ignore[arg-type]
        cookie_secure=False,
        log_json=False,
        log_level="WARNING",
    )
    app = create_app(s)
    try:
        async with app.router.lifespan_context(app):
            st = app.state.apex
            async with st.sessionmaker() as ses:
                await ses.execute(
                    text(
                        "TRUNCATE core.users, core.audit_log, market.ticks, market.bars, "
                        "intel.economic_events RESTART IDENTITY"
                    )
                )
                ses.add(User(email="a@x.io", password_hash=hash_password(PW), role=Role.ADMIN))
                rng = np.random.default_rng(11)
                now = datetime.now(UTC)
                for tf, n in (("1d", 120), ("4h", 200), ("1h", 300), ("15m", 400), ("5m", 400)):
                    start = bucket_start(now, tf) - timedelta(seconds=STEP[tf] * n)
                    px = 4000.0
                    for i in range(n):
                        o = px
                        px += float(rng.normal(0.3, 2.0))
                        hi, lo = (
                            max(o, px) + abs(float(rng.normal(0, 1))),
                            min(o, px) - abs(float(rng.normal(0, 1))),
                        )
                        ses.add(
                            BarRow(
                                source="hist",
                                symbol="XAUUSD",
                                timeframe=tf,
                                start=start + timedelta(seconds=STEP[tf] * i),
                                open=D(f"{o:.2f}"),
                                high=D(f"{hi:.2f}"),
                                low=D(f"{lo:.2f}"),
                                close=D(f"{px:.2f}"),
                                spread_avg=D("0.2"),
                                spread_max=D("0.4"),
                                tick_count=10,
                            )
                        )
                await ses.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                await c.post("/auth/login", json={"email": "a@x.io", "password": PW})
                yield c, st
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")


async def test_context_overlays_and_stats(client: Any) -> None:
    c, _ = client
    ctx = (await c.get("/analysis/context")).json()
    assert set(ctx["timeframes"]) == {"1d", "4h", "1h", "15m", "5m"}
    assert ctx["bias"] in ("bullish", "bearish", "neutral") and -1 <= ctx["bias_score"] <= 1
    assert any(lv["name"] == "prev_day_high" for lv in ctx["levels"])
    tf5 = ctx["timeframes"]["5m"]
    assert tf5["regime"]["label"] != "insufficient_data" and tf5["role"] == "execution"

    ov = (await c.get("/analysis/overlays", params={"timeframe": "15m"})).json()
    assert {"events", "fvgs", "order_blocks", "pools", "sweeps", "levels", "regime"} <= set(ov)
    for e in ov["events"]:
        assert e["kind"] in ("BOS", "CHoCH") and e["invalidation"] and 0 <= e["score"] <= 1
    assert (await c.get("/analysis/overlays", params={"timeframe": "3m"})).status_code == 422

    for pattern in ("bos", "fvg", "sweep"):
        s = (await c.get("/analysis/stats", params={"pattern": pattern, "timeframe": "15m"})).json()
        assert s["bars_analyzed"] == 400 and s["n"] >= 0 and "horizons" in s


async def test_calendar_import_upcoming_and_lockout(client: Any) -> None:
    c, _ = client
    setup = (await c.post("/auth/mfa/setup")).json()
    secret = pyotp.parse_uri(setup["otpauth_uri"]).secret
    await c.post(
        "/auth/mfa/confirm", json={"setup_token": setup["setup_token"], "code": pyotp.TOTP(secret).now()}
    )
    await c.post("/auth/login", json={"email": "a@x.io", "password": PW, "totp": pyotp.TOTP(secret).now()})
    now = datetime.now(UTC).replace(microsecond=0)
    events = [
        {
            "event_code": "US_CPI",
            "title": "US CPI",
            "country": "US",
            "currency": "USD",
            "importance": "high",
            "scheduled_at": (now + timedelta(minutes=10)).isoformat(),
            "forecast": "0.3%",
        },
        {
            "event_code": "US_NFP",
            "title": "Non-Farm Payrolls",
            "country": "US",
            "currency": "USD",
            "importance": "high",
            "scheduled_at": (now + timedelta(days=2)).isoformat(),
        },
    ]
    body = {"reauth": {"password": PW, "totp": pyotp.TOTP(secret).now()}, "events": events}
    assert (await c.post("/calendar/import", json=body)).json() == {"imported": 2}
    assert (await c.post("/calendar/import", json=body)).json() == {"imported": 2}  # idempotent upsert
    up = (await c.get("/calendar/upcoming")).json()
    assert [e["event_code"] for e in up] == ["US_CPI", "US_NFP"]
    lk = (await c.get("/calendar/lockout")).json()
    assert lk["locked"] and lk["event_title"] == "US CPI" and lk["before_min"] == 15
    bad = {**body, "events": [{**events[0], "scheduled_at": "2026-12-04T13:30:00"}]}  # naive time refused
    assert (await c.post("/calendar/import", json=bad)).status_code == 422
