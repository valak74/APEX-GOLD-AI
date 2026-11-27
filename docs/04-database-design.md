# Database Design

PostgreSQL 16 + TimescaleDB. Schemas separate concerns and permissions:

| Schema | Content | Write access |
|---|---|---|
| `core` | users, roles, broker_accounts, credential refs, config, audit_log | api |
| `market` | ticks, bars, symbol_specs, data_quality, cross-asset series | market-data |
| `intel` | news, economic_events, agent_outputs | intel, decision |
| `trading` | proposals, risk_decisions, orders, fills, positions, trades, risk_events, mode_changes | decision, risk, execution |
| `research` | strategies, backtests, backtest_trades, models, model_versions, datasets, metrics | worker |
| `ops` | alerts, health_checks | all |

Money columns: `NUMERIC(20,8)`. Prices: `NUMERIC(18,6)` in OLTP tables; ticks use scaled `BIGINT`
(price × 10^digits) for compactness. All timestamps `timestamptz` UTC.

## 1. Market data (hypertables)

```sql
CREATE TABLE market.ticks (
  source      smallint     NOT NULL,          -- FK market.sources
  symbol_id   smallint     NOT NULL,
  ts          timestamptz  NOT NULL,          -- event time UTC
  ts_ns       bigint       NOT NULL,          -- ns precision
  recv_ts     timestamptz,                    -- our receive time (latency)
  bid         bigint       NOT NULL,          -- scaled
  ask         bigint       NOT NULL,
  bid_vol     real, ask_vol real,              -- null when not provided
  flags       smallint     NOT NULL DEFAULT 0  -- dq flags bitmask
);
SELECT create_hypertable('market.ticks', 'ts', chunk_time_interval => interval '1 day');
CREATE UNIQUE INDEX ON market.ticks (source, symbol_id, ts_ns, bid, ask);   -- dedupe
ALTER TABLE market.ticks SET (timescaledb.compress,
  timescaledb.compress_segmentby = 'source, symbol_id', timescaledb.compress_orderby = 'ts');
-- compress chunks older than 7 days; ~10-20x reduction typical for tick data

CREATE TABLE market.bars (
  source smallint, symbol_id smallint, timeframe text, ts timestamptz,  -- bar open time
  open numeric(18,6), high numeric(18,6), low numeric(18,6), close numeric(18,6),  -- mid
  spread_avg numeric(10,6), spread_max numeric(10,6),
  tick_count int, volume numeric,               -- volume NULL unless real volume
  complete boolean NOT NULL, dq_score real,
  PRIMARY KEY (source, symbol_id, timeframe, ts)
);  -- hypertable; 1m built from ticks, higher TFs via continuous aggregates

CREATE TABLE market.symbol_specs (           -- versioned snapshots from broker
  id bigserial PK, broker_account_id, symbol, captured_at,
  contract_size, digits, tick_size, tick_value, volume_min, volume_step, volume_max,
  stops_level, margin_rate, swap_long, swap_short, commission, trade_mode, raw jsonb);

CREATE TABLE market.data_quality (ts, source, symbol_id, score, stale_ms, gap_count,
  outlier_count, dup_count, spread_z, details jsonb);   -- hypertable
CREATE TABLE market.series (ts, series_code, value, source, licence_tag);  -- cross-asset/macro
```

`licence_tag` marks series that may not be persisted long-term or used for training (e.g. `fred_display_only`);
a retention job purges them and the training pipeline refuses them.

## 2. Intelligence

```sql
intel.news(id, source, source_tier, url, published_at, ingested_at, headline, body_hash,
           dedupe_cluster_id, is_official, language, raw jsonb)   -- body stored only if licence permits
intel.news_assessments(news_id, model_version, event_type, novelty, verified, gold_direction,
           impact 0-100, confidence 0-100, urgency, horizon, priced_in_est, summary, created_at)
intel.economic_events(id, source, country, event_code, title, importance, scheduled_at,
           previous, forecast, actual, revised, surprise_z, released_at)
intel.agent_outputs(id, agent, agent_version, ts, ttl_s, symbol, payload jsonb, schema_version,
           input_refs jsonb)          -- hypertable; every agent opinion is persisted
intel.market_regimes(ts, timeframe, regime, probabilities jsonb, model_version)
```

## 3. Trading (the audit trail)

```sql
trading.proposals(id uuid PK, created_at, mode, strategy_id, strategy_version, side, entry, sl,
           tps jsonb, p_win, ev_r, confidence, regime, dq, reasons jsonb, warnings jsonb,
           invalidations jsonb, evidence jsonb, market_snapshot_ref)
trading.risk_decisions(proposal_id PK/FK, approved, rejection_codes text[], volume, risk_amount,
           risk_pct, margin_required, checks jsonb, config_hash, decided_at)
trading.orders(id uuid, client_order_id UNIQUE,  -- idempotency key
           proposal_id, mode, broker_account_id, broker_order_id, type, side, volume, price, sl, tp,
           status, submitted_at, acked_at, latency_ms, reject_reason)
trading.fills(id, order_id, broker_fill_id UNIQUE, ts, price, volume, commission, slippage)
trading.positions(id, mode, broker_position_id, side, volume, open_price, sl, tp, opened_at,
           closed_at, status)
trading.position_actions(id, position_id, ts, action, old_sl, new_sl, old_tp, new_tp, volume, reason, rule_id)
trading.trades(id, position_id, mode, strategy_id, entry/exit ts & price, gross_pnl, commission, swap,
           net_pnl, r_multiple, mfe, mae, exit_reason, holding_s, regime_at_entry, session)
trading.trade_reviews(trade_id, reviewer (rule|model|human), followed_rules, entry_quality,
           exit_quality, execution_quality, regime_correct, notes jsonb)
trading.risk_events(id, ts, type, severity, details, action_taken)   -- limit hits, lockouts, kill switch
trading.mode_changes(id, ts, user_id, from_mode, to_mode, checks jsonb, confirmation_text)
```

`mode` is a NOT NULL enum on every trading table; live and paper rows never mix in queries because
all views filter by mode explicitly. Row-level security can additionally restrict the paper process
role from writing `mode='live'` rows.

## 4. Research & ML

```sql
research.strategies(id, name, version, params jsonb, code_hash, status: draft|validated|paper|live|retired)
research.backtests(id, strategy_id, dataset_id, config jsonb, cost_model jsonb, period, seed,
           code_hash, started_at, finished_at, status)
research.backtest_trades(...)             -- same shape as trading.trades
research.backtest_metrics(backtest_id, segment (all|year|regime|session|fold), metrics jsonb)
research.datasets(id, name, sources jsonb, period, row_count, content_hash, licence_tags)
research.models(id, name, task) ; research.model_versions(id, model_id, version, dataset_id, features jsonb,
           train_metrics, val_metrics, oos_metrics, artifact_uri, status: candidate|validated|paper|live|retired,
           approved_by, deployed_at, rolled_back_at)
```

## 5. Core / ops

```sql
core.users(id, email, password_hash (argon2id), mfa_secret_enc, role, created_at, disabled)
core.broker_accounts(id, broker, platform, account_ref, mode_allowed, currency, credential_ref, created_at)
core.credentials(id, kind, ciphertext, key_id, created_at, rotated_at)   -- envelope-encrypted; never returned by API
core.risk_config(id, version, payload jsonb, created_by, created_at, active)  -- immutable versions
core.audit_log(id, ts, actor, action, target, before jsonb, after jsonb, ip, hash_prev, hash)  -- hash-chained
ops.alerts(id, ts, type, severity, payload, channels, delivered jsonb, acked_by)
ops.health(ts, component, status, latency_ms, details)
```

## 6. Retention

| Data | Hot | Compressed | Archive |
|---|---|---|---|
| Ticks | 7 days | 2 years | Parquet on object storage indefinitely |
| 1m bars | 90 days | forever | — |
| Agent outputs | 30 days | 2 years | Parquet |
| Trading/audit | forever, uncompressed | — | backups |
