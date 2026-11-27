# Security Architecture

## 1. Threat model (what we protect against)

| Threat | Impact | Primary controls |
|---|---|---|
| Stolen broker credentials | Account drained / unauthorised trading | Credentials never in repo/frontend/logs; envelope encryption; trading-only (no withdrawal) API scopes where offered; IP allow-listing at broker |
| Unauthorised dashboard access | Someone flips to live or disables risk | Auth + MFA, RBAC, re-auth for dangerous actions, audit log |
| Malicious/compromised dependency | Code execution in trading process | Pinned & hashed deps, `pip-audit`/`npm audit` in CI, minimal images, non-root containers |
| Prompt injection via news text | LLM output tries to trigger trades | LLM outputs are data only, schema-validated, bounded numeric fields; LLMs have no tools that touch trading; risk engine ignores AI for limits |
| Runaway bug (order loop, wrong size) | Large loss | Idempotency keys, per-minute order rate cap, max lot hard cap in risk engine **and** in execution gateway, kill switch |
| Replay/tampering of audit trail | Lost accountability | Hash-chained audit log, append-only DB role |
| Paper/live confusion | Real money traded by test code | Separate processes, credentials only mounted into live container, mode in every record, UI banner |

## 2. Secrets

- Dev: `.env` (git-ignored), `.env.example` committed with placeholders only.
- Prod: Docker secrets or SOPS-encrypted files (age key outside repo); optional HashiCorp Vault later.
- Broker credentials entered via UI are encrypted with a data key (AES-256-GCM) wrapped by a master
  key from the secret store (`core.credentials`). The API never returns them; only the execution
  service can decrypt, and only for `mode_allowed` accounts.
- Secret scanning (gitleaks) in pre-commit and CI.

## 3. Authentication & authorisation

- Password hashing: argon2id. TOTP MFA mandatory for `trader` and `admin` roles.
- Sessions: short-lived JWT access token (15 min) + rotating refresh token in httpOnly, Secure, SameSite=strict cookie.
- Roles: `viewer` (read-only), `researcher` (backtests, models), `trader` (paper control, approve proposals), `admin` (risk config, live activation, credentials).
- Dangerous actions (enable live, change risk config, add credentials, re-arm after kill switch) require re-authentication + MFA and typed confirmation, and are audited with before/after.
- The **kill switch** is available to `trader`+ without re-auth (stopping must be easy; starting must be hard).

## 4. API hardening

- Pydantic validation on every input; strict enums; no raw SQL string building.
- Rate limiting per user/IP (Redis token bucket); stricter on auth endpoints.
- CORS locked to the dashboard origin; CSP headers on frontend.
- Internal services communicate on a private Docker network; only API gateway (and frontend) exposed, behind TLS reverse proxy (Caddy/Traefik).

## 5. Risk configuration immutability

- Risk config is versioned (`core.risk_config`), loaded read-only by the risk engine at start and on authenticated reload.
- No code path from decision/AI modules can write risk config (enforced by DB role grants, not just convention).
- Config hash is attached to every `RiskDecision`.

## 6. Operational

- Containers run as non-root, read-only root FS where possible.
- Daily encrypted DB backups; restore tested.
- Structured logs with secret redaction filter.
