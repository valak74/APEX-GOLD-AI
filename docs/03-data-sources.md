# Data Source Comparison

Legend: ✅ suitable · ⚠️ usable with caveats · ❌ not suitable. Prices change often; verify before purchase.

## 1. XAU/USD prices

| Source | Type | History | Real-time | Cost | Verdict |
|---|---|---|---|---|---|
| **Broker feed** (MT5/cTrader/OANDA) | Bid/ask ticks of *your* execution venue | Short (months–few years, broker-dependent) | ✅ | Included | ✅ **Source of truth for live trading & cost calibration** |
| **Dukascopy** | Bid/ask ticks (Swiss bank/ECN) | Many years (start date to be measured) | Delayed/streaming via JForex | Free (export tool; community downloaders) | ✅ **Primary long-horizon research dataset** |
| TrueFX / HistData | FX ticks/1m | Long | — | Free | ⚠️ Gold coverage limited/inconsistent |
| Twelve Data / Polygon-style vendors | Aggregated quotes | Varies | ✅ | Paid tiers | ⚠️ Not your execution price; useful as independent cross-check for DQ |
| **Databento CME GLBX.MDP3** (`GC`, `MGC`) | Exchange futures: trades, MBP, **MBO** | From June 2010 | ✅ live feed | Usage-based (from ~$0.50/GB historical); live licence extra | ✅ **Only real source for gold order flow / volume** |

**Spot vs futures**: GC trades at a basis to spot (carry); intraday correlation is very high but
level-based signals (e.g. order-flow at a price) must be mapped via the live basis. Order-flow features
derived from GC are *proxy* features and are labelled as such.

**Data-quality checks applied to every source**: monotonic timestamps, duplicate ticks, bid > ask,
spread outliers (rolling robust z-score), price jumps vs ATR, gaps vs expected session calendar,
staleness (no tick for N seconds during open session), cross-source divergence (broker vs Dukascopy
vs GC basis-adjusted).

## 2. Cross-asset / macro series

| Series | Source (live display) | Source (storable / ML training) | Notes |
|---|---|---|---|
| DXY | Broker CFD (`USDX`/`DXY`) or ICE via vendor | Vendor licence | Broker CFD acceptable for intraday features |
| US 10y nominal | Broker bond CFD, vendor | US Treasury daily yield curve (public) | |
| US 10y real yield (TIPS) | FRED `DFII10` | **US Treasury real yield curve** (public domain) | FRED terms forbid caching & ML training |
| EUR/USD, USD/JPY | Broker | Broker / Dukascopy | |
| Silver, Oil, S&P 500, Nasdaq | Broker CFDs | Dukascopy / vendor | |
| VIX | Vendor (CBOE data licence) | Vendor | Often delayed on free tiers |
| Bitcoin | Exchange public APIs (Coinbase/Kraken) | Same | |
| Gold ETF flows, CB purchases | World Gold Council publications | WGC (check terms) | Low frequency; macro context only |

## 3. Economic calendar

| Source | Pros | Cons | Verdict |
|---|---|---|---|
| Official agency schedules (BLS, BEA, Fed FOMC calendar, ECB, BoE, BoJ) | Authoritative, free | Multiple formats; no consensus forecasts | ✅ Baseline for **lockouts** (only need times + importance) |
| Trading Economics API | Broad coverage, forecasts, actuals | Paid; pricing custom/unclear | ✅ Best paid option if budget allows |
| FMP economic calendar | Cheaper | Plan tier for calendar unconfirmed | ⚠️ Evaluate trial |
| Finnhub | Good API | Calendar is premium | ⚠️ |
| Scraped ForexFactory/Investing.com | Free | ToS risk, brittle | ❌ for production |

Consensus forecasts (needed to compute *surprise*) are only available from paid vendors.

## 4. News

| Tier | Source | Latency (indicative) | Cost | Use |
|---|---|---|---|---|
| 1 Official | Fed/ECB/BoE/BoJ/Treasury/BLS/BEA press releases & RSS | Seconds–minutes (polling) | Free | Highest trust; data releases & statements |
| 2 Pro wires | Reuters, Bloomberg, Dow Jones | Sub-second | Enterprise licence (very expensive) | Out of scope initially |
| 3 Financial news APIs | Benzinga, Finnhub news, Polygon news, Marketaux, etc. | Seconds–minutes | ~$50–$500+/mo | Main automated news stream |
| 4 Aggregators | GDELT | ~15 min batches | Free | Geopolitical risk index, not trading triggers |
| 5 Social | X API, Telegram channels | Fast but unverified | X API expensive | Only as corroborated signal |

Design consequence: at retail news latency we are **later than institutions** on every headline.
News is therefore used for (1) risk lockouts, (2) regime context, (3) post-event confirmation
strategies with measured edge — not for racing headlines.

## 5. Budget scenarios (monthly, indicative)

| Scenario | Components | Est. cost |
|---|---|---|
| **Bootstrap (recommended start)** | Broker feed, Dukascopy, official calendars/RSS, FRED (display only), GDELT | ~$0 + VPS |
| Standard | + paid calendar with consensus (TE/FMP) + one news API | ~$100–$400 |
| Order-flow research | + Databento GC historical (one-off, size-dependent) + live CME licence | +$ variable; get quote via `get_cost` |
| Institutional | + pro news wire | $$$$ — not justified until edge proven |
