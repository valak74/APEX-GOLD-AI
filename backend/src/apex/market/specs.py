"""Persist the broker's contract specification so offline tools (CLI, research) use real costs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.broker.factory import PAPER_XAUUSD_SPEC
from apex.db.models import SymbolSpecRow
from apex.domain.models import SymbolSpec, utcnow


async def save_spec(maker: async_sessionmaker[AsyncSession], spec: SymbolSpec, source: str) -> None:
    payload: dict[str, Any] = spec.model_dump(mode="json")
    stmt = insert(SymbolSpecRow).values(
        symbol=spec.symbol, source=source, payload=payload, updated_at=utcnow()
    )
    async with maker() as s:
        await s.execute(
            stmt.on_conflict_do_update(
                index_elements=["symbol"],
                set_={
                    "source": stmt.excluded.source,
                    "payload": stmt.excluded.payload,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )
        await s.commit()


async def load_spec(
    maker: async_sessionmaker[AsyncSession], symbol: str = "XAUUSD"
) -> tuple[SymbolSpec, str]:
    """Last spec received from the broker, else the documented placeholder (flagged as such)."""
    async with maker() as s:
        row = await s.get(SymbolSpecRow, symbol)
    if row is None:
        return PAPER_XAUUSD_SPEC, "placeholder"
    return SymbolSpec.model_validate(row.payload), row.source
