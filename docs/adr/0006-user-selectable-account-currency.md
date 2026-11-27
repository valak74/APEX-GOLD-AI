# ADR 0006 — Account currency is chosen by the user

Status: Accepted (2026-11-27) — owner decision

**Context.** Users may hold broker accounts in EUR, USD, GBP, CHF or other currencies. XAU/USD P&L is
in USD (quote currency); risk budgets, limits and the dashboard must be in the account currency.

**Decision.**
- `account_currency` is a per-broker-account setting chosen by the user (limited to what the broker
  supports) and **verified against the broker's reported account currency at connect time**; a
  mismatch blocks trading.
- All money values (balance, equity, margin, risk budget, P&L, limits) are stored and displayed in the
  account currency, with the currency code alongside every amount (`Money(amount: Decimal, ccy: str)`).
  Arithmetic between different currencies raises an error — conversion must be explicit.
- Quote→account conversion uses a live rate from the broker feed (e.g. EURUSD, GBPUSD, USDCHF,
  USDJPY), resolved automatically (direct or inverse pair). For risk sizing the **conservative side**
  of the bid/ask is used. If the conversion rate is stale or missing → no trade (fail-closed).
- Where the broker returns its own tick value / margin in account currency, the engine cross-checks
  its own computation against it and rejects on divergence > tolerance.
- Percent-based limits (risk %, daily loss %) are currency-independent; absolute limits are stored with
  their currency and are re-entered by the user if the account currency changes.

**Consequences.** Sizing tests cover USD, EUR, GBP, CHF, JPY accounts and inverse-pair conversion.
