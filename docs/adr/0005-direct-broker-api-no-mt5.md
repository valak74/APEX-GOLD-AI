# ADR 0005 — Dedicated platform connected directly to the broker API (no MetaTrader)

Status: Accepted (2026-11-27) — owner decision

**Context.** The owner does not want to depend on MetaTrader 5. APEX GOLD AI is its own platform
(backend + dashboard); the broker is only an execution and account-data venue.

**Decision.**
- No MT4/MT5 terminal, bridge, EA or Wine anywhere in the stack.
- Live execution goes through a broker's **official programmatic API** (REST + WebSocket/streaming, or FIX).
- Only brokers whose API supports, for XAU/USD: streaming prices, account balance/equity/margin,
  orders with attached stop-loss, order/position modification, and position monitoring are eligible.
- The `BrokerAdapter` interface stays broker-agnostic; one adapter is written for the chosen broker.

**Consequences.**
- Brokers that are MT-only (no public API) are excluded, whatever their spreads.
- The platform's own UI replaces the MT terminal for monitoring and manual control.
- Candidate brokers with documented public APIs (eligibility for the owner's jurisdiction and gold
  availability on the API to be verified): IG (REST + streaming), Capital.com (REST + WebSocket),
  Saxo (OpenAPI), Interactive Brokers (TWS / Web API), cTrader-based brokers (Open API), OANDA v20
  (not available to every OANDA entity — the Polish entity serving many EU clients is excluded).
