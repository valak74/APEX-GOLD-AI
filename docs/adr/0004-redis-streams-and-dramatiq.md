# ADR 0004 — Redis Streams for events, Dramatiq for jobs, TimescaleDB for time series

Status: Accepted (2026-11-27)

**Decision.** Redis Streams (consumer groups, replay) for real-time events; Dramatiq for long-running jobs (backtests, training); PostgreSQL+TimescaleDB for all persistent data. Kafka is unnecessary at single-symbol scale.

**Consequences.** Two stateful dependencies (Postgres, Redis) instead of four. Revisit if throughput or multi-node needs grow.
