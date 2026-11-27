from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from apex.api.state import AppState
from apex.db.models import User
from apex.domain.enums import Role
from apex.security import crypto
from apex.security.passwords import verify_password
from apex.security.tokens import InvalidTokenError, decode_access_token, verify_totp

COOKIE_NAME = "apex_session"


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.apex
    return state


StateDep = Annotated[AppState, Depends(get_state)]


async def get_session(state: StateDep) -> AsyncIterator[AsyncSession]:
    async with state.sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


async def current_user(request: Request, state: StateDep, session: SessionDep) -> User:
    token = request.cookies.get(COOKIE_NAME)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
    if not token:
        raise _unauthorized()
    try:
        data = decode_access_token(token, state.settings.jwt_secret.get_secret_value())
    except InvalidTokenError:
        raise _unauthorized("invalid or expired session") from None
    user = await session.get(User, int(data["sub"]))
    if user is None or user.disabled:
        raise _unauthorized()
    return user


UserDep = Annotated[User, Depends(current_user)]


def require_role(minimum: Role) -> Callable[[User], Awaitable[User]]:
    async def _dep(user: UserDep) -> User:
        if user.role.level < minimum.level:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {minimum.value}")
        return user

    return _dep


class ReAuth(BaseModel):
    """Re-authentication required for dangerous actions (docs/05 §3)."""

    password: str
    totp: str


def verify_reauth(user: User, reauth: ReAuth, state: AppState) -> None:
    if user.mfa_secret_enc is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "MFA must be enabled for this action")
    if not verify_password(user.password_hash, reauth.password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "re-authentication failed")
    secret = crypto.decrypt(
        user.mfa_secret_enc, state.settings.master_key.get_secret_value(), f"mfa:{user.id}".encode()
    )
    if not verify_totp(secret, reauth.totp):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "re-authentication failed")
