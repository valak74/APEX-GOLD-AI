import time

import pyotp
import pytest

from apex.core.logging import _redact
from apex.domain.enums import Role
from apex.security.crypto import DecryptionError, decrypt, encrypt, generate_master_key
from apex.security.passwords import WeakPasswordError, hash_password, verify_password
from apex.security.tokens import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    new_totp_secret,
    verify_totp,
)

SECRET = "x" * 40


def test_password_roundtrip_and_weak_rejected() -> None:
    h = hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery")
    assert not verify_password(h, "wrong password!!")
    assert not verify_password("not-a-hash", "whatever")
    with pytest.raises(WeakPasswordError):
        hash_password("short")


def test_jwt_roundtrip_and_tamper() -> None:
    tok = create_access_token(7, Role.TRADER, SECRET, 15)
    data = decode_access_token(tok, SECRET)
    assert data["sub"] == "7" and data["role"] == "trader"
    with pytest.raises(InvalidTokenError):
        decode_access_token(tok, "y" * 40)
    with pytest.raises(InvalidTokenError):
        decode_access_token(tok[:-2] + "aa", SECRET)


def test_jwt_expired() -> None:
    tok = create_access_token(1, Role.VIEWER, SECRET, -1)
    with pytest.raises(InvalidTokenError):
        decode_access_token(tok, SECRET)


def test_jwt_short_secret_refused() -> None:
    with pytest.raises(ValueError):
        create_access_token(1, Role.VIEWER, "short", 15)


def test_totp() -> None:
    s = new_totp_secret()
    assert verify_totp(s, pyotp.TOTP(s).at(time.time()))
    assert not verify_totp(s, "000000") or pyotp.TOTP(s).now() == "000000"


def test_envelope_encryption() -> None:
    mk = generate_master_key()
    blob = encrypt("client-secret-value", mk, b"credential:1")
    assert b"client-secret-value" not in blob
    assert decrypt(blob, mk, b"credential:1") == "client-secret-value"
    with pytest.raises(DecryptionError):
        decrypt(blob, mk, b"credential:2")  # bound to its record
    with pytest.raises(DecryptionError):
        decrypt(blob, generate_master_key(), b"credential:1")
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(DecryptionError):
        decrypt(tampered, mk, b"credential:1")


def test_log_redaction() -> None:
    ev = _redact(
        None, "info", {"event": "x", "password": "p", "client_secret": "s", "api_key": "k", "user": "u"}
    )
    assert ev["password"] == ev["client_secret"] == ev["api_key"] == "***REDACTED***"
    assert ev["user"] == "u"
