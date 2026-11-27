# ADR 0003 — No fabricated order flow on spot gold

Status: Accepted (2026-11-27)

**Context.** Spot XAU/USD is OTC; broker "volume" is tick count. Delta, footprint and depth computed from it would be fiction.

**Decision.** Order-flow module is disabled for spot feeds. If enabled, it uses COMEX GC/MGC exchange data (e.g. Databento MDP3), features are labelled `proxy_futures`, and they must prove OOS value before use.

**Consequences.** Honest feature set; optional cost for futures data.
