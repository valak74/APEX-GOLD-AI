"""Broker abstraction. Every broker (paper, simulation, cTrader, IG, ...) implements ``BrokerAdapter``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from apex.core.money import Money
from apex.domain.enums import CloseReason, TradingMode
from apex.domain.models import (
    AccountInfo,
    ClosedTrade,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
    SymbolSpec,
)


class BrokerError(Exception):
    """Base class for broker failures."""


class BrokerNotConnectedError(BrokerError):
    pass


class UnknownSymbolError(BrokerError):
    pass


class PositionNotFoundError(BrokerError):
    pass


class InvalidModificationError(BrokerError):
    pass


class Capabilities(BaseModel):
    """What the adapter can do. The engine adapts or refuses based on this, never assumes."""

    name: str
    mode: TradingMode
    push_quotes: bool
    sltp_on_entry: bool
    modify_sltp: bool
    partial_close: bool
    pending_orders: bool
    market_depth: bool
    real_volume: bool  # True only for exchange data; spot gold CFDs => False


class BrokerAdapter(ABC):
    capabilities: Capabilities

    @property
    def mode(self) -> TradingMode:
        return self.capabilities.mode

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    async def get_account_info(self) -> AccountInfo: ...

    async def get_balance(self) -> Money:
        return (await self.get_account_info()).balance

    async def get_equity(self) -> Money:
        return (await self.get_account_info()).equity

    async def get_free_margin(self) -> Money:
        return (await self.get_account_info()).free_margin

    @abstractmethod
    async def get_symbol_spec(self, symbol: str) -> SymbolSpec: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_market_data(self, symbol: str) -> Quote: ...

    @abstractmethod
    def stream_quotes(self, symbols: list[str]) -> AsyncIterator[Quote]: ...

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult: ...

    @abstractmethod
    async def modify_order(
        self, position_id: UUID, stop_loss: Decimal | None = None, take_profit: Decimal | None = None
    ) -> Position: ...

    @abstractmethod
    async def close_position(
        self, position_id: UUID, volume: Decimal | None = None, reason: CloseReason = CloseReason.MANUAL
    ) -> ClosedTrade: ...

    @abstractmethod
    async def close_all_positions(self, reason: CloseReason = CloseReason.MANUAL) -> list[ClosedTrade]: ...

    @abstractmethod
    async def get_order_status(self, client_order_id: str) -> OrderResult | None: ...
