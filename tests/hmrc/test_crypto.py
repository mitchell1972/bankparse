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
    c._KEY_CACHE = None
    # Also reload config to refresh HMRC_TOKEN_ENCRYPTION_KEY constant.
    import importlib, hmrc.config
    importlib.reload(hmrc.config)
    importlib.reload(c)
    yield
    c._KEY_CACHE = None


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
    c._KEY_CACHE = None
    importlib.reload(c)
    with pytest.raises(RuntimeError, match="HMRC_TOKEN_ENCRYPTION_KEY"):
        c.encrypt("x")
