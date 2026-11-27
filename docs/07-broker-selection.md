# Broker Selection — Direct API brokers for XAU/USD (no MetaTrader)

Date: 2026-11-27 · Context: ADR 0005 (dedicated platform, direct broker API) · Account currency: user's choice (ADR 0006)

> Figures come from public broker pages and third-party comparisons, several of which disagree or are
> undated. They are **indicative**. Final numbers must come from the broker's own contract
> specifications and from measuring the live spread on a demo account (Phase 3 tooling).

## 1. Criteria (weighted)

| # | Criterion | Weight | Why |
|---|---|---|---|
| 1 | Official API allowed for automated retail trading, full order lifecycle | Eliminatory | Without it the project cannot exist |
| 2 | Stop-loss attached server-side at entry | Eliminatory | Capital protection if our platform crashes |
| 3 | All-in cost on gold (spread + commission) | 30% | Intraday/scalping edge is mostly eaten by costs |
| 4 | API quality: push streaming, latency, rate limits, stability | 25% | Execution and data reliability |
| 5 | Demo account with identical API | 15% | Mandatory paper/validation phase |
| 6 | Regulation / EU passport for French residents | 15% | Fund safety, legal clarity |
| 7 | Portability (one adapter → many brokers) | 15% | Avoid lock-in |

## 2. Comparison

Cost column = indicative round-trip cost for **1 standard lot (100 oz)**, spread × 100 + commission.
1 cent of spread = $1 per 100 oz.

| Broker (EU entity) | API | Streaming | Documented limits | Indicative gold cost (100 oz round trip) | Demo via API | Notes |
|---|---|---|---|---|---|---|
| **Pepperstone EU** (CySEC, passported) via **cTrader Open API** | Protobuf/JSON over TLS, OAuth2 | Push spot quotes | Per-connection message limits (Open API docs) | Razor: avg spread 0.12 + $7 comm ≈ **$19** (broker-published Aug 2026 avg) · Standard: 0.23 ≈ $23 | Yes | Same adapter works with every cTrader broker. **To verify:** Open API enabled and XAUUSD listed as a *symbol* for French retail accounts |
| **IC Markets EU** via cTrader Open API | Same as above | Push | Same | Raw ≈ 0.08–0.10 + $7 ≈ **$15–17** (sources conflict) | Yes | EU-entity cTrader availability and gold not confirmed |
| **IG** (present in France) | REST + Lightstreamer streaming | Push | 100 trade req/min/account (another IG page says ~40), 30 non-trading req/min/account, **10,000 historical points/week** | Spread from 0.30, no commission ≈ **$30** | Yes | Mature, well-documented, large broker. History allowance is low (irrelevant: we use Dukascopy for history) |
| **Capital.com** (CySEC, EU) | REST + WebSocket | Push (max 40 instruments, ping every 10 min) | **10 req/s**; session creation limited; demo order limits historically 1000/h | Spread ~0.30–0.75 (sources vary), no commission ≈ **$30–75** | Yes (separate demo host) | Simple API. Costs higher and less transparent |
| **Saxo** | OpenAPI REST + WebSocket | Push (deltas up to 3/s) | 120 req/min per session per service group; 10M/day per app | ~0.40 (undated source) ≈ **$40** | Yes (SIM environment) | Bank-grade, very robust API. Higher minimum deposit, wider gold cost |
| **Interactive Brokers (IE)** | TWS API / Web API | Push via Gateway | Market-data line limits | Metals CFD: 0.015% of notional, min $2/order → at ~$4,000/oz ≈ **$60/side** on 100 oz plus spread | Yes (paper account) | Very robust, but needs IB Gateway process running; gold access requires permissions; cost structure poor for scalping |
| Axi | No public retail API confirmed (MT4/MT5 + proprietary platform; **no cTrader**); FIX/REST only via AxiPrime (institutional, unconfirmed) | — | — | Not evaluated | — | **Excluded**: MT-centric (contradicts ADR 0005). Sources conflict on French eligibility: CySEC entity (Solaris EMEA) reportedly not passported to France → French clients routed to offshore SVG entity (weaker protection). Re-evaluate only if Axi publishes a documented retail API |
| OANDA | v20 | Push | 20 streams/IP | — | — | **Excluded**: v20 not offered by OANDA TMS (EU) |

### What the cost means in practice

At the indicative figures, a 0.10 lot (10 oz) trade costs ≈ $1.9 (Pepperstone Razor) vs ≈ $3 (IG) vs
≈ $4 (Saxo). For a scalping trade with a $3.00 stop (≈ 300 cents), cost is ~6–7% of the risk at
Pepperstone Razor and ~10% at IG, 13% at Saxo. **Over hundreds of trades this difference alone can
decide whether a strategy is profitable.** For slower intraday trades (stops of $10–20) the gap matters less.

## 3. Recommendation

1. **Primary: cTrader Open API, with a raw-spread EU-regulated broker (Pepperstone EU first candidate).**
   - Lowest all-in cost of the candidates found.
   - Push-based, modern API, SL/TP attached on order, amend supported, OAuth2.
   - **Portability:** one adapter covers every cTrader broker → we can switch broker without rewriting code.
2. **Secondary / fallback: IG REST + Streaming API.**
   - Strong API and French presence; higher cost; tighter rate limits (fine for a single symbol).
   - Good choice if cTrader+gold verification fails, or as a second adapter for redundancy.
3. **Not recommended initially:** Capital.com (cost), Saxo (cost, deposit), IBKR (fees, gateway dependency) — kept as possible future adapters.

### Verification checklist before committing (demo accounts, Phase 2/3)

- [ ] Account opens for French residents on the EU entity, retail, in the chosen currency
- [ ] Open API application registered and authorised on the account
- [ ] `XAUUSD` present in `ProtoOASymbolsListReq` (not only in the asset list) with tradable status
- [ ] Symbol spec: contract size, digits, min/step/max volume, stops level, commission, swap
- [ ] Market order with SL+TP accepted; amend SL/TP; close; partial close
- [ ] Spread distribution measured over ≥ 2 weeks incl. NFP/CPI/FOMC and daily rollover
- [ ] Reconnect behaviour and message-rate limits measured
- Same checklist executed on IG as fallback.

## 3b. Choosing among cTrader brokers (update 2026-11-27)

| Broker | EU entity for French residents | cTrader commission | Verdict |
|---|---|---|---|
| IC Markets EU | CySEC — **France not offered as country of residence at sign-up (owner check, 2026-11-27)** | $3.00/side ($6 RT) | Excluded (not available to French residents) |
| Pepperstone EU | CySEC (French-language site, serves France) | $3.50/side ($7 RT) | **Selected** — pending demo verification |
| FP Markets | CySEC entity exists | to verify | Reserve |
| FxPro | CySEC / FCA | to verify | Reserve |
| Fusion Markets | No EU entity identified → offshore for French clients | lowest advertised | Excluded (regulation) |
| Skilling, Purple Trading | CySEC | fees reported mixed / no data | Not prioritised |

Decision: **Pepperstone EU (Razor, cTrader)** is the primary broker. Fallbacks: another CySEC cTrader broker accepting France (FP Markets / FxPro, to verify) — same adapter — or IG (separate adapter).

Original method (kept for future comparisons): open demo accounts at finalists; the platform's cTrader adapter (identical for both)
records XAUUSD spread, slippage and fill latency for ≥ 2 weeks; the broker with the lower **measured**
all-in cost and better execution wins. Commission difference is only ~$1 per lot round trip, so the
measured gold spread decides.

Sources: https://www.compareforexbrokers.com/trading-platforms/ctrader/ , https://brokerchooser.com/best-brokers/best-brokers-for-ctrader-in-france , https://www.fxstreet.com/brokers/best/ctrader-brokers

## 4. Sources

- Capital.com API: https://open-api.capital.com/ , https://help.capital.com/hc/en-us/articles/6630830103058-Do-you-have-any-limitations-on-your-API , https://capital.com/api-changelog
- IG API limits: https://net-labs.ig.com/faq.html , https://www.ig.com/uk/trading-platforms/trading-apis/how-to-use-ig-api
- Saxo OpenAPI: https://www.developer.saxo/openapi/learn/rate-limiting , https://developer.saxo/openapi/learn/streaming
- IBKR metals CFDs: https://ibkb.interactivebrokers.com/article/3642 , https://interactivebrokers.ie/fr/trading/products-metals.php
- Pepperstone EU / API: https://pepperstone.com/en-eu/trading-platforms/tools/api-trading
- Gold spread comparisons: https://www.economies.com/investing/commodities/gold/best-gold-trading-broker-in-the-uae-2026-xauusd-silver-and-oil-154 , https://brokerchooser.com/broker-reviews/ig-review/xauusd-spread , https://brokerchooser.com/broker-reviews/capitalcom-review/xauusd-spread
- cTrader Open API: https://help.ctrader.com/open-api
- Axi: https://brokerchooser.com/broker-reviews/axitrader-review/axitrader-ctrader , https://dailyforex.com/forex-brokers/axi-review , https://www.broker-forex.fr/Axi.php , https://comparebroker.io/brokers/is-axi-legal-in-france
