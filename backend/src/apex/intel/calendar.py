"""Economic calendar: storage, import and trading lockouts around high-impact events.

Data sources (docs/03-data-sources.md §3): official agency schedules or a paid calendar API.
Neither is wired in
automatically yet — official sites need per-agency parsers and paid APIs need a key. Until then events are
imported from a JSON file / the admin API in the documented format below. The lockout logic does not depend on
the source.

Import format (list of objects)::

    {"event_code": "US_NFP", "title": "Non-Farm Payrolls", "country": "US", "currency": "USD",
     "importance": "high", "scheduled_at": "2026-12-04T13:30:00Z", "forecast": "150K", "previous": "143K"}

Lockout rule: an event with ``importance`` in ``LOCKOUT_IMPORTANCE`` and ``currency`` in ``GOLD_SENSITIVE``
blocks new entries from ``before`` minutes ahead until ``after`` minutes after its scheduled time (values come
from the active risk configuration). Gold is priced in USD, so USD events are always relevant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from apex.db.models import EconomicEvent

GOLD_SENSITIVE = {"USD", "XAU", "CNY"}
LOCKOUT_IMPORTANCE = {"high"}


class EventIn(BaseModel):
    event_code: str = Field(pattern=r"^[A-Z0-9_]{2,80}$")
    title: str = Field(min_length=2, max_length=200)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    importance: Literal["low", "medium", "high"]
    scheduled_at: datetime
    previous: str | None = Field(default=None, max_length=40)
    forecast: str | None = Field(default=None, max_length=40)
    actual: str | None = Field(default=None, max_length=40)

    @field_validator("scheduled_at")
    @classmethod
    def _tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("scheduled_at must include a timezone (e.g. Z)")
        return v


async def import_events(session: AsyncSession, events: list[EventIn], source: str) -> int:
    if not events:
        return 0
    rows = [{**e.model_dump(), "source": source} for e in events]
    stmt = insert(EconomicEvent)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["source", "event_code", "scheduled_at"],
            set_={
                k: stmt.excluded[k]
                for k in ("title", "importance", "previous", "forecast", "actual", "currency")
            },
        ),
        rows,
    )
    await session.commit()
    return len(rows)


async def upcoming(
    session: AsyncSession, now: datetime, days: int = 7, back_hours: int = 6
) -> list[EconomicEvent]:
    q = (
        select(EconomicEvent)
        .where(
            EconomicEvent.scheduled_at >= now - timedelta(hours=back_hours),
            EconomicEvent.scheduled_at <= now + timedelta(days=days),
        )
        .order_by(EconomicEvent.scheduled_at)
    )
    return list((await session.execute(q)).scalars())


@dataclass
class Lockout:
    locked: bool
    reason: str | None
    event_title: str | None
    event_at: datetime | None
    until: datetime | None
    next_event_title: str | None
    next_event_at: datetime | None


def evaluate_lockout(events: list[EconomicEvent], now: datetime, before_min: int, after_min: int) -> Lockout:
    relevant = sorted(
        (e for e in events if e.importance in LOCKOUT_IMPORTANCE and e.currency in GOLD_SENSITIVE),
        key=lambda e: e.scheduled_at,
    )
    before, after = timedelta(minutes=before_min), timedelta(minutes=after_min)
    for e in relevant:
        if e.scheduled_at - before <= now <= e.scheduled_at + after:
            phase = "avant" if now < e.scheduled_at else "après"
            return Lockout(
                True,
                f"annonce majeure ({phase}) : {e.title}",
                e.title,
                e.scheduled_at,
                e.scheduled_at + after,
                None,
                None,
            )
    nxt = next((e for e in relevant if e.scheduled_at - before > now), None)
    return Lockout(
        False, None, None, None, None, nxt.title if nxt else None, nxt.scheduled_at if nxt else None
    )
