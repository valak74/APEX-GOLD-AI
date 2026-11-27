"""Core domain models shared by brokers, risk and API layers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apex.core.money import Money
from apex.domain.enums import CloseReason, OrderStatus, OrderType, PositionStatus, Side, TradingMode


def utcnow() -> datetime:
    return datetime.now(UTC)


class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    bid: Decimal
    ask: Decimal
    ts: datetime  # event time, timezone-aware UTC

    @field_validator("ts")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("quote timestamp must be timezone-aware")
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _crossed(self) -> Quote:
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError("prices must be positive")
        if self.bid > self.ask:
            raise ValueError(f"crossed quote bid={self.bid} > ask={self.ask}")
        return self

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


class SymbolSpec(BaseModel):
    """Broker-specific instrument specification. Always read from the broker, never assumed."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    base_ccy: str  # "XAU"
    quote_ccy: str  # "USD"
    contract_size: Decimal  # units of base per 1.00 lot, e.g. 100 oz
    digits: int
    tick_size: Decimal
    volume_min: Decimal
    volume_step: Decimal
    volume_max: Decimal
    stops_level: Decimal = Decimal(0)  # min distance of SL/TP from market, in price units
    commission_per_lot_per_side: Decimal = Decimal(0)  # in commission_ccy
    commission_ccy: str = "USD"
    margin_rate: Decimal | None = None  # e.g. 0.05 for 20:1; None => use account leverage

    @model_validator(mode="after")
    def _sane(self) -> SymbolSpec:
        if self.contract_size <= 0 or self.tick_size <= 0:
            raise ValueError("contract_size and tick_size must be > 0")
        if not (0 < self.volume_min <= self.volume_max) or self.volume_step <= 0:
            raise ValueError("invalid volume limits")
        if (self.volume_min / self.volume_step) % 1 != 0:
            raise ValueError("volume_min must be a multiple of volume_step")
        return self


class AccountInfo(BaseModel):
    mode: TradingMode
    account_id: str
    currency: str
    balance: Money
    equity: Money
    margin_used: Money
    free_margin: Money
    leverage: int
    margin_level_pct: Decimal | None  # None when no margin used


class OrderRequest(BaseModel):
    client_order_id: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=64)
    symbol: str
    side: Side
    volume: Decimal = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    price: Decimal | None = None  # for limit/stop
    stop_loss: Decimal  # mandatory — the platform never sends an order without SL
    take_profit: Decimal | None = None
    comment: str = ""

    @model_validator(mode="after")
    def _levels(self) -> OrderRequest:
        if self.stop_loss <= 0:
            raise ValueError("stop_loss must be positive")
        if self.order_type is not OrderType.MARKET and self.price is None:
            raise ValueError("price required for non-market orders")
        return self


class OrderResult(BaseModel):
    order_id: UUID
    client_order_id: str
    status: OrderStatus
    symbol: str
    side: Side
    requested_volume: Decimal
    filled_volume: Decimal = Decimal(0)
    fill_price: Decimal | None = None
    position_id: UUID | None = None
    reject_reason: str | None = None
    commission: Money | None = None
    ts: datetime = Field(default_factory=utcnow)


class Position(BaseModel):
    position_id: UUID
    symbol: str
    side: Side
    volume: Decimal
    open_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    opened_at: datetime
    status: PositionStatus = PositionStatus.OPEN
    unrealized_pnl: Money | None = None


class ClosedTrade(BaseModel):
    position_id: UUID
    symbol: str
    side: Side
    volume: Decimal
    open_price: Decimal
    close_price: Decimal
    opened_at: datetime
    closed_at: datetime
    reason: CloseReason
    gross_pnl: Money  # account currency
    commission: Money  # account currency, both sides
    net_pnl: Money
