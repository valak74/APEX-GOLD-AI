from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from apex.broker.base import BrokerNotConnectedError, InvalidModificationError, PositionNotFoundError
from apex.broker.factory import PAPER_XAUUSD_SPEC, LiveTradingUnavailableError, build_broker
from apex.broker.sim import SimBroker
from apex.core.config import Settings
from apex.domain.enums import CloseReason, OrderStatus, OrderType, Side, TradingMode
from apex.domain.models import OrderRequest, Quote

T0 = datetime(2026, 11, 27, 14, 0, tzinfo=UTC)
SPEC = PAPER_XAUUSD_SPEC  # 100 oz, 3.5 USD/lot/side, 5 % margin


def q(bid: str, ask: str, t: int = 0, sym: str = "XAUUSD") -> Quote:
    return Quote(symbol=sym, bid=D(bid), ask=D(ask), ts=T0 + timedelta(seconds=t))


async def make(ccy: str = "USD", balance: str = "10000", slippage: str = "0") -> SimBroker:
    b = SimBroker(
        mode=TradingMode.SIMULATION,
        account_id="t",
        currency=ccy,
        initial_balance=D(balance),
        leverage=20,
        specs=[SPEC],
        slippage=D(slippage),
    )
    await b.connect()
    await b.on_quote(q("1.0500", "1.0502", sym="EURUSD"))
    await b.on_quote(q("4000.00", "4000.20"))
    return b


def buy(vol: str = "0.10", sl: str = "3990", tp: str | None = "4020", cid: str | None = None) -> OrderRequest:
    kw = {"client_order_id": cid} if cid else {}
    return OrderRequest(
        symbol="XAUUSD",
        side=Side.BUY,
        volume=D(vol),
        stop_loss=D(sl),
        take_profit=D(tp) if tp else None,
        **kw,
    )


async def test_market_buy_fills_at_ask_and_charges_commission() -> None:
    b = await make()
    r = await b.place_order(buy())
    assert r.status is OrderStatus.FILLED and r.fill_price == D("4000.20")
    info = await b.get_account_info()
    assert info.balance.amount == D(10000) - D("0.35")
    # unrealized at bid 4000.00: -0.20 * 0.1 * 100 = -2
    assert info.equity.amount == D(10000) - D("0.35") - D(2)
    assert info.margin_used.amount == D("0.10") * 100 * D("4000.20") * D("0.05")


async def test_slippage_is_adverse() -> None:
    b = await make(slippage="0.05")
    r = await b.place_order(buy())
    assert r.fill_price == D("4000.25")


async def test_take_profit_hit() -> None:
    b = await make()
    await b.place_order(buy())
    closed = await b.on_quote(q("4021.00", "4021.20", t=5))
    assert len(closed) == 1 and closed[0].reason is CloseReason.TAKE_PROFIT
    assert closed[0].close_price == D(4020)
    # (4020-4000.20)*10 = 198 gross, 0.70 commission
    assert closed[0].net_pnl.amount == D("198") - D("0.70")
    assert (await b.get_account_info()).balance.amount == D(10000) + D("198") - D("0.70")


async def test_stop_loss_gap_fills_worse_than_stop() -> None:
    b = await make()
    await b.place_order(buy())
    closed = await b.on_quote(q("3985.00", "3985.30", t=5))  # gapped through 3990
    assert closed[0].reason is CloseReason.STOP_LOSS and closed[0].close_price == D("3985.00")


async def test_sell_side_sl_tp() -> None:
    b = await make()
    r = await b.place_order(
        OrderRequest(
            symbol="XAUUSD", side=Side.SELL, volume=D("0.10"), stop_loss=D("4010"), take_profit=D("3990")
        )
    )
    assert r.fill_price == D("4000.00")
    closed = await b.on_quote(q("4009.90", "4010.10", t=1))  # ask crosses SL
    assert closed[0].reason is CloseReason.STOP_LOSS and closed[0].close_price == D("4010.10")


async def test_eur_account_pnl_converted() -> None:
    b = await make(ccy="EUR")
    await b.place_order(buy())
    closed = await b.on_quote(q("4021.00", "4021.20", t=5))
    assert closed[0].gross_pnl.ccy == "EUR"
    assert closed[0].gross_pnl.amount == pytest.approx(D(198) / D("1.0501"))


@pytest.mark.parametrize(
    ("req", "reason"),
    [
        (buy(sl="4005"), "invalid_stop_loss"),  # SL above buy price
        (buy(tp="3995"), "invalid_take_profit"),  # TP below buy price
        (buy(vol="0.015"), "invalid_volume"),  # not a step multiple
        (buy(vol="0.001"), "invalid_volume"),  # below min
        (buy(vol="51"), "invalid_volume"),  # above max
        (buy(vol="10"), "insufficient_margin"),  # 10 lots ≈ 200k margin
        (OrderRequest(symbol="XAGUSD", side=Side.BUY, volume=D("0.1"), stop_loss=D(1)), "unknown_symbol"),
        (
            OrderRequest(
                symbol="XAUUSD",
                side=Side.BUY,
                volume=D("0.1"),
                stop_loss=D(1),
                order_type=OrderType.LIMIT,
                price=D(3990),
            ),
            "order_type_not_supported",
        ),
    ],
)
async def test_rejections(req: OrderRequest, reason: str) -> None:
    b = await make()
    r = await b.place_order(req)
    assert r.status is OrderStatus.REJECTED and r.reject_reason == reason
    assert await b.get_positions() == []


async def test_stale_quote_rejected() -> None:
    b = await make()
    await b.on_quote(q("1.0500", "1.0502", t=60, sym="EURUSD"))  # clock advances, gold quote now 60s old
    r = await b.place_order(buy())
    assert r.reject_reason == "stale_market_data"


async def test_duplicate_client_order_id_is_idempotent() -> None:
    b = await make()
    r1 = await b.place_order(buy(cid="dup-order-1"))
    r2 = await b.place_order(buy(cid="dup-order-1"))
    assert r1 == r2 and len(await b.get_positions()) == 1


async def test_modify_and_partial_close() -> None:
    b = await make()
    r = await b.place_order(buy(vol="0.10"))
    assert r.position_id
    p = await b.modify_order(r.position_id, stop_loss=D("3999"))
    assert p.stop_loss == D("3999")
    with pytest.raises(InvalidModificationError):
        await b.modify_order(r.position_id, stop_loss=D("4001"))  # above current bid
    t = await b.close_position(r.position_id, volume=D("0.04"))
    assert t.volume == D("0.04")
    assert (await b.get_positions())[0].volume == D("0.06")
    with pytest.raises(InvalidModificationError):
        await b.close_position(r.position_id, volume=D("0.07"))


async def test_close_all_and_unknown_position() -> None:
    b = await make()
    await b.place_order(buy())
    await b.place_order(buy())
    assert len(await b.close_all_positions(CloseReason.KILL_SWITCH)) == 2
    assert await b.get_positions() == []
    from uuid import uuid4

    with pytest.raises(PositionNotFoundError):
        await b.close_position(uuid4())


async def test_stop_out_closes_positions() -> None:
    b = await make(balance="1000")
    # 0.04 lot margin ≈ 800; without SL protection far away
    r = await b.place_order(buy(vol="0.04", sl="3000", tp=None))
    assert r.status is OrderStatus.FILLED
    closed = await b.on_quote(q("3950.00", "3950.20", t=1))  # loss ≈ 201 -> equity ≈ 799, level ≈ 100%
    assert closed == []
    closed = await b.on_quote(q("3800.00", "3800.20", t=2))  # loss ≈ 801 -> level < 50%
    assert closed and closed[0].reason is CloseReason.STOP_OUT


async def test_requires_connection() -> None:
    b = SimBroker(
        mode=TradingMode.PAPER,
        account_id="t",
        currency="USD",
        initial_balance=D(1),
        leverage=20,
        specs=[SPEC],
    )
    with pytest.raises(BrokerNotConnectedError):
        await b.get_account_info()


def test_sim_broker_refuses_live() -> None:
    with pytest.raises(ValueError):
        SimBroker(
            mode=TradingMode.LIVE,
            account_id="x",
            currency="USD",
            initial_balance=D(1),
            leverage=1,
            specs=[SPEC],
        )


def test_factory_refuses_live_and_defaults_to_paper() -> None:
    assert Settings().trading_mode is TradingMode.PAPER
    assert build_broker(Settings()).mode is TradingMode.PAPER
    with pytest.raises(LiveTradingUnavailableError):
        build_broker(Settings(trading_mode=TradingMode.LIVE))
