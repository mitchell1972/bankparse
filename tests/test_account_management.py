"""
Comprehensive tests for the account-management surface:

  - /api/cancel-subscription
  - /api/forgot-password
  - /api/reset-password

Each behaviour is pinned: cancel only works for users with a real Stripe
sub; forgot-password never enumerates accounts; reset-password requires a
single-use, time-bounded token; password_hash is replaced cleanly without
disturbing other user fields.
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_account_mgmt.db"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.delenv("UNLIMITED_EMAILS", raising=False)


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


def _register_and_verify(email: str, password: str = "password12345"):
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/register",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    return client, _db.get_user_by_email(email), csrf


# ---------------------------------------------------------------------------
# /api/cancel-subscription
# ---------------------------------------------------------------------------

def test_cancel_subscription_requires_auth():
    """No bp_auth cookie → 401."""
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post("/api/cancel-subscription", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401


def test_cancel_subscription_refuses_users_without_a_stripe_sub():
    """Free-tier users — or anyone with subscription_status set in the DB
    but stripe_subscription_id NULL — cannot cancel because there's nothing
    to cancel. 400 with a clear message."""
    client, user, csrf = _register_and_verify("free@example.com")
    r = client.post("/api/cancel-subscription", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
    assert "no active subscription" in r.json()["detail"].lower()


def test_cancel_subscription_marks_cancel_at_period_end():
    """Happy path: the endpoint calls stripe.Subscription.modify with
    cancel_at_period_end=True. The user keeps access until period end."""
    import database as _db
    client, user, csrf = _register_and_verify("paying@example.com")
    _db.update_user(
        user["id"],
        subscription_status="active",
        stripe_subscription_id="sub_paying_test",
        stripe_customer_id="cus_paying_test",
    )

    period_end_ts = int(time.time() + 7 * 86400)
    fake_sub = MagicMock(cancel_at=period_end_ts, current_period_end=period_end_ts)
    with patch("stripe.Subscription.modify", return_value=fake_sub) as modify_mock:
        r = client.post("/api/cancel-subscription", headers={"X-CSRF-Token": csrf})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["cancel_at"] == period_end_ts
    # Confirm the Stripe call was made with the right flag
    modify_mock.assert_called_once_with(
        "sub_paying_test",
        cancel_at_period_end=True,
    )


def test_cancel_subscription_handles_stripe_error_with_500():
    import database as _db
    client, user, csrf = _register_and_verify("payer2@example.com")
    _db.update_user(
        user["id"],
        subscription_status="active",
        stripe_subscription_id="sub_will_fail",
    )
    with patch("stripe.Subscription.modify", side_effect=RuntimeError("stripe outage")):
        r = client.post("/api/cancel-subscription", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 500
    assert "cancellation failed" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /api/forgot-password
# ---------------------------------------------------------------------------

def test_forgot_password_returns_200_for_unknown_email_no_enumeration():
    """Always 200. We must NOT leak whether the email is registered —
    attackers could harvest the user list otherwise."""
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/forgot-password",
        json={"email": "ghost@example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert "if an account exists" in r.json()["message"].lower()


def test_forgot_password_returns_200_for_known_email_and_creates_token():
    """For a real account, the endpoint must also return 200 (same shape)
    AND have stored a single-use reset token in the DB."""
    client, user, csrf = _register_and_verify("forgetful@example.com")
    with patch("otp.send_password_reset_email", return_value=True) as send_mock:
        r = client.post(
            "/api/forgot-password",
            json={"email": "forgetful@example.com"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200

    # Sanity: a reset token now exists in the DB.
    import database as _db
    conn = _db._get_sqlite()
    rows = conn.execute(
        "SELECT token, user_id, used FROM password_reset_tokens WHERE user_id = ?",
        (user["id"],),
    ).fetchall()
    assert len(rows) == 1
    token, uid, used = rows[0]
    assert uid == user["id"]
    assert used == 0
    # The email-send was attempted with the link containing the token.
    assert send_mock.called
    sent_link = send_mock.call_args[0][1]
    assert token in sent_link


def test_forgot_password_rejects_missing_email():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/forgot-password",
        json={"email": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_forgot_password_invalidates_prior_unused_tokens():
    """If a user requests a reset twice, the FIRST token must no longer
    work — only the most recent stands. Stops attackers replaying old
    tokens captured from email logs."""
    client, user, csrf = _register_and_verify("resetagain@example.com")
    import database as _db

    with patch("otp.send_password_reset_email", return_value=True):
        client.post("/api/forgot-password",
                    json={"email": "resetagain@example.com"},
                    headers={"X-CSRF-Token": csrf})
        client.post("/api/forgot-password",
                    json={"email": "resetagain@example.com"},
                    headers={"X-CSRF-Token": csrf})

    conn = _db._get_sqlite()
    rows = conn.execute(
        "SELECT token, used FROM password_reset_tokens WHERE user_id = ? ORDER BY created_at",
        (user["id"],),
    ).fetchall()
    assert len(rows) == 2
    first_token, first_used = rows[0]
    second_token, second_used = rows[1]
    assert first_used == 1   # invalidated by the second request
    assert second_used == 0  # still valid


# ---------------------------------------------------------------------------
# /api/reset-password
# ---------------------------------------------------------------------------

def test_reset_password_with_valid_token_updates_password():
    """End-to-end: forgot → consume token → log in with new password."""
    client, user, csrf = _register_and_verify("changemypw@example.com", "oldpass12345")
    import database as _db

    # Generate the token directly (skip the email roundtrip)
    token = _db.create_password_reset_token(user["id"])

    r = client.post(
        "/api/reset-password",
        json={"token": token, "password": "newpass67890"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text

    # Old password no longer works
    r_old = client.post(
        "/api/login",
        json={"email": "changemypw@example.com", "password": "oldpass12345"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r_old.status_code == 401

    # New password works
    r_new = client.post(
        "/api/login",
        json={"email": "changemypw@example.com", "password": "newpass67890"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r_new.status_code == 200


def test_reset_password_token_is_single_use():
    """Same token, used twice → second use rejected."""
    client, user, csrf = _register_and_verify("singleuse@example.com")
    import database as _db
    token = _db.create_password_reset_token(user["id"])

    r1 = client.post(
        "/api/reset-password",
        json={"token": token, "password": "newpass1234"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/api/reset-password",
        json={"token": token, "password": "anotherpass"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r2.status_code == 400
    assert "invalid or expired" in r2.json()["detail"].lower()


def test_reset_password_rejects_expired_token():
    """Token older than TTL → 400. Backdate created_at to simulate."""
    client, user, csrf = _register_and_verify("expired@example.com")
    import database as _db
    token = _db.create_password_reset_token(user["id"])

    # Backdate the token to be older than the TTL.
    conn = _db._get_sqlite()
    conn.execute(
        "UPDATE password_reset_tokens SET created_at = ? WHERE token = ?",
        (time.time() - _db.PASSWORD_RESET_TTL_SECONDS - 60, token),
    )
    conn.commit()

    r = client.post(
        "/api/reset-password",
        json={"token": token, "password": "newpass1234"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_reset_password_rejects_unknown_token():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/reset-password",
        json={"token": "totally-fake-token-12345", "password": "newpass1234"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_reset_password_rejects_short_passwords():
    client, user, csrf = _register_and_verify("shortpw@example.com")
    import database as _db
    token = _db.create_password_reset_token(user["id"])
    r = client.post(
        "/api/reset-password",
        json={"token": token, "password": "short"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "at least 8" in r.json()["detail"]


def test_reset_password_does_not_disturb_other_user_fields():
    """Critical: changing the password must NOT wipe the user's Stripe
    subscription state, email_verified flag, credit balance, etc."""
    client, user, csrf = _register_and_verify("preserved@example.com")
    import database as _db
    _db.update_user(
        user["id"],
        subscription_status="active",
        stripe_subscription_id="sub_keep_me",
        stripe_customer_id="cus_keep_me",
        ai_credit_balance_gbp=12.34,
    )
    token = _db.create_password_reset_token(user["id"])

    r = client.post(
        "/api/reset-password",
        json={"token": token, "password": "newpass1234"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200

    fresh = _db.get_user_by_id(user["id"])
    assert fresh["subscription_status"] == "active"
    assert fresh["stripe_subscription_id"] == "sub_keep_me"
    assert fresh["stripe_customer_id"] == "cus_keep_me"
    assert fresh["ai_credit_balance_gbp"] == 12.34
    assert fresh["email_verified"] == 1


# ---------------------------------------------------------------------------
# Pages render (smoke)
# ---------------------------------------------------------------------------

def test_forgot_password_page_renders():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/forgot-password")
    assert r.status_code == 200
    assert "forgot your password" in r.text.lower() or "reset" in r.text.lower()


def test_reset_password_page_renders():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/reset-password?token=xyz")
    assert r.status_code == 200
    assert "new password" in r.text.lower() or "reset" in r.text.lower()


def test_login_page_links_to_forgot_password():
    """The login page must expose a 'Forgot your password?' link —
    otherwise users have no way to discover the new flow."""
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/login")
    assert r.status_code == 200
    assert "/forgot-password" in r.text


# ---------------------------------------------------------------------------
# Universal paywall contract — not just live.co.uk
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("email", [
    "freebie1@example.com",
    "freebie2@gmail.com",
    "tester+labels@outlook.com",
    "UPPER@CASE.com",                              # case-normalised
    "user.with.dots@subdomain.example.co.uk",
])
def test_every_non_paying_user_hits_paywall_on_dashboard(email):
    """The paywall must fire for ANY non-paying user, not just live.co.uk."""
    client, user, csrf = _register_and_verify(email.lower())
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302, (
        f"User {email} reached the dashboard without a subscription. "
        "The paywall must apply universally."
    )
    assert r.headers["location"] == "/start-trial"


def test_paying_user_with_stripe_sub_reaches_dashboard():
    """Sanity: don't accidentally block subscribers."""
    import database as _db
    client, user, csrf = _register_and_verify("subscriber@example.com")
    _db.update_user(
        user["id"],
        subscription_status="active",
        stripe_subscription_id="sub_real_active",
    )
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200


def test_yahoo_admin_bypasses_universally():
    client, user, csrf = _register_and_verify("mitchell_agoma@yahoo.co.uk")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
