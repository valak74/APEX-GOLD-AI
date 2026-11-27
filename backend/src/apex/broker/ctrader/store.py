"""Persist and load cTrader credentials (envelope-encrypted, audited)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apex.broker.ctrader.adapter import CTraderCredentials
from apex.db.audit import append_audit
from apex.db.models import BrokerAccount, Credential
from apex.domain.enums import TradingMode
from apex.domain.models import utcnow
from apex.security import crypto

KINDS = ("client_id", "client_secret", "access_token", "refresh_token")


class CredentialsMissingError(LookupError):
    pass


def _aad(account_id: int, kind: str) -> bytes:
    # Same binding as the API's write-only credential endpoint.
    return f"credential:{account_id}:{kind}".encode()


async def _put(session: AsyncSession, master_key: str, account_id: int, kind: str, value: str) -> None:
    blob = crypto.encrypt(value, master_key, _aad(account_id, kind))
    row = (
        await session.execute(
            select(Credential).where(Credential.broker_account_id == account_id, Credential.kind == kind)
        )
    ).scalar_one_or_none()
    if row:
        row.ciphertext, row.rotated_at = blob, utcnow()
    else:
        session.add(Credential(broker_account_id=account_id, kind=kind, ciphertext=blob))


async def save_ctrader_account(
    session: AsyncSession,
    master_key: str,
    *,
    actor: str,
    broker: str,
    ctid_account_id: int,
    currency: str,
    creds: CTraderCredentials,
) -> BrokerAccount:
    if creds.environment != "demo":
        raise ValueError("only demo accounts can be stored in this build")
    acc = (
        await session.execute(
            select(BrokerAccount).where(
                BrokerAccount.broker == broker, BrokerAccount.account_ref == str(ctid_account_id)
            )
        )
    ).scalar_one_or_none()
    if acc is None:
        acc = BrokerAccount(
            broker=broker,
            platform="ctrader",
            account_ref=str(ctid_account_id),
            mode_allowed=TradingMode.PAPER,
            currency=currency,
        )
        session.add(acc)
        await session.flush()
    for kind, value in zip(
        KINDS, (creds.client_id, creds.client_secret, creds.access_token, creds.refresh_token), strict=True
    ):
        await _put(session, master_key, acc.id, kind, value)
    await append_audit(
        session,
        actor,
        "ctrader_account_linked",
        target=f"broker_account:{acc.id}",
        details={"ctid_account_id": ctid_account_id, "currency": currency, "env": "demo"},
    )
    await session.commit()
    return acc


async def load_ctrader_credentials(
    session: AsyncSession, master_key: str, account_id: int
) -> CTraderCredentials:
    acc = await session.get(BrokerAccount, account_id)
    if acc is None or acc.platform != "ctrader":
        raise CredentialsMissingError(f"no cTrader broker account {account_id}")
    rows = (
        await session.execute(select(Credential).where(Credential.broker_account_id == account_id))
    ).scalars()
    values = {r.kind: crypto.decrypt(r.ciphertext, master_key, _aad(account_id, r.kind)) for r in rows}
    missing = [k for k in KINDS if k not in values]
    if missing:
        raise CredentialsMissingError(f"broker account {account_id} missing {missing}")
    return CTraderCredentials(
        client_id=values["client_id"],
        client_secret=values["client_secret"],
        access_token=values["access_token"],
        refresh_token=values["refresh_token"],
        ctid_trader_account_id=int(acc.account_ref),
        environment="demo",
    )


def token_saver(maker: async_sessionmaker[AsyncSession], master_key: str, account_id: int):  # type: ignore[no-untyped-def]
    """Callback for ``CTraderAdapter(on_tokens_refreshed=...)``: rotate stored tokens."""

    async def save(access: str, refresh: str) -> None:
        async with maker() as session:
            await _put(session, master_key, account_id, "access_token", access)
            await _put(session, master_key, account_id, "refresh_token", refresh)
            await append_audit(
                session, "system", "ctrader_tokens_refreshed", target=f"broker_account:{account_id}"
            )
            await session.commit()

    return save
