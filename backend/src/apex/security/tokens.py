from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp

from apex.domain.enums import Role

ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    pass


def create_access_token(user_id: int, role: Role, secret: str, ttl_minutes: int) -> str:
    if len(secret) < 32:
        raise ValueError("JWT secret must be at least 32 characters")
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role.value,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    try:
        data: dict[str, Any] = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError as e:
        raise InvalidTokenError(str(e)) from e
    return data


def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)
