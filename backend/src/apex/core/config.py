"""Environment-based configuration.

All settings come from environment variables prefixed ``APEX_`` (or a local ``.env`` in development).
Secrets are typed as ``SecretStr`` so they never appear in reprs or logs.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from apex.domain.enums import TradingMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APEX_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    # Default is PAPER. LIVE is refused in this build (no live adapter yet) — see apex.broker.factory.
    trading_mode: TradingMode = TradingMode.PAPER

    database_url: str = "postgresql+asyncpg://apex:apex@localhost:5432/apex"
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: SecretStr = Field(default=SecretStr(""))
    jwt_ttl_minutes: int = 15
    cookie_secure: bool = True
    cors_origins: list[str] = ["http://localhost:3000"]
    login_rate_limit_per_minute: int = 10

    # Envelope encryption master key (base64 32 bytes) for broker credentials.
    master_key: SecretStr = Field(default=SecretStr(""))

    # Paper account defaults
    paper_initial_balance: str = "10000"
    paper_account_currency: str = "EUR"
    paper_leverage: int = 20

    # Market data: "none" (no feed) or "ctrader" (live quotes from a linked cTrader DEMO account)
    market_data_source: Literal["none", "ctrader"] = "none"
    ctrader_broker_account_id: int | None = None
    ctrader_max_gold_leverage: int = 20  # conservative margin assumption (ESMA retail cap for gold)
    ctrader_host: str | None = None  # default: official demo host
    ctrader_port: int = 5035
    ctrader_use_tls: bool = True  # may only be disabled in the test environment

    # Opportunity scanner and optional LLM explanations (off by default: each explanation is a paid API call)
    scanner_enabled: bool = True
    risk_guard_enabled: bool = True
    scanner_interval_s: int = 30
    llm_enabled: bool = False
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high"] = "low"

    # Fundamental data (FRED needs a free key; CFTC COT needs none). Synced in the background, never in tests.
    fred_api_key: SecretStr | None = None
    macro_sync_enabled: bool = True
    macro_sync_interval_s: int = 6 * 3600
    macro_history_start: date = date(2018, 1, 1)

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("ctrader_broker_account_id", "ctrader_host", mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        return None if v == "" else v  # docker compose passes unset variables as ""

    @model_validator(mode="after")
    def _require_secrets_outside_dev(self) -> Settings:
        if not self.ctrader_use_tls and self.environment != "test":
            raise ValueError("APEX_CTRADER_USE_TLS=false is only allowed in the test environment")
        if self.environment == "prod":
            if len(self.jwt_secret.get_secret_value()) < 32:
                raise ValueError("APEX_JWT_SECRET must be set (>= 32 chars) in prod")
            if not self.master_key.get_secret_value():
                raise ValueError("APEX_MASTER_KEY must be set in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
