"""Explicit currency conversion from live quotes (ADR 0006).

Rules:
* direct pair ``FROMTO`` or inverse ``TOFROM`` must be quoted; otherwise ``FxRateUnavailableError``.
* a quote older than ``max_age`` raises ``StaleFxRateError`` — callers must fail closed.
* ``conservative="high"`` returns the larger possible converted amount (used for losses/costs),
  ``conservative="low"`` the smaller (used for gains/available capital), ``"mid"`` the mid price.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from apex.core.money import Money
from apex.domain.models import Quote

Side = Literal["high", "low", "mid"]


class FxRateUnavailableError(LookupError):
    pass


class StaleFxRateError(RuntimeError):
    pass


class FxConverter:
    def __init__(self, max_age: timedelta = timedelta(seconds=60)) -> None:
        self._quotes: dict[str, Quote] = {}
        self.max_age = max_age

    def update(self, quote: Quote) -> None:
        self._quotes[quote.symbol.upper()] = quote

    def _fresh(self, q: Quote, now: datetime) -> Quote:
        if now - q.ts > self.max_age:
            raise StaleFxRateError(f"{q.symbol} quote is {now - q.ts} old (max {self.max_age})")
        return q

    def rate(self, from_ccy: str, to_ccy: str, now: datetime, conservative: Side = "high") -> Decimal:
        """Units of ``to_ccy`` per 1 unit of ``from_ccy``."""
        f, t = from_ccy.upper(), to_ccy.upper()
        if f == t:
            return Decimal(1)
        direct = self._quotes.get(f + t)
        if direct is not None:
            q = self._fresh(direct, now)
            return {"high": q.ask, "low": q.bid, "mid": q.mid}[conservative]
        inverse = self._quotes.get(t + f)
        if inverse is not None:
            q = self._fresh(inverse, now)
            # 1/bid > 1/ask — dividing by the bid yields the higher converted amount
            return {"high": 1 / q.bid, "low": 1 / q.ask, "mid": 1 / q.mid}[conservative]
        raise FxRateUnavailableError(f"no quote for {f}{t} or {t}{f}")

    def convert(self, money: Money, to_ccy: str, now: datetime, conservative: Side = "high") -> Money:
        return Money(
            amount=money.amount * self.rate(money.ccy, to_ccy, now, conservative), ccy=to_ccy.upper()
        )
