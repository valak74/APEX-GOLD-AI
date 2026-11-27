from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apex.broker.base import BrokerAdapter
from apex.core.config import Settings
from apex.domain.models import utcnow
from apex.market.feed import MarketFeed


@dataclass
class TradingState:
    """Process-wide trading switch. Starts disabled; kill switch requires an admin to re-arm."""

    trading_enabled: bool = False
    kill_switch_engaged: bool = False
    kill_switch_reason: str | None = None
    kill_switch_at: datetime | None = None
    kill_switch_by: str | None = None

    def engage(self, actor: str, reason: str) -> None:
        self.trading_enabled = False
        self.kill_switch_engaged = True
        self.kill_switch_reason = reason
        self.kill_switch_at = utcnow()
        self.kill_switch_by = actor

    def rearm(self) -> None:
        self.kill_switch_engaged = False
        self.kill_switch_reason = None
        # re-arming does NOT enable trading; that is a separate explicit action
        self.trading_enabled = False


class RateLimiter:
    """In-process sliding-window limiter (single API process). Replace with Redis when scaled out."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    broker: BrokerAdapter
    login_limiter: RateLimiter
    trading: TradingState = field(default_factory=TradingState)
    market: MarketFeed = field(default_factory=MarketFeed)
    scanner: Any = None  # OpportunityScanner (set at startup)
    risk: Any = None  # RiskService (set at startup)
    macro: Any = None  # MacroService (set at startup)
