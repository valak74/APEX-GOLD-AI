# Development Roadmap

Each phase ends with: tests green, docs updated, this file updated, a short report of done / remaining / blockers.
Exit criteria are **gates** — the next phase does not start on a failed gate.

| # | Phase | Status | Key deliverables | Exit gate |
|---|---|---|---|---|
| 1 | Research & architecture | ✅ Done (2026-11-27) | `docs/00`–`06`, ADRs | Docs reviewed; **broker identified** (open) |
| 2 | Foundation | ⏳ Next | Repo layout, `pyproject`, Docker Compose (Timescale, Redis, api), config system, structured logging, auth (users/roles/MFA), domain models, `BrokerAdapter` ABC + `PaperAdapter` + `SimulationAdapter`, Next.js shell with mode banner, CI (lint, type-check, tests) | `docker compose up` healthy; paper adapter round-trip test; CI green |
| 3 | Market data | | Dukascopy importer, tick normaliser, bar builder (1s–1W), DQ checks & score, Timescale storage/compression, live feed via paper data source, market-state cache | Import ≥10y XAUUSD ticks; DQ report published; bar builder property-tested vs reference |
| 4 | Analysis | | Indicators, swing/structure engine (BOS/CHoCH), liquidity (EQH/EQL, sweeps), FVG/OB with objective definitions & invalidations, sessions/levels, regime classifier, MTF context, calendar lockouts, macro feeds, news ingestion & classification (LLM async) | Each detector unit-tested on synthetic fixtures; pattern stats computed on history |
| 5 | Strategies | | Strategy plug-in API; first 2 strategies (session breakout, liquidity-sweep reversal); then others only after the first two complete phase 7 | Deterministic signal tests; no look-ahead (tested by truncation invariance) |
| 6 | Risk management | | Position sizing, SL/TP validation, all global limits, lockouts, kill switch, emergency close, reconciliation | Property-based tests (hypothesis) + edge-case suite; 100% branch coverage on risk module |
| 7 | Backtesting | | Event-driven bid/ask backtester sharing the *same* strategy/risk code as live; cost model (spread, commission, swap, slippage, latency, rejections); walk-forward; Monte Carlo; bootstrap; sensitivity; reports | Backtester reproduces hand-checked trades; strategy passes pre-registered OOS criteria (below) |
| 8 | AI system | | Agent interfaces & schemas, quant probability model (calibrated), fusion engine, performance analyst, model registry (MLflow), drift detection, rollback | Fusion improves OOS EV vs strategy-alone, or is not used |
| 9 | Paper trading | | 24/5 paper run on live data, full journal, live-vs-backtest divergence dashboards, alerts (Telegram/email) | ≥ 3 months **and** ≥ 100 trades; metrics within backtest confidence intervals; zero unexplained risk events |
| 10 | Live | | Live broker adapter, activation flow, minimum risk (0.1–0.25%), daily review | Ongoing monitoring; automatic demotion to paper on breach |

Dashboard pages are built incrementally alongside the phase that produces their data.

## Strategy validation criteria (pre-registered, applied in phase 7)

A strategy may move from `draft` → `validated` only if, on data it was **not** tuned on:
- ≥ 200 OOS trades (or justified lower bound with wider CI)
- Expectancy > 0 after full costs, with bootstrap 95% CI lower bound > 0
- Profit factor ≥ 1.2, max DD within risk budget at intended sizing
- Stable across walk-forward folds (no single fold > 50% of profit)
- Parameter sensitivity: neighbouring parameters also profitable (no knife-edge optimum)
- Deflated Sharpe ratio accounting for number of variants tried
- Survives 1.5× cost stress

## Open blockers

1. **Broker not yet identified** → blocks the live adapter (phase 10) and exact cost calibration; does not block phases 2–9.
2. Budget for paid data (calendar consensus, news API, Databento) → affects phases 4 & 7 scope.
3. Production host (Linux VPS; plus Windows VPS if MT5).
