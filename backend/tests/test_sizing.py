from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from apex.core.fx import FxConverter
from apex.core.money import Money
from apex.domain.enums import Side
from apex.domain.models import Quote, SymbolSpec
from apex.risk.sizing import (
    ABSOLUTE_MAX_RISK_PCT,
    SizingInput,
    SizingRejection,
    compute_position_size,
    floor_to_step,
)

NOW = datetime(2026, 11, 27, 14, 0, tzinfo=UTC)

GOLD_100 = SymbolSpec(
    symbol="XAUUSD",
    base_ccy="XAU",
    quote_ccy="USD",
    contract_size=D(100),
    digits=2,
    tick_size=D("0.01"),
    volume_min=D("0.01"),
    volume_step=D("0.01"),
    volume_max=D(50),
    commission_per_lot_per_side=D("3.0"),
    commission_ccy="USD",
)


def fx(eurusd: tuple[str, str] = ("1.0500", "1.0502"), age: timedelta = timedelta(0)) -> FxConverter:
    c = FxConverter()
    c.update(Quote(symbol="EURUSD", bid=D(eurusd[0]), ask=D(eurusd[1]), ts=NOW - age))
    c.update(Quote(symbol="USDCHF", bid=D("0.8800"), ask=D("0.8802"), ts=NOW - age))
    c.update(Quote(symbol="USDJPY", bid=D("150.00"), ask=D("150.02"), ts=NOW - age))
    return c


def inp(ccy: str = "USD", equity: str = "10000", **kw: object) -> SizingInput:
    base = dict(
        side=Side.BUY,
        entry=D("4000.00"),
        stop_loss=D("3995.00"),
        risk_pct=D("0.0025"),
        equity=Money.of(equity, ccy),
        balance=Money.of(equity, ccy),
        free_margin=Money.of(equity, ccy),
        leverage=20,
    )
    base.update(kw)
    return SizingInput(**base)  # type: ignore[arg-type]


def test_usd_account_hand_computed() -> None:
    # risk budget 25 USD; loss/lot = 5*100 + 6 commission = 506 -> 0.0494 -> 0.04 lots
    r = compute_position_size(inp(), GOLD_100, fx(), NOW)
    assert r.approved
    assert r.loss_per_lot == Money.of("506", "USD")
    assert r.volume == D("0.04")
    assert r.risk_amount == Money.of("20.24", "USD")
    assert r.risk_amount <= Money.of("25", "USD")
    # margin = 0.04 * 100 * 4000 / 20 = 800 USD
    assert r.margin_required == Money.of("800", "USD")


def test_eur_account_uses_conservative_rate() -> None:
    # 10k EUR, 0.25% -> 25 EUR. USD->EUR via 1/bid(EURUSD)=1/1.05
    r = compute_position_size(inp(ccy="EUR"), GOLD_100, fx(), NOW)
    assert r.approved
    assert r.loss_per_lot is not None
    assert r.loss_per_lot.ccy == "EUR"
    assert r.loss_per_lot.amount == pytest.approx(D(506) / D("1.0500"))
    assert r.volume == D("0.05")  # 25 / 481.9 = 0.0518 -> 0.05
    assert r.risk_amount is not None and r.risk_amount.amount <= D(25)


@pytest.mark.parametrize("ccy", ["USD", "EUR", "CHF", "JPY"])
def test_never_exceeds_budget_any_currency(ccy: str) -> None:
    equity = "1500000" if ccy == "JPY" else "10000"
    r = compute_position_size(inp(ccy=ccy, equity=equity), GOLD_100, fx(), NOW)
    assert r.approved, r.rejections
    assert r.risk_amount is not None and r.risk_budget is not None
    assert r.risk_amount <= r.risk_budget
    assert r.risk_amount.ccy == ccy


def test_one_oz_contract() -> None:
    spec = GOLD_100.model_copy(
        update={
            "contract_size": D(1),
            "volume_min": D(1),
            "volume_step": D(1),
            "volume_max": D(10000),
            "commission_per_lot_per_side": D(0),
        }
    )
    r = compute_position_size(inp(), spec, fx(), NOW)
    assert r.volume == D(5)  # 25 / 5 = 5 oz


def test_sell_stop_must_be_above() -> None:
    r = compute_position_size(inp(side=Side.SELL), GOLD_100, fx(), NOW)
    assert r.rejections == [SizingRejection.INVALID_STOP_SIDE]


def test_buy_stop_must_be_below() -> None:
    r = compute_position_size(inp(stop_loss=D("4001")), GOLD_100, fx(), NOW)
    assert r.rejections == [SizingRejection.INVALID_STOP_SIDE]


def test_stop_too_close_vs_stops_level() -> None:
    spec = GOLD_100.model_copy(update={"stops_level": D("0.50")})
    r = compute_position_size(inp(stop_loss=D("3999.80")), spec, fx(), NOW)
    assert r.rejections == [SizingRejection.STOP_TOO_CLOSE]


def test_small_account_below_min_volume() -> None:
    # 200 EUR at 0.25% = 0.50 EUR risk; min lot 0.01 with $5 stop loses ~4.8 EUR -> reject
    r = compute_position_size(inp(ccy="EUR", equity="200"), GOLD_100, fx(), NOW)
    assert not r.approved
    assert r.rejections == [SizingRejection.BELOW_MIN_VOLUME]


@pytest.mark.parametrize("pct", ["0", "-0.01", str(ABSOLUTE_MAX_RISK_PCT + D("0.0001"))])
def test_risk_pct_bounds(pct: str) -> None:
    r = compute_position_size(inp(risk_pct=D(pct)), GOLD_100, fx(), NOW)
    assert r.rejections == [SizingRejection.INVALID_RISK_PCT]


def test_stale_fx_rejects() -> None:
    r = compute_position_size(inp(ccy="EUR"), GOLD_100, fx(age=timedelta(minutes=5)), NOW)
    assert r.rejections == [SizingRejection.FX_STALE]


def test_missing_fx_rejects() -> None:
    r = compute_position_size(inp(ccy="GBP"), GOLD_100, fx(), NOW)
    assert r.rejections == [SizingRejection.FX_UNAVAILABLE]


def test_insufficient_margin() -> None:
    r = compute_position_size(inp(free_margin=Money.of("100", "USD")), GOLD_100, fx(), NOW)
    assert r.rejections == [SizingRejection.INSUFFICIENT_MARGIN]


def test_uses_lower_of_equity_and_balance() -> None:
    r = compute_position_size(inp(balance=Money.of("5000", "USD")), GOLD_100, fx(), NOW)
    assert r.risk_budget == Money.of("12.5", "USD")


def test_negative_equity_rejected() -> None:
    r = compute_position_size(inp(equity="-1", balance=Money.of("100", "USD")), GOLD_100, fx(), NOW)
    assert r.rejections == [SizingRejection.NON_POSITIVE_EQUITY]


def test_platform_cap_reduces_volume() -> None:
    r = compute_position_size(inp(equity="10000000", max_volume=D("1")), GOLD_100, fx(), NOW)
    # 10M * 0.25% = 25k budget would give ~49 lots; capped at 1 — but margin check still applies
    assert r.approved and r.volume == D(1) and r.warnings


def test_currency_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        compute_position_size(inp(balance=Money.of("1", "EUR")), GOLD_100, fx(), NOW)


def test_floor_to_step() -> None:
    assert floor_to_step(D("0.0499"), D("0.01")) == D("0.04")
    assert floor_to_step(D("0.01"), D("0.01")) == D("0.01")
    assert floor_to_step(D("-1"), D("0.01")) == D(0)


@settings(max_examples=400, deadline=None)
@given(
    equity=st.decimals(min_value=D(100), max_value=D(5_000_000), places=2),
    stop_dist=st.decimals(min_value=D("0.05"), max_value=D(200), places=2),
    risk_bp=st.integers(min_value=1, max_value=200),
    side=st.sampled_from([Side.BUY, Side.SELL]),
    ccy=st.sampled_from(["USD", "EUR", "CHF"]),
    slip=st.decimals(min_value=D(0), max_value=D(2), places=2),
)
def test_property_risk_never_exceeds_budget(
    equity: D, stop_dist: D, risk_bp: int, side: Side, ccy: str, slip: D
) -> None:
    entry = D("4000.00")
    stop = entry - stop_dist if side is Side.BUY else entry + stop_dist
    i = inp(
        ccy=ccy,
        equity=str(equity),
        side=side,
        stop_loss=stop,
        risk_pct=D(risk_bp) / D(10000),
        slippage_buffer=slip,
    )
    r = compute_position_size(i, GOLD_100, fx(), NOW)
    if r.approved:
        assert r.risk_amount is not None and r.risk_budget is not None
        assert r.risk_amount <= r.risk_budget
        assert r.volume >= GOLD_100.volume_min
        assert (r.volume / GOLD_100.volume_step) % 1 == 0
        assert r.volume <= GOLD_100.volume_max
    else:
        assert r.rejections
        assert r.risk_amount is None
