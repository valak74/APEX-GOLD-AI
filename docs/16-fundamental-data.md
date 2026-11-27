# Fundamental data and cost calibration

Status: implemented and tested with mocked API responses (2026-11-27). The real data arrives when the owner sets
the FRED key and runs `apex macro-sync`, or lets the background synchronisation run.

## Sources (free, official)

| Data | Source | Frequency | Considered known from (point-in-time) |
|---|---|---|---|
| Real 10-year yield (DFII10), nominal 10-year and 2-year yields (DGS10, DGS2), 10-year inflation expectations (T10YIE), effective fed funds rate (DFF) | FRED, St. Louis Fed (free key) | daily | next business day, 17:00 New York |
| Broad dollar index (DTWEXBGS) | FRED, from the Fed's H.10 release | daily, published weekly | the Monday after the observation's week |
| Managed-money net position and open interest in COMEX gold | CFTC, Commitments of Traders (public API, no key) | weekly (Tuesday) | the following Monday (absorbs holiday delays) |
| Dates of NFP, CPI, PCE, GDP, FOMC, PPI, retail sales and JOLTS (history and upcoming) | FRED release dates | — | public in advance |

Release times follow the official conventions: 08:30 New York for most data, 10:00 for JOLTS, 14:00 for the FOMC.
FRED release names are resolved at sync time. A name that is not found is **reported** (for example, if the FOMC
is not exposed under the expected name), never invented. Those dates can then be imported with
`apex calendar-import`.

**No look-ahead.** Every value stores `available_at`. Analyses, diagnostics and backtests use only values whose
`available_at` is at or before the moment being studied.

## Uses

- **Lockouts.** High-impact releases block new entries live and in backtests, using the risk config's before/after
  window.
- **Diagnostics.** These conditions are added, then tested and corrected like the others:
  - real yields over 5 days: rising / falling / stable (±5 bp);
  - dollar over 5 days: rising / falling / stable (±0.3 %);
  - speculators (COT): their percentile over 3 years, computed from past data only, classified as heavily long /
    lightly long / neutral;
  - release day: yes or no.
- **Dashboard.** The "Contexte macro" card shows each value with its change and its date. It adds a short
  reading of general historical tendencies, explicitly presented as context and not as a signal.

## Cost calibration

- The spreads recorded live from cTrader (IC Markets) are compared hour by hour with Dukascopy's.
- After one week of live data (2 000 5m bars), the weighted ratio rescales Dukascopy spreads in backtests and
  diagnostics. Each report states which calibration it used.
- The broker's contract spec (real commission) is saved at the first cTrader connection. The CLI tools reuse it
  and warn when they fall back to the placeholder.

## Not included, and why

- **X (Twitter).**
  - **Cost:** the API is paid per use. Reading 1 000 posts costs about $5, and reading the full history needed
    for a backtest would cost far more.
  - **Bot-side limits:** news on X is fast but noisy, sometimes manipulated, and professional systems already
    react within milliseconds.
  - **Cannot be validated:** without history it cannot be measured, so it would be an unvalidated input.
  - **Possible later role:** a risk filter that pauses trading on unexpected, sudden news. It would never be a
    buy/sell signal.
- **Analyst consensus, and news feeds.** These are paid sources, outside the current budget.
- **GLD ETF holdings and central-bank purchases.** These are not wired in: no stable official API, or monthly data
  of little use intraday.
