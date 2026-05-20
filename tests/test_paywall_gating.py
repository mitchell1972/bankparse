"""
Tests for the paywall gating on the dashboard route.

Before this fix, BOTH founder emails (yahoo + gmail) bypassed the paywall
by default — which meant signing in as gmail silently skipped the
subscription flow and the founder couldn't see what real users see.

Now ONLY mitchell_agoma@yahoo.co.uk bypasses by default. Everyone else
(including mitchellagoma@gmail.com unless they actually subscribe)
hits /start-trial.

These tests pin the behaviour so it can't quietly drift back.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_paywall_gating.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Make sure no stray env var puts an email into UNLIMITED_EMAILS for tests.
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
            # Reset counters so registrations from these tests don't
            # bleed into the next test's 10-per-minute /api/register cap.
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
    """Register + email-verify a user. Returns (TestClient, user dict).

    Does NOT grandfather the user — that's the whole point of the gating
    tests. Caller may flip grandfathered_trial / subscription_status to
    test different states.
    """
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
    # Explicitly clear grandfathered_trial so the migration backfill that
    # runs in init_db (which sweeps everyone < cutoff) doesn't accidentally
    # turn this on. Real prod users registered today already start at 0.
    _db.update_user(user["id"], grandfathered_trial=0)
    user = _db.get_user_by_email(email)
    return client, user


# ---------------------------------------------------------------------------
# UNLIMITED_EMAILS scope
# ---------------------------------------------------------------------------

def test_only_yahoo_email_is_admin_by_default():
    """The default admin set must be a singleton — yahoo only. The gmail
    address was previously included and that silently bypassed paywall."""
    from core import _ADMIN_DEFAULTS, UNLIMITED_EMAILS
    assert _ADMIN_DEFAULTS == {"mitchell_agoma@yahoo.co.uk"}
    # UNLIMITED_EMAILS = defaults | env. With no UNLIMITED_EMAILS env var
    # (cleared in the fixture), it should equal the defaults.
    assert UNLIMITED_EMAILS == {"mitchell_agoma@yahoo.co.uk"}


def test_extra_admins_can_be_added_via_env_var(monkeypatch):
    """Setting UNLIMITED_EMAILS=foo@bar.com adds it to the admin set —
    but yahoo stays in by default. This is how we expand without
    re-deploying."""
    monkeypatch.setenv("UNLIMITED_EMAILS", "another@example.com,third@example.com")
    # Reimport core to pick up the env var change.
    import importlib
    import core
    importlib.reload(core)
    try:
        assert "mitchell_agoma@yahoo.co.uk" in core.UNLIMITED_EMAILS
        assert "another@example.com" in core.UNLIMITED_EMAILS
        assert "third@example.com" in core.UNLIMITED_EMAILS
        # gmail must STILL not be in by default
        assert "mitchellagoma@gmail.com" not in core.UNLIMITED_EMAILS
    finally:
        # Reset back to the canonical state for downstream tests.
        monkeypatch.delenv("UNLIMITED_EMAILS")
        importlib.reload(core)


# ---------------------------------------------------------------------------
# Dashboard gating — the user-facing redirect behaviour
# ---------------------------------------------------------------------------

def test_yahoo_admin_lands_on_dashboard_without_subscription():
    """The yahoo founder bypasses /start-trial. This is intentional and
    must keep working."""
    client, _ = _register_and_verify("mitchell_agoma@yahoo.co.uk")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200, (
        f"yahoo admin should land on dashboard (got {r.status_code}); "
        "if this fails the admin email was removed by accident."
    )


def test_gmail_account_is_redirected_to_start_trial():
    """REGRESSION TEST for the bug the user reported: gmail used to be in
    UNLIMITED_EMAILS so it bypassed the paywall. Now it must NOT bypass."""
    client, _ = _register_and_verify("mitchellagoma@gmail.com")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302, (
        f"gmail account should be paywall-redirected (got {r.status_code}). "
        "If this fails the admin bypass is leaking again."
    )
    assert r.headers["location"] == "/start-trial"


def test_random_user_is_redirected_to_start_trial():
    """Sanity check: a brand-new user with no subscription, no admin
    privilege, no grandfathering hits /start-trial."""
    client, _ = _register_and_verify("random@example.com")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/start-trial"


def test_grandfathered_user_now_hits_paywall_too():
    """Grandfathered users used to bypass /start-trial. As of the
    'paywall every free user' change, they DON'T anymore — every legacy
    user must enter a card on their next login so they go through the
    same Stripe 7-day-trial flow as everyone else."""
    import database as _db
    client, user = _register_and_verify("grandfathered@example.com")
    _db.update_user(user["id"], grandfathered_trial=1)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302, (
        "grandfathered users must now be paywalled — they enter a card and "
        "start the new 7-day Stripe trial like every other user."
    )
    assert r.headers["location"] == "/start-trial"


def test_grandfathered_user_who_subscribes_lands_on_dashboard():
    """After a grandfathered user completes Stripe Checkout (which sets
    subscription_status='trialing' + trial_end_at), they reach the
    dashboard — same flow as a brand-new user."""
    import time as _time
    import database as _db
    client, user = _register_and_verify("grandfathered-then-subbed@example.com")
    _db.update_user(
        user["id"],
        grandfathered_trial=1,  # legacy flag preserved for analytics
        subscription_status="trialing",
        stripe_subscription_id="sub_test_grandfathered",
        trial_end_at=_time.time() + 7 * 24 * 3600,
    )
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_trialing_user_lands_on_dashboard():
    """Users mid-Stripe-trial (subscription_status='trialing' WITH a real
    stripe_subscription_id) skip the paywall — they already entered a card."""
    import database as _db
    client, user = _register_and_verify("trialing@example.com")
    _db.update_user(
        user["id"],
        subscription_status="trialing",
        stripe_subscription_id="sub_real_trialing",
    )
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_active_subscriber_lands_on_dashboard():
    import database as _db
    client, user = _register_and_verify("active@example.com")
    _db.update_user(
        user["id"],
        subscription_status="active",
        stripe_subscription_id="sub_real_active",
    )
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_unverified_user_goes_to_verify_email_first():
    """Unverified users get bounced to /verify-email regardless of
    subscription state — that gate runs first."""
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/register",
        json={"email": "unverified@example.com", "password": "password12345"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    # Do NOT verify the email.
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/verify-email"


# ---------------------------------------------------------------------------
# Migration — un-grandfather the gmail account on every init_db
# ---------------------------------------------------------------------------

def test_init_db_un_grandfathers_gmail_account():
    """If the gmail account exists with grandfathered_trial=1 in the DB
    (left over from the pre-fix state), the next init_db must clear it.
    Idempotent — running again is a no-op."""
    import database as _db

    # Set up the pre-fix state: gmail account is grandfathered.
    client, user = _register_and_verify("mitchellagoma@gmail.com")
    _db.update_user(user["id"], grandfathered_trial=1)
    user = _db.get_user_by_email("mitchellagoma@gmail.com")
    assert user["grandfathered_trial"] == 1

    # Re-run init_db (the migration). The gmail account should be flipped.
    _db.init_db()
    user = _db.get_user_by_email("mitchellagoma@gmail.com")
    assert user["grandfathered_trial"] == 0, (
        "init_db must un-grandfather the gmail account on every run "
        "(idempotent)."
    )

    # Other grandfathered users are NOT affected.
    other_client, other_user = _register_and_verify("other-grandfathered@example.com")
    _db.update_user(other_user["id"], grandfathered_trial=1)
    _db.init_db()
    other = _db.get_user_by_email("other-grandfathered@example.com")
    assert other["grandfathered_trial"] == 1, (
        "The migration must only target the gmail account; everyone else "
        "keeps their grandfathering."
    )
