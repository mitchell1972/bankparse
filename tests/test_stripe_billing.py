"""
BankParse -- Stripe Billing Tests
Tests for create-checkout, verify-session, stripe-webhook, manage-billing,
config, and health endpoints.  Uses unittest.mock to avoid real Stripe calls.
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

# Ensure the project root is on sys.path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_stripe.db"


@pytest.fixture(autouse=True)
def clean_db():
    """Delete and reinit the test database before each test."""
    import database

    # Remove old test db if present
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)

    # Close existing connection if any
    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
    database._sqlite_conn = None

    # Monkey-patch _get_sqlite to use the test db path
    import sqlite3

    def _get_sqlite_test():
        if database._sqlite_conn is None:
            conn = sqlite3.connect(TEST_DB_PATH, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            database._sqlite_conn = conn
        return database._sqlite_conn

    database._get_sqlite = _get_sqlite_test

    # Re-initialize schema on test db
    database.init_db()
    yield

    # Cleanup after test
    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


from fastapi.testclient import TestClient
from app import app


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_auth.py)
# ---------------------------------------------------------------------------

def _get_csrf_token(client: TestClient) -> str:
    """Hit GET /login to get a CSRF cookie into the client jar, return its value."""
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _register(client: TestClient, email: str, password: str):
    """Register a user, handling CSRF automatically. Returns the response."""
    csrf = _get_csrf_token(client)
    return client.post(
        "/api/register",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )


def _login(client: TestClient, email: str, password: str):
    """Login a user, handling CSRF automatically. Returns the response."""
    csrf = client.cookies.get("bp_csrf", "") or _get_csrf_token(client)
    return client.post(
        "/api/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )


def _auth_client(client: TestClient, email: str = "billing@example.com", password: str = "securepass123"):
    """Register + login a user. Returns (client, csrf_token) with auth cookies set."""
    _register(client, email, password)
    _login(client, email, password)
    csrf = client.cookies.get("bp_csrf", "") or _get_csrf_token(client)
    return csrf


# ---------------------------------------------------------------------------
# 1. Create checkout - not authenticated
# ---------------------------------------------------------------------------

def test_create_checkout_not_authenticated():
    """POST /api/create-checkout without auth returns 401."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = client.post(
                "/api/create-checkout",
                json={"plan": "starter"},
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. Create checkout - missing email (user has no email -- not possible
#    with auth, so we test that a logged-in user without a plan body works
#    since email comes from user record; test missing plan defaults to starter
#    which still needs price configured.  We interpret "missing email" as
#    unauthenticated -> 401.)
# ---------------------------------------------------------------------------

def test_create_checkout_missing_email_unauthenticated():
    """POST /api/create-checkout with no auth (and thus no email) returns 401."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = client.post(
                "/api/create-checkout",
                json={},
                headers={"X-CSRF-Token": csrf},
            )
            # No auth means no email -> 401
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. Create checkout - invalid plan (no price configured for it)
# ---------------------------------------------------------------------------

def test_create_checkout_invalid_plan():
    """POST /api/create-checkout with unknown plan returns 500 (no price configured)."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _auth_client(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"), \
             patch("app.STRIPE_STARTER_PRICE_ID", ""), \
             patch("app.STRIPE_PRO_PRICE_ID", ""), \
             patch("app.STRIPE_BUSINESS_PRICE_ID", ""), \
             patch("app.STRIPE_ENTERPRISE_PRICE_ID", ""):
            resp = client.post(
                "/api/create-checkout",
                json={"plan": "nonexistent_plan"},
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 500
            assert "price not configured" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# 4. Create checkout - Stripe not configured
# ---------------------------------------------------------------------------

def test_create_checkout_stripe_not_configured():
    """POST /api/create-checkout when Stripe is unavailable returns 501."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _auth_client(client)
        with patch("app.STRIPE_AVAILABLE", False), \
             patch("app.STRIPE_SECRET_KEY", ""):
            resp = client.post(
                "/api/create-checkout",
                json={"plan": "starter"},
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 501
            assert "not configured" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# 5. Verify session - missing session_id param
# ---------------------------------------------------------------------------

def test_verify_session_missing_session_id():
    """GET /api/verify-session without session_id query param returns 422."""
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = client.get("/api/verify-session")
            assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 6. Verify session - Stripe not configured
# ---------------------------------------------------------------------------

def test_verify_session_stripe_not_configured():
    """GET /api/verify-session when Stripe is unavailable returns 501."""
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch("app.STRIPE_AVAILABLE", False), \
             patch("app.STRIPE_SECRET_KEY", ""):
            resp = client.get("/api/verify-session", params={"session_id": "cs_test_123"})
            assert resp.status_code == 501
            assert "not configured" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# 7. Webhook - missing signature
# ---------------------------------------------------------------------------

def test_webhook_missing_signature():
    """POST /api/stripe-webhook without stripe-signature header returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"), \
             patch("app.STRIPE_WEBHOOK_SECRET", "whsec_test_fake"), \
             patch("app.stripe.Webhook.construct_event", side_effect=ValueError("No signature")):
            resp = client.post(
                "/api/stripe-webhook",
                content=json.dumps({"type": "test"}),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 8. Webhook - invalid signature
# ---------------------------------------------------------------------------

def test_webhook_invalid_signature():
    """POST /api/stripe-webhook with wrong signature returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        import stripe as stripe_mod
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"), \
             patch("app.STRIPE_WEBHOOK_SECRET", "whsec_test_fake"), \
             patch("app.stripe.Webhook.construct_event",
                   side_effect=stripe_mod.error.SignatureVerificationError("bad sig", "sig_header")):
            resp = client.post(
                "/api/stripe-webhook",
                content=json.dumps({"type": "test"}),
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=123,v1=badsig",
                },
            )
            assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 9. Webhook - missing webhook secret
# ---------------------------------------------------------------------------

def test_webhook_missing_webhook_secret():
    """POST /api/stripe-webhook with no STRIPE_WEBHOOK_SECRET returns 500."""
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"), \
             patch("app.STRIPE_WEBHOOK_SECRET", ""):
            resp = client.post(
                "/api/stripe-webhook",
                content=json.dumps({"type": "test"}),
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=123,v1=sig",
                },
            )
            assert resp.status_code == 500
            assert "webhook secret" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# 10. Webhook - Stripe not available (ignored, returns 200)
# ---------------------------------------------------------------------------

def test_webhook_stripe_not_available():
    """POST /api/stripe-webhook when Stripe is not available returns 200 ignored."""
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch("app.STRIPE_AVAILABLE", False), \
             patch("app.STRIPE_SECRET_KEY", ""):
            resp = client.post(
                "/api/stripe-webhook",
                content=json.dumps({"type": "test"}),
                headers={
                    "Content-Type": "application/json",
                    "stripe-signature": "t=123,v1=sig",
                },
            )
            assert resp.status_code == 200
            assert resp.json().get("status") == "ignored"


# ---------------------------------------------------------------------------
# 11. Config endpoint - returns all plan info
# ---------------------------------------------------------------------------

def test_config_returns_all_plans():
    """GET /api/config returns 4 plans with correct structure."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()

        plans = data.get("plans", {})
        expected_plan_names = {"starter", "pro", "business", "enterprise"}
        assert set(plans.keys()) == expected_plan_names

        for plan_key in expected_plan_names:
            plan = plans[plan_key]
            assert "price" in plan, f"Plan '{plan_key}' missing 'price'"
            assert "name" in plan, f"Plan '{plan_key}' missing 'name'"
            assert "statements" in plan, f"Plan '{plan_key}' missing 'statements'"
            assert "receipts" in plan, f"Plan '{plan_key}' missing 'receipts'"
            assert "clients" in plan, f"Plan '{plan_key}' missing 'clients'"


# ---------------------------------------------------------------------------
# 12. Config endpoint - stripe key present
# ---------------------------------------------------------------------------

def test_config_stripe_key_present():
    """GET /api/config response contains stripe_publishable_key."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "stripe_publishable_key" in data


# ---------------------------------------------------------------------------
# 13. Health endpoint - returns ok
# ---------------------------------------------------------------------------

def test_health_returns_ok():
    """GET /api/health returns status ok and version field."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


# ---------------------------------------------------------------------------
# 14. Manage billing - not authenticated
# ---------------------------------------------------------------------------

def test_manage_billing_not_authenticated():
    """POST /api/manage-billing without auth returns 401."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = client.post(
                "/api/manage-billing",
                json={},
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 15. Manage billing - no Stripe customer
# ---------------------------------------------------------------------------

def test_manage_billing_no_stripe_customer():
    """POST /api/manage-billing for user without stripe_customer_id returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _auth_client(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = client.post(
                "/api/manage-billing",
                json={},
                headers={"X-CSRF-Token": csrf},
            )
            assert resp.status_code == 400
            assert "no subscription" in resp.json().get("detail", "").lower()
