"""Creates the broker adapter for the configured trading mode, enforcing mode separation."""

from __future__ import annotations

from decimal import Decimal

from apex.broker.base import BrokerAdapter
from apex.broker.sim import SimBroker
from apex.core.config import Settings
from apex.domain.enums import TradingMode
from apex.domain.models import SymbolSpec


class LiveTradingUnavailableError(RuntimeError):
    pass


# PAPER-ONLY placeholder specification. Values are typical for a 100 oz XAUUSD CFD but are NOT read
# from a broker. The live/paper cTrader adapter (next phase) replaces these with the broker's actual
# symbol specification at connect time.
PAPER_XAUUSD_SPEC = SymbolSpec(
    symbol="XAUUSD",
    base_ccy="XAU",
    quote_ccy="USD",
    contract_size=Decimal(100),
    digits=2,
    tick_size=Decimal("0.01"),
    volume_min=Decimal("0.01"),
    volume_step=Decimal("0.01"),
    volume_max=Decimal(50),
    stops_level=Decimal(0),
    commission_per_lot_per_side=Decimal("3.5"),
    commission_ccy="USD",
    margin_rate=Decimal("0.05"),  # ESMA retail gold: 20:1
)


def build_broker(settings: Settings) -> BrokerAdapter:
    mode = settings.trading_mode
    if mode is TradingMode.LIVE:
        # Live trading requires: a live broker adapter, explicit human activation, risk engine checks and a
        # completed paper period (docs/06-roadmap.md phase 10). None exists in this build: refuse.
        raise LiveTradingUnavailableError("LIVE mode is not available in this build; use PAPER")
    return SimBroker(
        mode=mode,
        account_id=f"{mode.value}-1",
        currency=settings.paper_account_currency,
        initial_balance=Decimal(settings.paper_initial_balance),
        leverage=settings.paper_leverage,
        specs=[PAPER_XAUUSD_SPEC],
    )
