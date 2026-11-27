# Broker API Comparison (XAU/USD)

> Nothing here substitutes for verifying the **specific** broker and account. Every field below must be
> confirmed programmatically by the adapter's `verify()` routine at connect time and stored as a
> `broker_capabilities` record. Symbol specs are read from the API, never hardcoded.

## 1. Options

| | MetaTrader 5 (via `MetaTrader5` Python pkg) | cTrader Open API | OANDA v20 | FIX API |
|---|---|---|---|---|
| Official API | Yes (terminal IPC) | Yes (Protobuf/JSON over TLS) | Yes (REST + HTTP streaming) | Broker-dependent |
| OS | **Windows only** (Linux via Wine bridge, third-party) | Any | Any | Any |
| Market data | Ticks, bars, `copy_ticks_range` history; `market_book_*` depth if broker publishes | Spot quotes push, trendbars, tick data history | Pricing stream, candles (S5..M) | Yes |
| Push vs poll | **Poll** (or terminal-side EA pushing via socket) | Push | Push (stream) | Push |
| Submit orders | Yes (`order_send`) | Yes | Yes | Yes |
| Modify orders/SL/TP | Yes (`TRADE_ACTION_SLTP`, `MODIFY`) | Yes (amend) | Yes (dependent orders) | Yes |
| SL/TP attached on entry | Yes | Yes | Yes (`stopLossOnFill`, `takeProfitOnFill`) | Depends |
| Balance/equity/margin | Yes (`account_info`) | Yes (trader + margin events) | Yes (account summary) | Partly |
| Symbol spec (contract size, min/step/max lot, tick size/value, stops level) | Yes (`symbol_info`) | Yes (symbol entity) | Yes (instrument: `tradeUnitsPrecision`, `minimumTradeSize`, `marginRate`) — units, not lots | Often out-of-band |
| Rate limits | No documented limit; terminal-bound | Documented per-connection message limits | REST limits per development guide (verify current value); 20 streams/IP | Session-based |
| Multiple accounts | One per terminal | Many per app | Many per token | Per session |
| Typical availability | Most retail CFD brokers | Many ECN-style brokers | OANDA only; not all divisions | Pro/institutional |
| Main risk | Windows dependency, polling, broker-specific symbol suffixes, unaudited Linux bridges | Gold symbol may be missing for some brokers; OAuth app approval | Jurisdiction/division eligibility; unit-based sizing | Access cost & minimums |

## 2. Decision: direct broker API, no MetaTrader (ADR 0005)

The owner chose to build a dedicated platform connected **directly** to the broker's API.
MT5 is out of scope (column kept above for reference only). Eligible brokers must expose an official
API with streaming prices, account/margin data, orders with attached SL, modifications and position
monitoring for XAU/USD.

| Broker | API | Protocol | Gold on API | Notes / to verify |
|---|---|---|---|---|
| IG | IG REST + Streaming API | REST + Lightstreamer streaming | Gold CFD/spread bet (market "EPIC") | Regulated in France (via EU entity) — verify API access on CFD account, rate limits |
| Capital.com | Capital.com public API | REST + WebSocket | Gold CFD | Verify API enabled for EU entity, rate/session limits |
| Saxo | Saxo OpenAPI | REST + WebSocket streaming | XAUUSD (FX spot metal) / gold CFD | App registration, OAuth; minimum deposit |
| Interactive Brokers | TWS API / Web API | Socket (TWS/Gateway) or REST | Spot gold / gold CFD depending on entity; GC futures | Requires IB Gateway process (not MT, but a local gateway) |
| cTrader brokers | cTrader Open API | Protobuf/JSON over TLS | Broker-dependent | Our platform uses the API only, no cTrader UI needed |
| OANDA | v20 REST + streaming | REST + HTTP stream | XAU_USD | **Not available** for OANDA TMS (EU/Poland) and OANDA Global Markets |

Selection criteria, in order: (1) API officially supports automated trading for the account type;
(2) SL attached server-side on entry; (3) streaming quotes; (4) all-in gold cost (spread + commission +
swap); (5) demo account with identical API; (6) rate limits; (7) regulator.

## 3. XAU/USD specifics to verify (per broker)

| Field | Why it matters | Typical (do **not** assume) |
|---|---|---|
| Symbol name | `XAUUSD`, `GOLD`, `XAUUSD.r`, `XAU_USD` | — |
| Contract size | Sizing | 100 oz/lot common; some brokers 1 oz or 10 oz |
| Price digits / tick size | SL rounding | 0.01 (2 digits) common; some 3 digits |
| Min / step / max volume | Rounding & rejection | 0.01 / 0.01 / 50–100 |
| Stops level / freeze level | SL too close → reject | Varies, can widen at news |
| Leverage | Margin | ESMA retail: max **20:1** on gold (5% margin); pro/offshore higher |
| Margin call / stop-out | Forced liquidation | ESMA retail close-out at 50% margin level |
| Spread | Costs | Floating; widens heavily at rollover (~17:00 NY) and data releases |
| Commission | Costs | Per lot per side on raw/ECN accounts |
| Swap long/short | Overnight cost | Usually negative both sides recently; triple on Wednesday |
| Trading hours | Session rules | ~Sun 23:00 – Fri 22:00 UTC with daily 1h break; broker-specific |
| Execution type | Slippage model | Market execution vs instant (requotes) |
| Order types | Strategy capability | Market, limit, stop, stop-limit, OCO? |
| Hedging vs netting account | Position model | MT5 can be either |

## 4. Adapter verification routine (to implement in Phase 2/10)

On connect, the adapter runs `verify()` and records results:
1. Authenticate; read account currency, leverage, hedging/netting mode, trade permission flags.
2. Resolve gold symbol; read full spec; sanity-check (contract size > 0, step divides min, etc.).
3. Subscribe to quotes; measure update rate, spread distribution, clock offset vs server time.
4. (Demo only) place a min-size limit order far from market, modify SL/TP, cancel → capability test.
5. Persist a `broker_capabilities` snapshot; risk engine refuses to run if critical capabilities missing.

## 5. Information needed from you

- Broker name and regulating entity
- Platform(s) offered (MT5 / cTrader / proprietary / FIX)
- Account currency, type (retail/pro), hedging or netting
- Whether a demo account with identical specs is available
- Whether you can host a Windows VPS (only needed for MT5)
