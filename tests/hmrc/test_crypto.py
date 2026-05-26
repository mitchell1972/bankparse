"""Round-trip and tamper-detection tests for hmrc.services.crypto."""

import base64
import os
import secrets
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(autouse=True)
def _set_key(monkeypatch):
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", key)
    # Reset the module-level cache so the new key is picked up.
    import hmrc.services.crypto as c
    c._reset_key_cache_for_tests()
    # Also reload config to refresh HMRC_TOKEN_ENCRYPTION_KEY constant.
    import importlib, hmrc.config
    importlib.reload(hmrc.config)
    importlib.reload(c)
    yield
    c._reset_key_cache_for_tests()


def test_roundtrip_basic():
    from hmrc.services import crypto
    pt = "hello world"
    assert crypto.decrypt(crypto.encrypt(pt)) == pt


def test_roundtrip_long_token_like_hmrc_refresh():
    """HMRC tokens are ~120-200 chars of base64. Make sure we handle that fine."""
    from hmrc.services import crypto
    pt = base64.b64encode(secrets.token_bytes(150)).decode()
    assert crypto.decrypt(crypto.encrypt(pt)) == pt


def test_unique_nonce_per_encryption():
    """Same plaintext should produce different ciphertext each call (random nonce)."""
    from hmrc.services import crypto
    pt = "same input"
    a = crypto.encrypt(pt)
    b = crypto.encrypt(pt)
    assert a != b
    assert crypto.decrypt(a) == pt
    assert crypto.decrypt(b) == pt


def test_tamper_detection():
    """Flipping a bit in the ciphertext must cause decrypt to fail."""
    from hmrc.services import crypto
    blob = crypto.encrypt("important secret")
    raw = bytearray(base64.b64decode(blob))
    raw[-1] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(Exception):
        crypto.decrypt(tampered)


def test_missing_key_refuses_to_run(monkeypatch):
    """No key set → encrypt/decrypt must raise, not silently store plaintext."""
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", "")
    import importlib, hmrc.config, hmrc.services.crypto as c
    importlib.reload(hmrc.config)
    c._reset_key_cache_for_tests()
    importlib.reload(c)
    with pytest.raises(RuntimeError, match="HMRC_TOKEN_ENCRYPTION_KEY"):
        c.encrypt("x")


# ---------------------------------------------------------------------------
# Two-key rotation (HMRC_TOKEN_ENCRYPTION_KEY_OLD fallback)
# ---------------------------------------------------------------------------


def _b64key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def _reload(monkeypatch, active: str, old: str | None):
    import importlib, hmrc.config, hmrc.services.crypto as c
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", active)
    if old is None:
        monkeypatch.delenv("HMRC_TOKEN_ENCRYPTION_KEY_OLD", raising=False)
    else:
        monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY_OLD", old)
    importlib.reload(hmrc.config)
    c._reset_key_cache_for_tests()
    importlib.reload(c)
    return c


def test_decrypt_falls_back_to_old_key(monkeypatch):
    """A blob encrypted under the OLD key must decrypt fine after rotation."""
    old_key = _b64key()
    c = _reload(monkeypatch, active=old_key, old=None)
    blob = c.encrypt("legacy refresh token")

    # Rotate — new key promoted to active, old_key moves to OLD.
    new_key = _b64key()
    c = _reload(monkeypatch, active=new_key, old=old_key)

    # The legacy blob must still decrypt — via the fallback.
    assert c.decrypt(blob) == "legacy refresh token"


def test_encrypt_after_rotation_uses_new_key(monkeypatch):
    """Once the active key changes, new blobs encrypt under it, NOT old."""
    old_key = _b64key()
    new_key = _b64key()
    c = _reload(monkeypatch, active=new_key, old=old_key)
    new_blob = c.encrypt("fresh token")

    # Retire OLD — the new_blob still decrypts cleanly.
    c = _reload(monkeypatch, active=new_key, old=None)
    assert c.decrypt(new_blob) == "fresh token"


def test_decrypt_supports_multiple_old_keys(monkeypatch):
    """OLD can be a comma-separated list — supports multi-step rotations."""
    key1 = _b64key()
    c = _reload(monkeypatch, active=key1, old=None)
    blob1 = c.encrypt("blob under key1")

    key2 = _b64key()
    c = _reload(monkeypatch, active=key2, old=key1)
    blob2 = c.encrypt("blob under key2")

    # Another rotation — key2 becomes OLD, alongside key1.
    key3 = _b64key()
    c = _reload(monkeypatch, active=key3, old=f"{key2},{key1}")

    # Both legacy blobs must still decrypt.
    assert c.decrypt(blob1) == "blob under key1"
    assert c.decrypt(blob2) == "blob under key2"


def test_decrypt_fails_when_old_key_fully_retired(monkeypatch):
    """A blob encrypted under a key that's no longer active+OLD must fail."""
    from cryptography.exceptions import InvalidTag

    old_key = _b64key()
    c = _reload(monkeypatch, active=old_key, old=None)
    blob = c.encrypt("doomed")

    new_key = _b64key()
    c = _reload(monkeypatch, active=new_key, old=None)
    with pytest.raises(InvalidTag):
        c.decrypt(blob)


def test_old_key_whitespace_is_tolerated(monkeypatch):
    """Real env-var values often have stray whitespace from copy-paste."""
    old_key = _b64key()
    c = _reload(monkeypatch, active=old_key, old=None)
    blob = c.encrypt("survives copy paste")

    new_key = _b64key()
    c = _reload(monkeypatch, active=new_key, old=f"  {old_key} ,")
    assert c.decrypt(blob) == "survives copy paste"
