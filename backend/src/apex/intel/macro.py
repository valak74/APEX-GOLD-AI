"""Fundamental context for gold, from free official sources, stored point-in-time.

Sources
-------
* **FRED** (Federal Reserve Bank of St. Louis, free API key): US real and nominal yields, inflation
  expectations, the broad dollar index, the effective fed funds rate — and the historical + scheduled dates of
  the major US releases (employment, CPI, PCE, GDP, FOMC…), which feed the trading lockouts live and in
  backtests.
* **CFTC** Commitments of Traders (public Socrata API, no key): managed-money positioning in COMEX gold.

Point-in-time rule (no look-ahead)
----------------------------------
Every observation carries ``available_at``: the earliest time it could have been known, chosen conservatively
from the publication schedule (``SERIES[...].lag``). Analyses, diagnostics and backtests only ever use values
with ``available_at`` ≤ the bar's time. A daily yield for day D is published the next business day; the broad
dollar index is published weekly (the following Monday); a COT report as of Tuesday is released on Friday —
we use the following Monday to absorb holiday delays.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.core.logging import get_logger
from apex.db.models import EconomicEvent, MacroObservation
from apex.intel.calendar import EventIn, import_events

log = get_logger(__name__)
NY = ZoneInfo("America/New_York")
FRED_URL = "https://api.stlouisfed.org/fred"
CFTC_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"  # disaggregated, futures only
CFTC_GOLD = "088691"  # COMEX gold


def _next_business_day(d: date) -> date:
    n = d + timedelta(days=1)
    while n.weekday() >= 5:
        n += timedelta(days=1)
    return n


def lag_next_business_day(d: date) -> datetime:
    """Daily H.15-type series: published the next business day in the afternoon (New York)."""
    return datetime.combine(_next_business_day(d), time(17, 0), NY).astimezone(UTC)


def lag_next_monday(d: date) -> datetime:
    """Weekly-published series (H.10 dollar index, COT): the Monday after the observation's week."""
    n = d + timedelta(days=(7 - d.weekday()) or 7)
    return datetime.combine(n, time(17, 0), NY).astimezone(UTC)


@dataclass(frozen=True)
class SeriesDef:
    key: str
    fred_id: str | None
    label: str
    unit: str
    lag: Callable[[date], datetime]
    source: str = "fred"


SERIES: dict[str, SeriesDef] = {
    "REAL_10Y": SeriesDef("REAL_10Y", "DFII10", "Taux réel US 10 ans", "%", lag_next_business_day),
    "NOMINAL_10Y": SeriesDef("NOMINAL_10Y", "DGS10", "Taux nominal US 10 ans", "%", lag_next_business_day),
    "NOMINAL_2Y": SeriesDef("NOMINAL_2Y", "DGS2", "Taux nominal US 2 ans", "%", lag_next_business_day),
    "BREAKEVEN_10Y": SeriesDef(
        "BREAKEVEN_10Y", "T10YIE", "Inflation anticipée 10 ans", "%", lag_next_business_day
    ),
    "DOLLAR_BROAD": SeriesDef("DOLLAR_BROAD", "DTWEXBGS", "Dollar (indice large)", "pts", lag_next_monday),
    "FED_FUNDS": SeriesDef("FED_FUNDS", "DFF", "Taux effectif des Fed funds", "%", lag_next_business_day),
    "COT_MM_NET": SeriesDef(
        "COT_MM_NET",
        None,
        "Spéculateurs (managed money) : position nette",
        "contrats",
        lag_next_monday,
        "cftc",
    ),
    "COT_OI": SeriesDef("COT_OI", None, "Intérêt ouvert COMEX or", "contrats", lag_next_monday, "cftc"),
}


@dataclass(frozen=True)
class ReleaseDef:
    fred_name: str  # FRED release name, resolved to an id at sync time
    code: str
    title: str
    hour: int
    minute: int
    importance: str


RELEASES: tuple[ReleaseDef, ...] = (
    ReleaseDef("Employment Situation", "US_NFP", "Rapport sur l'emploi (NFP)", 8, 30, "high"),
    ReleaseDef("Consumer Price Index", "US_CPI", "Inflation CPI", 8, 30, "high"),
    ReleaseDef("Personal Income and Outlays", "US_PCE", "Revenus et dépenses (PCE)", 8, 30, "high"),
    ReleaseDef("Gross Domestic Product", "US_GDP", "PIB", 8, 30, "high"),
    ReleaseDef("FOMC Press Release", "FOMC_DECISION", "Décision de la Fed (FOMC)", 14, 0, "high"),
    ReleaseDef("Producer Price Index", "US_PPI", "Prix à la production (PPI)", 8, 30, "medium"),
    ReleaseDef(
        "Advance Monthly Sales for Retail and Food Services", "US_RETAIL", "Ventes au détail", 8, 30, "medium"
    ),
    ReleaseDef(
        "Job Openings and Labor Turnover Survey", "US_JOLTS", "Offres d'emploi (JOLTS)", 10, 0, "medium"
    ),
)


# --------------------------------------------------------------------------- parsing (pure, tested)
def parse_fred_observations(payload: dict[str, Any], s: SeriesDef) -> list[dict[str, Any]]:
    out = []
    for o in payload.get("observations", []):
        v = str(o.get("value", "."))
        if v in (".", "", "NaN"):
            continue  # FRED marks missing values with "."
        d = date.fromisoformat(o["date"])
        out.append(
            {"series": s.key, "obs_date": d, "value": Decimal(v), "available_at": s.lag(d), "source": "fred"}
        )
    return out


def parse_cot(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        raw = str(r.get("report_date_as_yyyy_mm_dd", ""))[:10]
        try:
            d = date.fromisoformat(raw)
            long_, short = (
                int(float(r["m_money_positions_long_all"])),
                int(float(r["m_money_positions_short_all"])),
            )
            oi = int(float(r["open_interest_all"]))
        except (KeyError, ValueError):
            continue
        avail = SERIES["COT_MM_NET"].lag(d)
        out.append(
            {
                "series": "COT_MM_NET",
                "obs_date": d,
                "value": Decimal(long_ - short),
                "available_at": avail,
                "source": "cftc",
            }
        )
        out.append(
            {"series": "COT_OI", "obs_date": d, "value": Decimal(oi), "available_at": avail, "source": "cftc"}
        )
    return out


def release_events(dates: Iterable[str], r: ReleaseDef) -> list[EventIn]:
    out = []
    for ds in dates:
        d = date.fromisoformat(ds)
        when = datetime.combine(d, time(r.hour, r.minute), NY).astimezone(UTC)
        out.append(
            EventIn(
                event_code=r.code,
                title=r.title,
                country="US",
                currency="USD",
                importance=r.importance,
                scheduled_at=when,
            )
        )
    return out


# --------------------------------------------------------------------------- sync (network)
@dataclass
class SyncReport:
    observations: dict[str, int] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    finished_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observations": self.observations,
            "events": self.events,
            "errors": self.errors,
            "finished_at": self.finished_at,
        }


async def _upsert(maker: async_sessionmaker[AsyncSession], rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    async with maker() as s:
        for i in range(0, len(rows), 5000):
            stmt = insert(MacroObservation)
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["series", "obs_date"],
                    set_={
                        "value": stmt.excluded.value,
                        "available_at": stmt.excluded.available_at,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                ),
                rows[i : i + 5000],
            )
        await s.commit()
    return len(rows)


async def sync_macro(
    maker: async_sessionmaker[AsyncSession],
    fred_key: str | None,
    start: date,
    http: httpx.AsyncClient | None = None,
) -> SyncReport:
    """Fetch everything that is reachable; each source fails independently and is reported, never raised."""
    rep = SyncReport()
    own = http is None
    client = http or httpx.AsyncClient(timeout=30.0)
    try:
        if fred_key:
            for s in SERIES.values():
                if s.fred_id is None:
                    continue
                try:
                    r = await client.get(
                        f"{FRED_URL}/series/observations",
                        params={
                            "series_id": s.fred_id,
                            "api_key": fred_key,
                            "file_type": "json",
                            "observation_start": start.isoformat(),
                        },
                    )
                    r.raise_for_status()
                    rep.observations[s.key] = await _upsert(maker, parse_fred_observations(r.json(), s))
                except (httpx.HTTPError, ValueError, KeyError) as e:
                    rep.errors.append(f"FRED {s.fred_id} : {type(e).__name__}")
            await _sync_releases(maker, client, fred_key, start, rep)
        else:
            rep.errors.append("FRED : clé APEX_FRED_API_KEY absente (gratuite sur fred.stlouisfed.org)")
        try:
            r = await client.get(
                CFTC_URL,
                params={
                    "$where": f"cftc_contract_market_code='{CFTC_GOLD}' AND report_date_as_yyyy_mm_dd >= '{start.isoformat()}T00:00:00'",
                    "$order": "report_date_as_yyyy_mm_dd",
                    "$limit": "50000",
                },
            )
            r.raise_for_status()
            rows = parse_cot(r.json())
            rep.observations["COT"] = await _upsert(maker, rows)
        except (httpx.HTTPError, ValueError) as e:
            rep.errors.append(f"CFTC COT : {type(e).__name__}")
    finally:
        if own:
            await client.aclose()
    rep.finished_at = datetime.now(UTC)
    log.info("macro_sync", observations=rep.observations, events=rep.events, errors=len(rep.errors))
    return rep


async def _sync_releases(
    maker: async_sessionmaker[AsyncSession], client: httpx.AsyncClient, key: str, start: date, rep: SyncReport
) -> None:
    try:
        ids: dict[str, int] = {}
        offset = 0
        while True:
            r = await client.get(
                f"{FRED_URL}/releases",
                params={"api_key": key, "file_type": "json", "limit": 1000, "offset": offset},
            )
            r.raise_for_status()
            page = r.json().get("releases", [])
            ids.update({x["name"]: int(x["id"]) for x in page})
            if len(page) < 1000:
                break
            offset += 1000
    except (httpx.HTTPError, ValueError, KeyError) as e:
        rep.errors.append(f"FRED liste des publications : {type(e).__name__}")
        return
    for rd in RELEASES:
        rid = ids.get(rd.fred_name)
        if rid is None:
            rep.errors.append(
                f"FRED : publication « {rd.fred_name} » introuvable — à importer via apex calendar-import"
            )
            continue
        try:
            r = await client.get(
                f"{FRED_URL}/release/dates",
                params={
                    "release_id": rid,
                    "api_key": key,
                    "file_type": "json",
                    "realtime_start": start.isoformat(),
                    "include_release_dates_with_no_data": "true",
                    "limit": 10000,
                    "sort_order": "asc",
                },
            )
            r.raise_for_status()
            events = release_events([x["date"] for x in r.json().get("release_dates", [])], rd)
            async with maker() as s:
                rep.events[rd.code] = await import_events(s, events, source="fred")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            rep.errors.append(f"FRED dates {rd.code} : {type(e).__name__}")


# --------------------------------------------------------------------------- point-in-time context
@dataclass
class _Series:
    avail: list[datetime]
    dates: list[date]
    values: list[float]


class MacroContext:
    """As-of access to stored observations and events: ``at(t)`` only sees what was published by ``t``."""

    def __init__(self, obs: Sequence[MacroObservation], events: Sequence[EconomicEvent] = ()):
        by: dict[str, list[MacroObservation]] = {}
        for o in obs:
            by.setdefault(o.series, []).append(o)
        self.series: dict[str, _Series] = {}
        for k, rows in by.items():
            rows.sort(key=lambda o: (o.available_at, o.obs_date))
            self.series[k] = _Series(
                [o.available_at for o in rows], [o.obs_date for o in rows], [float(o.value) for o in rows]
            )
        self.high_days = sorted(
            {
                (e.scheduled_at.astimezone(NY) - timedelta(hours=17)).date()
                for e in events
                if e.importance == "high" and e.currency in ("USD", "XAU")
            }
        )

    def _known(self, key: str, t: datetime) -> int:
        s = self.series.get(key)
        return -1 if s is None else bisect.bisect_right(s.avail, t) - 1

    def value(self, key: str, t: datetime, back: int = 0) -> float | None:
        k = self._known(key, t) - back
        return self.series[key].values[k] if k >= 0 else None

    def change(self, key: str, t: datetime, n: int) -> float | None:
        a, b = self.value(key, t), self.value(key, t, n)
        return None if a is None or b is None else a - b

    def percentile(self, key: str, t: datetime, window: int = 156) -> float | None:
        k = self._known(key, t)
        if k < 20:
            return None
        hist = self.series[key].values[max(0, k - window + 1) : k + 1]
        cur = hist[-1]
        return sum(1 for v in hist if v <= cur) / len(hist)

    def event_day(self, t: datetime) -> bool:
        """A high-impact US release is scheduled in this trading day (17:00→17:00 New York). Schedules are
        public in advance, so this is known at t."""
        d = (t.astimezone(NY) - timedelta(hours=17)).date()
        i = bisect.bisect_left(self.high_days, d)
        return i < len(self.high_days) and self.high_days[i] == d

    def labels(self, t: datetime) -> dict[str, str]:
        out: dict[str, str] = {}
        ry = self.change("REAL_10Y", t, 5)
        if ry is not None:
            out["taux réels (5 j)"] = "en hausse" if ry > 0.05 else "en baisse" if ry < -0.05 else "stables"
        dx = self.change("DOLLAR_BROAD", t, 5)
        lvl = self.value("DOLLAR_BROAD", t, 5)
        if dx is not None and lvl:
            pct = dx / lvl * 100
            out["dollar (5 j)"] = "en hausse" if pct > 0.3 else "en baisse" if pct < -0.3 else "stable"
        pc = self.percentile("COT_MM_NET", t)
        if pc is not None:
            out["spéculateurs (COT)"] = (
                "très acheteurs" if pc >= 0.8 else "peu acheteurs" if pc <= 0.2 else "neutres"
            )
        if self.high_days:
            out["jour d'annonce"] = "oui" if self.event_day(t) else "non"
        return out

    def snapshot(self, t: datetime) -> dict[str, Any]:
        items = []
        for key, sd in SERIES.items():
            k = self._known(key, t)
            if k < 0:
                continue
            s = self.series[key]
            n = 1 if key in ("COT_MM_NET", "COT_OI") else 5
            items.append(
                {
                    "key": key,
                    "label": sd.label,
                    "unit": sd.unit,
                    "value": s.values[k],
                    "as_of": s.dates[k],
                    "available_at": s.avail[k],
                    "change": self.change(key, t, n),
                    "change_window": "1 sem." if n == 1 else "5 j",
                    "percentile_3y": self.percentile(key, t) if key == "COT_MM_NET" else None,
                }
            )
        return {"at": t, "series": items, "labels": self.labels(t)}


async def load_context(
    maker: async_sessionmaker[AsyncSession], start: datetime | None = None
) -> MacroContext:
    async with maker() as s:
        q = select(MacroObservation)
        if start is not None:
            q = q.where(
                MacroObservation.available_at >= start - timedelta(days=4 * 365)
            )  # room for percentiles
        obs = (await s.execute(q)).scalars().all()
        evq = select(EconomicEvent).where(EconomicEvent.importance == "high")
        events = (await s.execute(evq)).scalars().all()
    return MacroContext(obs, events)
