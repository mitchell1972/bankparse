"""
AES-GCM encryption helpers for HMRC tokens at rest.

We use Python's `hashlib`/`hmac` stdlib + `cryptography` for AES-GCM because:
  - We must not log or store HMRC refresh tokens in plaintext.
  - HMRC refresh tokens live for 18 months. A DB leak would let an attacker
    submit fake quarterly returns on every connected user's behalf.
  - AES-GCM provides authenticated encryption: tampering with the ciphertext
    is detected at decrypt time.

Key handling:
  - Master key is base64(32 bytes) in env var HMRC_TOKEN_ENCRYPTION_KEY.
  - Each encryption produces a fresh 12-byte random nonce, stored prepended
    to the ciphertext.
  - On-disk format: base64(nonce || ciphertext || tag).

If/when we ever rotate keys, the simplest approach is to add a key-version
prefix byte to the payload — deferred until rotation actually happens.
"""

from __future__ import annotations

import base64
import os

from .. import config as _cfg


_KEY_CACHE: bytes | None = None


def _key() -> bytes:
    """Decode and cache the 32-byte AES-GCM key from env."""
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE
    raw = _cfg.HMRC_TOKEN_ENCRYPTION_KEY
    if not raw:
        raise RuntimeError(
            "HMRC_TOKEN_ENCRYPTION_KEY not set — refusing to handle tokens "
            "in plaintext. Generate one with: "
            'python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"'
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"HMRC_TOKEN_ENCRYPTION_KEY must decode to 32 bytes, got {len(key)}"
        )
    _KEY_CACHE = key
    return key


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return a base64-encoded blob safe for SQL storage."""
    if plaintext is None:
        raise ValueError("plaintext is required")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(_key())
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(blob: str) -> str:
    """Decrypt a blob produced by `encrypt()`. Raises on tampering."""
    if not blob:
        raise ValueError("blob is required")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.b64decode(blob)
    if len(raw) < 12 + 16:
        raise ValueError("ciphertext too short to be a valid AES-GCM payload")
    nonce, ct = raw[:12], raw[12:]
    aesgcm = AESGCM(_key())
    pt = aesgcm.decrypt(nonce, ct, associated_data=None)
    return pt.decode("utf-8")
