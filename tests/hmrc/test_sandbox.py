"""
Tests for the sandbox-only `/api/hmrc/sandbox/*` endpoints.

These routes exist so a developer (or an end-user on the sandbox-pointed
deploy) can provision MTD ITSA test businesses on their NINO without
copy-pasting curl. They MUST NEVER be reachable in production — the gate
is `HMRC_ENV=production`, and the first test in this file pins that.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_sandbox.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Default to sandbox for this whole file — individual tests can override.
    monkeypatch.setenv("HMRC_ENV", "sandbox")

    # See test_obligations.py / test_business_details.py for why we patch the
    # config attribute + reset the AES key cache.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_csrf(client):
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_user(email="sandbox-test@example.com"):
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
    """Simulate OAuth complete + NINO saved (but no businesses)."""
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user_id, access_token="sandbox-access-token",
        refresh_token="sandbox-refresh-token",
        expires_in_seconds=14400, scope="read:self-assessment write:self-assessment",
    )
    _tokens.save_nino_and_businesses(user_id, nino, [])


# ---------------------------------------------------------------------------
# Production gate
# ---------------------------------------------------------------------------

def test_endpoint_returns_404_in_production(monkeypatch):
    """The sandbox helper must never be reachable on a production deploy."""
    monkeypatch.setenv("HMRC_ENV", "production")
    client, csrf, _ = _client_with_user()
    r = client.post(
        "/api/hmrc/sandbox/create-test-business",
        json={"type_of_business": "self-employment"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404
    assert "sandbox-only" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Authentication / connection gates
# ---------------------------------------------------------------------------

def test_endpoint_requires_authentication():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        csrf = _seed_csrf(client)
        r = client.post(
            "/api/hmrc/sandbox/create-test-business",
            json={"type_of_business": "self-employment"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 401


def test_endpoint_requires_nino_saved_first():
    """Without a NINO we can't form the HMRC URL — must 409."""
    client, csrf, user = _client_with_user()
    # OAuth tokens only, no NINO — _connect_with_nino-but-skip-nino
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user["id"], access_token="x", refresh_token="y",
        expires_in_seconds=14400, scope="",
    )

    r = client.post(
        "/api/hmrc/sandbox/create-test-business",
        json={"type_of_business": "self-employment"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409
    assert "NINO" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_endpoint_calls_hmrc_and_persists_new_business():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    mock_resp = MagicMock(
        status_code=201,
        json={"businessId": "XAIS00000999001", "typeOfBusiness": "self-employment"},
        headers={}, audit_id="audit-sandbox-1",
    )
    with patch(
        "hmrc.services.sandbox._client.request",
        return_value=mock_resp,
    ) as mock_call:
        r = client.post(
            "/api/hmrc/sandbox/create-test-business",
            json={"type_of_business": "self-employment", "trading_name": "Test SE"},
            headers={"X-CSRF-Token": csrf},
        )

    assert r.status_code == 200, r.text

    # The exact URL we hit at HMRC — bumping this URL is a real change.
    call_kwargs = mock_call.call_args.kwargs
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["path"] == "/individuals/business/details/CX139207A/test-only/create-business"

    # Sandbox HMRC requires uk-property wire value — but for SE we send self-employment.
    body = call_kwargs["json_body"]
    assert body["typeOfBusiness"] == "self-employment"
    assert body["tradingName"] == "Test SE"
    assert body["accountingType"] == "CASH"
    # Accounting period must fall inside a real MTD tax year.
    assert body["firstAccountingPeriodStartDate"].endswith("-04-06")
    assert body["firstAccountingPeriodEndDate"].endswith("-04-05")

    # Response shape
    out = r.json()
    assert out["status"] == "ok"
    assert out["business"]["business_id"] == "XAIS00000999001"
    assert out["business"]["type_of_business"] == "self-employment"

    # Persisted on the user's connection
    from hmrc.repositories import tokens as _tokens
    info = _tokens.get_tokens(user["id"])
    ids = [b["business_id"] for b in info["businesses"]]
    assert "XAIS00000999001" in ids


def test_property_business_maps_to_uk_property_wire_value():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    mock_resp = MagicMock(
        status_code=201,
        json={"businessId": "XPIS00000999002"},
        headers={}, audit_id="audit-sandbox-2",
    )
    with patch(
        "hmrc.services.sandbox._client.request",
        return_value=mock_resp,
    ) as mock_call:
        r = client.post(
            "/api/hmrc/sandbox/create-test-business",
            json={"type_of_business": "property"},
            headers={"X-CSRF-Token": csrf},
        )

    assert r.status_code == 200
    # HMRC wire format uses 'uk-property', not 'property'.
    assert mock_call.call_args.kwargs["json_body"]["typeOfBusiness"] == "uk-property"
    out = r.json()
    assert out["business"]["type_of_business"] == "property"   # our normalised value


def test_repeated_calls_do_not_duplicate_business_in_storage():
    """If HMRC returns the same businessId twice we don't double-insert."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    mock_resp = MagicMock(
        status_code=201,
        json={"businessId": "XAIS00000999003"},
        headers={}, audit_id="audit-sandbox-3",
    )
    with patch("hmrc.services.sandbox._client.request", return_value=mock_resp):
        for _ in range(3):
            r = client.post(
                "/api/hmrc/sandbox/create-test-business",
                json={"type_of_business": "self-employment"},
                headers={"X-CSRF-Token": csrf},
            )
            assert r.status_code == 200

    from hmrc.repositories import tokens as _tokens
    info = _tokens.get_tokens(user["id"])
    ids = [b["business_id"] for b in info["businesses"]]
    assert ids.count("XAIS00000999003") == 1


# ---------------------------------------------------------------------------
# Error pass-through
# ---------------------------------------------------------------------------

def test_hmrc_error_is_surfaced_to_caller():
    from hmrc.services.client import HmrcApiError
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    with patch(
        "hmrc.services.sandbox._client.request",
        side_effect=HmrcApiError(401, {"code": "INVALID_BEARER_TOKEN"}),
    ):
        r = client.post(
            "/api/hmrc/sandbox/create-test-business",
            json={"type_of_business": "self-employment"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 400
    assert "401" in r.json()["detail"]


def test_missing_business_id_in_response_returns_502():
    """If HMRC returns 2xx but no businessId, we don't pretend it succeeded."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    mock_resp = MagicMock(
        status_code=201, json={"acknowledged": True},
        headers={}, audit_id="audit-no-id",
    )
    with patch("hmrc.services.sandbox._client.request", return_value=mock_resp):
        r = client.post(
            "/api/hmrc/sandbox/create-test-business",
            json={"type_of_business": "self-employment"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 502
    assert "businessId" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Friendly error mapping for the existing connect-businesses route
# ---------------------------------------------------------------------------

def test_connect_businesses_404_maps_to_sandbox_hint():
    """A fresh sandbox NINO returns 404 MATCHING_RESOURCE_NOT_FOUND. We
    want the dashboard to see a helpful message + status 404 so the UI
    can offer the sandbox helper buttons."""
    from hmrc.services.client import HmrcApiError
    client, csrf, user = _client_with_user()
    # OAuth done but no businesses — same state as the sandbox flow this fix
    # is built for.
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user["id"], access_token="x", refresh_token="y",
        expires_in_seconds=14400, scope="",
    )

    with patch(
        "hmrc.services.business_details._client.request",
        side_effect=HmrcApiError(404, {"code": "MATCHING_RESOURCE_NOT_FOUND",
                                       "message": "Not found"}),
    ):
        r = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "CX139207A"},
            headers={"X-CSRF-Token": csrf},
        )

    assert r.status_code == 404
    detail = r.json()["detail"]
    # Sandbox build (HMRC_ENV=sandbox via fixture) — should mention the helper.
    assert "Create sandbox test business" in detail
