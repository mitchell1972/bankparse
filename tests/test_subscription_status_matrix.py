"""
Comprehensive paywall-gating matrix: every (subscription_status,
grandfathered_trial, has_stripe_sub_id) combination, asserted against
BOTH the dashboard gate (`GET /`) and the upload endpoint
(`POST /api/parse` → 200 / 403).

This file is the regression net for the "everyone marked free goes
through Stripe Checkout, everyone marked trialing/active stays on the
dashboard" rule.

If a future change re-introduces an exception (e.g. someone adds
grandfathered_trial back into the dashboard gate), at least one
assertion in this file fails.

The matrix is also a single-glance documentation of the gating policy.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_subscription_matrix.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("UNLIMITED_EMAILS", raising=False)
    # Stub Anthropic key so the parse endpoint doesn't 501 before the
    # paywall check fires.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stub")
    import app as _app_module
    monkeypatch.setattr(_app_module, "ANTHROPIC_API_KEY", "sk-ant-stub")

    try:
        if getattr(_app_module.app.state, "limiter", None) is not None:
            _app_module.app.state.limiter.enabled = False
    except Exception:
        pass
    yield
    try:
        if getattr(_app_module.app.state, "limiter", None) is not None:
            _app_module.app.state.limiter.enabled = True
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_csrf(client):
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _make_user(
    *,
    email: str,
    subscription_status: str | None = None,
    grandfathered_trial: int = 0,
    has_stripe_sub: bool = False,
    trial_end_offset_seconds: int | None = None,
    verify_email: bool = True,
):
    """Build a fully-set-up test user row to the exact shape we want."""
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
    if verify_email:
        _db.mark_email_verified(user["id"])

    update_kwargs = {"grandfathered_trial": grandfathered_trial}
    if subscription_status is not None:
        update_kwargs["subscription_status"] = subscription_status
    if has_stripe_sub:
        update_kwargs["stripe_subscription_id"] = "sub_test_" + email.split("@")[0]
    if trial_end_offset_seconds is not None:
        update_kwargs["trial_end_at"] = time.time() + trial_end_offset_seconds
    _db.update_user(user["id"], **update_kwargs)
    return client, csrf, _db.get_user_by_email(email)


# ---------------------------------------------------------------------------
# The matrix — dashboard gate (GET /)
#
# Each row is: (label, user-row state, expected_status, expected_location)
# expected_status: 200 means "lands on dashboard", 302 means "paywalled"
# expected_location: where we get redirected to when 302
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,state,expected_status,expected_location", [
    # NEW users (no Stripe customer record at all) ----------------------------
    (
        "fresh user, no subscription, no grandfathering",
        {"subscription_status": None, "grandfathered_trial": 0,
         "has_stripe_sub": False},
        302, "/start-trial",
    ),

    # LEGACY users (the 100+ rows in admin showing 'free' + grandfathered=1)
    # The REGRESSION row: these used to bypass; now they MUST be paywalled.
    (
        "legacy grandfathered user, no Stripe sub yet",
        {"subscription_status": None, "grandfathered_trial": 1,
         "has_stripe_sub": False},
        302, "/start-trial",
    ),

    # POST-CHECKOUT users (Stripe webhook fired) ------------------------------
    (
        "trialing user with card on file (active 7-day Stripe trial)",
        {"subscription_status": "trialing", "grandfathered_trial": 0,
         "has_stripe_sub": True, "trial_end_offset_seconds": 7 * 86400},
        200, None,
    ),
    (
        "active paying customer",
        {"subscription_status": "active", "grandfathered_trial": 0,
         "has_stripe_sub": True},
        200, None,
    ),
    (
        "past_due customer (payment failed but grace period)",
        {"subscription_status": "past_due", "grandfathered_trial": 0,
         "has_stripe_sub": True},
        200, None,
    ),

    # CANCELLED — back behind the paywall -------------------------------------
    (
        "cancelled subscriber",
        {"subscription_status": "cancelled", "grandfathered_trial": 0,
         "has_stripe_sub": True},
        302, "/start-trial",
    ),

    # EDGE CASE: grandfathered AND already on Stripe trial --------------------
    # Some legacy users have already gone through the new flow. They MUST
    # reach the dashboard — don't double-paywall someone with a card on file.
    (
        "legacy grandfathered user who already entered card",
        {"subscription_status": "trialing", "grandfathered_trial": 1,
         "has_stripe_sub": True, "trial_end_offset_seconds": 7 * 86400},
        200, None,
    ),

    # EDGE CASE: trialing but Stripe trial window expired ---------------------
    (
        "trialing status but trial_end_at in past — webhook hasn't caught up",
        {"subscription_status": "trialing", "grandfathered_trial": 0,
         "has_stripe_sub": True, "trial_end_offset_seconds": -86400},
        # Dashboard gate only checks subscription_status — Stripe webhook
        # would normally flip status to 'past_due' or 'unpaid' when the
        # trial ends. While the status is still 'trialing' we let them in.
        200, None,
    ),
])
def test_dashboard_gate_per_subscription_status(
    label, state, expected_status, expected_location,
):
    email = "matrix-" + label.lower().replace(" ", "-").replace(",", "")[:30] + "@example.com"
    client, _, _ = _make_user(email=email, **state)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == expected_status, (
        f"[{label}] expected status {expected_status}, got {r.status_code}\n"
        f"body: {r.text[:200]}"
    )
    if expected_location is not None:
        assert r.headers["location"] == expected_location, (
            f"[{label}] expected redirect to {expected_location}, "
            f"got {r.headers.get('location')}"
        )


# ---------------------------------------------------------------------------
# Upload endpoint gate (POST /api/parse) — mirrors the dashboard rules but
# also enforces the quota tier limits from core.check_can_use.
# ---------------------------------------------------------------------------

_TINY_CSV = (
    b"Date,Description,Amount\n"
    b"01/03/2026,TESCO STORES LONDON E14,-12.50\n"
    b"02/03/2026,STRIPE PAYOUT,150.00\n"
)


@pytest.mark.parametrize("label,state,expected_status,expected_detail", [
    (
        "trialing user uploads OK",
        {"subscription_status": "trialing", "grandfathered_trial": 0,
         "has_stripe_sub": True, "trial_end_offset_seconds": 7 * 86400},
        200, None,
    ),
    (
        "free user with no Stripe sub blocked (payment_method_required)",
        {"subscription_status": None, "grandfathered_trial": 0,
         "has_stripe_sub": False},
        403, "PAYMENT_METHOD_REQUIRED",
    ),
    (
        "legacy grandfathered without sub blocked too — REGRESSION test",
        {"subscription_status": None, "grandfathered_trial": 1,
         "has_stripe_sub": False},
        403, "PAYMENT_METHOD_REQUIRED",
    ),
])
def test_upload_endpoint_per_subscription_status(
    label, state, expected_status, expected_detail,
):
    """Note: 'active' subscribers are covered by the dashboard-gate matrix
    above. Asserting the upload endpoint accepts them too needs a full
    Stripe customer + price ID setup (so get_user_tier resolves to a paid
    tier instead of falling back to 'free'); that's covered by the
    integration tests in tests/test_billing_trial.py, not here."""
    email = "uplmat-" + label.lower().replace(" ", "-")[:30] + "@example.com"
    client, csrf, _ = _make_user(email=email, **state)
    r = client.post(
        "/api/parse",
        files={"file": ("tiny.csv", _TINY_CSV, "text/csv")},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == expected_status, (
        f"[{label}] expected {expected_status}, got {r.status_code}\n"
        f"body: {r.text[:200]}"
    )
    if expected_detail:
        body = r.json()
        assert expected_detail in body.get("detail", ""), (
            f"[{label}] expected detail to mention {expected_detail!r}, "
            f"got {body!r}"
        )


# ---------------------------------------------------------------------------
# /start-trial page behaviour
# ---------------------------------------------------------------------------

def test_start_trial_page_shown_to_grandfathered_user():
    """REGRESSION — grandfathered users used to be redirected away from
    /start-trial. They now SEE it so they can enter a card."""
    import database as _db
    client, _, user = _make_user(
        email="gf-sees-trial@example.com",
        grandfathered_trial=1,
    )
    r = client.get("/start-trial", follow_redirects=False)
    assert r.status_code == 200, (
        f"grandfathered user must see /start-trial; got {r.status_code}"
    )


def test_start_trial_page_redirects_trialing_user_to_dashboard():
    """Already-subscribed users shouldn't see the paywall a second time."""
    client, _, _ = _make_user(
        email="already-trialing@example.com",
        subscription_status="trialing",
        has_stripe_sub=True,
        trial_end_offset_seconds=7 * 86400,
    )
    r = client.get("/start-trial", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_start_trial_page_redirects_active_user_to_dashboard():
    client, _, _ = _make_user(
        email="already-active@example.com",
        subscription_status="active",
        has_stripe_sub=True,
    )
    r = client.get("/start-trial", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


# ---------------------------------------------------------------------------
# is_trial_active — the single Stripe-driven path
# ---------------------------------------------------------------------------

def test_is_trial_active_grandfathered_only_returns_false():
    """A user with grandfathered_trial=1 but NO Stripe trial must return
    False — grandfathering no longer grants trial access on its own."""
    from core import is_trial_active
    assert not is_trial_active({"grandfathered_trial": 1})
    assert not is_trial_active(
        {"grandfathered_trial": 1, "subscription_status": None}
    )


def test_is_trial_active_requires_stripe_trialing_status():
    from core import is_trial_active
    future = time.time() + 86400
    past = time.time() - 86400

    assert is_trial_active({
        "subscription_status": "trialing", "trial_end_at": future,
    })
    # Future end but not 'trialing' → False
    assert not is_trial_active({
        "subscription_status": "active", "trial_end_at": future,
    })
    # Trialing but ended → False
    assert not is_trial_active({
        "subscription_status": "trialing", "trial_end_at": past,
    })
    # Trialing but no end at all → False
    assert not is_trial_active({"subscription_status": "trialing"})


# ---------------------------------------------------------------------------
# check_can_use — payment_method_required vs trial_expired
# ---------------------------------------------------------------------------

def test_check_can_use_grandfathered_without_sub_is_payment_method_required():
    """Before this change, a grandfathered user without a Stripe sub got
    `trial_expired` (because they were considered to have a legacy trial
    that ended). Now they get `payment_method_required` — the UI shows
    'Add card to start your 7-day free trial' which is the right CTA."""
    from core import check_can_use
    user = {
        "id": 1, "email": "gf-no-sub@example.com",
        "grandfathered_trial": 1, "subscription_status": None,
        "stripe_subscription_id": None, "trial_end_at": None,
    }
    with patch("core.is_email_verified", return_value=True), \
         patch("core.get_global_daily_ai_spend", return_value=0.0), \
         patch("core.get_user_today_spend", return_value=0.0):
        ok, tier, reason, _ = check_can_use(user, "statement")
    assert ok is False
    assert reason == "payment_method_required", (
        f"expected payment_method_required, got {reason}"
    )


def test_check_can_use_user_with_expired_stripe_trial_is_trial_expired():
    """A user who DID enter a card (has stripe_subscription_id) but whose
    trial window ended → 'trial_expired'. UI shows 'Resubscribe'."""
    from core import check_can_use
    user = {
        "id": 1, "email": "expired@example.com",
        "grandfathered_trial": 0,
        "subscription_status": "incomplete",
        "stripe_subscription_id": "sub_old",
        "trial_end_at": time.time() - 86400,
    }
    with patch("core.is_email_verified", return_value=True), \
         patch("core.get_global_daily_ai_spend", return_value=0.0), \
         patch("core.get_user_today_spend", return_value=0.0):
        ok, tier, reason, _ = check_can_use(user, "statement")
    assert ok is False
    assert reason == "trial_expired", f"expected trial_expired, got {reason}"


def test_check_can_use_active_trialing_user_passes():
    from core import check_can_use
    user = {
        "id": 1, "email": "tr@example.com",
        "grandfathered_trial": 0,
        "subscription_status": "trialing",
        "stripe_subscription_id": "sub_xyz",
        "trial_end_at": time.time() + 7 * 86400,
    }
    with patch("core.is_email_verified", return_value=True), \
         patch("core.get_global_daily_ai_spend", return_value=0.0), \
         patch("core.get_user_today_spend", return_value=0.0):
        ok, tier, reason, _ = check_can_use(user, "statement")
    assert ok is True
    assert reason == "ok"


def test_check_can_use_admin_email_always_passes():
    """The yahoo founder bypasses every paywall check."""
    from core import check_can_use
    user = {
        "id": 1, "email": "mitchell_agoma@yahoo.co.uk",
        "grandfathered_trial": 0, "subscription_status": None,
        "stripe_subscription_id": None, "trial_end_at": None,
    }
    with patch("core.is_email_verified", return_value=True), \
         patch("core.get_global_daily_ai_spend", return_value=0.0), \
         patch("core.get_user_today_spend", return_value=0.0):
        ok, tier, reason, _ = check_can_use(user, "statement")
    assert ok is True
    assert reason == "ok"
