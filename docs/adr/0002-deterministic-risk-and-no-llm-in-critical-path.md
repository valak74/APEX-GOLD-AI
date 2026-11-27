# ADR 0002 — Deterministic risk engine; LLMs off the critical path

Status: Accepted (2026-11-27)

**Context.** LLM latency (hundreds of ms to seconds), non-determinism and prompt-injection exposure are incompatible with order decisions and risk limits.

**Decision.** The risk engine is pure, deterministic Python with `Decimal` math and is the only component that can approve an order. LLM agents run asynchronously, produce schema-validated outputs with TTLs, and can only lower confidence / add vetoes or bounded evidence. No LLM has tools that touch orders or configuration.

**Consequences.** Reproducible decisions; LLM outage degrades gracefully (fewer trades, never unsafe trades).
