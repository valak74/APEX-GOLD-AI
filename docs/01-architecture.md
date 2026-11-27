# Architecture & Technical Specification

## 1. Design goals (in priority order)

1. **Safety** — a deterministic risk engine sits between every decision and the broker; nothing bypasses it.
2. **Auditability** — every decision is a persisted, schema-validated record that can be replayed.
3. **Correctness of money math** — position sizing and P&L use broker-specific specs, `Decimal`, and are exhaustively tested.
4. **Replaceability** — brokers, data feeds, news providers, LLM providers, models are all behind interfaces.
5. **Latency** — fast enough for 1m/5m intraday decisions (target signal→order < 250 ms excluding broker). Not HFT.

## 2. High-level component map

```
                       ┌──────────────────────── Frontend (Next.js) ───────────────────────┐
                       │ Overview · Chart · Intelligence · News · Calendar · Trades ·        │
                       │ Strategies · Backtests · Models · System status · Kill switch       │
                       └───────────────▲─────────────────────────────┬─────────────────────┘
                                       │ WebSocket (read)            │ REST (authenticated)
┌──────────────────────────────────────┴─────────────────────────────▼─────────────────────┐
│                                   API Gateway (FastAPI)                                   │
│   auth · RBAC · rate-limit · audit log · input validation · NO broker credentials out      │
└──────┬───────────────┬──────────────┬───────────────┬───────────────┬───────────────┬─────┘
       │               │ Redis Streams (event bus)    │               │               │
┌──────▼─────┐ ┌───────▼──────┐ ┌─────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
│ Market Data│ │ Analysis     │ │ Intel      │ │ Decision    │ │ Risk Engine │ │ Execution   │
│ Service    │ │ Service      │ │ Service    │ │ (Fusion)    │ │ (determin.) │ │ Service     │
│ ticks→bars │ │ TA · SMC ·   │ │ news · cal │ │ strategies  │ │ sizing · SL │ │ broker      │
│ DQ score   │ │ regime · MTF │ │ macro · LLM│ │ + agents    │ │ limits ·    │ │ adapters    │
│            │ │              │ │ (async)    │ │ → Proposal  │ │ kill switch │ │ reconcile   │
└──────┬─────┘ └──────┬───────┘ └─────┬──────┘ └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
       │              │               │               │               │               │
┌──────▼──────────────▼───────────────▼───────────────▼───────────────▼───────────────▼──────┐
│ PostgreSQL + TimescaleDB (ticks, bars, decisions, orders, trades, audit)  ·  Redis (state)  │
└────────────────────────────────────────────────────────────────────────────────────────────┘
   Offline: Backtester · Research notebooks · Training pipeline · Model registry · Performance analyst
```

### Critical path (synchronous, deterministic, no LLM)

```
tick → MarketState update → DQ gate → feature/analysis update (bar close)
     → strategy evaluate → TradeProposal → Fusion (uses cached agent outputs)
     → RiskEngine.evaluate → ApprovedOrder | Rejection → ExecutionService → BrokerAdapter
```

LLM-based agents (news interpretation, macro narrative, explanations) run **asynchronously**, publish
structured, schema-validated outputs with timestamps and TTLs into Redis. The fusion engine reads the
latest valid outputs; stale or missing outputs reduce confidence or block trading — they never block
the event loop.

## 3. Service responsibilities

| Service | Owns | Must never |
|---|---|---|
| **market-data** | Feed connections, tick normalisation, bar building (1s…1W), gap/stale/outlier/duplicate detection, DQ score, persistence | Make trading decisions |
| **analysis** | Indicators, market structure (swings, BOS/CHoCH), liquidity (equal highs/lows, sweeps), FVG/OB, sessions, regime classifier, MTF context | Call brokers |
| **intel** | Economic calendar, news ingestion, dedupe, source scoring, event classification, LLM summarisation, cross-asset feeds (DXY, yields, VIX…) | Place orders |
| **decision** | Strategy plug-ins, agent outputs, signal fusion → `TradeProposal` (schema-validated) | Size positions or alter risk limits |
| **risk** | Position sizing, SL/TP validation, all global limits, lockouts, kill switch, mode guard | Be influenced by AI at runtime (config is read-only, changed only via authenticated human action + audit) |
| **execution** | Order lifecycle, broker adapters, partial fills, retries with idempotency keys, position management rules, reconciliation | Accept orders not signed by risk |
| **api** | Auth, REST/WebSocket for UI, admin actions | Expose credentials |
| **worker (offline)** | Backtests, training, walk-forward, reports | Touch live execution |

Phase 2 will start these as **modules in one Python package with clear boundaries** (a modular
monolith) running as separate processes via Docker Compose. Splitting into separately deployed
services happens only when needed. This keeps the codebase maintainable while preserving the seams.

## 4. Key domain contracts (Pydantic)

```python
class TradeProposal(BaseModel):         # produced by decision layer
    proposal_id: UUID
    created_at: datetime                # UTC
    mode: Literal["simulation", "paper", "live"]
    symbol: str
    side: Literal["buy", "sell"]
    strategy_id: str; strategy_version: str
    timeframe: str
    entry_type: Literal["market", "limit", "stop"]
    entry_price: Decimal
    stop_loss: Decimal                  # mandatory
    take_profits: list[TakeProfitLeg]
    p_win: float                        # calibrated probability
    expected_value_r: float             # net of estimated costs
    confidence: float
    market_regime: str
    data_quality: float
    reasons: list[str]; warnings: list[str]; invalidations: list[str]
    evidence: dict[str, AgentOutputRef] # pointers to agent outputs used

class RiskDecision(BaseModel):          # produced ONLY by risk engine
    proposal_id: UUID
    approved: bool
    rejection_codes: list[RiskRejection]
    volume_lots: Decimal | None
    risk_amount_account_ccy: Decimal | None
    risk_pct_equity: Decimal | None
    margin_required: Decimal | None
    checks: list[RiskCheckResult]       # every check, pass or fail, with values
    config_hash: str                    # hash of risk config used — audit
```

`BrokerAdapter` (abstract, async) exactly as specified, plus `get_symbol_spec()`,
`get_server_time()`, `get_order_history()`, `stream_ticks()`. Implementations:
`SimulationAdapter` (backtest), `PaperAdapter` (live prices, simulated fills with spread/slippage
model), and one live adapter per broker. Each adapter declares a `Capabilities` object
(supports_sltp_on_order, supports_modify, supports_depth, push_quotes, …); the engine adapts or refuses.

**Mode separation**: mode is set at process start from config + DB, stored in every record, shown in
UI header. The live adapter can only be instantiated when `mode == "live"` **and** the live-activation
record (human confirmation, checks passed, paper period satisfied) exists. A paper process physically
lacks live credentials.

## 5. Position sizing (XAU/USD) — the formula we will implement

Never a generic "pip value" formula. Using broker symbol spec:

```
price_risk        = |entry − stop_loss|                       (quote ccy per unit, USD/oz)
loss_per_lot_qc   = price_risk × contract_size                (USD per lot; contract_size from broker, e.g. 100 oz)
                    (equivalently ticks × tick_value, using broker trade_tick_size/value)
loss_per_lot_ac   = loss_per_lot_qc × fx(quote→account)       (account ccy chosen by user — ADR 0006; USD→EUR via 1/EURUSD,
                                                               USD→CHF via USDCHF, etc.; conservative side; stale rate ⇒ no trade)
                    + estimated_costs_per_lot_ac               (spread at stop + commission round-trip + slippage buffer)
risk_budget_ac    = equity × risk_pct  (min of equity/balance; capped by remaining daily/weekly budget)
raw_lots          = risk_budget_ac / loss_per_lot_ac
lots              = floor_to_step(raw_lots, volume_step)       (ALWAYS round DOWN)
reject if lots < volume_min; clamp/reject if > volume_max or > exposure cap
margin_required   = lots × contract_size × price × fx / leverage   (or broker's order_calc_margin when available)
reject if free_margin − margin_required < min_free_margin_buffer
```

Tested against hand-computed cases including EUR accounts, 1-oz and 100-oz contracts, min/step edge cases.

## 6. Signal fusion approach

- Each agent outputs `(direction, strength, confidence, horizon, timestamp, ttl)`.
- Fusion is **gated, not averaged**: hard vetoes first (DQ < threshold, lockout, regime not allowed
  for strategy, HTF conflict), then a **calibrated probability model** (logistic / GBM trained on
  historical proposals) combining strategy setup statistics with agent features. Weights are
  regime-dependent and learned out-of-sample; disagreement (entropy across agents) reduces confidence.
- Trade only if `EV_net = p_win·avg_win − (1−p_win)·avg_loss − costs > threshold` with p_win's
  lower confidence bound, not point estimate.

## 7. Technology choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12+ (dev on 3.13) | Ecosystem for quant/ML |
| API | FastAPI + Pydantic v2 | Typed contracts, async, OpenAPI |
| Event bus | **Redis Streams** | Persistent, consumer groups, replay; simpler than Kafka at this scale |
| Task queue | **Dramatiq** (Redis broker) | Simpler & more predictable than Celery for long backtests |
| Time-series | TimescaleDB hypertables + compression + continuous aggregates | Ticks/bars at scale in Postgres |
| Numerics | NumPy, Polars (research), Numba where hot | Speed without C++ |
| ML | LightGBM, scikit-learn, PyTorch (later), MLflow for registry/versioning | Standard, reproducible |
| LLM | Provider abstraction (`LLMProvider` interface); structured JSON outputs validated by Pydantic | Swap providers; never trust raw text |
| Frontend | Next.js + TS + Tailwind + TradingView Lightweight Charts | As specified |
| Observability | structlog (JSON) → Loki; Prometheus metrics; Grafana; health endpoints | Standard |
| Secrets | `.env` for dev only; prod: Docker secrets / SOPS-encrypted files or Vault; DB-stored broker creds encrypted with envelope key | See security doc |
| Testing | pytest, hypothesis (property tests for sizing/risk), pytest-asyncio, testcontainers | Money math deserves property tests |

ADRs in `docs/adr/` record the non-obvious decisions.

## 8. Time handling

All internal timestamps are **UTC, timezone-aware, nanosecond-capable** (`timestamptz` + separate
`ts_ns` bigint for ticks). Broker server time (often EET/EEST) is converted at the adapter boundary
and the offset is monitored. Sessions (Asia/London/NY) are defined in their local zones to handle DST
correctly. The "trading day" boundary is 17:00 America/New_York (gold's daily rollover).

## 9. Failure behaviour (fail-safe)

| Failure | Behaviour |
|---|---|
| Data stale > N s or DQ score < threshold | Block new entries; existing positions keep broker-side SL/TP |
| Broker disconnect | Block entries; reconnect with backoff; on reconnect **reconcile** positions/orders vs DB before resuming |
| Redis/DB down | Execution service halts entries; positions protected broker-side |
| Agent output stale | Its evidence is dropped; if required by strategy, no trade |
| Risk engine exception | Treated as rejection (fail-closed) |
| Kill switch | Cancel pending → close all (configurable) → disable execution → alert; requires human to re-arm |
