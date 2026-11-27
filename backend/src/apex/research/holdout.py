"""The reserved final out-of-sample period.

Every look at data teaches something, and what is learnt leaks into the next decision. So the most recent
months are put aside ONCE (as soon as enough history exists) and never used by diagnostics or ordinary
backtests. They are consulted only for a final verdict, explicitly, and every consultation is recorded: the
more often it is looked at, the less it proves.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.db.models import BacktestRow, BarRow, HoldoutRow

HOLDOUT_DAYS = 182
MIN_SPAN_DAYS = 60  # below this there is nothing meaningful to reserve (development data)


def holdout_start_for(first: datetime, last: datetime) -> datetime | None:
    span = last - first
    if span < timedelta(days=MIN_SPAN_DAYS):
        return None
    start = max(first + span * 0.75, last - timedelta(days=HOLDOUT_DAYS))  # ≤ 25 % of a short history
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


async def get_holdout(maker: async_sessionmaker[AsyncSession]) -> HoldoutRow | None:
    async with maker() as s:
        return (await s.execute(select(HoldoutRow).order_by(HoldoutRow.id).limit(1))).scalar_one_or_none()


async def ensure_holdout(maker: async_sessionmaker[AsyncSession], actor: str) -> HoldoutRow | None:
    """Create the holdout the first time enough 5m history exists; afterwards always return the same row."""
    row = await get_holdout(maker)
    if row is not None:
        return row
    async with maker() as s:
        first, last = (
            await s.execute(
                select(func.min(BarRow.start), func.max(BarRow.start)).where(
                    BarRow.symbol == "XAUUSD", BarRow.timeframe == "5m"
                )
            )
        ).one()
        if first is None:
            return None
        start = holdout_start_for(first, last)
        if start is None:
            return None
        row = HoldoutRow(
            start=start,
            created_by=actor,
            note=f"réservé automatiquement : historique {first:%Y-%m-%d} → {last:%Y-%m-%d}",
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row


async def holdout_consultations(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(BacktestRow)
                    .where(BacktestRow.request["final_holdout"].as_boolean().is_(True))
                )
            ).scalar_one()
        )
