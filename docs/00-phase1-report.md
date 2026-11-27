# Phase 1 Report — Research, Feasibility, Architecture

Status: **Phase 1 complete (documentation)**. No trading code exists yet.
Date: 2026-11-27

## 1. Environment inspection

| Item | Finding |
|---|---|
| OS | Ubuntu 24.04.5 LTS, x86_64, 4 vCPU, 15 GiB RAM (cloud dev container) |
| Python | 3.13.18 (spec asks 3.12+ — OK) |
| Node | v22.23.5 |
| Docker | 29.9.3 available |
| PostgreSQL | 16.15 client/server (TimescaleDB extension **not** installed — will run via Docker image) |
| Redis | 7.0.15 |
| Repository | `valak74/APEX-GOLD-AI`, single initial commit, only a README. Greenfield. |

Implication: this container is a **development/CI environment**, not a production host. A live trading
host needs to be always-on, close (latency-wise) to the broker's servers, and — if MT5 is used — able
to run the Windows MT5 terminal (see §3).

## 2. The single biggest open question: which broker?

> **Update:** owner decided — no MetaTrader; dedicated platform connected directly to the broker's official API (ADR 0005).

The specification refers to "the broker API" but no broker is named. **Everything in the execution
layer depends on this answer.** The architecture below is broker-agnostic (adapter pattern), and the
paper/simulation adapters can be built without a broker, but the live adapter cannot.

Questions for the account owner (see `02-broker-api-comparison.md` §5 for the full checklist):

1. Broker name and legal entity (EU/ESMA? which regulator?).
2. Platform offered: MT5, cTrader, proprietary REST/FIX?
3. Account currency (EUR or USD?) — affects every position-size calculation.
4. Account type: retail or professional (ESMA retail leverage cap on gold CFDs is 20:1).
5. Is a demo account available on the **same** server/symbol specs as live?

## 3. Key research findings

### Broker connectivity
- **MetaTrader 5**: the official `MetaTrader5` Python package talks to a locally running terminal via
  IPC and is **Windows-only**. On Linux it requires a bridge (terminal under Wine + RPC, e.g.
  `mt5linux`/`pymt5linux`/`mt5-remote` — third-party, unaudited). It is polling-based (no push
  stream), one account per terminal process, and broker-specific symbol names (`XAUUSD`, `GOLD`,
  `XAUUSD.r`, …). Contract size varies by broker (commonly 100 oz/lot, but not always).
- **cTrader Open API**: Protobuf/JSON over TLS, works on Linux natively, OAuth2 account authorisation,
  push-based spot quotes, supports SL/TP on orders and amendments. Gold availability is broker-specific
  (a forum report shows gold present as an asset but absent as a symbol for one broker).
- **OANDA v20 REST + streaming**: first-class HTTP/streaming API, Linux-native, `XAU_USD` is a
  standard CFD instrument in most divisions, but **not every OANDA division offers v20** (OANDA
  Global Markets and OANDA TMS Brokers are excluded). Streaming limited to 20 streams per IP.
- **FIX**: generally only offered on institutional/pro accounts with minimum balances. Not a
  realistic starting point.

### Market data
- Spot XAU/USD is an **OTC market**. There is no consolidated tape, no true volume, no public order
  book. Broker "volume" is **tick volume** (number of price updates), not traded size.
- Real order flow (bid/ask volume, delta, depth) exists only for **exchange-traded gold**: COMEX `GC`
  / `MGC` futures. Databento provides CME MDP3 data (incl. MBO) from June 2010, billed per GB
  (from ~$0.50/GB; use `metadata.get_cost` before downloading).
- **The spec's order-flow module therefore must be either (a) disabled, or (b) built on GC futures as a
  proxy, with explicit basis/timing caveats.** We will not fabricate order flow from tick volume.

### Historical data
- **Dukascopy** offers free historical bid/ask tick data for XAUUSD via its export tool and
  community downloaders. Start date and gap quality must be measured, not assumed.
- Broker's own history (MT5 `copy_ticks_range`, OANDA candles) is the most *representative* of
  execution prices/spreads but usually much shorter.
- Plan: Dukascopy for long-horizon research; broker history for spread/cost calibration; GC futures
  for order-flow research only.

### Macro / news
- **FRED API**: free with key; covers DFII10 (10y real yield), DGS10, DTWEXBGS etc. **Terms prohibit
  storing/caching/archiving and using content to train ML/LLM models.** Consequence: FRED may be
  used for *live display and features computed on the fly*; datasets used for model training must
  come from the primary source (US Treasury publishes real yield curve data directly) or a licensed
  vendor. Legal review recommended.
- **Economic calendar**: no reliable free official API. Trading Economics (paid, pricing custom/
  conflicting across sources), Finnhub (calendar is premium), FMP (endpoint exists, plan tier
  unconfirmed). Scraping ForexFactory et al. is legally fragile — avoid for production.
  Official release schedules (BLS, BEA, Fed FOMC calendar) can be ingested directly as a fallback.
- **News**: Reuters/Bloomberg/Dow Jones require enterprise licences. Realistic tiers: official
  sources (Fed, ECB, BLS, BEA, Treasury press releases/RSS) → paid news API (Benzinga, Finnhub,
  Polygon-type) → GDELT for broad geopolitical signal (free, ~15 min latency, noisy). X/Twitter API
  access is expensive and should be treated as unverified signal only.
- Latency claims by data vendors are marketing; they must be measured by our own monitoring.

## 4. Feasibility verdict

| Capability | Feasible? | Notes |
|---|---|---|
| Paper/simulated trading on real-time XAU/USD prices | **Yes** | Any broker API or data feed |
| Live automated execution | **Yes, conditional** | Depends on broker API (see §2) |
| Accurate position sizing from live account data | **Yes** | Requires broker symbol specs + FX conversion |
| Tick-level backtesting with realistic costs | **Yes** | Dukascopy + broker spread calibration |
| True order-flow analysis on spot gold | **No** | Only via GC futures proxy |
| Sub-second news trading | **Not recommended** | Retail news feeds lag institutional wires; spreads blow out. Use news for **risk lockout**, not entries |
| LLM agents in the execution path | **No (by design)** | Too slow & non-deterministic; LLMs advise, deterministic engine decides |
| Guaranteed profitability | **No** | Edge must be measured; most retail intraday gold strategies do not survive costs |

## 5. Biggest risks

**Financial**
1. *No edge exists after costs.* Gold spreads (typically tens of cents, widening sharply at news/rollover) plus slippage consume most scalping edges. Mitigation: EV-after-costs gate; strategies are rejected by default.
2. *Overfitting / data-snooping* from testing many strategies × parameters. Mitigation: walk-forward, locked out-of-sample, deflated Sharpe / multiple-testing correction, pre-registered hypotheses.
3. *Tail events* (gaps at weekly open, flash moves on CPI/NFP/FOMC). Mitigation: event lockouts, weekend flat rule, hard max-loss per trade, broker-side SL always attached.
4. *Leverage & margin* (ESMA 20:1 retail on gold; margin close-out at 50%). Mitigation: risk engine margin checks with buffer.
5. *Backtest ≠ live* (spread model, latency, requotes). Mitigation: mandatory paper period, live-vs-paper divergence monitoring.

**Technical**
1. Broker API limitations (MT5 Windows-only, polling, symbol quirks).
2. Data quality (gaps, stale quotes, DST/timezone errors — Dukascopy is UTC, MT5 servers usually EET).
3. Unsafe state after crash (orphaned positions). Mitigation: broker-side SL on every order, reconciliation on startup, kill switch.
4. Credential leakage. Mitigation: secrets never in repo, encrypted at rest, backend-only.
5. Scope creep — the spec is very large. Mitigation: phased delivery, each phase tested.

**Legal / licensing**
1. Data redistribution and ML-training restrictions (FRED; most news vendors).
2. Broker terms of service on automated trading/latency arbitrage.
3. Automated trading for oneself is generally fine; managing others' money is regulated.

## 6. Recommended initial implementation strategy

1. **Build the deterministic core first, broker-independent**: domain models, risk engine,
   position sizing, paper adapter, backtester. These are where bugs lose money, and they need no
   external services.
2. **Data before strategies**: ingest Dukascopy XAUUSD history into TimescaleDB, build the data-quality
   scorer, measure gaps/spreads.
3. **One or two simple, falsifiable strategies** (e.g. session-range breakout, liquidity-sweep
   reversal) through the full pipeline (signal → risk → paper execution → journal → evaluation)
   before adding ML/LLM layers. Prove the pipeline, then search for edge.
4. **News/macro as risk filters first** (calendar lockouts), signal sources later only if they
   demonstrably add value out-of-sample.
5. **Live adapter last**, once the broker is chosen and the paper period passes its gates.

See `06-roadmap.md` for phases and exit criteria.

## 7. Sources consulted

- mt5linux / pymt5linux / mt5-remote PyPI pages: https://pypi.org/project/mt5linux/, https://pypi.org/project/pymt5linux/, https://pypi.org/project/mt5-remote/
- OANDA v20 docs: https://developer.oanda.com/rest-live-v20/introduction/, https://developer.oanda.com/rest-live-v20/development-guide
- cTrader Open API: https://help.ctrader.com/open-api, https://community.ctrader.com/forum/connect-api-support/43756
- Dukascopy historical data: https://www.dukascopy.com/swiss/english/marketwatch/historical/
- Databento CME GC: https://databento.com/catalog/cme/GLBX.MDP3/futures/GC, https://databento.com/docs/datasets/glbx-mdp3
- FRED API terms: https://fred.stlouisfed.org/docs/api/terms_of_use.html
- Calendar pricing comparisons: https://businessquant.com/resources/best-macroeconomic-data-providers, https://site.financialmodelingprep.com/datasets/economics
