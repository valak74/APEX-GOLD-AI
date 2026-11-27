"""Currency-safe money type (ADR 0006).

Amounts are ``Decimal`` and always carry an ISO currency code. Arithmetic between different
currencies raises ``CurrencyMismatchError`` — conversion must be explicit via ``FxConverter``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class CurrencyMismatchError(ValueError):
    pass


def to_decimal(value: Any) -> Decimal:
    """Convert to Decimal without float artefacts (floats go through ``str``)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


class Money(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: Decimal
    ccy: str

    @field_validator("ccy")
    @classmethod
    def _ccy(cls, v: str) -> str:
        v = v.upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError(f"invalid currency code: {v!r}")
        return v

    @classmethod
    def of(cls, amount: Any, ccy: str) -> Money:
        return cls(amount=to_decimal(amount), ccy=ccy)

    def _check(self, other: Money) -> None:
        if self.ccy != other.ccy:
            raise CurrencyMismatchError(f"{self.ccy} vs {other.ccy}")

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount + other.amount, ccy=self.ccy)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount - other.amount, ccy=self.ccy)

    def __mul__(self, factor: Decimal | int) -> Money:
        return Money(amount=self.amount * Decimal(factor), ccy=self.ccy)

    def __neg__(self) -> Money:
        return Money(amount=-self.amount, ccy=self.ccy)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    def rounded(self, places: int = 2) -> Money:
        q = Decimal(1).scaleb(-places)
        return Money(amount=self.amount.quantize(q, rounding=ROUND_HALF_EVEN), ccy=self.ccy)

    def __str__(self) -> str:
        return f"{self.amount:.2f} {self.ccy}"
