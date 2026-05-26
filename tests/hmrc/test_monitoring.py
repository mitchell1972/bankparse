"""Unit tests for the Sentry monitoring wrapper.

These tests use a fake sentry_sdk so they don't actually contact Sentry.
Covers:

  - init_sentry() is a no-op without SENTRY_DSN
  - capture_hmrc_failure() ignores 4xx (user error, not alertable)
  - capture_hmrc_failure() sends 5xx + 0 (network) with structured tags
  - NINOs are scrubbed from the path tag
  - user_hash is short, stable, non-reversible
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from hmrc.services import monitoring as mon


@pytest.fixture
def fake_sentry(monkeypatch):
    """Install a stub sentry_sdk module into sys.modules. Returns the
    module so the test can inspect captured events."""
    fake = types.ModuleType("sentry_sdk")
    fake.capture_message = MagicMock()
    fake.init = MagicMock()
    fake.push_scope = MagicMock()

    class _Scope:
        def __init__(self):
            self.tags = {}
            self.level = None

        def set_tag(self, k, v):
            self.tags[k] = v

        def set_level(self, lvl):
            self.level = lvl

    class _Ctx:
        def __init__(self):
            self.scope = _Scope()

        def __enter__(self):
            return self.scope

        def __exit__(self, *a):
            return False

    fake.push_scope.return_value = _Ctx()
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    yield fake


@pytest.fixture(autouse=True)
def _reset_initialised():
    mon._initialised = False
    yield
    mon._initialised = False


# ---------------------------------------------------------------------------
# init_sentry()
# ---------------------------------------------------------------------------


def test_init_is_noop_without_dsn(monkeypatch, fake_sentry):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    mon.init_sentry()
    fake_sentry.init.assert_not_called()


def test_init_is_idempotent(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    mon.init_sentry()
    mon.init_sentry()
    mon.init_sentry()
    # Doesn't crash — second call sees _initialised=True and returns.


# ---------------------------------------------------------------------------
# capture_hmrc_failure()
# ---------------------------------------------------------------------------


def test_4xx_is_not_captured(fake_sentry):
    """User-error status codes should NEVER reach Sentry."""
    for code in (400, 401, 403, 404, 409, 422):
        fake_sentry.capture_message.reset_mock()
        mon.capture_hmrc_failure(
            endpoint="/individuals/business/property/AB123456C/XPIS00000000002/obligations",
            method="GET", status_code=code, body={"code": "BAD_REQUEST"},
            user_id=42, audit_id="aud-x",
        )
        fake_sentry.capture_message.assert_not_called()


def test_5xx_is_captured_with_structured_tags(fake_sentry):
    """5xx hits Sentry with all the diagnostic context we need to triage."""
    mon.capture_hmrc_failure(
        endpoint="/individuals/business/self-employment/AB123456C/XAIS00000000001/obligations",
        method="GET", status_code=503,
        body={"code": "SERVICE_UNAVAILABLE", "message": "down"},
        user_id=42, audit_id="aud-abc",
    )
    fake_sentry.capture_message.assert_called_once()
    scope = fake_sentry.push_scope.return_value.scope
    assert scope.tags["hmrc.status"] == "503"
    assert scope.tags["hmrc.method"] == "GET"
    assert scope.tags["hmrc.code"] == "SERVICE_UNAVAILABLE"
    assert scope.tags["audit_id"] == "aud-abc"
    assert scope.tags["user_hash"]  # set, non-empty
    assert scope.level == "error"


def test_network_failure_captured_as_status_0(fake_sentry):
    """Network-level failure tagged status=0 → Sentry alerts."""
    mon.capture_hmrc_failure(
        endpoint="/oauth/token", method="POST", status_code=0,
        body={"network_error": "Connection refused"},
        user_id=42, audit_id="aud-z",
    )
    fake_sentry.capture_message.assert_called_once()


def test_nino_stripped_from_endpoint_tag(fake_sentry):
    """NINOs are PII — must be scrubbed from the endpoint tag."""
    mon.capture_hmrc_failure(
        endpoint="/individuals/business/property/AB123456C/XPIS00000000002/obligations",
        method="GET", status_code=500, body={},
        user_id=42, audit_id="aud-1",
    )
    scope = fake_sentry.push_scope.return_value.scope
    assert "AB123456C" not in scope.tags["hmrc.endpoint"], (
        f"NINO leaked: {scope.tags['hmrc.endpoint']!r}"
    )
    assert "[NINO]" in scope.tags["hmrc.endpoint"]


def test_capture_is_safe_without_sentry_sdk(monkeypatch):
    """If sentry-sdk isn't installed, capture_hmrc_failure must NOT raise."""
    # Pretend sentry_sdk doesn't import.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    # No exception should be raised.
    mon.capture_hmrc_failure(
        endpoint="/x", method="GET", status_code=500, body={}, user_id=1,
    )


# ---------------------------------------------------------------------------
# user_hash + NINO scrubbing helpers
# ---------------------------------------------------------------------------


def test_user_hash_is_short_stable_non_reversible():
    h1 = mon._user_hash(42)
    h2 = mon._user_hash(42)
    h3 = mon._user_hash(43)
    assert h1 == h2  # stable
    assert h1 != h3  # different inputs
    assert len(h1) == 12  # short
    assert "42" not in h1  # not the raw id


def test_nino_scrubber_handles_nested_structures():
    payload = {
        "msg": "NINO AB123456C is invalid",
        "nested": ["MX654321A", {"deep": "WK999999D"}],
        "safe": 42,
    }
    scrubbed = mon._scrub_ninos(payload)
    assert "[NINO]" in scrubbed["msg"]
    assert scrubbed["nested"][0] == "[NINO]"
    assert scrubbed["nested"][1]["deep"] == "[NINO]"
    assert scrubbed["safe"] == 42
    # AB pair is in the disallowed-prefix list per HMRC; the regex
    # excludes A/B leading letters, so verify we don't accidentally
    # mangle other tokens that look NINO-ish but aren't.
    untouched = "SOMETOKEN AB" + "C" * 6 + "D"
    assert mon._scrub_ninos(untouched) == untouched
