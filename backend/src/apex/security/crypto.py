"""Envelope encryption for broker credentials (docs/05-security-architecture.md §2).

Each secret is encrypted with a fresh 256-bit data key (AES-256-GCM); the data key is itself encrypted
with the master key (AES-256-GCM). The master key comes from the secret store (env/Docker secret),
never from the database. The associated data binds a ciphertext to its record (e.g. credential id).
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"v1"


class DecryptionError(Exception):
    pass


def generate_master_key() -> str:
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


def _master(master_key_b64: str) -> AESGCM:
    key = base64.b64decode(master_key_b64)
    if len(key) != 32:
        raise ValueError("master key must be 32 bytes (base64)")
    return AESGCM(key)


def encrypt(plaintext: str, master_key_b64: str, aad: bytes) -> bytes:
    data_key = AESGCM.generate_key(bit_length=256)
    n1, n2 = os.urandom(12), os.urandom(12)
    wrapped = _master(master_key_b64).encrypt(n1, data_key, aad)
    body = AESGCM(data_key).encrypt(n2, plaintext.encode(), aad)
    return _VERSION + n1 + len(wrapped).to_bytes(2, "big") + wrapped + n2 + body


def decrypt(blob: bytes, master_key_b64: str, aad: bytes) -> str:
    try:
        if blob[:2] != _VERSION:
            raise DecryptionError("unknown ciphertext version")
        n1 = blob[2:14]
        wlen = int.from_bytes(blob[14:16], "big")
        wrapped = blob[16 : 16 + wlen]
        n2 = blob[16 + wlen : 28 + wlen]
        body = blob[28 + wlen :]
        data_key = _master(master_key_b64).decrypt(n1, wrapped, aad)
        return AESGCM(data_key).decrypt(n2, body, aad).decode()
    except DecryptionError:
        raise
    except Exception as e:
        raise DecryptionError("decryption failed") from e
