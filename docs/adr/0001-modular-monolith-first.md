# ADR 0001 — Modular monolith with process separation, not microservices on day one

Status: Accepted (2026-11-27)

**Context.** The spec lists ~8 services. Building and operating them as independent deployables from the start multiplies infrastructure work before any edge is proven.

**Decision.** One Python package (`apex`) with strict module boundaries (`market`, `analysis`, `intel`, `decision`, `risk`, `execution`, `api`, `research`). Modules communicate only through typed contracts and Redis Streams. Docker Compose runs them as separate processes (risk+execution isolated from api and intel).

**Consequences.** Low overhead now; services can be split later without changing contracts. Import-linter rules in CI enforce boundaries (e.g. `decision` may not import `execution`).
