from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apex.broker.factory import PAPER_XAUUSD_SPEC
from apex.core.fx import FxConverter
from apex.core.money import Money
from apex.domain.enums import Side, TradingMode
from apex.domain.models import AccountInfo, Position, Quote
from apex.risk.config import RiskConfig
from apex.risk.engine import Proposal, Snapshot, evaluate
from apex.risk.ledger import TradeRecord, compute_stats

NOW = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)  # Wednesday 10:00 New York: market open, far from rollover
CFG = RiskConfig()


def account(
    equity: str = "10000", balance: str = "10000", margin: str = "0", ccy: str = "USD"
) -> AccountInfo:
    e, b, m = D(equity), D(balance), D(margin)
    return AccountInfo(
        mode=TradingMode.PAPER,
        account_id="t",
        currency=ccy,
        balance=Money.of(b, ccy),
        equity=Money.of(e, ccy),
        margin_used=Money.of(m, ccy),
        free_margin=Money.of(e - m, ccy),
        leverage=20,
        margin_level_pct=None,
    )


def fx() -> FxConverter:
    f = FxConverter()
    f.update(Quote(symbol="EURUSD", bid=D("1.05"), ask=D("1.0502"), ts=NOW))
    return f


def snap(**over: Any) -> Snapshot:
    base: dict[str, Any] = dict(
        now=NOW,
        mode=TradingMode.PAPER,
        trading_enabled=True,
        kill_switch=False,
        account=account(),
        positions=[],
        quote=Quote(symbol="XAUUSD", bid=D("4000.00"), ask=D("4000.20"), ts=NOW - timedelta(seconds=1)),
        spec=PAPER_XAUUSD_SPEC,
        fx=fx(),
        ledger=compute_stats([], D(10000), NOW),
        data_quality_ok=True,
        data_quality_issues=[],
        news_lock_reason=None,
        atr=D("4.0"),
    )
    base.update(over)
    return Snapshot(**base)


def prop(
    side: Side = Side.BUY, entry: str = "4000.20", stop: str = "3995.20", tp: str = "4010.20"
) -> Proposal:
    return Proposal("opp-1", "XAUUSD", side, D(entry), D(stop), D(tp))


def failed(d: Any) -> set[str]:
    return set(d.rejections)


# --------------------------------------------------------------------------- happy path
def test_all_clear_is_approved_with_sized_order() -> None:
    d = evaluate(prop(), snap(), CFG)
    assert d.approved, d.rejections
    assert d.order is not None and d.order.stop_loss == D("3995.20") and d.order.take_profit == D("4010.20")
    assert d.order.client_order_id.startswith("apx-") and d.order.volume >= PAPER_XAUUSD_SPEC.volume_min
    assert d.sizing is not None and d.sizing.risk_amount is not None
    assert d.sizing.risk_amount.amount <= D(25)  # 0.25 % of 10 000
    assert d.net_risk_reward is not None and d.net_risk_reward >= CFG.min_risk_reward
    assert d.config_hash == CFG.config_hash() and all(c.passed for c in d.checks)


def test_deterministic_and_idempotent_order_id() -> None:
    a, b = evaluate(prop(), snap(), CFG), evaluate(prop(), snap(), CFG)
    assert a.order is not None and b.order is not None
    assert a.order.client_order_id == b.order.client_order_id and a.checks == b.checks


def test_sell_side_approved() -> None:
    d = evaluate(prop(Side.SELL, "4000.00", "4005.00", "3990.00"), snap(), CFG)
    assert d.approved, d.rejections


# --------------------------------------------------------------------------- one failure at a time
@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"kill_switch": True}, "kill_switch"),
        ({"trading_enabled": False}, "trading_enabled"),
        ({"mode": TradingMode.LIVE}, "mode"),
        ({"already_submitted": True}, "duplicate"),
        ({"data_quality_ok": False, "data_quality_issues": ["stale"]}, "data_quality"),
        ({"quote": None}, "quote_fresh"),
        (
            {"quote": Quote(symbol="XAUUSD", bid=D(4000), ask=D("4000.2"), ts=NOW - timedelta(seconds=30))},
            "quote_fresh",
        ),
        ({"quote": Quote(symbol="XAUUSD", bid=D(4000), ask=D("4001.5"), ts=NOW)}, "spread"),
        ({"news_lock_reason": "annonce majeure : CPI"}, "news"),
        ({"now": datetime(2026, 11, 28, 12, tzinfo=UTC)}, "market_open"),  # Saturday
        ({"atr": None}, "stop_distance"),
        ({"atr": D("50")}, "stop_distance"),  # 5 $ stop = 0.1 ATR: noise
        ({"atr": D("0.5")}, "stop_distance"),  # 5 $ stop = 10 ATR: too wide
    ],
)
def test_each_condition_blocks(over: dict[str, Any], code: str) -> None:
    s = snap(**over)
    if "now" in over:
        s = replace(
            s,
            quote=s.quote.model_copy(update={"ts": over["now"]}),
            ledger=compute_stats([], D(10000), over["now"]),
        )  # type: ignore[union-attr]
    d = evaluate(prop(), s, CFG)
    assert not d.approved and code in failed(d) and d.order is None


def test_rollover_and_friday_close_buffers() -> None:
    before_roll = datetime(2026, 11, 25, 21, 50, tzinfo=UTC)  # Wed 16:50 NY, 10 min before rollover
    s = snap(
        now=before_roll,
        quote=Quote(symbol="XAUUSD", bid=D(4000), ask=D("4000.2"), ts=before_roll),
        ledger=compute_stats([], D(10000), before_roll),
    )
    assert "rollover" in failed(evaluate(prop(), s, CFG))
    fri = datetime(2026, 11, 27, 21, 20, tzinfo=UTC)  # Fri 16:20 NY, 40 min before the weekly close
    s = snap(
        now=fri,
        quote=Quote(symbol="XAUUSD", bid=D(4000), ask=D("4000.2"), ts=fri),
        ledger=compute_stats([], D(10000), fri),
    )
    assert "weekly_close" in failed(evaluate(prop(), s, CFG))


def test_bad_geometry_and_low_reward() -> None:
    assert "stop_side" in failed(evaluate(prop(stop="4001"), snap(), CFG))
    assert "target_side" in failed(evaluate(prop(tp="3999"), snap(), CFG))
    d = evaluate(prop(tp="4005.40"), snap(), CFG)  # 1.04 R gross, below 1.5 after costs
    assert "risk_reward" in failed(d) and d.net_risk_reward is not None and d.net_risk_reward < D("1.5")


def test_costs_reduce_risk_reward() -> None:
    # 1.5 R gross is not enough once spread and commission are counted
    d = evaluate(prop(tp="4007.70"), snap(), CFG)
    assert d.net_risk_reward is not None and d.net_risk_reward < D("1.5") and "risk_reward" in failed(d)


def test_open_positions_and_no_hedging() -> None:
    pos = Position(
        position_id=uuid4(),
        symbol="XAUUSD",
        side=Side.SELL,
        volume=D("0.01"),
        open_price=D(4001),
        stop_loss=D(4010),
        take_profit=None,
        opened_at=NOW - timedelta(minutes=5),
    )
    d = evaluate(prop(), snap(positions=[pos]), CFG)
    assert {"open_positions", "no_hedge"} <= failed(d)


def test_daily_loss_limit_including_open_loss() -> None:
    trades = [TradeRecord(NOW - timedelta(hours=1), D("-60"))]
    led = compute_stats(trades, D(10000), NOW)
    # realised -60 and an open loss of -45: 105 > 1 % of 10 000
    d = evaluate(prop(), snap(ledger=led, account=account(equity="9895", balance="9940")), CFG)
    assert "daily_loss" in failed(d)


def test_remaining_daily_budget_caps_risk() -> None:
    led = compute_stats([TradeRecord(NOW - timedelta(hours=1), D("-90"))], D(10000), NOW)
    d = evaluate(prop(), snap(ledger=led, account=account(equity="9910", balance="9910")), CFG)
    # 10 left before the 100 daily cap: risk is capped (refused if even the min lot risks more)
    assert any("budget" in r for r in d.reductions)
    if d.approved:
        assert (
            d.sizing is not None and d.sizing.risk_amount is not None and d.sizing.risk_amount.amount <= D(10)
        )
    else:
        assert "sizing" in failed(d)


def test_weekly_loss_and_drawdown_is_critical() -> None:
    mon = datetime(2026, 11, 23, 15, tzinfo=UTC)
    trades = [TradeRecord(mon, D("-300"))]
    led = compute_stats(trades, D(10000), NOW)
    assert "weekly_loss" in failed(evaluate(prop(), snap(ledger=led, account=account("9700", "9700")), CFG))
    big = compute_stats(
        [TradeRecord(NOW - timedelta(days=10), D(2000)), TradeRecord(mon, D(-1000))], D(10000), NOW
    )
    d = evaluate(
        prop(), snap(ledger=big, account=account("11000", "11000")), CFG
    )  # peak 12 000 -> 8.3 % drawdown
    assert "drawdown" in failed(d) and d.critical


def test_losing_streak_and_trades_per_day() -> None:
    streak = [TradeRecord(NOW - timedelta(minutes=10 * i), D(-1)) for i in range(4)]
    assert "losing_streak" in failed(evaluate(prop(), snap(ledger=compute_stats(streak, D(10000), NOW)), CFG))
    many = [TradeRecord(NOW - timedelta(minutes=i + 1), D(1) if i % 2 else D(-1)) for i in range(6)]
    assert "trades_per_day" in failed(evaluate(prop(), snap(ledger=compute_stats(many, D(10000), NOW)), CFG))


def test_regime_and_drawdown_only_reduce_risk() -> None:
    d = evaluate(prop(), snap(regime_multiplier=D("0.5")), CFG)
    assert d.approved and d.risk_pct_effective == CFG.risk_per_trade_pct / 2
    up = evaluate(prop(), snap(regime_multiplier=D("3")), CFG)  # a buggy/hostile multiplier cannot raise risk
    assert up.risk_pct_effective == CFG.risk_per_trade_pct
    led = compute_stats(
        [TradeRecord(NOW - timedelta(days=3), D(-450))], D(10000), NOW
    )  # 4.5 % dd ≥ half of 8 %
    dd = evaluate(prop(), snap(ledger=led, account=account("9550", "9550")), CFG)
    assert any("drawdown" in r for r in dd.reductions)


def test_insufficient_margin() -> None:
    d = evaluate(prop(), snap(account=account(equity="10000", margin="9990")), CFG)
    assert "sizing" in failed(d)


def test_internal_error_fails_closed() -> None:
    d = evaluate(prop(), snap(spec=None), CFG)  # type: ignore[arg-type]
    assert not d.approved and d.critical and d.rejections == ["internal_error"]


def test_config_and_snapshot_not_mutated() -> None:
    s = snap()
    before = (CFG.model_dump(), s.account.model_dump(), list(s.positions))
    evaluate(prop(), s, CFG)
    assert before == (CFG.model_dump(), s.account.model_dump(), list(s.positions))


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        RiskConfig(min_stop_atr=D(7), max_stop_atr=D(6))
    with pytest.raises(ValueError):
        RiskConfig(min_risk_reward=D("0.5"))


# --------------------------------------------------------------------------- properties
@settings(max_examples=300, deadline=None)
@given(
    equity=st.decimals(min_value=D(500), max_value=D(1_000_000), places=2),
    stop=st.decimals(min_value=D("0.5"), max_value=D(40), places=2),
    rr=st.decimals(min_value=D("0.5"), max_value=D(6), places=2),
    lost=st.decimals(min_value=D(0), max_value=D("0.02"), places=4),
    mult=st.decimals(min_value=D(0), max_value=D(2), places=2),
    side=st.sampled_from([Side.BUY, Side.SELL]),
)
def test_property_approved_orders_respect_every_budget(
    equity: D, stop: D, rr: D, lost: D, mult: D, side: Side
) -> None:
    entry = D("4000.20") if side is Side.BUY else D("4000.00")
    sl = entry - stop * side.sign
    tp = entry + stop * rr * side.sign
    loss = (equity * lost).quantize(D("0.01"))
    led = compute_stats([TradeRecord(NOW - timedelta(hours=1), -loss)] if loss else [], equity + loss, NOW)
    d = evaluate(
        Proposal("p", "XAUUSD", side, entry, sl, tp),
        snap(account=account(str(equity), str(equity)), ledger=led, regime_multiplier=mult),
        CFG,
    )
    assert d.risk_pct_effective <= CFG.risk_per_trade_pct
    if d.approved:
        assert d.order is not None and d.sizing is not None and d.sizing.risk_amount is not None
        assert d.order.stop_loss == sl  # never moved
        r = d.sizing.risk_amount.amount
        assert r <= CFG.risk_per_trade_pct * equity
        remaining = CFG.max_daily_loss_pct * (equity + loss) - loss
        assert r <= remaining + D("0.000001")
        assert d.net_risk_reward is not None and d.net_risk_reward >= CFG.min_risk_reward
    else:
        assert d.order is None and d.rejections
