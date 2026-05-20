"""
Regression tests for the paywall-bypass singleton.

Bug history: the paywall was originally gated on UNLIMITED_EMAILS, which
is env-extendable. Setting `UNLIMITED_EMAILS=foo@example.com` on Railway
gave foo@example.com permanent dashboard access. In particular,
mitchell_agoma@live.co.uk was bypassing the paywall on production via
this env-var path.

The fix: PAYWALL_BYPASS_EMAILS is a HARDCODED frozenset containing only
mitchell_agoma@yahoo.co.uk. It cannot be extended by env var. The
UNLIMITED_EMAILS env var is still used for feature gates (admin panel,
enterprise tier auto-grant) but no longer bypasses the paywall.

These tests pin the contract so the bug can't return.
"""

from __future__ import annotations

import base64
import importlib
import os
import secrets
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_paywall_singleton.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Tests must NEVER leak UNLIMITED_EMAILS into each other.
    monkeypatch.delenv("UNLIMITED_EMAILS", raising=False)

    try:
        from app import app as _app
        if getattr(_app.state, "limiter", None) is not None:
            _app.state.limiter.enabled = False
    except Exception:
        pass
    yield
    try:
        from app import app as _app
        lim = getattr(_app.state, "limiter", None)
        if lim is not None:
            # Reset the in-memory counters before re-enabling — otherwise
            # the registrations these tests do leak through to later tests
            # and trip the /api/register 10-per-minute limit.
            try: lim.reset()
            except Exception: pass
            lim.enabled = True
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_db():
    import database
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)
    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
    database._sqlite_conn = None

    import sqlite3
    def _get_sqlite_test():
        if database._sqlite_conn is None:
            conn = sqlite3.connect(TEST_DB_PATH, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            database._sqlite_conn = conn
        return database._sqlite_conn

    database._get_sqlite = _get_sqlite_test
    database.init_db()
    yield

    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


def _seed_csrf(client):
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _register_and_verify(email: str):
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/register",
        json={"email": email, "password": "password12345"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    # Explicitly clear grandfathered_trial so nothing else bypasses.
    _db.update_user(user["id"], grandfathered_trial=0)
    return client, _db.get_user_by_email(email)


# ---------------------------------------------------------------------------
# Singleton contract
# ---------------------------------------------------------------------------

def test_paywall_bypass_is_exactly_one_email():
    """The bypass set MUST be a singleton containing only yahoo. If anyone
    adds a second hardcoded email, this test forces them to update the
    comment / docs at the same time."""
    from core import PAYWALL_BYPASS_EMAILS
    assert PAYWALL_BYPASS_EMAILS == frozenset({"mitchell_agoma@yahoo.co.uk"})


def test_paywall_bypass_is_frozenset_not_mutable():
    """A regular set could be mutated at runtime (`.add(...)`). Frozenset
    can't. This protects against a future contributor accidentally
    extending the bypass list dynamically."""
    from core import PAYWALL_BYPASS_EMAILS
    assert isinstance(PAYWALL_BYPASS_EMAILS, frozenset)


# ---------------------------------------------------------------------------
# The literal bug — mitchell_agoma@live.co.uk
# ---------------------------------------------------------------------------

def test_mitchell_agoma_live_co_uk_is_paywalled():
    """REGRESSION — this exact email was bypassing paywall on production
    because UNLIMITED_EMAILS env var on Railway contained it. The bypass
    set is now hardcoded; this email MUST be paywalled."""
    client, _ = _register_and_verify("mitchell_agoma@live.co.uk")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302, (
        f"mitchell_agoma@live.co.uk MUST be paywalled (got {r.status_code}). "
        "If this fails, the UNLIMITED_EMAILS bypass leaked back into the "
        "dashboard gate."
    )
    assert r.headers["location"] == "/start-trial"


def test_yahoo_admin_still_bypasses():
    """The one allowed bypass — must keep working."""
    client, _ = _register_and_verify("mitchell_agoma@yahoo.co.uk")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200, (
        f"yahoo admin must land on dashboard (got {r.status_code})"
    )


# ---------------------------------------------------------------------------
# The env-poisoning attack — even if UNLIMITED_EMAILS is malicious, the
# paywall must hold. This is the test that proves the singleton is sealed.
# ---------------------------------------------------------------------------

def test_env_unlimited_emails_does_not_grant_paywall_bypass(monkeypatch):
    """Even if Railway's UNLIMITED_EMAILS env var contains an attacker's
    email, that email does NOT bypass the paywall. UNLIMITED_EMAILS is
    only for feature gates (admin panel, enterprise tier) now."""
    # Simulate the poisoned env: UNLIMITED_EMAILS contains an arbitrary email.
    monkeypatch.setenv("UNLIMITED_EMAILS",
                       "mitchell_agoma@live.co.uk,attacker@example.com")
    # Reload core so the env-derived set picks up the new value.
    import core
    importlib.reload(core)
    try:
        # Sanity: the env var DID seep into UNLIMITED_EMAILS — that's still
        # how feature gates work. But it must NOT be in PAYWALL_BYPASS_EMAILS.
        assert "mitchell_agoma@live.co.uk" in core.UNLIMITED_EMAILS
        assert "attacker@example.com" in core.UNLIMITED_EMAILS
        assert "mitchell_agoma@live.co.uk" not in core.PAYWALL_BYPASS_EMAILS
        assert "attacker@example.com" not in core.PAYWALL_BYPASS_EMAILS

        # And the actual gate must paywall the live.co.uk login too.
        # We have to reload app so its import of PAYWALL_BYPASS_EMAILS picks
        # up the (still-singleton) set.
        import app as _app_module
        importlib.reload(_app_module)
        client = TestClient(_app_module.app, raise_server_exceptions=False)
        client.__enter__()
        csrf = _seed_csrf(client)
        r = client.post(
            "/api/register",
            json={"email": "mitchell_agoma@live.co.uk", "password": "password12345"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        import database as _db
        user = _db.get_user_by_email("mitchell_agoma@live.co.uk")
        _db.mark_email_verified(user["id"])

        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302, (
            "Even with UNLIMITED_EMAILS env var containing live.co.uk, the "
            "paywall must hold."
        )
        assert r.headers["location"] == "/start-trial"
    finally:
        monkeypatch.delenv("UNLIMITED_EMAILS")
        importlib.reload(core)


def test_check_can_use_does_not_bypass_paywall_via_unlimited_emails_env(monkeypatch):
    """The upload endpoint goes through check_can_use, which has its own
    free-tier bypass. That bypass MUST also use PAYWALL_BYPASS_EMAILS, not
    UNLIMITED_EMAILS. Otherwise an env-poisoned account could upload
    without ever entering a card."""
    monkeypatch.setenv("UNLIMITED_EMAILS", "mitchell_agoma@live.co.uk")
    import core
    importlib.reload(core)
    try:
        # Build a user matching the live.co.uk case — verified, no Stripe
        # subscription, not grandfathered.
        user = {
            "id": 1, "email": "mitchell_agoma@live.co.uk",
            "grandfathered_trial": 0, "subscription_status": None,
            "stripe_subscription_id": None, "trial_end_at": None,
            "stripe_customer_id": None,
        }
        from unittest.mock import patch
        with patch("core.is_email_verified", return_value=True), \
             patch("core.get_global_daily_ai_spend", return_value=0.0), \
             patch("core.get_user_today_spend", return_value=0.0), \
             patch("core.get_user_tier", return_value="free"):
            ok, _tier, reason, _ = core.check_can_use(user, "statement")
        assert ok is False, (
            "An UNLIMITED_EMAILS-only entry must NOT pass check_can_use "
            "without a Stripe subscription."
        )
        assert reason == "payment_method_required"
    finally:
        monkeypatch.delenv("UNLIMITED_EMAILS")
        importlib.reload(core)


# ---------------------------------------------------------------------------
# Trialing / active still bypass — sanity, didn't regress PR #34
# ---------------------------------------------------------------------------

def test_live_co_uk_with_active_stripe_trial_lands_on_dashboard():
    """A live.co.uk user who's already entered a card (subscription_status
    = 'trialing') must reach the dashboard normally — the paywall fix
    didn't accidentally block them too."""
    import database as _db
    client, user = _register_and_verify("mitchell_agoma@live.co.uk")
    _db.update_user(
        user["id"],
        subscription_status="trialing",
        stripe_subscription_id="sub_test_live",
        trial_end_at=time.time() + 7 * 86400,
    )
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200, (
        "A live.co.uk user with an active Stripe trial must reach the "
        "dashboard — the paywall fix should not have blocked subscribers."
    )


# ---------------------------------------------------------------------------
# THE LIVE.CO.UK PRODUCTION BUG — orphan subscription_status without Stripe sub
# ---------------------------------------------------------------------------

def test_stale_trialing_status_without_stripe_sub_is_paywalled():
    """REGRESSION — In production, mitchell_agoma@live.co.uk had
    subscription_status='trialing' set in the DB but NO stripe_subscription_id
    (the Stripe sub was never created, or was deleted, or the column was
    manually set during testing). The old gate honoured 'trialing' as a
    bypass without checking the sub id — so the user reached the dashboard
    without ever entering a card.

    The fix: has_active_subscription requires BOTH subscription_status in
    (trialing/active/past_due) AND stripe_subscription_id non-empty."""
    import database as _db
    client, user = _register_and_verify("mitchell_agoma@live.co.uk")
    _db.update_user(
        user["id"],
        subscription_status="trialing",
        stripe_subscription_id=None,                  # ← the bug condition
        trial_end_at=time.time() + 7 * 86400,
    )
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302, (
        "A 'trialing' status without a Stripe sub is an orphan — must NOT "
        "bypass the paywall. This was the live.co.uk production bug."
    )
    assert r.headers["location"] == "/start-trial"


def test_stale_active_status_without_stripe_sub_is_paywalled():
    """Same orphan-row attack but for 'active' status."""
    import database as _db
    client, user = _register_and_verify("orphan-active@example.com")
    _db.update_user(
        user["id"],
        subscription_status="active",
        stripe_subscription_id=None,
    )
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/start-trial"


def test_is_trial_active_requires_stripe_subscription_id():
    """Unit test on the core helper — 'trialing' without a stripe_subscription_id
    is no longer enough."""
    from core import is_trial_active
    user_orphan = {
        "subscription_status": "trialing",
        "stripe_subscription_id": None,
        "trial_end_at": time.time() + 7 * 86400,
    }
    user_real = {
        "subscription_status": "trialing",
        "stripe_subscription_id": "sub_real_test",
        "trial_end_at": time.time() + 7 * 86400,
    }
    assert is_trial_active(user_orphan) is False
    assert is_trial_active(user_real) is True


def test_has_active_subscription_requires_both_status_and_sub_id():
    """Unit test on the new helper across the four states."""
    from core import has_active_subscription
    # No status, no sub
    assert has_active_subscription({"subscription_status": None, "stripe_subscription_id": None}) is False
    # Status but no sub (the orphan bug)
    assert has_active_subscription({"subscription_status": "trialing", "stripe_subscription_id": None}) is False
    assert has_active_subscription({"subscription_status": "active",   "stripe_subscription_id": ""})   is False
    # Both present
    assert has_active_subscription({"subscription_status": "trialing", "stripe_subscription_id": "sub_x"}) is True
    assert has_active_subscription({"subscription_status": "active",   "stripe_subscription_id": "sub_y"}) is True
    assert has_active_subscription({"subscription_status": "past_due", "stripe_subscription_id": "sub_z"}) is True
    # Cancelled / unpaid is paywall-eligible even with a sub id
    assert has_active_subscription({"subscription_status": "canceled", "stripe_subscription_id": "sub_x"}) is False
