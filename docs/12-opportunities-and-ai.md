# Opportunities & AI explanations

## How the "AI" is split (and why)

| Layer | What it does | Technology | In the decision path? |
|---|---|---|---|
| Detection | finds setups from structure, liquidity, regime, MTF context; builds a complete plan | deterministic engine (`analysis/opportunities.py`) | yes — reproducible, tested, no look-ahead |
| Evidence | confluence checklist ✓/✗, warnings, blockers, historical statistics of the pattern | deterministic | yes |
| Explanation | writes the "why" in French from those facts only | LLM (Claude, `intel/explainer.py`) | **no** — display only |
| Probability (later) | calibrated probability of success | ML model, phase 8, only if it beats the rules out-of-sample | yes, once validated |

An LLM is never allowed to decide or size a trade: it is slow, non-deterministic, and text must never be able
to trigger an order. It receives only the engine's structured facts (no internet text → no prompt-injection
surface), its output is JSON-schema-validated, and the dashboard shows it next to the facts it was given.

## Setups (first two)
* **Retournement après balayage de liquidité** — a wick beyond a pool of equal highs/lows (or a swing) that
  closes back inside. Stop beyond the wick + 0.2 ATR; targets = opposing liquidity ≥ 1R away (else 2R).
* **Repli dans la tendance de fond** — higher-timeframe bias (1d/4h data required), pullback into an active FVG
  or order block. Stop beyond the zone + 0.2 ATR.

Each opportunity: plan, R:R, quality (share of confluence weight met — heuristic, not a probability), warnings,
blockers (news lockout, data quality, kill switch), invalidation, chart evidence, and the historical hit rate of
the same pattern for the same target/stop distance in ATR, with its 95 % interval and the break-even rate.

## Lifecycle
Scanner every 30 s on completed 5m/15m bars → stored in `trading.opportunities` (one idea per triggering event)
→ paper-tracked to *objectif atteint*, *invalidée* (stop) or *expirée* (12 bars). Nothing is ever sent to a broker.

## Enabling the written AI analysis (optional, paid)
```
APEX_LLM_ENABLED=true
ANTHROPIC_API_KEY=...        # in .env only — never in chat or in the repository
APEX_LLM_MODEL=claude-opus-5-5
APEX_LLM_EFFORT=low          # low | medium | high (cost/quality)
```
One API call per opportunity (cached). Server-side refusal fallback is enabled. Without a key the dashboard
shows the deterministic explanation only.

## Chart
Selecting an opportunity: switches to its timeframe, draws Entrée / Stop / Objectifs, the swept level or zone,
an "Opportunité" marker on the triggering bar; EMA 50/200 and RSI(14) panel (toggle *Indicateurs*).

## Bugs found while building this
1. One wick produced two opportunities (pool + its own swing) → deduplicated, causally (caught by the
   no-look-ahead property test when the first fix leaked future information).
2. The same sweep was re-announced on 3 consecutive scans → ids are based on the triggering event.
3. Targets closer than 1R and near-duplicate second targets → removed.
