from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    SIMULATION = "simulation"
    PAPER = "paper"
    LIVE = "live"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class CloseReason(StrEnum):
    MANUAL = "manual"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    STOP_OUT = "stop_out"
    KILL_SWITCH = "kill_switch"
    TIME_EXIT = "time_exit"
    WEEKLY_CLOSE = "weekly_close"


class Role(StrEnum):
    VIEWER = "viewer"
    RESEARCHER = "researcher"
    TRADER = "trader"
    ADMIN = "admin"

    @property
    def level(self) -> int:
        return {"viewer": 0, "researcher": 1, "trader": 2, "admin": 3}[self.value]
