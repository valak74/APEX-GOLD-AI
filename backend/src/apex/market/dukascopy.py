"""Dukascopy historical tick importer (research data; free for personal use — check Dukascopy's terms).

File layout (public datafeed): ``{BASE}/{SYMBOL}/{YYYY}/{MM-1:02d}/{DD:02d}/{HH:02d}h_ticks.bi5``
— one hour of ticks, LZMA-compressed; each record is 20 bytes big-endian:
ms offset in the hour (uint32), ask (uint32),
bid (uint32), ask volume (float32), bid volume (float32). Prices are integers in points; for XAUUSD one
point = 1/1000 (``POINT_DIVISOR``). Months are 0-based in the URL. Empty files mean no ticks (weekend).

Safety: every decoded file is validated (prices within a plausible band, ask >= bid, monotonic times,
bounded spread). A file that fails validation is rejected whole and reported — never partially imported.
Volumes are ignored: they are Dukascopy liquidity figures, not market volume.
"""

from __future__ import annotations

import asyncio
import lzma
import struct
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.core.logging import get_logger
from apex.db.models import BarRow, Tick
from apex.domain.models import Quote
from apex.market.bars import Bar, BarBuilder

log = get_logger(__name__)

BASE_URL = "https://datafeed.dukascopy.com/datafeed"
POINT_DIVISOR = {"XAUUSD": 1000, "EURUSD": 100000, "GBPUSD": 100000, "USDCHF": 100000, "USDJPY": 1000}
PLAUSIBLE = {"XAUUSD": (Decimal(100), Decimal(100000)), "EURUSD": (Decimal("0.5"), Decimal(3))}
MAX_SPREAD_FRACTION = Decimal("0.01")  # a spread above 1 % of price means corrupted data
RECORD = struct.Struct(">IIIff")


class InvalidFileError(ValueError):
    pass


def hour_url(symbol: str, hour: datetime) -> str:
    h = hour.astimezone(UTC)
    return f"{BASE_URL}/{symbol}/{h.year}/{h.month - 1:02d}/{h.day:02d}/{h.hour:02d}h_ticks.bi5"


def decode_hour(symbol: str, hour: datetime, raw: bytes) -> list[Quote]:
    """Decode and validate one hourly .bi5 payload. Raises ``InvalidFileError`` on any inconsistency."""
    if not raw:
        return []
    try:
        data = lzma.decompress(raw)
    except lzma.LZMAError as e:
        raise InvalidFileError(f"not LZMA: {e}") from None
    if len(data) % RECORD.size:
        raise InvalidFileError(f"payload size {len(data)} is not a multiple of {RECORD.size}")
    div = Decimal(POINT_DIVISOR[symbol])
    lo, hi = PLAUSIBLE.get(symbol, (Decimal(0), Decimal(10**9)))
    out: list[Quote] = []
    last_ms = -1
    for ms, ask_i, bid_i, _av, _bv in RECORD.iter_unpack(data):
        if ms < last_ms or ms >= 3_600_000:
            raise InvalidFileError(f"time offset {ms} out of order or outside the hour")
        last_ms = ms
        bid, ask = Decimal(bid_i) / div, Decimal(ask_i) / div
        if not (lo <= bid <= hi and lo <= ask <= hi):
            raise InvalidFileError(
                f"price {bid}/{ask} outside plausible band {lo}-{hi} (wrong point divisor?)"
            )
        if ask < bid or (ask - bid) > bid * MAX_SPREAD_FRACTION:
            raise InvalidFileError(f"invalid spread bid={bid} ask={ask}")
        out.append(Quote(symbol=symbol, bid=bid, ask=ask, ts=hour + timedelta(milliseconds=ms)))
    return out


def hours(start: datetime, end: datetime) -> Iterable[datetime]:
    h = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    while h < end:
        yield h
        h += timedelta(hours=1)


@dataclass
class ImportReport:
    hours_total: int = 0
    hours_skipped_existing: int = 0
    hours_empty: int = 0
    hours_imported: int = 0
    ticks: int = 0
    rejected: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


async def _fetch(http: httpx.AsyncClient, url: str, attempts: int = 4) -> bytes | None:
    delay = 1.0
    for i in range(attempts):
        try:
            r = await http.get(url)
            if r.status_code == 404:
                return b""  # no data for this hour
            r.raise_for_status()
            return r.content
        except httpx.HTTPError:
            if i == attempts - 1:
                return None
            await asyncio.sleep(delay)
            delay *= 2
    return None


async def import_ticks(
    maker: async_sessionmaker[AsyncSession],
    symbol: str,
    start: datetime,
    end: datetime,
    http: httpx.AsyncClient | None = None,
    concurrency: int = 4,
    force: bool = False,
) -> ImportReport:
    rep = ImportReport()
    owns = http is None
    http = http or httpx.AsyncClient(timeout=30, headers={"User-Agent": "apex-gold-ai-research-importer"})
    sem = asyncio.Semaphore(concurrency)

    async def one(hour: datetime) -> None:
        async with sem:
            if not force:
                async with maker() as s:
                    exists = (
                        await s.execute(
                            select(Tick.ts)
                            .where(
                                Tick.source == "dukascopy",
                                Tick.symbol == symbol,
                                Tick.ts >= hour,
                                Tick.ts < hour + timedelta(hours=1),
                            )
                            .limit(1)
                        )
                    ).first()
                if exists:
                    rep.hours_skipped_existing += 1
                    return
            raw = await _fetch(http, hour_url(symbol, hour))
            if raw is None:
                rep.failed.append(hour.isoformat())
                return
            try:
                quotes = decode_hour(symbol, hour, raw)
            except InvalidFileError as e:
                rep.rejected.append(f"{hour.isoformat()}: {e}")
                log.warning("dukascopy_file_rejected", hour=hour.isoformat(), reason=str(e))
                return
            if not quotes:
                rep.hours_empty += 1
                return
            rows = [
                {
                    "source": "dukascopy",
                    "symbol": symbol,
                    "ts": q.ts,
                    "bid": q.bid,
                    "ask": q.ask,
                    "recv_ts": None,
                }
                for q in quotes
            ]
            async with maker() as s:
                for i in range(0, len(rows), 5000):
                    await s.execute(insert(Tick).on_conflict_do_nothing(), rows[i : i + 5000])
                await s.commit()
            rep.hours_imported += 1
            rep.ticks += len(rows)

    try:
        hs = list(hours(start, end))
        rep.hours_total = len(hs)
        await asyncio.gather(*(one(h) for h in hs))
    finally:
        if owns:
            await http.aclose()
    return rep


async def stream_ticks(
    maker: async_sessionmaker[AsyncSession], source: str, symbol: str, start: datetime, end: datetime
) -> AsyncIterator[Quote]:
    cursor = start
    while cursor < end:
        async with maker() as s:
            rows = (
                (
                    await s.execute(
                        select(Tick)
                        .where(
                            Tick.source == source,
                            Tick.symbol == symbol,
                            Tick.ts >= cursor,
                            Tick.ts < end,
                        )
                        .order_by(Tick.ts)
                        .limit(50_000)
                    )
                )
                .scalars()
                .all()
            )
        if not rows:
            return
        for r in rows:
            yield Quote(symbol=symbol, bid=r.bid, ask=r.ask, ts=r.ts)
        last = rows[-1].ts
        cursor = last + timedelta(microseconds=1) if len(rows) == 50_000 else end


async def rebuild_bars(
    maker: async_sessionmaker[AsyncSession],
    source: str,
    symbol: str,
    start: datetime,
    end: datetime,
    timeframes: tuple[str, ...] = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"),
) -> int:
    """Recompute bars from stored ticks (deterministic; the last, incomplete bar per timeframe is skipped)."""
    builder = BarBuilder(symbol, timeframes=timeframes)
    pending: list[Bar] = []
    written = 0

    async def flush() -> None:
        nonlocal written
        if not pending:
            return
        stmt = insert(BarRow)
        cols = ("open", "high", "low", "close", "spread_avg", "spread_max", "tick_count")
        async with maker() as s:
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["source", "symbol", "timeframe", "start"],
                    set_={c: stmt.excluded[c] for c in cols},
                ),
                [
                    {
                        "source": source,
                        "symbol": b.symbol,
                        "timeframe": b.timeframe,
                        "start": b.start,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "spread_avg": b.spread_avg,
                        "spread_max": b.spread_max,
                        "tick_count": b.tick_count,
                    }
                    for b in pending
                ],
            )
            await s.commit()
        written += len(pending)
        pending.clear()

    async for q in stream_ticks(maker, source, symbol, start, end):
        pending.extend(builder.update(q))
        if len(pending) >= 5000:
            await flush()
    await flush()
    return written
