# Phase 3 — Market data: report

Status: **complete (A + B), tested against a protocol simulator and on TimescaleDB 2.17; awaiting the owner's first run on the real IC Markets demo server and a real Dukascopy download (both hosts are blocked from the build environment).**

## Part A — cTrader connectivity

## Delivered

| Component | Notes | Where |
|---|---|---|
| Protocol layer | Message classes built at runtime from Spotware's official descriptors (MIT, bundled) — no hand-copied payload numbers; length-prefixed framing, 16 MB frame cap | `broker/ctrader/proto.py` |
| Async client | TLS, request/response by `clientMsgId`, event queue, heartbeat, client-side rate limit, fail-all-pending on disconnect, error mapping | `broker/ctrader/client.py` |
| Spec mapping | Lot size / volumes from "cents", price scale 1e5, money digits, 4 commission types incl. per-million-notional and minimum commission, stop distance; **refuses** anything ambiguous (percentage distances, currency mismatches, non-enabled trading mode) | `broker/ctrader/mapping.py` |
| Adapter (read-only) | App + account auth, automatic token refresh (persisted encrypted), gold symbol found by assets (robust to `XAUUSD.r` suffixes), FX pair for account & paper currency, merged partial spot events, crossed quotes dropped, positions/margin/equity, reconnect with backoff + resubscribe. **Order methods raise; live accounts refused.** | `broker/ctrader/adapter.py` |
| OAuth & storage | Auth URL, code extraction, token exchange; credentials envelope-encrypted, audited | `broker/ctrader/oauth.py`, `store.py` |
| Operator CLI | `apex ctrader-setup` (link demo account), `apex ctrader-check` (spec + live quotes + spread stats) | `broker/ctrader/setup_cli.py` |
| Feed supervisor | Background connect/retry; quotes → paper broker; real spec replaces placeholder; status exposed | `market/feed.py` |
| API / UI | `/market/quote`, `/market/spec`, status; dashboard live price panel | `api/routes/system.py`, `frontend/components/LivePrice.tsx` |
| Commission model | Generalised (`per_lot`, `per_million_notional`, `pct_notional`, minimum per side); sizing steps volume down when a minimum makes small sizes dearer; sim broker splits open commission on partial closes | `domain/models.py`, `risk/sizing.py`, `broker/sim.py` |

## Verification

- 93 backend tests pass (`APEX_REQUIRE_DB=1`: CI now **fails** instead of skipping when PostgreSQL is missing).
- New: 18 cTrader unit/integration tests against an in-process fake server speaking the real wire format
  (auth, token refresh + persistence, spec, quotes incl. partial updates and crossed-quote rejection, positions/equity,
  disconnect → reconnect → resubscribe, trading disabled, live refused); 8 end-to-end tests
  (encrypted creds in DB → fake server → paper broker fills at live price with broker commission → API).
- Visual check: dashboard with a moving simulated feed, live calculator using live EUR/USD.

## Bugs found during this part

1. Helper `proto.new(name, ...)` collided with protobuf fields called `name` → positional-only parameter.
2. A structlog keyword (`event=`) crashed the reconnect loop on the first disconnect — **the feed would have died silently in production**. Fixed; one bad message can no longer stop the feed.
3. API tests could be silently skipped when the DB was down → `APEX_REQUIRE_DB=1` in CI.
4. Docker passes unset variables as empty strings → treated as unset.

## Not verified yet (needs the real server — blocked here by network policy)

- Exact interpretation of IC Markets' commission fields and stop distance on the real symbol → `apex ctrader-check` output.
- Real token-exchange response shape (both camelCase and snake_case accepted).
- Behaviour across the daily break and weekend.

## Part B — storage, bars, quality, history, dashboard

| Component | Notes | Where |
|---|---|---|
| Trading calendar | NY 17:00 rollover, Sunday 18:00 open, Friday 17:00 close, daily break; DST-safe (tested both seasons) | `market/sessions.py` |
| Bar builder | 1s→1w at once, mid OHLC, spread avg/max, tick count (**never** shown as volume), closes only when the next period starts (no look-ahead), out-of-order quotes rejected | `market/bars.py` |
| Data-quality score | staleness, robust spread z-score, price jumps, duplicates, out-of-order, future timestamps, market closed, warm-up → score 0–1 + `tradeable` + reasons | `market/quality.py` |
| Storage | `market.ticks` / `market.bars`; TimescaleDB hypertables + tick compression (7 d) when available, plain tables otherwise | migration `…_market_ticks_and_bars.py` |
| Recorder | batched, idempotent, off the quote path; buffers through DB outages | `market/recorder.py` |
| Warm-up | on restart, rebuilds the current day's bars from stored ticks (history until the first live tick, then live) — otherwise the day would "open" at the restart price | `market/feed.py` |
| Bars API | DB + memory + current bar; live source wins over history; a live partial bar keeps the true open/extremes from history | `GET /market/bars`, `GET /market/quality` |
| Dukascopy importer | `.bi5` decoding; **whole-file validation** (plausible price band — catches a wrong point divisor —, spread, time order); resumable; deterministic bar rebuild | `market/dukascopy.py`, `apex import-dukascopy` |
| Dashboard redesign | sticky header with live price, day change, data/broker/trading status, kill switch modal; TradingView Lightweight Charts candles (1m…1d, French locale, OHLC readout); account, data-quality gauge, position sizing, risk limits, positions; responsive; Geist fonts | `frontend/` |

Chart colours: teal up / orange down, validated for colour-blind separation on the dark background (the usual green/red pair fails); up candles hollow, down filled — never colour alone. Changes carry ▲/▼ and a sign.

### Bugs found in part B
1. A restart mid-session made the day bar open at the restart price → warm-up from stored ticks.
2. History and live bars for the same period were mixed arbitrarily → live wins; live partial keeps the historical open.
3. Mobile: chart axis overflowed onto the next card; risk grid too narrow → fixed.

### Known limitations
- The current **week** bar is warmed only from the current day.
- Dukascopy point divisor for XAUUSD (1/1000) is from the documented format; a wrong value is caught by validation, but the first real import must be checked.
- Broker holiday schedules are not yet in the calendar.

## Verification (A + B)
125 backend tests (strict mypy, ruff), 0 npm vulnerabilities; migrations up/down/up and the full suite verified on TimescaleDB 2.17; desktop + mobile visual checks with a simulated live feed (no horizontal overflow, no console errors).
