from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pyotp
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from apex.api.deps import COOKIE_NAME, SessionDep, StateDep, UserDep
from apex.db.audit import append_audit
from apex.db.models import User
from apex.security import crypto
from apex.security.passwords import DUMMY_HASH, verify_password
from apex.security.tokens import create_access_token, new_totp_secret, verify_totp

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    # Identifier only: no deliverability rules (self-hosted installs may use e.g. *.local addresses).
    email: str = Field(min_length=3, max_length=320)
    password: str
    totp: str | None = None


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    mfa_enabled: bool


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, role=u.role.value, mfa_enabled=u.mfa_secret_enc is not None)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginIn, request: Request, response: Response, state: StateDep, session: SessionDep
) -> UserOut:
    ip = request.client.host if request.client else "unknown"
    if not state.login_limiter.allow(f"{ip}|{body.email.lower()}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts")
    user = (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    ok = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
    if ok and user is not None and not user.disabled and user.mfa_secret_enc is not None:
        secret = crypto.decrypt(
            user.mfa_secret_enc, state.settings.master_key.get_secret_value(), f"mfa:{user.id}".encode()
        )
        ok = body.totp is not None and verify_totp(secret, body.totp)
    if not ok or user is None or user.disabled:
        await append_audit(session, body.email.lower(), "login_failed", ip=ip)
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    s = state.settings
    token = create_access_token(user.id, user.role, s.jwt_secret.get_secret_value(), s.jwt_ttl_minutes)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=s.cookie_secure,
        samesite="strict",
        max_age=s.jwt_ttl_minutes * 60,
        path="/",
    )
    await append_audit(session, user.email, "login", ip=ip)
    await session.commit()
    return _out(user)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: UserDep) -> UserOut:
    return _out(user)


class MfaSetupOut(BaseModel):
    setup_token: str
    otpauth_uri: str


class MfaConfirmIn(BaseModel):
    setup_token: str
    code: str


@router.post("/mfa/setup", response_model=MfaSetupOut)
async def mfa_setup(user: UserDep, state: StateDep) -> MfaSetupOut:
    if not state.settings.master_key.get_secret_value():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "APEX_MASTER_KEY not configured")
    if user.mfa_secret_enc is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "MFA already enabled")
    secret = new_totp_secret()
    tok = jwt.encode(
        {"sub": str(user.id), "mfa": secret, "exp": datetime.now(UTC) + timedelta(minutes=10)},
        state.settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    return MfaSetupOut(
        setup_token=tok,
        otpauth_uri=pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="APEX GOLD AI"),
    )


@router.post("/mfa/confirm", response_model=UserOut)
async def mfa_confirm(body: MfaConfirmIn, user: UserDep, state: StateDep, session: SessionDep) -> UserOut:
    try:
        data = jwt.decode(
            body.setup_token, state.settings.jwt_secret.get_secret_value(), algorithms=["HS256"]
        )
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid setup token") from None
    if data.get("sub") != str(user.id) or not verify_totp(data["mfa"], body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid code")
    user.mfa_secret_enc = crypto.encrypt(
        data["mfa"], state.settings.master_key.get_secret_value(), f"mfa:{user.id}".encode()
    )
    await append_audit(session, user.email, "mfa_enabled", target=f"user:{user.id}")
    await session.commit()
    return _out(user)
