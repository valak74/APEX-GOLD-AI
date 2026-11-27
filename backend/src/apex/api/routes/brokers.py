"""Broker accounts and write-only credentials. Secrets are never returned by any endpoint."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apex.api.deps import ReAuth, SessionDep, StateDep, require_role, verify_reauth
from apex.db.audit import append_audit
from apex.db.models import BrokerAccount, Credential, User
from apex.domain.enums import Role, TradingMode
from apex.domain.models import utcnow
from apex.security import crypto

router = APIRouter(prefix="/broker-accounts", tags=["brokers"])
Admin = Annotated[User, Depends(require_role(Role.ADMIN))]
Viewer = Annotated[User, Depends(require_role(Role.VIEWER))]

CredentialKind = Literal["client_id", "client_secret", "access_token", "refresh_token"]


class BrokerAccountIn(BaseModel):
    broker: str = Field(pattern=r"^[a-z0-9-]{2,50}$")
    platform: Literal["ctrader", "ig", "paper"]
    account_ref: str = Field(min_length=1, max_length=100)
    mode_allowed: TradingMode
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class BrokerAccountOut(BaseModel):
    id: int
    broker: str
    platform: str
    account_ref: str
    mode_allowed: TradingMode
    currency: str
    credentials_present: list[str]


async def _out(session: SessionDep, a: BrokerAccount) -> BrokerAccountOut:
    kinds = (
        await session.execute(select(Credential.kind).where(Credential.broker_account_id == a.id))
    ).scalars()
    return BrokerAccountOut(
        id=a.id,
        broker=a.broker,
        platform=a.platform,
        account_ref=a.account_ref,
        mode_allowed=a.mode_allowed,
        currency=a.currency,
        credentials_present=sorted(kinds),
    )


@router.get("", response_model=list[BrokerAccountOut])
async def list_accounts(_: Viewer, session: SessionDep) -> list[BrokerAccountOut]:
    rows = (await session.execute(select(BrokerAccount).order_by(BrokerAccount.id))).scalars().all()
    return [await _out(session, a) for a in rows]


class CreateAccountIn(BaseModel):
    reauth: ReAuth
    account: BrokerAccountIn


@router.post("", response_model=BrokerAccountOut, status_code=201)
async def create_account(
    body: CreateAccountIn, user: Admin, state: StateDep, session: SessionDep
) -> BrokerAccountOut:
    verify_reauth(user, body.reauth, state)
    a = BrokerAccount(**body.account.model_dump())
    session.add(a)
    try:
        await session.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "account already exists") from None
    await append_audit(
        session,
        user.email,
        "broker_account_created",
        target=f"broker_account:{a.id}",
        details=body.account.model_dump(mode="json"),
    )
    await session.commit()
    return await _out(session, a)


class CredentialIn(BaseModel):
    reauth: ReAuth
    kind: CredentialKind
    value: SecretStr = Field(min_length=1)


@router.put("/{account_id}/credentials", status_code=204)
async def set_credential(
    account_id: int, body: CredentialIn, user: Admin, state: StateDep, session: SessionDep
) -> None:
    verify_reauth(user, body.reauth, state)
    mk = state.settings.master_key.get_secret_value()
    if not mk:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "APEX_MASTER_KEY not configured")
    if await session.get(BrokerAccount, account_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "broker account not found")
    existing = (
        await session.execute(
            select(Credential).where(Credential.broker_account_id == account_id, Credential.kind == body.kind)
        )
    ).scalar_one_or_none()
    aad = f"credential:{account_id}:{body.kind}".encode()
    blob = crypto.encrypt(body.value.get_secret_value(), mk, aad)
    if existing:
        existing.ciphertext, existing.rotated_at = blob, utcnow()
    else:
        session.add(Credential(broker_account_id=account_id, kind=body.kind, ciphertext=blob))
    await append_audit(
        session,
        user.email,
        "credential_set",
        target=f"broker_account:{account_id}",
        details={"kind": body.kind, "rotated": existing is not None},
    )  # never the value
    await session.commit()
