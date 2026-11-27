"""Pure conversions from cTrader Open API messages to APEX domain models (no I/O, fully unit-tested).

Unit conventions (cTrader Open API documentation):
* spot / trendbar prices: integer, 1/100000 of a unit           -> ``price_from_int``
* volumes (min/step/max, lotSize, position volume): "cents" of units, 100 = 1 unit of base asset
* money (balance, usedMargin, PnL): integer scaled by 10^moneyDigits (default 2)
* preciseTradingCommissionRate / preciseMinCommission: 10^8 for amount types, 10^5 for percentage

Anything we cannot map with certainty raises ``UnsupportedSpecError`` — the platform must not trade on a
guessed specification.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from apex.broker.base import BrokerError
from apex.broker.ctrader import proto
from apex.domain.enums import Side
from apex.domain.models import SymbolSpec


class UnsupportedSpecError(BrokerError):
    pass


def price_from_int(v: int) -> Decimal:
    return Decimal(v) / proto.PRICE_SCALE


def money_from_int(v: int, money_digits: int | None) -> Decimal:
    return Decimal(v).scaleb(-(2 if money_digits is None else money_digits))


def ts_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def side_from_proto(v: int) -> Side:
    return Side.BUY if v == proto.enum_value("ProtoOATradeSide", "BUY") else Side.SELL


def canonical_name(light: Any, assets: dict[int, str]) -> str | None:
    """``XAU``+``USD`` -> ``XAUUSD`` regardless of broker suffixes such as ``XAUUSD.r``."""
    base, quote = assets.get(int(light.baseAssetId)), assets.get(int(light.quoteAssetId))
    return f"{base}{quote}" if base and quote else None


def find_symbol(lights: list[Any], assets: dict[int, str], base: str, quote: str) -> Any:
    """Enabled symbol with the given base/quote assets; prefer the exact canonical name."""
    matches = [
        s
        for s in lights
        if canonical_name(s, assets) == base + quote and (not s.HasField("enabled") or s.enabled)
    ]
    if not matches:
        raise UnsupportedSpecError(f"no tradable {base}/{quote} symbol on this account")
    exact = [s for s in matches if s.symbolName == base + quote]
    return (exact or sorted(matches, key=lambda s: len(s.symbolName)))[0]


def _commission(sym: Any, quote_ccy: str) -> dict[str, Any]:
    ctype = (
        proto.enum_name("ProtoOACommissionType", int(sym.commissionType))
        if sym.HasField("commissionType")
        else None
    )
    if ctype is None:
        return {"commission_type": "per_lot", "commission_rate": Decimal(0), "commission_ccy": quote_ccy}
    if not sym.HasField("preciseTradingCommissionRate"):
        raise UnsupportedSpecError("symbol has a commission type but no preciseTradingCommissionRate")
    raw = Decimal(sym.preciseTradingCommissionRate)
    if ctype == "USD_PER_MILLION_USD":
        out = {
            "commission_type": "per_million_notional",
            "commission_rate": raw / proto.PRECISE_RATE_SCALE,
            "commission_ccy": "USD",
        }
    elif ctype == "USD_PER_LOT":
        out = {
            "commission_type": "per_lot",
            "commission_rate": raw / proto.PRECISE_RATE_SCALE,
            "commission_ccy": "USD",
        }
    elif ctype == "QUOTE_CCY_PER_LOT":
        out = {
            "commission_type": "per_lot",
            "commission_rate": raw / proto.PRECISE_RATE_SCALE,
            "commission_ccy": quote_ccy,
        }
    elif ctype == "PERCENTAGE_OF_VALUE":
        out = {
            "commission_type": "pct_notional",
            "commission_rate": raw / proto.PRECISE_PCT_SCALE,
            "commission_ccy": quote_ccy,
        }
    else:
        raise UnsupportedSpecError(f"unknown commission type {ctype}")
    if out["commission_type"] != "per_lot" and out["commission_ccy"] != quote_ccy:
        raise UnsupportedSpecError(f"{ctype} on a non-{out['commission_ccy']}-quoted symbol is not supported")

    if sym.HasField("preciseMinCommission") and sym.preciseMinCommission > 0:
        mtype = (
            proto.enum_name("ProtoOAMinCommissionType", int(sym.minCommissionType))
            if sym.HasField("minCommissionType")
            else "CURRENCY"
        )
        min_ccy = quote_ccy if mtype == "QUOTE_CURRENCY" else (sym.minCommissionAsset or "USD")
        if min_ccy != out["commission_ccy"]:
            raise UnsupportedSpecError(
                f"min commission in {min_ccy} vs commission in {out['commission_ccy']}"
            )
        out["min_commission"] = Decimal(sym.preciseMinCommission) / proto.PRECISE_RATE_SCALE
    return out


def symbol_spec_from_proto(
    sym: Any, light: Any, assets: dict[int, str], max_leverage: int | None = None
) -> SymbolSpec:
    """Build a ``SymbolSpec`` from ``ProtoOASymbol`` + ``ProtoOALightSymbol``."""
    base, quote = assets.get(int(light.baseAssetId)), assets.get(int(light.quoteAssetId))
    if not base or not quote:
        raise UnsupportedSpecError("unknown base/quote asset")
    for f in ("lotSize", "minVolume", "stepVolume", "maxVolume"):
        if not sym.HasField(f) or getattr(sym, f) <= 0:
            raise UnsupportedSpecError(f"symbol {light.symbolName} missing {f}")
    trading_mode = (
        proto.enum_name("ProtoOATradingMode", int(sym.tradingMode))
        if sym.HasField("tradingMode")
        else "ENABLED"
    )

    lot = Decimal(sym.lotSize)  # in volume cents
    digits = int(sym.digits)
    tick = Decimal(1).scaleb(-digits)
    stops = Decimal(0)
    if sym.HasField("slDistance") and sym.slDistance > 0:
        dist_type = (
            proto.enum_name("ProtoOASymbolDistanceType", int(sym.distanceSetIn))
            if sym.HasField("distanceSetIn")
            else "SYMBOL_DISTANCE_IN_POINTS"
        )
        if dist_type != "SYMBOL_DISTANCE_IN_POINTS":
            raise UnsupportedSpecError(f"{dist_type} for stop distance is not supported yet")
        stops = Decimal(sym.slDistance) * tick

    spec = SymbolSpec(
        symbol=f"{base}{quote}",
        base_ccy=base,
        quote_ccy=quote,
        contract_size=lot / proto.VOLUME_SCALE,
        digits=digits,
        tick_size=tick,
        volume_min=Decimal(sym.minVolume) / lot,
        volume_step=Decimal(sym.stepVolume) / lot,
        volume_max=Decimal(sym.maxVolume) / lot,
        stops_level=stops,
        # Margin: symbol-specific leverage tiers are not in this message; callers pass a conservative cap.
        margin_rate=(Decimal(1) / Decimal(max_leverage)) if max_leverage else None,
        **_commission(sym, quote),
    )
    if trading_mode != "ENABLED":
        raise UnsupportedSpecError(f"{spec.symbol} trading mode is {trading_mode}")
    return spec
