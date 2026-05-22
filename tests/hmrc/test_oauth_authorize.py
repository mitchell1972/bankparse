"""
Tests for the OAuth authorize URL — specifically the prompt=login pathway
that fixes the OAUTH_NINO_MISMATCH issue.

Background: HMRC's sandbox holds onto a Government Gateway session cookie
across OAuth rounds. A developer who mints a fresh test individual on the
dashboard, then clicks Connect to HMRC, gets silently OAuthed back in as
the *previous* test identity — token NINO ≠ minted NINO → all subsequent
create-test-business calls hit MATCHING_RESOURCE_NOT_FOUND.

The fix routes Mint / Disconnect-and-retry flows through
`/api/hmrc/connect?fresh=1`, which adds `prompt=login` to HMRC's authorize
URL so the sticky session cookie is dropped and the user gets a fresh
sign-in form.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
import urllib.parse as _urlparse

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


TEST_DB_PATH = "/tmp/test_bankparse_hmrc_oauth_authorize.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("HMRC_ENV", "sandbox")
    monkeypatch.setenv("HMRC_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("HMRC_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("HMRC_REDIRECT_URI", "https://test.bankscanai.com/api/hmrc/callback")

    from hmrc import config as _cfg
    from hmrc.services import crypto as _crypto
    monkeypatch.setattr(_cfg, "HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setattr(_cfg, "HMRC_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(_cfg, "HMRC_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(_cfg, "HMRC_REDIRECT_URI", "https://test.bankscanai.com/api/hmrc/callback")
    monkeypatch.setattr(_crypto, "_KEY_CACHE", None)

    try:
        from app import app as _app
        if getattr(_app.state, "limiter", None) is not None:
            _app.state.limiter.enabled = False
    except Exception:
        pass
    yield
    try:
        from app import app as _app
        if getattr(_app.state, "limiter", None) is not None:
            _app.state.limiter.enabled = True
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
# Pure-function tests for build_authorize_url
# ---------------------------------------------------------------------------

def _parse_query(url: str) -> dict[str, str]:
    qs = _urlparse.urlparse(url).query
    return {k: v[0] for k, v in _urlparse.parse_qs(qs).items()}


def test_authorize_url_default_has_no_prompt_param():
    """Belt-and-braces: the default authorize URL must NOT include
    prompt=login, otherwise we'd needlessly force re-auth on every
    routine reconnect."""
    from hmrc.services import oauth as _oauth
    url = _oauth.build_authorize_url("state-token-123")
    params = _parse_query(url)
    assert "prompt" not in params
    assert params["response_type"] == "code"
    assert params["state"] == "state-token-123"


def test_authorize_url_with_prompt_login_includes_prompt_login():
    """When prompt_login=True, the URL must include prompt=login so HMRC
    drops any sticky GG session cookie before letting the user grant
    consent. This is the root-cause fix for OAUTH_NINO_MISMATCH after
    minting a fresh sandbox test individual."""
    from hmrc.services import oauth as _oauth
    url = _oauth.build_authorize_url("state-token-123", prompt_login=True)
    params = _parse_query(url)
    assert params.get("prompt") == "login"
    # All the other required OAuth 2.0 params must still be present.
    assert params["response_type"] == "code"
    assert params["state"] == "state-token-123"
    assert params["client_id"] == "test-client-id"


def test_authorize_url_explicit_false_omits_prompt():
    """Explicit prompt_login=False behaves the same as the default."""
    from hmrc.services import oauth as _oauth
    url = _oauth.build_authorize_url("s", prompt_login=False)
    assert "prompt=" not in url


# ---------------------------------------------------------------------------
# Route tests for /api/hmrc/connect — query param wiring
# ---------------------------------------------------------------------------

def _seed_csrf(client):
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_user(email="oauth-authorize-test@example.com"):
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
    _db.update_user(user["id"], grandfathered_trial=1)
    return client, csrf, user


def test_connect_route_without_fresh_param_omits_prompt_login():
    """Plain /api/hmrc/connect must NOT add prompt=login — that's the
    routine reconnect path, no need to force re-auth."""
    client, _, _ = _client_with_user()
    r = client.get("/api/hmrc/connect", follow_redirects=False)
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert "/oauth/authorize" in location
    assert "prompt=login" not in location


def test_connect_route_with_fresh_param_adds_prompt_login():
    """/api/hmrc/connect?fresh=1 MUST add prompt=login to the HMRC
    authorize URL. This is the user-facing entry point that fixes
    OAUTH_NINO_MISMATCH after minting a fresh test user."""
    client, _, _ = _client_with_user(email="fresh-1@example.com")
    r = client.get("/api/hmrc/connect?fresh=1", follow_redirects=False)
    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert "/oauth/authorize" in location
    assert "prompt=login" in location


@pytest.mark.parametrize("truthy", ["1", "true", "yes"])
def test_connect_route_accepts_multiple_truthy_values(truthy):
    """Defensive: accept the common truthy spellings so future callers
    don't accidentally get the default behaviour by typing 'true'
    instead of '1'."""
    client, _, _ = _client_with_user(email=f"fresh-{truthy}@example.com")
    r = client.get(f"/api/hmrc/connect?fresh={truthy}", follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "prompt=login" in r.headers["location"]


def test_connect_route_with_falsy_fresh_omits_prompt_login():
    """fresh=0 or fresh=anything-else must NOT add prompt=login —
    avoids the inverse failure mode of always-on prompt=login if a
    caller mis-spells the flag."""
    client, _, _ = _client_with_user(email="not-fresh@example.com")
    r = client.get("/api/hmrc/connect?fresh=0", follow_redirects=False)
    assert r.status_code == 302
    assert "prompt=login" not in r.headers["location"]
