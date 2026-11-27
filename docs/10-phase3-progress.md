# Phase 3 — Progress report (part A: cTrader connectivity)

Status: **part A complete and tested against a protocol simulator; awaiting first run on the real demo server.**

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

## Next (part B)

Tick persistence in TimescaleDB (hypertable + compression), bar builder 1s→1W, data-quality score
(staleness, gaps, spread outliers, cross-checks), Dukascopy historical importer, candlestick chart.
