# Phases 5 + 7 — Strategies and backtesting

Status: **implemented and tested on synthetic data** (2026-11-27). **Not yet run on real history**: no strategy
is validated. The first real result comes from the owner's run on Dukascopy history (see "How to run").

## What exists

| Piece | Where | Notes |
|---|---|---|
| Signal generation | `backend/src/apex/backtest/signals.py` | Same detectors as the live scanner (`analysis/structure`, `liquidity`, `regime`), computed once on the full series and **causal** (truncation-invariance test). Two setups: `liquidity_sweep_reversal`, `trend_pullback`. |
| Execution engine | `backtest/engine.py` | Bar-based, one position at a time. Every trade goes through `risk.engine.evaluate` with the **active risk config**: same checks, same sizing, same refusals as live. |
| Metrics and gate | `backtest/metrics.py` | Net-of-cost statistics, confidence intervals, Monte Carlo, pre-registered gate. |
| Report | `backtest/runner.py` | Base run, cost stress, walk-forward OOS, sensitivity, equity and drawdown. |
| Service, API, CLI | `backtest/service.py`, `api/routes/backtests.py`, `apex backtest` | Stored in `research.backtests` (JSONB report). |
| UI | `frontend/app/backtests` | Form, run history, full report. |

## Execution model (deliberately conservative)

- The signal fires at the close of bar *i*. Entry is at the **open of bar i+1**, at ask (long) or bid (short), plus slippage.
- The spread of each bar is the recorded average spread × `spread_mult`.
- When the stop and the target both sit inside the same bar, the **stop is assumed hit first**.
- A gap through the stop fills at the open, which is worse than the stop.
- Time exit after `max_hold_bars`. Forced exit before the Friday close.
- Commission comes from the symbol spec on both sides.
- Swap is optional; when it is 0, the report flags it as "non modélisé".
- High-impact USD/XAU/CNY events stored in `intel.economic_events` block entries, as in live.
- The account currency is USD, with no FX noise.

The live detector now drops ideas whose **net R:R** (after spread and commission) is below `min_risk_reward`, so
live and backtest apply the same economics.

## Validation gate (pre-registered, `metrics.validate`)

A strategy is "validée" only if **all** of the following hold:

1. at least 200 trades;
2. the bootstrap 95 % CI lower bound of the expectancy is > 0 R;
3. the profit factor is ≥ 1.2;
4. the expectancy is still > 0 with costs × 1.5;
5. walk-forward stability: no fold holds more than 50 % of the gains, and most folds are positive;
6. every neighbouring parameter variant (stop buffer ± 0.1 ATR) has expectancy > 0.

The gate is evaluated on the **out-of-sample** walk-forward trades when there are any, otherwise on the base run.
Walk-forward picks the best of 6 variants (stop buffer 0.1/0.2/0.3 × min quality 0/50) on past periods only, then
judges it on the next period.

The test suite asserts that pure random-walk data never passes the gate for either setup.

## Bug found during QA (fixed)

In the live risk engine, the losing-streak check could **block forever**: the streak only reset on a winning trade,
but no trade was allowed once the limit was hit. The streak now counts the current trading day only, so the
pause ends at the next 17:00 New York rollover. A regression test covers this.

## Known limitations

- The bars are mid OHLC with an average spread per bar, not tick-by-tick bid/ask; intrabar ordering is unknown,
  hence the stop-first rule.
- Walk-forward reuses one full-history simulation per variant, partitioned by period. Account-dependent sizing makes
  this a close approximation of separate runs.
- Not yet implemented: the deflated Sharpe ratio (roadmap criterion), latency modelling, partial fills, and the
  "session breakout" strategy listed in the roadmap.
- Sharpe and Sortino are reported only with at least 20 trading days.

## How to run (owner)

```bash
apex import-dukascopy --from 2023-01-01 --to 2026-11-01   # history
apex backtest --setup liquidity_sweep_reversal --timeframe 5m
apex backtest --setup trend_pullback --timeframe 15m
```

Or use the **Backtests** tab in the dashboard. A "non validée" result is the expected default and a useful one:
it means that no edge has been shown and nothing should be traded.
