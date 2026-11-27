"""Global risk configuration. Versioned and immutable once stored (docs/05 §5).

Only authenticated admins can create a new version; no decision/AI module has a write path.
Defaults are the conservative profile agreed for the first paper period.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apex.risk.sizing import ABSOLUTE_MAX_RISK_PCT


class RiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_per_trade_pct: Decimal = Field(default=Decimal("0.0025"), gt=0, le=ABSOLUTE_MAX_RISK_PCT)
    max_daily_loss_pct: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.10"))
    max_weekly_loss_pct: Decimal = Field(default=Decimal("0.025"), gt=0, le=Decimal("0.20"))
    max_total_drawdown_pct: Decimal = Field(default=Decimal("0.08"), gt=0, le=Decimal("0.50"))
    max_open_positions: int = Field(default=1, ge=1, le=10)
    max_consecutive_losses: int = Field(default=4, ge=1, le=20)
    max_trades_per_day: int = Field(default=6, ge=1, le=100)
    max_spread: Decimal = Field(default=Decimal("0.60"), gt=0)  # price units (USD/oz)
    max_slippage: Decimal = Field(default=Decimal("0.30"), gt=0)
    min_free_margin_ratio: Decimal = Field(default=Decimal("0.5"), ge=0, le=1)
    max_volume_lots: Decimal = Field(default=Decimal("1.0"), gt=0)
    news_lockout_minutes_before: int = Field(default=15, ge=0, le=240)
    news_lockout_minutes_after: int = Field(default=15, ge=0, le=240)
    # Trade-quality floors (checked after costs)
    min_risk_reward: Decimal = Field(default=Decimal("1.5"), ge=Decimal("1.0"), le=Decimal("10"))
    min_stop_atr: Decimal = Field(default=Decimal("0.3"), gt=0)  # tighter stops are noise-stopped
    max_stop_atr: Decimal = Field(default=Decimal("6"), gt=0)
    max_quote_age_s: int = Field(default=5, ge=1, le=60)
    # No new entries this many minutes before the Friday close / daily rollover
    weekly_close_buffer_minutes: int = Field(default=60, ge=0, le=600)
    rollover_buffer_minutes: int = Field(default=15, ge=0, le=120)
    # When drawdown exceeds this share of the max drawdown, risk per trade is halved
    drawdown_reduce_at: Decimal = Field(default=Decimal("0.5"), gt=0, le=1)

    @model_validator(mode="after")
    def _ordering(self) -> RiskConfig:
        if not (
            self.risk_per_trade_pct
            <= self.max_daily_loss_pct
            <= self.max_weekly_loss_pct
            <= self.max_total_drawdown_pct
        ):
            raise ValueError("limits must satisfy per-trade <= daily <= weekly <= total drawdown")
        if self.min_stop_atr >= self.max_stop_atr:
            raise ValueError("min_stop_atr must be below max_stop_atr")
        return self

    def config_hash(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
