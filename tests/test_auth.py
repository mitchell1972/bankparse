"""
BankParse — Auth Flow Tests
Tests for register, login, logout, usage, protected routes, and CSRF.
"""

import os
import sys
import pytest

# Ensure the project root is on sys.path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse.db"


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
    # The CSRF cookie is now in the client's cookie jar
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
    # Reuse existing CSRF token from cookie jar if available
    csrf = client.cookies.get("bp_csrf", "") or _get_csrf_token(client)
    return client.post(
        "/api/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )


# -----------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------


def test_home_redirects_to_login():
    """GET / without auth cookie returns 302 redirect to /login."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("location", "")


def test_login_page_loads():
    """GET /login returns 200 with 'Sign In' in body."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "Sign In" in resp.text


def test_register_success():
    """POST /api/register with valid data returns 200 and sets bp_auth cookie."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = _register(client, "newuser@example.com", "securepass123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["email"] == "newuser@example.com"
        assert "bp_auth" in resp.cookies


def test_register_duplicate_email():
    """Register same email twice returns 409."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp1 = _register(client, "dup@example.com", "password1234")
        assert resp1.status_code == 200

        resp2 = _register(client, "dup@example.com", "password5678")
        assert resp2.status_code == 409


def test_register_short_password():
    """Password < 8 chars returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = _register(client, "short@example.com", "abc")
        assert resp.status_code == 400


def test_register_invalid_email():
    """Email without @ returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = _register(client, "not-an-email", "password1234")
        assert resp.status_code == 400


def test_login_success():
    """Register then login with correct credentials returns 200."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _register(client, "loginuser@example.com", "password1234")
        resp = _login(client, "loginuser@example.com", "password1234")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["email"] == "loginuser@example.com"


def test_login_wrong_password():
    """Login with wrong password returns 401."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _register(client, "wrongpw@example.com", "correctpass1")
        resp = _login(client, "wrongpw@example.com", "wrongpass99")
        assert resp.status_code == 401


def test_login_nonexistent_email():
    """Login with unregistered email returns 401."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = _login(client, "nobody@example.com", "password1234")
        assert resp.status_code == 401


def test_logout():
    """Login then POST /api/logout clears cookie, GET / redirects to /login."""
    with TestClient(app, raise_server_exceptions=False) as client:
        reg_resp = _register(client, "logout@example.com", "password1234")
        assert reg_resp.status_code == 200

        # Now logout — need CSRF from cookie jar
        csrf = client.cookies.get("bp_csrf", "") or _get_csrf_token(client)
        logout_resp = client.post(
            "/api/logout",
            headers={"X-CSRF-Token": csrf},
        )
        assert logout_resp.status_code == 200

        # After logout, visiting / without auth should redirect
        home_resp = client.get("/", follow_redirects=False)
        assert home_resp.status_code == 302
        assert "/login" in home_resp.headers.get("location", "")


def test_usage_authenticated():
    """Login then GET /api/usage returns user's usage data with email."""
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


def test_usage_unauthenticated():
    """GET /api/usage without auth returns 200 with email=None (unauthenticated view)."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] is None
        assert data["statements_used"] == 0


def test_parse_requires_auth():
    """POST /api/parse without auth returns 401."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        resp = client.post(
            "/api/parse",
            files={"file": ("test.pdf", b"%PDF-1.4 fake", "application/pdf")},
            headers={"X-CSRF-Token": csrf},
            cookies={"bp_csrf": csrf},
        )
        assert resp.status_code == 401


def test_csrf_required():
    """POST /api/register without X-CSRF-Token header returns 403."""
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/register",
            json={"email": "nocsrf@example.com", "password": "password1234"},
        )
        assert resp.status_code == 403


def test_csrf_wrong_token():
    """POST with mismatched CSRF cookie and header returns 403."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        resp = client.post(
            "/api/register",
            json={"email": "badcsrf@example.com", "password": "password1234"},
            headers={"X-CSRF-Token": "wrong-token-value"},
            cookies={"bp_csrf": csrf},
        )
        assert resp.status_code == 403
