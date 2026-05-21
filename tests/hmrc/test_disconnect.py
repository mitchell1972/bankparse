"""
Tests for POST /api/hmrc/disconnect — the "sign in as a different test
user" escape hatch. Without this, a user who minted a fresh sandbox NINO
is stuck on the connect page because /hmrc/connect refuses to re-OAuth
when they're already connected.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_disconnect.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("HMRC_ENV", "sandbox")

    from hmrc import config as _cfg
    from hmrc.services import crypto as _crypto
    monkeypatch.setattr(_cfg, "HMRC_TOKEN_ENCRYPTION_KEY", test_key)
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


def _seed_csrf(client):
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_user(email="disconnect-test@example.com"):
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


def _connect_with_nino(user_id: int, nino: str = "CX139207A"):
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user_id, access_token="sandbox-access-token",
        refresh_token="sandbox-refresh-token",
        expires_in_seconds=14400, scope="read:self-assessment write:self-assessment",
    )
    _tokens.save_nino_and_businesses(user_id, nino, [
        {"business_id": "XAIS00000001", "type_of_business": "self-employment",
         "label": "Existing SE"},
    ])


def test_disconnect_requires_authentication():
    """Anonymous POSTs must be rejected with 401."""
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/hmrc/disconnect",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 401, r.text


def test_disconnect_wipes_tokens_and_nino_and_businesses():
    """Successful disconnect must clear EVERYTHING — tokens, NINO,
    businesses — so the next OAuth round starts from a clean slate."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"], nino="BC057858D")

    from hmrc.repositories import tokens as _tokens
    info_before = _tokens.get_tokens(user["id"])
    assert info_before is not None
    assert info_before.get("access_token") == "sandbox-access-token"
    assert info_before.get("nino") == "BC057858D"
    assert len(info_before.get("businesses") or []) == 1

    r = client.post(
        "/api/hmrc/disconnect",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    info_after = _tokens.get_tokens(user["id"])
    assert info_after is None, (
        f"Expected the hmrc_connections row to be gone after disconnect, "
        f"got {info_after!r}"
    )


def test_disconnect_is_idempotent_when_already_disconnected():
    """If the user has no stored connection, disconnect must still
    succeed (no-op) — avoids 500 errors on a double-click."""
    client, csrf, user = _client_with_user()

    # No tokens stored at all
    from hmrc.repositories import tokens as _tokens
    assert _tokens.get_tokens(user["id"]) is None

    r = client.post(
        "/api/hmrc/disconnect",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


def test_disconnect_csrf_required():
    """Without the CSRF token, the request must be rejected by the
    middleware before reaching the handler."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    r = client.post("/api/hmrc/disconnect", json={})
    assert r.status_code == 403, r.text


def test_connect_page_shows_disconnect_button_when_connected_in_sandbox():
    """Smoke test: the /hmrc/connect page must render the Disconnect
    button when the user has stored tokens and HMRC_ENV is sandbox."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"], nino="BC057858D")

    r = client.get("/hmrc/connect")
    assert r.status_code == 200
    body = r.text
    # The button itself
    assert "hmrcDisconnectBtn" in body
    assert "Disconnect" in body and "sign in with different credentials" in body
    # And the user can see which NINO their session is bound to
    assert "BC057858D" in body


def test_connect_page_omits_disconnect_button_when_not_connected():
    """The disconnect button only makes sense when there's something to
    disconnect — not when the user has never OAuthed."""
    client, csrf, user = _client_with_user()

    r = client.get("/hmrc/connect")
    assert r.status_code == 200
    assert "hmrcDisconnectBtn" not in r.text
