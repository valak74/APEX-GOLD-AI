"""Deterministic position sizing for CFD-style instruments such as XAU/USD.

Formula (docs/01-architecture.md §5):

    price_risk       = |entry - stop|                          (quote ccy per unit of base)
    loss_per_lot_qc  = (price_risk + slippage_buffer) * contract_size
    loss_per_lot_ac  = convert(loss_per_lot_qc, quote->account, conservative high)
                       + round-trip commission per lot (converted, conservative high)
    risk_budget      = min(equity, balance) * risk_pct
    lots             = floor_to_step(risk_budget / loss_per_lot_ac)

Every rejection is explicit; nothing is silently adjusted upward. All math is Decimal.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from apex.core.fx import FxConverter, FxRateUnavailableError, StaleFxRateError
from apex.core.money import Money
from apex.domain.enums import Side
from apex.domain.models import SymbolSpec

# Hard ceiling, independent of user configuration. Not modifiable at runtime.
ABSOLUTE_MAX_RISK_PCT = Decimal("0.02")


class SizingRejection(StrEnum):
    INVALID_RISK_PCT = "invalid_risk_pct"
    INVALID_STOP_SIDE = "invalid_stop_side"
    STOP_TOO_CLOSE = "stop_too_close"
    FX_UNAVAILABLE = "fx_unavailable"
    FX_STALE = "fx_stale"
    NON_POSITIVE_EQUITY = "non_positive_equity"
    BELOW_MIN_VOLUME = "below_min_volume"
    INSUFFICIENT_MARGIN = "insufficient_margin"


class SizingInput(BaseModel):
    side: Side
    entry: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    risk_pct: Decimal  # fraction, e.g. 0.0025 for 0.25 %
    equity: Money
    balance: Money
    free_margin: Money
    leverage: int = Field(gt=0)
    slippage_buffer: Decimal = Decimal(0)  # extra adverse price units assumed at stop
    max_volume: Decimal | None = None  # optional platform cap (lots), below broker max
    min_free_margin_ratio: Decimal = Decimal("0.5")  # free margin after trade must stay >= ratio * equity


class SizingResult(BaseModel):
    approved: bool
    rejections: list[SizingRejection] = []
    warnings: list[str] = []
    volume: Decimal = Decimal(0)
    raw_volume: Decimal = Decimal(0)
    price_risk: Decimal = Decimal(0)
    loss_per_lot: Money | None = None
    risk_budget: Money | None = None
    risk_amount: Money | None = None  # actual risk at the rounded volume, incl. costs
    risk_pct_actual: Decimal | None = None
    margin_required: Money | None = None


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if value <= 0:
        return Decimal(0)
    steps = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return (steps * step).normalize() if steps else Decimal(0)


def _reject(*codes: SizingRejection, **kw: object) -> SizingResult:
    return SizingResult(approved=False, rejections=list(codes), **kw)


def compute_position_size(inp: SizingInput, spec: SymbolSpec, fx: FxConverter, now: datetime) -> SizingResult:
    acc = inp.equity.ccy
    if inp.balance.ccy != acc or inp.free_margin.ccy != acc:
        raise ValueError("equity, balance and free_margin must share the account currency")

    if not (Decimal(0) < inp.risk_pct <= ABSOLUTE_MAX_RISK_PCT):
        return _reject(SizingRejection.INVALID_RISK_PCT)

    # Stop must be on the losing side of entry.
    if (inp.side is Side.BUY and inp.stop_loss >= inp.entry) or (
        inp.side is Side.SELL and inp.stop_loss <= inp.entry
    ):
        return _reject(SizingRejection.INVALID_STOP_SIDE)

    price_risk = abs(inp.entry - inp.stop_loss)
    if price_risk < max(spec.stops_level, spec.tick_size):
        return _reject(SizingRejection.STOP_TOO_CLOSE, price_risk=price_risk)

    capital = min(inp.equity, inp.balance)
    if capital.amount <= 0:
        return _reject(SizingRejection.NON_POSITIVE_EQUITY, price_risk=price_risk)

    try:
        loss_qc = Money(amount=(price_risk + inp.slippage_buffer) * spec.contract_size, ccy=spec.quote_ccy)
        loss_ac = fx.convert(loss_qc, acc, now, conservative="high")
        commission_rt = Money(amount=spec.commission_per_lot_per_side * 2, ccy=spec.commission_ccy)
        loss_per_lot = loss_ac + fx.convert(commission_rt, acc, now, conservative="high")
        notional_per_lot = fx.convert(
            Money(amount=inp.entry * spec.contract_size, ccy=spec.quote_ccy), acc, now, conservative="high"
        )
    except StaleFxRateError:
        return _reject(SizingRejection.FX_STALE, price_risk=price_risk)
    except FxRateUnavailableError:
        return _reject(SizingRejection.FX_UNAVAILABLE, price_risk=price_risk)

    risk_budget = capital * inp.risk_pct
    raw = risk_budget.amount / loss_per_lot.amount
    volume = floor_to_step(raw, spec.volume_step)
    warnings: list[str] = []

    cap = spec.volume_max if inp.max_volume is None else min(spec.volume_max, inp.max_volume)
    if volume > cap:
        warnings.append(f"volume reduced from {volume} to cap {cap}")
        volume = floor_to_step(cap, spec.volume_step)

    base = {
        "raw_volume": raw,
        "price_risk": price_risk,
        "loss_per_lot": loss_per_lot,
        "risk_budget": risk_budget,
        "warnings": warnings,
    }
    if volume < spec.volume_min:
        return _reject(SizingRejection.BELOW_MIN_VOLUME, **base)

    margin_rate = spec.margin_rate if spec.margin_rate is not None else Decimal(1) / Decimal(inp.leverage)
    margin_required = notional_per_lot * volume * margin_rate
    if inp.free_margin.amount - margin_required.amount < inp.min_free_margin_ratio * inp.equity.amount:
        return _reject(
            SizingRejection.INSUFFICIENT_MARGIN, volume=volume, margin_required=margin_required, **base
        )

    risk_amount = loss_per_lot * volume
    return SizingResult(
        approved=True,
        volume=volume,
        risk_amount=risk_amount,
        risk_pct_actual=risk_amount.amount / capital.amount,
        margin_required=margin_required,
        **base,
    )
