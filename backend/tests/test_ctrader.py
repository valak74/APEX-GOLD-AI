from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal as D
from typing import Any

import pytest

from apex.broker.base import BrokerNotConnectedError, UnknownSymbolError
from apex.broker.ctrader import proto
from apex.broker.ctrader.adapter import CTraderAdapter, CTraderCredentials, TradingDisabledError
from apex.broker.ctrader.client import CTraderApiError, CTraderClient
from apex.broker.ctrader.mapping import (
    UnsupportedSpecError,
    canonical_name,
    find_symbol,
    money_from_int,
    price_from_int,
    symbol_spec_from_proto,
)
from apex.domain.enums import Side
from apex.domain.models import OrderRequest
from tests.fake_ctrader import ASSETS, FakeCTrader, gold_symbol


def light(sid: int = 41, name: str = "XAUUSD", base: int = 3, quote: int = 1) -> Any:
    return proto.new(
        "ProtoOALightSymbol",
        symbolId=sid,
        symbolName=name,
        enabled=True,
        baseAssetId=base,
        quoteAssetId=quote,
    )


# --------------------------------------------------------------------------- protocol & mapping
def test_payload_types_match_official_enum() -> None:
    assert proto.payload_type("ProtoOAApplicationAuthReq") == 2100
    assert proto.payload_type("ProtoOASpotEvent") == 2131
    assert proto.payload_type("ProtoOAErrorRes") == 2142
    assert proto.payload_type("ProtoHeartbeatEvent") == 51


def test_frame_roundtrip_and_size_guard() -> None:
    frame = proto.encode_frame(proto.new("ProtoOATraderReq", ctidTraderAccountId=5), "abc")
    env = proto.decode_body(frame[4:])
    assert env.name == "ProtoOATraderReq" and env.client_msg_id == "abc"
    assert env.message.ctidTraderAccountId == 5  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        proto.frame_length((proto.MAX_FRAME + 1).to_bytes(4, "big"))


def test_units() -> None:
    assert price_from_int(400012345) == D("4000.12345")
    assert money_from_int(1_000_050, 2) == D("10000.50")
    assert money_from_int(123, None) == D("1.23")


def test_gold_spec_mapping_100oz_per_million_commission() -> None:
    spec = symbol_spec_from_proto(gold_symbol(), light(), ASSETS, max_leverage=20)
    assert spec.symbol == "XAUUSD" and spec.contract_size == D(100)
    assert (spec.volume_min, spec.volume_step, spec.volume_max) == (D("0.01"), D("0.01"), D(500))
    assert spec.tick_size == D("0.01") and spec.margin_rate == D("0.05")
    assert spec.commission_type == "per_million_notional" and spec.commission_rate == D(30)
    assert spec.commission_for(D(1), D(4000)) == D(12)


def test_spec_min_commission_and_stops() -> None:
    sym = gold_symbol(
        preciseMinCommission=50_000_000, minCommissionType=1, minCommissionAsset="USD", slDistance=50
    )
    spec = symbol_spec_from_proto(sym, light(), ASSETS)
    assert spec.min_commission == D("0.5") and spec.stops_level == D("0.50")


@pytest.mark.parametrize(
    "over",
    [
        {"distanceSetIn": 2, "slDistance": 10},  # percentage distance: unsupported -> refuse
        {"tradingMode": 3},  # close-only
        {"preciseMinCommission": 100, "minCommissionType": 1, "minCommissionAsset": "EUR"},  # ccy mismatch
    ],
)
def test_spec_fails_closed(over: dict[str, Any]) -> None:
    with pytest.raises(UnsupportedSpecError):
        symbol_spec_from_proto(gold_symbol(**over), light(), ASSETS)


def test_symbol_resolution_ignores_suffixes() -> None:
    lights = [light(99, "XAUUSD.x"), light(41, "XAUUSD"), light(1, "EURUSD", 2, 1)]
    assert canonical_name(lights[0], ASSETS) == "XAUUSD"
    assert find_symbol(lights, ASSETS, "XAU", "USD").symbolId == 41
    assert find_symbol([light(99, "XAUUSD.x")], ASSETS, "XAU", "USD").symbolId == 99
    with pytest.raises(UnsupportedSpecError):
        find_symbol(lights, ASSETS, "XAG", "USD")


# --------------------------------------------------------------------------- client & adapter vs fake server
@pytest.fixture
async def fake() -> AsyncIterator[FakeCTrader]:
    f = FakeCTrader()
    await f.start()
    yield f
    await f.stop()


def make_adapter(f: FakeCTrader, saved: list[tuple[str, str]] | None = None, **kw: Any) -> CTraderAdapter:
    async def save(a: str, r: str) -> None:
        if saved is not None:
            saved.append((a, r))

    creds = CTraderCredentials(
        client_id="cid",
        client_secret="csecret",
        access_token="atoken",
        refresh_token="rtoken",
        ctid_trader_account_id=777,
    )
    return CTraderAdapter(
        creds,
        client_factory=lambda h, p: CTraderClient(h, p, use_tls=False, heartbeat_s=0.05),
        host="127.0.0.1",
        port=f.port,
        on_tokens_refreshed=save,
        **kw,
    )


async def test_client_error_mapping(fake: FakeCTrader) -> None:
    c = CTraderClient("127.0.0.1", fake.port, use_tls=False)
    await c.connect()
    with pytest.raises(CTraderApiError) as e:
        await c.request(proto.new("ProtoOAApplicationAuthReq", clientId="x", clientSecret="y"))
    assert e.value.code == "CH_CLIENT_AUTH_FAILURE"
    await c.close()
    with pytest.raises(BrokerNotConnectedError):
        await c.request(proto.new("ProtoOAVersionReq"))


async def test_adapter_session_spec_account_and_quotes(fake: FakeCTrader) -> None:
    a = make_adapter(fake)
    await a.connect()
    assert a.connected and a.currency == "EUR" and a.leverage == 30 and a.broker_name == "FakeBroker"
    assert a.symbol_ids == {"XAUUSD": 41, "EURUSD": 1}
    spec = await a.get_symbol_spec("XAUUSD")
    assert spec.margin_rate == D("0.05")  # min(account 30, cap 20)
    info = await a.get_account_info()
    assert (
        info.currency == "EUR"
        and info.balance.amount == D("10000.00")
        and info.equity.amount == D("10000.00")
    )

    got: list[Any] = []

    async def listener(q: Any) -> None:
        got.append(q)

    a.add_quote_listener(listener)
    await fake.push_spot(41, 400012000, 400030000, ts_ms=1_764_000_000_000)
    await fake.push_spot(41, None, 400035000, ts_ms=1_764_000_000_500)  # only ask changed
    await fake.push_spot(1, 105000, 105020, ts_ms=1_764_000_000_600)
    for _ in range(50):
        if len(got) >= 3:
            break
        await asyncio.sleep(0.01)
    assert [(q.symbol, q.bid, q.ask) for q in got] == [
        ("XAUUSD", D("4000.12"), D("4000.30")),
        ("XAUUSD", D("4000.12"), D("4000.35")),
        ("EURUSD", D("1.05"), D("1.0502")),
    ]
    q = await a.get_market_data("XAUUSD")
    assert q.spread == D("0.23")
    assert a.fx.rate("USD", "EUR", q.ts, "high") == 1 / D("1.05")  # conversion pair is live
    await a.disconnect()


async def test_crossed_quote_is_dropped(fake: FakeCTrader) -> None:
    a = make_adapter(fake)
    await a.connect()
    await fake.push_spot(41, 400050000, 400010000)  # bid > ask
    await asyncio.sleep(0.05)
    with pytest.raises(UnknownSymbolError):
        await a.get_market_data("XAUUSD")
    await a.disconnect()


async def test_expired_token_is_refreshed_and_persisted(fake: FakeCTrader) -> None:
    fake.token_expired = True
    saved: list[tuple[str, str]] = []
    a = make_adapter(fake, saved)
    await a.connect()
    assert a.connected and saved == [("atoken2", "rtoken2")]
    assert a.creds.access_token == "atoken2"
    assert fake.received.count("ProtoOAAccountAuthReq") == 2 and "ProtoOARefreshTokenReq" in fake.received
    await a.disconnect()


async def test_positions_and_equity(fake: FakeCTrader) -> None:
    td = proto.new("ProtoOATradeData", symbolId=41, volume=500, tradeSide=2, openTimestamp=1_764_000_000_000)
    fake.positions = [
        proto.new(
            "ProtoOAPosition",
            positionId=9,
            tradeData=td,
            positionStatus=1,
            swap=0,
            price=4001.5,
            stopLoss=4010.0,
            usedMargin=20_000,
            moneyDigits=2,
        )
    ]
    fake.pnl = {9: -1234}
    a = make_adapter(fake)
    await a.connect()
    [p] = await a.get_positions()
    assert p.side is Side.SELL and p.volume == D("0.05") and p.open_price == D("4001.5")
    assert (
        p.stop_loss == D("4010.0") and p.unrealized_pnl is not None and p.unrealized_pnl.amount == D("-12.34")
    )
    info = await a.get_account_info()
    assert info.equity.amount == D("9987.66") and info.margin_used.amount == D("200.00")
    assert info.free_margin.amount == D("9787.66")
    await a.disconnect()


async def test_reconnects_after_drop(fake: FakeCTrader) -> None:
    a = make_adapter(fake)
    await a.connect()
    await fake.drop_all()
    for _ in range(100):
        await asyncio.sleep(0.02)
        if not a.connected:
            break
    assert not a.connected
    await asyncio.wait_for(a.connected_event.wait(), timeout=5)
    assert a.connected and fake.received.count("ProtoOASubscribeSpotsReq") == 2
    await a.disconnect()


async def test_trading_is_disabled_and_live_refused(fake: FakeCTrader) -> None:
    a = make_adapter(fake)
    await a.connect()
    with pytest.raises(TradingDisabledError):
        await a.place_order(OrderRequest(symbol="XAUUSD", side=Side.BUY, volume=D("0.01"), stop_loss=D(1)))
    with pytest.raises(TradingDisabledError):
        await a.close_all_positions()
    await a.disconnect()
    with pytest.raises(ValueError):
        CTraderAdapter(CTraderCredentials("a", "b", "c", "d", 1, environment="live"))


def test_credentials_repr_hides_secrets() -> None:
    c = CTraderCredentials("id", "SECRET", "ACCESS", "REFRESH", 1)
    assert "SECRET" not in repr(c) and "ACCESS" not in repr(c)
