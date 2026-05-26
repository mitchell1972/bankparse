"""
AES-GCM encryption helpers for HMRC tokens at rest.

We use Python's stdlib + `cryptography` for AES-GCM because:
  - We must not log or store HMRC refresh tokens in plaintext.
  - HMRC refresh tokens live for 18 months. A DB leak would let an attacker
    submit fake quarterly returns on every connected user's behalf.
  - AES-GCM provides authenticated encryption: tampering with the ciphertext
    is detected at decrypt time.

Key handling — both ACTIVE and DECRYPT-ONLY keys are supported, which is
what makes safe rotation possible:

  HMRC_TOKEN_ENCRYPTION_KEY       — the ACTIVE key. New blobs are
                                    encrypted with this one. base64(32B).
  HMRC_TOKEN_ENCRYPTION_KEY_OLD   — optional. Comma-separated base64(32B)
                                    keys we still try when decrypting.
                                    Used during rotation: set the new key
                                    as ACTIVE, keep the previous as OLD,
                                    run scripts/rotate_hmrc_token_key.py
                                    to re-encrypt all stored blobs under
                                    the new key, then clear OLD.

On-disk format: base64(nonce[12] || ciphertext || tag[16]). No explicit
key-version prefix — we just try each candidate key on decrypt. With 2-3
keys in flight at most, this is cheap and avoids a wire-format migration.

See hmrc/docs/key-rotation.md for the full rotation procedure.
"""

from __future__ import annotations

import base64
import os

from .. import config as _cfg


# Caches the parsed active key + the list of fallback (old) keys. Cleared
# by tests via `_reset_key_cache_for_tests()`.
_KEY_CACHE: bytes | None = None
_FALLBACK_KEYS: list[bytes] | None = None


def _parse_base64_32(raw: str) -> bytes:
    """Decode a base64 string into a 32-byte AES key. Raises on size mismatch."""
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"AES key must decode to 32 bytes, got {len(key)}"
        )
    return key


def _key() -> bytes:
    """Decode and cache the ACTIVE 32-byte AES-GCM key from env."""
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
    _KEY_CACHE = _parse_base64_32(raw)
    return _KEY_CACHE


def _fallback_keys() -> list[bytes]:
    """Decode + cache the comma-separated HMRC_TOKEN_ENCRYPTION_KEY_OLD.

    Empty list when unset. Used ONLY on decrypt, never on encrypt — old
    blobs encrypted with a retired key can still be read, but new blobs
    always use the active key."""
    global _FALLBACK_KEYS
    if _FALLBACK_KEYS is not None:
        return _FALLBACK_KEYS
    raw = (os.environ.get("HMRC_TOKEN_ENCRYPTION_KEY_OLD") or "").strip()
    if not raw:
        _FALLBACK_KEYS = []
        return _FALLBACK_KEYS
    keys: list[bytes] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            keys.append(_parse_base64_32(part))
    _FALLBACK_KEYS = keys
    return _FALLBACK_KEYS


def _reset_key_cache_for_tests() -> None:
    """Test helper — clears the cached parsed keys so env changes take effect.

    Some older tests still assign ``_KEY_CACHE = None`` directly — that works
    too for the active-key half, but won't clear ``_FALLBACK_KEYS``. New tests
    should prefer this helper.
    """
    global _KEY_CACHE, _FALLBACK_KEYS
    _KEY_CACHE = None
    _FALLBACK_KEYS = None


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return a base64-encoded blob safe for SQL storage.

    Always uses the ACTIVE key — fallback keys are decrypt-only.
    """
    if plaintext is None:
        raise ValueError("plaintext is required")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aesgcm = AESGCM(_key())
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(blob: str) -> str:
    """Decrypt a blob produced by `encrypt()`. Raises on tampering.

    Tries the ACTIVE key first, then any HMRC_TOKEN_ENCRYPTION_KEY_OLD
    candidates in order. Letting decrypt fall back lets us rotate the
    active key without flushing every connected user's HMRC tokens.
    """
    if not blob:
        raise ValueError("blob is required")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag

    raw = base64.b64decode(blob)
    if len(raw) < 12 + 16:
        raise ValueError("ciphertext too short to be a valid AES-GCM payload")
    nonce, ct = raw[:12], raw[12:]

    candidate_keys = [_key()] + _fallback_keys()
    last_err: Exception | None = None
    for k in candidate_keys:
        try:
            pt = AESGCM(k).decrypt(nonce, ct, associated_data=None)
            return pt.decode("utf-8")
        except InvalidTag as e:
            last_err = e
            continue
    # Every key rejected the tag — either the blob was encrypted under a
    # KEY WE NO LONGER HAVE, or it's been tampered with. Either is fatal.
    raise InvalidTag(
        "decrypt failed under all active + fallback keys — either the "
        "blob is corrupt, tampered, or was encrypted under a key that's "
        "been fully retired without re-encryption."
    ) from last_err
