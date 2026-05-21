"""
Tests for the sandbox 'mint a fresh test user' endpoint.

HMRC's Create Test User API uses a server-to-server (application-restricted)
access token via the OAuth client_credentials grant — NOT user OAuth. The
endpoint we wrap is /create-test-user/individuals on test-api.service.hmrc.gov.uk.

These tests cover:
  - Production-gate (route 404s on HMRC_ENV=production)
  - Auth gate (401 unauthenticated)
  - fetch_application_token does a real client_credentials POST
  - create_test_individual calls the right URL with the right body
  - Endpoint response shape mirrors HMRC's fields the dashboard needs
  - HMRC errors surface as 400/502 with the body text
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

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_sandbox_test_user.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("HMRC_ENV", "sandbox")
    monkeypatch.setenv("HMRC_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("HMRC_CLIENT_SECRET", "test-client-secret")

    from hmrc import config as _cfg
    from hmrc.services import crypto as _crypto
    monkeypatch.setattr(_cfg, "HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setattr(_cfg, "HMRC_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(_cfg, "HMRC_CLIENT_SECRET", "test-client-secret")
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
    def _g():
        if database._sqlite_conn is None:
            c = sqlite3.connect(TEST_DB_PATH, timeout=10, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            database._sqlite_conn = c
        return database._sqlite_conn
    database._get_sqlite = _g
    database.init_db()
    yield
    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


def _authed_client(email="testuser@example.com"):
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = client.get("/login").cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")
    r = client.post("/api/register",
                    json={"email": email, "password": "password12345"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], grandfathered_trial=1)
    return client, csrf, user


# ---------------------------------------------------------------------------
# Production gate
# ---------------------------------------------------------------------------


def test_endpoint_returns_404_in_production(monkeypatch):
    monkeypatch.setenv("HMRC_ENV", "production")
    client, csrf, _ = _authed_client()
    r = client.post(
        "/api/hmrc/sandbox/create-test-user",
        json={}, headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404
    assert "Sandbox-only" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_endpoint_requires_authentication():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    with anon:
        csrf = anon.get("/login").cookies.get("bp_csrf", "")
        r = anon.post(
            "/api/hmrc/sandbox/create-test-user",
            json={}, headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Service layer: fetch_application_token
# ---------------------------------------------------------------------------


def test_fetch_application_token_uses_client_credentials_grant():
    """Verifies the wire shape — HMRC's token endpoint takes
    grant_type=client_credentials + client_id/secret + scope."""
    from hmrc.services import sandbox as _sandbox

    fake = MagicMock(status_code=200, text="ok")
    fake.json.return_value = {"access_token": "app-token-xyz", "expires_in": 14400}
    with patch("httpx.Client") as ClientCls:
        ClientCls.return_value.__enter__.return_value.post.return_value = fake
        token = _sandbox.fetch_application_token()

    # Token plumbed through cleanly
    assert token == "app-token-xyz"

    # Inspect the POST call — the right URL + form payload
    post_call = ClientCls.return_value.__enter__.return_value.post.call_args
    args, kwargs = post_call
    assert args[0].endswith("/oauth/token")
    form = kwargs["data"]
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == "test-client-id"
    assert form["client_secret"] == "test-client-secret"
    assert "scope" in form


def test_fetch_application_token_surfaces_hmrc_errors():
    """A 4xx from HMRC's token endpoint becomes an HmrcApiError carrying
    HMRC's body text — useful when the env vars are misconfigured."""
    from hmrc.services import sandbox as _sandbox
    from hmrc.services import client as _client

    fake = MagicMock(status_code=401, text='{"error":"invalid_client"}')
    with patch("httpx.Client") as ClientCls:
        ClientCls.return_value.__enter__.return_value.post.return_value = fake
        with pytest.raises(_client.HmrcApiError) as exc_info:
            _sandbox.fetch_application_token()
    assert exc_info.value.status_code == 401
    assert "invalid_client" in str(exc_info.value.body)


def test_fetch_application_token_complains_when_no_token_in_response():
    """Defensive: HMRC could return 200 with a missing access_token (e.g.
    if they ever change the grant flow). We must not return None silently."""
    from hmrc.services import sandbox as _sandbox
    from hmrc.services import client as _client

    fake = MagicMock(status_code=200, text="{}")
    fake.json.return_value = {"some_other_field": "x"}  # no access_token
    with patch("httpx.Client") as ClientCls:
        ClientCls.return_value.__enter__.return_value.post.return_value = fake
        with pytest.raises(_client.HmrcApiError):
            _sandbox.fetch_application_token()


# ---------------------------------------------------------------------------
# Service layer: create_test_individual
# ---------------------------------------------------------------------------


def test_create_test_individual_hits_correct_url_and_returns_credentials():
    from hmrc.services import sandbox as _sandbox

    token_resp = MagicMock(status_code=200, text="ok")
    token_resp.json.return_value = {"access_token": "app-token"}
    create_resp = MagicMock(status_code=200, text="ok")
    create_resp.json.return_value = {
        "userId": "987654321",
        "password": "bLohysg8utsa",
        "userFullName": "Ida Newton",
        "emailAddress": "ida.newton@example.com",
        "nino": "AB123456C",
        "mtdItId": "XAIT0000001234",
        "saUtr": "1234567890",
        "groupIdentifier": "abcdef-123",
        "individualDetails": {
            "firstName": "Ida", "lastName": "Newton", "dateOfBirth": "1972-04-11",
        },
    }
    posts = []
    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        # First POST is the token request, second is the create call
        return token_resp if len(posts) == 1 else create_resp
    with patch("httpx.Client") as ClientCls:
        ClientCls.return_value.__enter__.return_value.post.side_effect = fake_post
        result = _sandbox.create_test_individual()

    assert result["nino"] == "AB123456C"
    assert result["userId"] == "987654321"
    assert result["mtdItId"] == "XAIT0000001234"

    # Verify the wire shape of the create call
    url, kw = posts[1]
    assert url.endswith("/create-test-user/individuals")
    headers = kw["headers"]
    assert headers["Authorization"] == "Bearer app-token"
    assert "vnd.hmrc.1.0+json" in headers["Accept"]
    assert "mtd-income-tax" in kw["json"]["serviceNames"]
    assert "national-insurance" in kw["json"]["serviceNames"]


def test_create_test_individual_surfaces_hmrc_error():
    from hmrc.services import sandbox as _sandbox
    from hmrc.services import client as _client

    token_resp = MagicMock(status_code=200, text="ok")
    token_resp.json.return_value = {"access_token": "tok"}
    create_resp = MagicMock(status_code=500,
                            text='{"code":"SERVER_ERROR","message":"oops"}')
    posts = []
    def fake_post(url, **kwargs):
        posts.append(url)
        return token_resp if len(posts) == 1 else create_resp
    with patch("httpx.Client") as ClientCls:
        ClientCls.return_value.__enter__.return_value.post.side_effect = fake_post
        with pytest.raises(_client.HmrcApiError) as exc:
            _sandbox.create_test_individual()
    assert exc.value.status_code == 500
    assert "SERVER_ERROR" in str(exc.value.body)


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


def test_endpoint_returns_credentials_in_camelCase_friendly_shape():
    """The endpoint should surface NINO, GG userId/password, mtdItId,
    saUtr, userFullName so the dashboard can render them in a card."""
    from hmrc.services import sandbox as _sandbox

    fake_data = {
        "userId": "987654321", "password": "secret",
        "nino": "AB123456C", "mtdItId": "XAIT001234",
        "saUtr": "1234567890",
        "userFullName": "Ida Newton",
        "emailAddress": "ida@example.com",
        "groupIdentifier": "abc-123",
        "individualDetails": {"firstName": "Ida"},
    }
    client, csrf, _ = _authed_client()
    with patch.object(_sandbox, "create_test_individual", return_value=fake_data):
        r = client.post(
            "/api/hmrc/sandbox/create-test-user",
            json={}, headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["nino"] == "AB123456C"
    # Pydantic aliases — populated either camelCase or snake_case
    assert body.get("user_id") == "987654321" or body.get("userId") == "987654321"
    assert body.get("password") == "secret"
    assert body.get("mtd_it_id") == "XAIT001234" or body.get("mtdItId") == "XAIT001234"
    # raw preserves the full HMRC payload
    assert body["raw"]["individualDetails"]["firstName"] == "Ida"


def test_endpoint_surfaces_hmrc_400_as_400():
    from hmrc.services import sandbox as _sandbox
    from hmrc.services import client as _client

    client, csrf, _ = _authed_client()
    err = _client.HmrcApiError(status_code=400, body="invalid_service_names")
    with patch.object(_sandbox, "create_test_individual", side_effect=err):
        r = client.post(
            "/api/hmrc/sandbox/create-test-user",
            json={}, headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 400
    assert "invalid_service_names" in r.json()["detail"]


def test_endpoint_surfaces_unparseable_response_as_502():
    from hmrc.services import sandbox as _sandbox
    from hmrc.services import client as _client

    client, csrf, _ = _authed_client()
    err = _client.HmrcApiError(status_code=0, body="HMRC ate the response")
    with patch.object(_sandbox, "create_test_individual", side_effect=err):
        r = client.post(
            "/api/hmrc/sandbox/create-test-user",
            json={}, headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 502
