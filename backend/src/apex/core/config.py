"""Environment-based configuration.

All settings come from environment variables prefixed ``APEX_`` (or a local ``.env`` in development).
Secrets are typed as ``SecretStr`` so they never appear in reprs or logs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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

    log_level: str = "INFO"
    log_json: bool = True

    @model_validator(mode="after")
    def _require_secrets_outside_dev(self) -> Settings:
        if self.environment == "prod":
            if len(self.jwt_secret.get_secret_value()) < 32:
                raise ValueError("APEX_JWT_SECRET must be set (>= 32 chars) in prod")
            if not self.master_key.get_secret_value():
                raise ValueError("APEX_MASTER_KEY must be set in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
