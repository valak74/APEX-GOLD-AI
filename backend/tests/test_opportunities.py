from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import anthropic
import httpx
import httpx2
import numpy as np
import pyotp  # noqa: F401  (kept for parity with other API tests)
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from apex.analysis.engine import mtf_context
from apex.analysis.indicators import Candles
from apex.analysis.opportunities import Opportunity, detect, opportunity_from_payload
from apex.api.main import create_app
from apex.core.config import Settings
from apex.db.models import BarRow, User
from apex.domain.enums import Role
from apex.domain.models import Quote
from apex.intel.explainer import SCHEMA, Explainer, Narrative, facts
from apex.market.sessions import bucket_start
from apex.security.passwords import hash_password

T0 = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
DB_URL = os.environ.get("APEX_TEST_DATABASE_URL", "postgresql+asyncpg://apex:apex@localhost:5432/apex_test")
PW = "correct horse battery staple"


def mk(rows: list[tuple[float, float, float, float]], tf: str = "5m") -> Candles:
    a = np.array(rows, dtype=float)
    step = {"5m": 300, "15m": 900}[tf]
    return Candles(
        [T0 + timedelta(seconds=step * i) for i in range(len(a))], a[:, 0], a[:, 1], a[:, 2], a[:, 3], tf
    )


def sweep_series() -> Candles:
    """Range with two equal lows at 3990, then a bar that wicks to 3986 and closes back at 3993."""
    rng = np.random.default_rng(4)
    rows, px = [], 4000.0
    for _ in range(70):
        o = px
        px = float(np.clip(px + rng.normal(0, 1.2), 3993, 4008))
        rows.append((o, max(o, px) + 0.6, min(o, px) - 0.6, px))
    for low in (3990.0, 3990.05):  # equal lows (liquidity pool), each a clear swing low
        rows += [
            (3996, 3997, 3994, 3995),
            (3995, 3996, low, 3994),
            (3994, 3998, 3993.5, 3997),
            (3997, 3998.5, 3996, 3998),
            (3998, 3999, 3997, 3998.5),
        ]
    rows += [(3998.5, 3999, 3996, 3997), (3997, 3998, 3986.0, 3993.0)]  # sweep below 3990, close back above
    return mk(rows)


# --------------------------------------------------------------------------- detector
def test_detects_liquidity_sweep_reversal_with_full_plan() -> None:
    c = sweep_series()
    opps = [o for o in detect(c) if o.setup == "liquidity_sweep_reversal"]
    assert opps, "sweep not detected"
    o = opps[-1]
    assert o.direction == "bullish" and o.timeframe == "5m"
    assert o.stop < 3986.0 < o.entry == 3993.0 and all(t > o.entry for t in o.targets)
    assert o.risk_reward == pytest.approx(round((o.targets[0] - o.entry) / (o.entry - o.stop), 2))
    by_key = {x.key: x for x in o.confluences}
    assert by_key["sweep"].ok and "3990" in by_key["sweep"].detail
    assert by_key["pool"].ok  # equal lows
    assert (
        0 <= o.quality <= 100
        and o.invalidation.startswith("clôture")
        and o.evidence["sweep"]["extreme"] == 3986.0
    )
    assert o.risk_reward >= 1.0  # targets closer than 1R are never proposed
    if len(o.targets) == 2:
        assert abs(o.targets[1] - o.targets[0]) >= 0.5 * (o.entry - o.stop)
    assert o.stats is not None and "hit_rate" in o.stats and "breakeven_rate" in o.stats
    assert detect(c)[-1].id == o.id  # deterministic id


def test_no_opportunity_from_incomplete_history_or_after_invalidation() -> None:
    c = sweep_series()
    assert detect(c.head(50)) == []  # < 60 bars
    # one more bar closing below the sweep extreme kills the idea (entry would be beyond the stop)
    a = np.array([[3993, 3994, 3980, 3981]], dtype=float)
    c2 = Candles(
        [*c.t, c.t[-1] + timedelta(minutes=5)],
        np.append(c.o, a[:, 0]),
        np.append(c.h, a[:, 1]),
        np.append(c.l, a[:, 2]),
        np.append(c.c, a[:, 3]),
        "5m",
    )
    assert not [
        o
        for o in detect(c2)
        if o.setup == "liquidity_sweep_reversal"
        and o.direction == "bullish"
        and o.evidence["sweep"]["extreme"] == 3986.0
    ]


def test_trend_pullback_needs_higher_timeframe_bias() -> None:
    rng = np.random.default_rng(9)
    closes = list(4000 + np.cumsum(rng.normal(0.5, 1.0, 300)))
    rows, prev = [], closes[0]
    for x in closes:
        o = (prev + x) / 2
        rows.append((o, max(o, x) + 0.4, min(o, x) - 0.4, x))
        prev = x
    c = mk(rows)
    frames = {
        "1d": Candles(c.t, c.o, c.h, c.l, c.c, "1d"),
        "4h": Candles(c.t, c.o, c.h, c.l, c.c, "4h"),
        "5m": c,
    }
    ctx = mtf_context(frames, None, c.t[-1])
    for o in detect(c, ctx):
        if o.setup == "trend_pullback":
            assert o.direction == ctx.bias and o.evidence["zone"]["bottom"] <= o.evidence["zone"]["top"]
    no_ctx = [o for o in detect(c, None) if o.setup == "trend_pullback"]
    assert no_ctx == []  # never without a higher-timeframe bias


def test_payload_roundtrip() -> None:
    o = next(x for x in detect(sweep_series()) if x.setup == "liquidity_sweep_reversal")
    back = opportunity_from_payload(json.loads(json.dumps(o.to_dict(), default=str)))
    assert back.id == o.id and back.confluences[0].label == o.confluences[0].label


# --------------------------------------------------------------------------- LLM explainer (mocked API)
def fake_client(reply: dict[str, Any], seen: dict[str, Any]) -> anthropic.AsyncAnthropic:
    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(req.content)
        seen["headers"] = dict(req.headers)
        return httpx2.Response(200, json=reply)

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def message(text_: str, stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text_}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


async def test_explainer_request_and_validated_output() -> None:
    o = next(x for x in detect(sweep_series()) if x.setup == "liquidity_sweep_reversal")
    good = {
        "resume": "Retournement haussier après balayage.",
        "pourquoi": ["a", "b", "c"],
        "risques": ["x", "y"],
        "invalidation": "clôture sous 3986",
        "prudence": "avantage non prouvé",
    }
    seen: dict[str, Any] = {}
    n = await Explainer(client=fake_client(message(json.dumps(good)), seen)).explain(o)
    assert isinstance(n, Narrative) and n.resume.startswith("Retournement") and n.model == "claude-opus-5-5"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert "server-side-fallback-2026-07-01" in seen["headers"].get("anthropic-beta", "")
    sent = json.loads(body["messages"][0]["content"].split("\n", 1)[1])
    assert sent == facts(o) and "N'utilise QUE les faits fournis" in body["system"]


@pytest.mark.parametrize(
    "reply",
    [
        message("", "refusal"),
        message("pas du json"),
        message(json.dumps({"resume": "x"})),
        message("{}", "max_tokens"),
    ],
)
async def test_explainer_never_returns_unvalidated_text(reply: dict[str, Any]) -> None:
    o = next(x for x in detect(sweep_series()) if x.setup == "liquidity_sweep_reversal")
    assert await Explainer(client=fake_client(reply, {})).explain(o) is None


async def test_explainer_api_error_is_contained() -> None:
    def boom(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            529, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        )

    client = anthropic.AsyncAnthropic(
        api_key="k", max_retries=0, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(boom))
    )
    o = next(x for x in detect(sweep_series()) if x.setup == "liquidity_sweep_reversal")
    assert await Explainer(client=client).explain(o) is None


# --------------------------------------------------------------------------- scanner + API (real DB)
class StubExplainer:
    def __init__(self) -> None:
        self.calls = 0

    async def explain(self, opp: Opportunity) -> Narrative:
        self.calls += 1
        return Narrative(
            resume="ok", pourquoi=["p"], risques=["r"], invalidation="i", prudence="pr", model="stub"
        )


@pytest.fixture
async def api() -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    s = Settings(
        environment="test",
        database_url=DB_URL,
        jwt_secret="t" * 40,
        cookie_secure=False,  # type: ignore[arg-type]
        log_json=False,
        log_level="WARNING",
        scanner_enabled=False,
    )
    app = create_app(s)
    try:
        async with app.router.lifespan_context(app):
            st = app.state.apex
            async with st.sessionmaker() as ses:
                await ses.execute(
                    text(
                        "TRUNCATE core.users, core.audit_log, market.bars, market.ticks, "
                        "intel.economic_events, trading.opportunities RESTART IDENTITY"
                    )
                )
                ses.add(User(email="t@x.io", password_hash=hash_password(PW), role=Role.TRADER))
                c = sweep_series()
                start = bucket_start(datetime.now(UTC), "5m") - timedelta(minutes=5 * (len(c) + 1))
                for i in range(len(c)):
                    ses.add(
                        BarRow(
                            source="hist",
                            symbol="XAUUSD",
                            timeframe="5m",
                            start=start + timedelta(minutes=5 * i),
                            open=D(f"{c.o[i]:.2f}"),
                            high=D(f"{c.h[i]:.2f}"),
                            low=D(f"{c.l[i]:.2f}"),
                            close=D(f"{c.c[i]:.2f}"),
                            spread_avg=D("0.2"),
                            spread_max=D("0.3"),
                            tick_count=5,
                        )
                    )
                await ses.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as cl:
                await cl.post("/auth/login", json={"email": "t@x.io", "password": PW})
                yield cl, st
    except (OSError, DBAPIError) as e:
        if os.environ.get("APEX_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"database unavailable: {e}")


async def test_scan_persist_block_explain_and_track(api: Any) -> None:
    cl, st = api
    assert (await cl.post("/opportunities/scan")).json()["new"] >= 1
    assert (await cl.post("/opportunities/scan")).json()["new"] == 0  # idempotent
    opps = (await cl.get("/opportunities")).json()
    o = next(x for x in opps if x["setup"] == "liquidity_sweep_reversal")
    assert o["status"] == "active" and o["confluences"] and o["evidence"]["sweep"]
    # no live quotes in this test -> data quality blocks trading, and it says so
    assert any("qualité des données" in b for b in o["blockers"])

    assert (await cl.post(f"/opportunities/{o['id']}/explain")).status_code == 503  # LLM disabled
    st.scanner.explainer = StubExplainer()
    n = (await cl.post(f"/opportunities/{o['id']}/explain")).json()
    assert n["resume"] == "ok"
    await cl.post(f"/opportunities/{o['id']}/explain")
    assert st.scanner.explainer.calls == 1  # cached on the opportunity
    assert (await cl.get(f"/opportunities/{o['id']}")).json()["narrative"]["model"] == "stub"

    # paper tracking: a quote below the stop invalidates the idea
    now = datetime.now(UTC)
    stop = D(str(o["stop"]))
    st.market.last["XAUUSD"] = Quote(symbol="XAUUSD", bid=stop - 1, ask=stop - D("0.8"), ts=now)
    await st.scanner.track(now)
    assert (await cl.get(f"/opportunities/{o['id']}")).json()["status"] == "invalidated"

    ind = (await cl.get("/analysis/indicators", params={"timeframe": "5m"})).json()
    assert len(ind["t"]) == len(ind["rsi"]) == len(ind["ema50"]) and ind["ema50"][-1] is not None


def test_same_sweep_seen_on_consecutive_scans_is_one_opportunity() -> None:
    """The detector looks back a few bars: a live scanner sees the same sweep several times."""
    c = sweep_series()
    calm = np.array([[3993, 3995, 3992, 3994], [3994, 3996, 3993, 3995]], dtype=float)
    ids = set()
    for k in range(3):
        rows = calm[:k]
        ck = Candles(
            [*c.t, *[c.t[-1] + timedelta(minutes=5 * (j + 1)) for j in range(k)]],
            np.append(c.o, rows[:, 0]),
            np.append(c.h, rows[:, 1]),
            np.append(c.l, rows[:, 2]),
            np.append(c.c, rows[:, 3]),
            "5m",
        )
        ids |= {o.id for o in detect(ck) if o.setup == "liquidity_sweep_reversal"}
    assert len(ids) == 1
    o = next(x for x in detect(c) if x.setup == "liquidity_sweep_reversal")
    assert o.evidence["trigger_bar"] == c.t[-1]  # marker on the sweep bar


def test_detector_drops_ideas_that_costs_make_unattractive() -> None:
    c = sweep_series()
    raw = [o for o in detect(c) if o.setup == "liquidity_sweep_reversal"]
    assert raw and raw[0].net_risk_reward == raw[0].risk_reward  # no costs given
    costly = [
        o for o in detect(c, cost_per_unit=50.0, min_net_rr=1.5) if o.setup == "liquidity_sweep_reversal"
    ]
    assert costly == []
