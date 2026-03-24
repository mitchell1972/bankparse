"""
BankParse — Miscellaneous API Tests
Tests for config, health, CSRF, session management, and edge cases.
"""

import os
import sys
import pytest

# Ensure the project root is on sys.path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_misc.db"


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


# -----------------------------------------------------------------------
# 1. Health endpoint
# -----------------------------------------------------------------------

def test_health_endpoint():
    """GET /api/health returns 200 with status=ok, version, and stripe_configured."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "stripe_configured" in data


# -----------------------------------------------------------------------
# 2. Config endpoint — plans
# -----------------------------------------------------------------------

def test_config_plans():
    """GET /api/config returns plans dict with starter/pro/business/enterprise,
    each having name, price, statements, and receipts."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "plans" in data
        plans = data["plans"]
        for tier in ("starter", "pro", "business", "enterprise"):
            assert tier in plans, f"Missing plan: {tier}"
            plan = plans[tier]
            assert "name" in plan, f"{tier} missing 'name'"
            assert "price" in plan, f"{tier} missing 'price'"
            assert "statements" in plan, f"{tier} missing 'statements'"
            assert "receipts" in plan, f"{tier} missing 'receipts'"


# -----------------------------------------------------------------------
# 3. Config endpoint — stripe_publishable_key
# -----------------------------------------------------------------------

def test_config_stripe_key():
    """GET /api/config includes stripe_publishable_key field."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "stripe_publishable_key" in data


# -----------------------------------------------------------------------
# 4. Robots.txt
# -----------------------------------------------------------------------

def test_robots_txt():
    """GET /robots.txt returns 200 and contains Sitemap: and Allow: /landing."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/robots.txt")
        assert resp.status_code == 200
        assert "Sitemap:" in resp.text
        assert "Allow: /landing" in resp.text


# -----------------------------------------------------------------------
# 5. Sitemap.xml
# -----------------------------------------------------------------------

def test_sitemap_xml():
    """GET /sitemap.xml returns 200 and contains bankscanai.com URLs."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert "bankscanai.com" in resp.text


# -----------------------------------------------------------------------
# 6. CSRF — GET sets cookie
# -----------------------------------------------------------------------

def test_csrf_get_sets_cookie():
    """GET /login should set the bp_csrf cookie."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/login")
        assert resp.status_code == 200
        csrf_value = resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")
        assert csrf_value, "Expected bp_csrf cookie to be set on GET /login"


# -----------------------------------------------------------------------
# 7. CSRF — POST without cookie
# -----------------------------------------------------------------------

def test_csrf_post_without_cookie():
    """POST without any CSRF cookie returns 403."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/register",
            json={"email": "nocsrf@example.com", "password": "password1234"},
        )
        assert resp.status_code == 403


# -----------------------------------------------------------------------
# 8. CSRF — POST with wrong token
# -----------------------------------------------------------------------

def test_csrf_post_wrong_token():
    """POST with mismatched X-CSRF-Token header returns 403."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        resp = client.post(
            "/api/register",
            json={"email": "badcsrf@example.com", "password": "password1234"},
            headers={"X-CSRF-Token": "wrong-token-value"},
            cookies={"bp_csrf": csrf},
        )
        assert resp.status_code == 403


# -----------------------------------------------------------------------
# 9. CSRF — POST with correct token
# -----------------------------------------------------------------------

def test_csrf_post_correct_token():
    """POST with matching cookie + header passes CSRF check (request proceeds)."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        resp = client.post(
            "/api/register",
            json={"email": "goodcsrf@example.com", "password": "password1234"},
            headers={"X-CSRF-Token": csrf},
        )
        # Should NOT be 403 — CSRF passed; expect 200 for valid registration
        assert resp.status_code != 403
        assert resp.status_code == 200


# -----------------------------------------------------------------------
# 10. CSRF — exempt paths
# -----------------------------------------------------------------------

def test_csrf_exempt_stripe_webhook():
    """POST /api/stripe-webhook should skip CSRF validation (not return 403)."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/stripe-webhook",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
        # Should NOT be 403 — this path is CSRF-exempt.
        # It may return 400/500 due to missing Stripe signature, but not 403.
        assert resp.status_code != 403


# -----------------------------------------------------------------------
# 11. Usage endpoint — unauthenticated
# -----------------------------------------------------------------------

def test_usage_unauthenticated():
    """GET /api/usage without auth returns default usage (0 statements, 0 receipts)."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] is None
        assert data["statements_used"] == 0
        assert data["receipts_used"] == 0


# -----------------------------------------------------------------------
# 12. Usage endpoint — authenticated
# -----------------------------------------------------------------------

def test_usage_authenticated():
    """GET /api/usage with auth returns the user's actual usage data."""
    with TestClient(app, raise_server_exceptions=False) as client:
        reg_resp = _register(client, "usage@example.com", "password1234")
        assert reg_resp.status_code == 200
        auth_cookie = reg_resp.cookies.get("bp_auth", "")

        resp = client.get("/api/usage", cookies={"bp_auth": auth_cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "usage@example.com"
        assert "statements_used" in data
        assert "receipts_used" in data


# -----------------------------------------------------------------------
# 13. Landing page
# -----------------------------------------------------------------------

def test_landing_page():
    """GET /landing returns 200 with HTML content."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/landing")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


# -----------------------------------------------------------------------
# 14. Login page
# -----------------------------------------------------------------------

def test_login_page():
    """GET /login returns 200 with HTML containing form elements."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        # Login page should contain a form or sign-in related content
        text = resp.text.lower()
        assert "sign in" in text or "<form" in text or "login" in text


# -----------------------------------------------------------------------
# 15. Home redirect
# -----------------------------------------------------------------------

def test_home_redirects_to_landing():
    """GET / without auth redirects to /landing (302)."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/landing" in resp.headers.get("location", "")


# -----------------------------------------------------------------------
# 16. Home authenticated
# -----------------------------------------------------------------------

def test_home_authenticated():
    """GET / with auth returns 200 (dashboard)."""
    with TestClient(app, raise_server_exceptions=False) as client:
        reg_resp = _register(client, "home@example.com", "password1234")
        assert reg_resp.status_code == 200
        auth_cookie = reg_resp.cookies.get("bp_auth", "")

        resp = client.get("/", cookies={"bp_auth": auth_cookie}, follow_redirects=False)
        assert resp.status_code == 200


# -----------------------------------------------------------------------
# 17. 404 handling
# -----------------------------------------------------------------------

def test_404_handling():
    """GET /nonexistent returns 404."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/nonexistent")
        assert resp.status_code == 404


# -----------------------------------------------------------------------
# 18. POST with invalid Content-Type
# -----------------------------------------------------------------------

def test_post_invalid_content_type():
    """POST /api/login with text/plain content type returns an error."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        resp = client.post(
            "/api/login",
            content="email=bad@example.com&password=password1234",
            headers={
                "Content-Type": "text/plain",
                "X-CSRF-Token": csrf,
            },
        )
        # Should fail — the endpoint expects JSON, not text/plain
        assert resp.status_code in (400, 422, 500)


# -----------------------------------------------------------------------
# 19. Empty POST body
# -----------------------------------------------------------------------

def test_empty_post_body():
    """POST /api/login with empty body returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        resp = client.post(
            "/api/login",
            content=b"",
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf,
            },
        )
        # Empty body cannot be parsed as JSON — expect 400 or 422
        assert resp.status_code in (400, 422, 500)
