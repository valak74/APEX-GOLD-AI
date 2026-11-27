# Phase 2 Report — Foundation

Status: **complete** (2026-11-27). Mode: PAPER only. LIVE is refused by the code.

## Delivered

| Area | What exists | Where |
|---|---|---|
| Domain | `Money` (currency-safe, Decimal), `Quote`, `SymbolSpec`, `AccountInfo`, orders/positions/trades | `backend/src/apex/core`, `domain` |
| FX | Explicit converter, conservative side, stale/missing rate ⇒ fail closed (ADR 0006) | `core/fx.py` |
| **Position sizing** | Broker-spec-based XAU/USD sizing in any account currency; costs included; floor to step; min/max/margin checks; hard 2 % ceiling | `risk/sizing.py` |
| Risk config | Versioned, immutable, ordered limits, hashed; admin + password + TOTP only; one active version enforced by DB | `risk/config.py`, `PUT /risk/config` |
| Broker abstraction | `BrokerAdapter` + `Capabilities`; `SimBroker` for PAPER/SIMULATION (bid/ask fills, adverse slippage, SL gap fills, TP, commissions, partial close, stop-out, idempotent `client_order_id`); LIVE refused | `broker/` |
| Auth & security | argon2id, JWT in httpOnly SameSite=strict cookie, TOTP 2FA, roles (viewer/researcher/trader/admin), login rate limit, re-auth for dangerous actions, security headers | `security/`, `api/` |
| Credentials | Write-only endpoint, AES-256-GCM envelope encryption bound to record, never returned or logged | `security/crypto.py`, `api/routes/brokers.py` |
| Audit | Hash-chained log, append-only enforced by DB trigger, verifiable | `db/audit.py`, migration |
| Kill switch | Trader can engage (no re-auth), closes all positions, disables trading; only admin+2FA can re-arm; re-arm never re-enables trading | `api/routes/system.py` |
| Database | PostgreSQL schema `core` via Alembic (upgrade/downgrade tested) | `backend/migrations` |
| Dashboard | Next.js 16 / React 19 / Tailwind 4, French UI: login, mode banner, account, positions, system status, risk limits, sizing calculator, kill switch. Unbuilt modules are labelled as such — no fake data | `frontend/` |
| Infra | Dockerfiles (non-root, read-only API FS), Compose (TimescaleDB, Redis, migrate, api, web; only dashboard exposed on localhost), `.env.example`, CI (lint, strict types, migrations, tests, npm audit, build) | root |

## Verification performed

- `pytest`: **63 passed** — 23 sizing (incl. 400 property-based cases: risk never exceeds budget), 22 paper/sim broker, 7 security, 11 API integration tests on real PostgreSQL.
- `ruff` clean, `mypy --strict` clean (33 files), `tsc` clean, `npm audit`: 0 vulnerabilities.
- Alembic upgrade → downgrade → upgrade.
- Full Docker Compose stack built and run: migrations applied, API healthy, dashboard proxy, login cookie flags, 401 without session, containers non-root, API root FS read-only.
- Headless browser: login → dashboard → sizing calculator (10 000 EUR, entry 4000, SL 3995, 1 USD = 0.95 EUR → 0.05 lot, real risk 24.08 EUR ≤ 25 EUR budget) → kill switch engaged in UI.

## Bugs found and fixed during verification

1. Login rejected reserved-domain identifiers (`*.local`) the CLI accepted → login now treats email as an identifier; regression test added.
2. Next.js `rewrites()` freeze `API_URL` at build time in standalone images → replaced by a runtime proxy route.
3. Pinned Next.js 15.5.4 had a published CVE → upgraded to 16.4.6.

## Known limitations (honest list)

- **No market data yet**: paper broker has no price feed, so it cannot fill orders outside tests (phase 3).
- Paper `XAUUSD` spec is a labelled placeholder (100 oz, 0.01 lot, $3.50/side, 5 % margin) until read from the broker.
- Login rate limiter and kill-switch state are in-process (single API instance); move to Redis when scaled out. Kill-switch state is not yet persisted across restarts — on restart trading starts **disabled**, which is the safe default.
- Redis is deployed but not used yet (event bus arrives with market data).
- No user-management UI (CLI `apex create-user`); no MFA enrolment UI yet (API endpoints exist).
- Docker builds in this cloud sandbox needed a local-only CA workaround (not committed); on a normal machine `docker compose up --build` works as-is.

## Next (phase 3)

cTrader Open API adapter (read-only first: auth, symbol spec, account, spot quotes) → live paper feed → tick storage in TimescaleDB, bar builder, data-quality score. Blocked on: Spotware approval of the Open API application.
