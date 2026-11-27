# Phase 4 — Analysis: report (part A)

Status: **part A complete** (technical / structure / liquidity / regime / MTF / statistics / calendar lockouts).
Part B — news ingestion & LLM interpretation, macro cross-asset feeds — not started (needs data-provider keys).

## Delivered

| Module | Content | Where |
|---|---|---|
| Indicators | SMA, EMA, Wilder RSI/ATR, MACD, Bollinger, ADX ±DI, efficiency ratio, ATR percentile rank — all causal. No VWAP/volume profile (spot gold has no real volume) | `analysis/indicators.py` |
| Structure | swings (k bars each side, known only `k` bars later), HH/HL/LH/LL, BOS, CHoCH; score, invalidation level & time | `analysis/structure.py` |
| Liquidity | FVG (≥ 0.25 ATR, mitigation & invalidation), equal highs/lows pools, sweeps (pierce + close back), order blocks (only with displacement ≥ 1 ATR), premium/discount | `analysis/liquidity.py` |
| Levels | previous day / week high-low, Asia / London / New York sessions, NY opening range (DST-safe, in-progress flagged) | `analysis/levels.py` |
| Regime | strong/weak trend, range, breakout, transition; volatility overlay; `risk_multiplier ≤ 1`; suitable strategy families; reasons in French | `analysis/regime.py` |
| MTF | 1d/4h fond, 1h/15m structure, 5m/1m exécution; weighted bias −1..+1, alignment, `htf_conflict`, `htf_available` | `analysis/engine.py` |
| Statistics | event studies per pattern: forward returns in ATR, first-touch hit rate, Wilson & bootstrap 95 % CIs, small-sample flag | `analysis/stats.py`, `GET /analysis/stats` |
| Calendar | `intel.economic_events`, JSON / admin import (idempotent), lockout of new entries ±N min (from risk config) around high-impact USD/XAU/CNY events | `intel/calendar.py`, `/calendar/*`, `apex calendar-import` |
| API | `/analysis/context`, `/analysis/overlays`, `/analysis/stats`, `/calendar/upcoming`, `/calendar/lockout`, `/calendar/import`; heavy computation off the event loop | `api/routes/analysis.py` |
| Dashboard | Analyse de marché (bias meter, MTF table), Calendrier (lockout banner, upcoming), chart overlays (previous day/week levels, BOS/CHoCH markers, toggles), header lockout indicator | `frontend/components/` |

## Proof of no look-ahead
Hypothesis property tests: for random price paths and random cut points, indicators, structure events, FVGs and
sweeps computed on the truncated series are identical to those computed on the full series up to the cut.

## Bugs found
1. Trading-day start for a timestamp inside the daily break was in the future → day now starts at the 17:00 NY
   rollover (affects daily/weekly bars; tests updated).
2. Steady trends without pullbacks left structure "undetermined" and the regime wrongly said "structure
   disagrees" → undetermined no longer blocks a strong trend; MTF uses an *effective* trend.
3. Oscillating markets were labelled weak trends (ADX noisy) → efficiency ratio decides range.
4. Bias displayed "aligned" when 1d/4h had no data → `htf_available` flag, UI says "court terme uniquement".

## Honest limitations
- Scores are heuristics, **not probabilities**; their value must be proven with `/analysis/stats` on long
  history, then by the backtester (phase 7), before any strategy relies on them.
- Calendar data is imported (no automatic source wired: official sites/paid APIs blocked or keyless here).
- Order flow remains disabled for spot gold (ADR 0003).
- Weekly reference levels need ≥ 2 weeks of 15m bars.

## Calendar import format
```json
[{"event_code": "US_NFP", "title": "Non-Farm Payrolls", "country": "US", "currency": "USD",
  "importance": "high", "scheduled_at": "2026-12-04T13:30:00Z", "forecast": "150K", "previous": "143K"}]
```
Times must include a timezone. Re-importing updates forecast/actual values.
