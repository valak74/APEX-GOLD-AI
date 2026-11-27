"""ORM models for Phase 2 tables (schema ``core``). Market/trading tables arrive in later phases."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from apex.domain.enums import Role, TradingMode


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="role", schema="core", values_callable=lambda e: [m.value for m in e])
    )
    mfa_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Append-only, hash-chained audit trail."""

    __tablename__ = "audit_log"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash_prev: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class BrokerAccount(Base):
    __tablename__ = "broker_accounts"
    __table_args__ = (UniqueConstraint("broker", "account_ref"), {"schema": "core"})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker: Mapped[str] = mapped_column(String(50))  # e.g. "icmarkets-ctrader"
    platform: Mapped[str] = mapped_column(String(30))  # e.g. "ctrader"
    account_ref: Mapped[str] = mapped_column(String(100))  # broker account number / ctid account id
    mode_allowed: Mapped[TradingMode] = mapped_column(
        Enum(TradingMode, name="trading_mode", schema="core", values_callable=lambda e: [m.value for m in e])
    )
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Credential(Base):
    """Envelope-encrypted secret. Never returned by any API endpoint."""

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("broker_account_id", "kind"), {"schema": "core"})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    broker_account_id: Mapped[int] = mapped_column(ForeignKey("core.broker_accounts.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(50))  # client_id, client_secret, access_token, refresh_token
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RiskConfigVersion(Base):
    __tablename__ = "risk_config"
    __table_args__ = (
        Index("uq_risk_config_single_active", "active", unique=True, postgresql_where=text("active")),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class Tick(Base):
    """Raw bid/ask quotes (TimescaleDB hypertable when the extension is available)."""

    __tablename__ = "ticks"
    __table_args__ = {"schema": "market"}

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bid: Mapped[Decimal] = mapped_column(Numeric(18, 6), primary_key=True)
    ask: Mapped[Decimal] = mapped_column(Numeric(18, 6), primary_key=True)
    recv_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BarRow(Base):
    """Completed OHLC (mid) bars. ``tick_count`` is the number of quote updates, not traded volume."""

    __tablename__ = "bars"
    __table_args__ = {"schema": "market"}

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(4), primary_key=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    high: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    low: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    spread_avg: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    spread_max: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    tick_count: Mapped[int] = mapped_column(Integer)


class EconomicEvent(Base):
    """Scheduled macro events. ``importance`` drives trading lockouts (see apex.intel.calendar)."""

    __tablename__ = "economic_events"
    __table_args__ = (UniqueConstraint("source", "event_code", "scheduled_at"), {"schema": "intel"})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    event_code: Mapped[str] = mapped_column(String(80))  # e.g. "US_NFP", "US_CPI", "FOMC_DECISION"
    title: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3))
    importance: Mapped[str] = mapped_column(String(10))  # low | medium | high
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    previous: Mapped[str | None] = mapped_column(String(40), nullable=True)
    forecast: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actual: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpportunityRow(Base):
    """Every opportunity the scanner detected, with its full explanation (audit trail, learning data)."""

    __tablename__ = "opportunities"
    __table_args__ = {"schema": "trading"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    setup: Mapped[str] = mapped_column(String(50))
    direction: Mapped[str] = mapped_column(String(8))
    timeframe: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(20), index=True)
    quality: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    narrative: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TradeRow(Base):
    """Closed trades (paper / simulation / live), the source of truth for daily/weekly loss and drawdown."""

    __tablename__ = "trades"
    __table_args__ = {"schema": "trading"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(12), index=True)
    account_id: Mapped[str] = mapped_column(String(64))
    position_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(4))
    volume: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    open_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str] = mapped_column(String(20))
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    commission: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    currency: Mapped[str] = mapped_column(String(3))
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RiskDecisionRow(Base):
    """Every risk-engine decision, approved or not, with all checks (audit trail)."""

    __tablename__ = "risk_decisions"
    __table_args__ = {"schema": "trading"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(64), index=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    approved: Mapped[bool] = mapped_column(Boolean)
    critical: Mapped[bool] = mapped_column(Boolean, default=False)
    rejections: Mapped[list[str]] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class BacktestRow(Base):
    """A backtest run: parameters, status and the full report (metrics, validation, trades)."""

    __tablename__ = "backtests"
    __table_args__ = {"schema": "research"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(320))
    kind: Mapped[str] = mapped_column(String(12), server_default="backtest", index=True)  # | diagnostic
    status: Mapped[str] = mapped_column(String(12))  # running | done | error
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HoldoutRow(Base):
    """The reserved final out-of-sample period. Set once, never moved: research stops at ``start``."""

    __tablename__ = "holdout"
    __table_args__ = {"schema": "research"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(320))
    note: Mapped[str] = mapped_column(Text)
