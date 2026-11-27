# Phase 6 — Risk engine: report

Status: **complete.** The engine is the only component that can approve an order. Orders are still not sent
automatically (that is phase 9, paper trading), but every opportunity can be checked on demand.

## Principles
* **Pure & deterministic** — `evaluate(proposal, snapshot, config)`; same inputs → same decision; nothing mutated.
* **Fail-closed** — any exception or missing data is a rejection (`internal_error`, critical).
* **Reduction only** — regime multiplier clamped to [0, 1]; risk halved past `drawdown_reduce_at` × max drawdown;
  capped to the remaining daily loss budget. Nothing can exceed `risk_per_trade_pct` (≤ 2 % hard-coded).
* **Fully auditable** — every check stored with value and limit (`trading.risk_decisions`), config hash attached,
  audit-log entry per evaluation.
* **Immutable limits** — config versions only via admin + password + TOTP; no AI write path.

## The 23 checks
System: mode · kill switch · trading enabled · idea not already executed.
Market & data: market open · not within N min of the daily rollover · not within N min of Friday close ·
data quality · quote age · spread · news lockout.
Account: daily loss (realised + open) · weekly loss · **max drawdown (critical → automatic kill switch)** ·
trades per day · losing streak · open positions · no hedging.
Trade: stop side · target side · stop distance in ATR (0.3–6) · **R:R after spread + commission ≥ 1.5** ·
position size & margin (sizing engine, slippage buffer included).

## New configuration fields (defaults)
`min_risk_reward` 1.5 · `min_stop_atr` 0.3 · `max_stop_atr` 6 · `max_quote_age_s` 5 ·
`weekly_close_buffer_minutes` 60 · `rollover_buffer_minutes` 15 · `drawdown_reduce_at` 0.5.

## Journal & guard
* Every closed trade is written to `trading.trades` (broker hook); daily/weekly figures follow the gold trading
  calendar; drawdown = realised-balance peak → current equity (open losses count, open gains never raise the peak).
* **Risk guard** (every 5 s): at the maximum drawdown it engages the kill switch, closes positions and audits it.
* **Trading activation** is explicit: admin + password + TOTP, refused while the kill switch is engaged;
  disabling is one click for any trader.

## API & dashboard
`GET /risk/status`, `POST /risk/evaluate/{opportunity_id}`, `GET /risk/decisions`,
`POST /system/trading/enable|disable`. Dashboard: *Moteur de risque* card (usage meters, remaining daily budget,
blocks, activation) and *Vérification par le moteur de risque* in the opportunity panel (verdict + all 23 checks).

## Verification
194 backend tests. Engine: one test per blocking condition, deterministic id, no mutation, fail-closed, and 300
property cases proving an approved order never risks more than the per-trade limit or the remaining daily
budget, never moves the stop and always meets the net R:R. End-to-end: refusal until activation, decisions
stored, trade journal → status, automatic kill switch at max drawdown, activation refused under kill switch.

## Observation worth knowing
On the visual test, a sweep opportunity with **1.02 R gross had 0.42 R after costs** (tight 1 $ stop, 0.20 spread,
commission) and was refused. Costs dominate short-stop scalping; the opportunity detector will be made
cost-aware in phase 5 so it stops proposing ideas the engine will always refuse.
