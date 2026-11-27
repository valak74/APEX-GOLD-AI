"""Hash-chained audit log. Appends are serialised with a transaction-scoped advisory lock."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apex.db.models import AuditLog

GENESIS = "0" * 64
_LOCK_KEY = 0x41504558  # "APEX"


def _digest(prev: str, actor: str, action: str, target: str | None, details: dict[str, Any]) -> str:
    body = json.dumps(
        {"prev": prev, "actor": actor, "action": action, "target": target, "details": details},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


async def append_audit(
    session: AsyncSession,
    actor: str,
    action: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    """Append an entry within the caller's transaction (caller commits)."""
    details = details or {}
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
    prev = (await session.execute(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))).scalar()
    prev = prev or GENESIS
    entry = AuditLog(
        actor=actor,
        action=action,
        target=target,
        details=details,
        ip=ip,
        hash_prev=prev,
        hash=_digest(prev, actor, action, target, details),
    )
    session.add(entry)
    await session.flush()
    return entry


async def verify_chain(session: AsyncSession) -> bool:
    prev = GENESIS
    for row in (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars():
        if row.hash_prev != prev or row.hash != _digest(prev, row.actor, row.action, row.target, row.details):
            return False
        prev = row.hash
    return True
