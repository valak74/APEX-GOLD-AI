"""Real-time data-quality monitor and score (0–1).

The score gates trading: below ``min_tradeable_score`` (or when stale) the platform must not open trades.
Checks per symbol:
* staleness   — no quote for longer than ``stale_after`` while the market should be open;
* spread      — current spread vs rolling median, robust z-score (median absolute deviation);
* jumps       — mid-price move vs rolling median absolute move (flags spikes / bad ticks);
* duplicates  — identical consecutive quotes with identical timestamps;
* out-of-order— timestamps going backwards;
* clock skew  — quote timestamps in the future vs our clock.
Each issue is reported with a reason so the dashboard can explain why trading is blocked.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from apex.domain.models import Quote, utcnow
from apex.market.sessions import is_market_open


@dataclass
class QualityReport:
    symbol: str
    score: float
    tradeable: bool
    market_open: bool
    last_quote_age_s: float | None
    spread: Decimal | None
    spread_median: Decimal | None
    issues: list[str]
    counters: dict[str, int]


@dataclass
class _SymbolState:
    spreads: deque[Decimal] = field(default_factory=lambda: deque(maxlen=600))
    moves: deque[Decimal] = field(default_factory=lambda: deque(maxlen=600))
    last: Quote | None = None
    recent_flags: deque[tuple[datetime, str]] = field(default_factory=lambda: deque(maxlen=200))
    counters: dict[str, int] = field(
        default_factory=lambda: {
            "quotes": 0,
            "duplicates": 0,
            "out_of_order": 0,
            "spread_spikes": 0,
            "price_jumps": 0,
            "future_ts": 0,
        }
    )


class DataQualityMonitor:
    def __init__(
        self,
        stale_after: timedelta = timedelta(seconds=10),
        spread_z_limit: float = 6.0,
        jump_multiple: float = 15.0,
        flag_window: timedelta = timedelta(minutes=2),
        min_tradeable_score: float = 0.8,
        max_future_skew: timedelta = timedelta(seconds=2),
    ) -> None:
        self.stale_after, self.spread_z_limit, self.jump_multiple = stale_after, spread_z_limit, jump_multiple
        self.flag_window, self.min_tradeable_score, self.max_future_skew = (
            flag_window,
            min_tradeable_score,
            max_future_skew,
        )
        self._s: dict[str, _SymbolState] = {}

    def _robust_z(self, value: Decimal, sample: deque[Decimal]) -> float:
        if len(sample) < 30:
            return 0.0  # not enough history to judge
        med = median(sample)
        mad = median(abs(x - med) for x in sample) or Decimal("0.0001")
        return float((value - med) / (Decimal("1.4826") * mad))

    def observe(self, q: Quote, now: datetime | None = None) -> list[str]:
        """Record a quote; returns the issues it raised (empty when clean)."""
        now = now or utcnow()
        st = self._s.setdefault(q.symbol, _SymbolState())
        st.counters["quotes"] += 1
        issues: list[str] = []
        if q.ts - now > self.max_future_skew:
            st.counters["future_ts"] += 1
            issues.append("future_timestamp")
        if st.last is not None:
            if q.ts < st.last.ts:
                st.counters["out_of_order"] += 1
                issues.append("out_of_order")
            elif q.ts == st.last.ts and q.bid == st.last.bid and q.ask == st.last.ask:
                st.counters["duplicates"] += 1
                return ["duplicate"]  # harmless, not added to statistics
            move = abs(q.mid - st.last.mid)
            if len(st.moves) >= 30:
                ref = median(st.moves) or Decimal("0.01")
                if move > ref * Decimal(self.jump_multiple):
                    st.counters["price_jumps"] += 1
                    issues.append("price_jump")
            st.moves.append(move)
        if self._robust_z(q.spread, st.spreads) > self.spread_z_limit:
            st.counters["spread_spikes"] += 1
            issues.append("spread_spike")
        st.spreads.append(q.spread)
        st.last = q
        for i in issues:
            st.recent_flags.append((now, i))
        return issues

    def report(self, symbol: str, now: datetime | None = None) -> QualityReport:
        now = now or utcnow()
        st = self._s.get(symbol)
        open_ = is_market_open(now)
        if st is None or st.last is None:
            return QualityReport(symbol, 0.0, False, open_, None, None, None, ["no_data"], {})
        age = (now - st.last.ts).total_seconds()
        issues: list[str] = []
        score = 1.0
        if open_ and age > self.stale_after.total_seconds():
            issues.append("stale")
            score = 0.0
        if not open_:
            issues.append("market_closed")
            score = min(score, 0.0)
        recent = [f for t, f in st.recent_flags if now - t <= self.flag_window]
        penalties = {"spread_spike": 0.15, "price_jump": 0.25, "out_of_order": 0.2, "future_timestamp": 0.3}
        for flag in sorted(set(recent)):
            issues.append(f"recent_{flag}")
            score -= penalties.get(flag, 0.1) * min(recent.count(flag), 3)
        if len(st.spreads) < 30:
            issues.append("warming_up")
            score = min(score, 0.7)  # not tradeable until we have a baseline
        score = max(0.0, min(1.0, score))
        return QualityReport(
            symbol=symbol,
            score=round(score, 3),
            tradeable=score >= self.min_tradeable_score and open_,
            market_open=open_,
            last_quote_age_s=round(age, 1),
            spread=st.last.spread,
            spread_median=median(st.spreads) if st.spreads else None,
            issues=issues,
            counters=dict(st.counters),
        )
