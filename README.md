# APEX GOLD AI

Research-driven, risk-first automated trading platform for XAU/USD.

> **Status: Phase 1 (research & architecture) complete. No trading code yet.**
> The system does not and cannot guarantee profits. Any edge must be demonstrated out-of-sample,
> then in paper trading, before live use. Default mode is PAPER.

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
| [ADRs](docs/adr/) | Key architectural decisions |
