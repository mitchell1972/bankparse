"""
Tests for the /ledger HTML page route + dashboard nav link.
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_ledger_page.db"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")


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


def _verified_subscriber(email: str) -> tuple:
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    client.post("/api/register",
                json={"email": email, "password": "password12345"},
                headers={"X-CSRF-Token": csrf})
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"],
                    subscription_status="trialing",
                    stripe_subscription_id="sub_ledger_page",
                    trial_end_at=time.time() + 7*86400)
    return client, user, csrf


# ---------------------------------------------------------------------------
# /ledger page
# ---------------------------------------------------------------------------


def test_ledger_page_redirects_to_login_when_unauthenticated():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/ledger", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


def test_ledger_page_redirects_to_paywall_for_non_subscriber():
    """The paywall gate applies — non-paying users go to /start-trial,
    not to the ledger page."""
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    client.post("/api/register",
                json={"email": "free@example.com", "password": "password12345"},
                headers={"X-CSRF-Token": csrf})
    user = _db.get_user_by_email("free@example.com")
    _db.mark_email_verified(user["id"])
    # No Stripe sub — should bounce to /start-trial
    r = client.get("/ledger", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/start-trial"


def test_ledger_page_renders_for_subscriber():
    client, user, _ = _verified_subscriber("paid@example.com")
    r = client.get("/ledger")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    html = r.text
    # The page identifies itself
    assert "HMRC Ledger" in html
    # All four data sources are wired in
    assert "/api/ledger" in html
    assert "/api/audit-summary" in html
    assert "/api/tax-forecast" in html
    # The accountant ZIP download is exposed
    assert "/api/accountant-export" in html
    # The certificate download is exposed
    assert "/api/audit-certificate" in html


def test_ledger_page_is_noindex():
    """Customer dashboard pages must never be indexed by Google."""
    client, user, _ = _verified_subscriber("noindex@example.com")
    r = client.get("/ledger")
    assert "noindex" in r.text


def test_ledger_page_yahoo_admin_bypasses_paywall():
    """Yahoo admin should always reach /ledger without needing a Stripe sub."""
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    client.post("/api/register",
                json={"email": "mitchell_agoma@yahoo.co.uk", "password": "password12345"},
                headers={"X-CSRF-Token": csrf})
    user = _db.get_user_by_email("mitchell_agoma@yahoo.co.uk")
    _db.mark_email_verified(user["id"])
    r = client.get("/ledger")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Dashboard nav link
# ---------------------------------------------------------------------------


def test_dashboard_has_ledger_link_in_header():
    """Discovery: a logged-in user on / must see a clear link to /ledger."""
    client, user, _ = _verified_subscriber("nav@example.com")
    r = client.get("/")
    # /ledger href must appear in the page
    assert "/ledger" in r.text
    # And specifically the visible nav element
    assert "HMRC Ledger" in r.text
