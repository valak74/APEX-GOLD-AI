# APEX GOLD AI

Research-driven, risk-first automated trading platform for XAU/USD.

> **Status: Phase 3 part A — live XAU/USD prices from a cTrader DEMO account (read-only); orders are simulated (PAPER).**
> The system does not and cannot guarantee profits. Any edge must be demonstrated out-of-sample,
> then in paper trading, before live use. LIVE mode is refused by the code in this build.

## Quick start (Docker)

```bash
cp .env.example .env
# generate the two secrets and paste them into .env:
python3 -c "import secrets,base64,os;print('APEX_JWT_SECRET='+secrets.token_urlsafe(48));print('APEX_MASTER_KEY='+base64.b64encode(os.urandom(32)).decode())"
docker compose up -d --build
docker compose exec api apex create-user --email you@example.com --role admin
# open http://localhost:3000
```

## Development

```bash
cd backend && uv venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q            # API tests need PostgreSQL (APEX_TEST_DATABASE_URL), skipped otherwise
.venv/bin/ruff check src tests && .venv/bin/mypy src
cd ../frontend && npm ci && npm run dev   # API_URL defaults to http://localhost:8000
```

## Layout

| Path | Content |
|---|---|
| `backend/src/apex/core` | config, logging (secret redaction), `Money`, FX conversion |
| `backend/src/apex/risk` | position sizing, risk configuration |
| `backend/src/apex/broker` | `BrokerAdapter` interface, paper/simulation broker, mode-guarded factory, `ctrader/` read-only adapter |
| `backend/src/apex/market` | market data feed supervisor |
| `backend/src/apex/security` | passwords, JWT/TOTP, envelope encryption |
| `backend/src/apex/api` | FastAPI app and routes |
| `backend/migrations` | Alembic migrations |
| `frontend/` | Next.js dashboard (French UI) |

## Documentation

| Doc | Content |
|---|---|
| [Phase 1 report](docs/00-phase1-report.md) | Environment, findings, feasibility, risks, recommended strategy |
| [Architecture](docs/01-architecture.md) | Components, contracts, sizing formula, fusion, failure behaviour |
| [Broker API comparison](docs/02-broker-api-comparison.md) | MT5 / cTrader / OANDA / FIX, verification checklist |
| [Data sources](docs/03-data-sources.md) | Prices, order flow, macro, calendar, news, budgets |
| [Database design](docs/04-database-design.md) | Schemas, hypertables, retention |
| [Security](docs/05-security-architecture.md) | Threat model, secrets, auth, immutability of risk config |
| [Roadmap](docs/06-roadmap.md) | Phases, exit gates, strategy validation criteria, blockers |
| [Broker selection](docs/07-broker-selection.md) | Direct-API broker comparison & recommendation |
| [Phase 2 report](docs/08-phase2-report.md) | What was built, how it was verified, known limitations |
| [Guide cTrader (FR)](docs/09-guide-connexion-ctrader.md) | Connecter votre compte démo IC Markets, pas à pas |
| [Phase 3 progress](docs/10-phase3-progress.md) | cTrader connectivity: delivered, verified, pending |
| [ADRs](docs/adr/) | Key architectural decisions |
