"""
Tests for the HMRC Obligations endpoint.

We don't talk to the real HMRC sandbox here — we mock `services.client.request`
and assert that:
  - the endpoint short-circuits to the demo fixture when not connected
  - it calls one HMRC URL per business once the user is fully set up
  - it normalises the wire shape into UiObligation correctly
  - it handles HMRC errors without raising to the user
  - the business-setup route stores NINO + business IDs encrypted

These act as the seed conformance tests. When the real sandbox is wired up
we'll add a `@pytest.mark.sandbox` integration test that hits a recorded
fixture from HMRC's test-api.service.hmrc.gov.uk.
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

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_obligations.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("HMRC_DEMO_OBLIGATIONS", raising=False)

    # `hmrc.config` reads HMRC_TOKEN_ENCRYPTION_KEY at IMPORT time. If a
    # previous test file imported hmrc.config without the env set, that
    # module-level constant is now "" and our monkeypatch.setenv can't
    # rescue it. Patch the constant directly + reset the AES key cache.
    from hmrc import config as _cfg
    from hmrc.services import crypto as _crypto
    monkeypatch.setattr(_cfg, "HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setattr(_crypto, "_KEY_CACHE", None)

    # Disable slowapi's 10/min /api/register cap so this file's 10 user
    # registrations don't trip 429 when run after other HMRC test files.
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


def _seed_csrf(client: TestClient) -> str:
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_user(email="obl-test@example.com"):
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post("/api/register",
                    json={"email": email, "password": "password12345"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], grandfathered_trial=1)
    return client, csrf, user


def _connect_hmrc(user_id: int, *, with_nino: bool = True, businesses: list | None = None):
    """Simulate a completed OAuth + business setup for `user_id`."""
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user_id, access_token="sandbox-access-token",
        refresh_token="sandbox-refresh-token",
        expires_in_seconds=14400, scope="read:self-assessment write:self-assessment",
    )
    if with_nino:
        _tokens.save_nino_and_businesses(
            user_id, "AB123456C",
            businesses or [{
                "business_id": "XAIS00000000001",
                "type_of_business": "self-employment",
                "label": "Test sole trader",
            }],
        )


# ---------------------------------------------------------------------------
# Demo / not-connected behaviour
# ---------------------------------------------------------------------------

def test_unauthenticated_returns_401():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        r = client.get("/api/hmrc/obligations")
        assert r.status_code == 401


def test_not_connected_returns_demo_fixture():
    client, _, _ = _client_with_user()
    r = client.get("/api/hmrc/obligations")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["demo"] is True
    assert len(body["obligations"]) > 0
    # Demo includes both SE and property businesses.
    biz_types = {o["business_type"] for o in body["obligations"]}
    assert biz_types == {"self-employment", "property"}


def test_demo_mode_flag_forces_fixture_even_when_connected(monkeypatch):
    """HMRC_DEMO_OBLIGATIONS=1 always returns the fixture — useful for demos
    on a live account."""
    monkeypatch.setenv("HMRC_DEMO_OBLIGATIONS", "1")
    client, _, user = _client_with_user()
    _connect_hmrc(user["id"])
    r = client.get("/api/hmrc/obligations")
    assert r.json()["demo"] is True


def test_connected_without_business_setup_falls_back_to_demo():
    """User connected to HMRC but hasn't told us NINO / business IDs yet —
    panel still renders something useful instead of an error."""
    client, _, user = _client_with_user()
    _connect_hmrc(user["id"], with_nino=False)
    r = client.get("/api/hmrc/obligations")
    body = r.json()
    assert body["connected"] is True
    assert body["demo"] is True


# ---------------------------------------------------------------------------
# Real-call path with mocked HMRC client
# ---------------------------------------------------------------------------

_SANDBOX_RESPONSE = {
    "obligations": [
        {
            "periodKey": "#001",
            "start": "2026-04-06", "end": "2026-07-05",
            "due": "2026-08-05",
            "status": "Fulfilled",
            "received": "2026-08-02",
        },
        {
            "periodKey": "#002",
            "start": "2026-07-06", "end": "2026-10-05",
            "due": "2026-11-05",
            "status": "Open",
        },
    ],
}


def test_connected_with_setup_calls_hmrc_and_maps_to_ui():
    client, _, user = _client_with_user()
    _connect_hmrc(user["id"])

    mock_resp = MagicMock(status_code=200, json=_SANDBOX_RESPONSE,
                          headers={"x-correlation-id": "abc-123"}, audit_id="audit-uuid")
    with patch("hmrc.services.obligations._client.request", return_value=mock_resp) as mock_call:
        r = client.get("/api/hmrc/obligations")
    assert r.status_code == 200

    # Real HMRC URL hit once — for the one configured business.
    assert mock_call.call_count == 1
    kwargs = mock_call.call_args.kwargs
    assert kwargs["method"] == "GET"
    assert kwargs["path"] == (
        "/individuals/business/self-employment/AB123456C/XAIS00000000001/obligations"
    )
    assert kwargs["user_id"] == user["id"]

    body = r.json()
    assert body["connected"] is True
    assert body["demo"] is False
    assert len(body["obligations"]) == 2

    filed = next(o for o in body["obligations"] if o["period_key"] == "#001")
    assert filed["status"] == "filed"
    assert "filed " in filed["human_due"]

    open_ob = next(o for o in body["obligations"] if o["period_key"] == "#002")
    assert open_ob["status"] in ("overdue", "open", "upcoming")
    # `due` should be echoed verbatim
    assert open_ob["due"] == "2026-11-05"


# --- Nested HMRC wire shape -------------------------------------------------
# The MTD ITSA Obligations endpoint returns obligations nested under
# `identification` + `obligationDetails` with the verbose
# `inboundCorrespondence*Date` field names. Earlier versions of our schema
# blew up with a ValidationError on this — caught by the Playwright submit
# journey on 2026-05-24. Now locked in here so the unit suite catches
# regressions without spinning up Chromium.

_NESTED_SANDBOX_RESPONSE = {
    "obligations": [
        {
            "identification": {
                "referenceType": "selfEmploymentId",
                "referenceNumber": "XAIS00000000001",
                "incomeSourceType": "self-employment",
            },
            "obligationDetails": [
                {
                    "status": "F",
                    "inboundCorrespondenceFromDate": "2026-04-06",
                    "inboundCorrespondenceToDate": "2026-07-05",
                    "inboundCorrespondenceDateReceived": "2026-08-02",
                    "inboundCorrespondenceDueDate": "2026-08-05",
                    "periodKey": "#001",
                },
                {
                    "status": "O",
                    "inboundCorrespondenceFromDate": "2026-07-06",
                    "inboundCorrespondenceToDate": "2026-10-05",
                    "inboundCorrespondenceDueDate": "2026-11-05",
                    "periodKey": "#002",
                },
            ],
        },
    ],
}


def test_nested_obligation_details_shape_is_accepted():
    """HMRC's MTD wire format nests obligationDetails under each business
    and uses inboundCorrespondence* field names + status codes O/F. We must
    accept that shape verbatim — failure mode before the schema fix was a
    500 from the obligations endpoint."""
    client, _, user = _client_with_user()
    _connect_hmrc(user["id"])

    mock_resp = MagicMock(
        status_code=200, json=_NESTED_SANDBOX_RESPONSE, headers={}, audit_id="abc"
    )
    with patch("hmrc.services.obligations._client.request", return_value=mock_resp):
        r = client.get("/api/hmrc/obligations")
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["connected"] is True
    assert body["demo"] is False
    # Both nested obligationDetails flattened into UiObligation rows.
    assert len(body["obligations"]) == 2
    open_one = next(o for o in body["obligations"] if o["period_key"] == "#002")
    assert open_one["due"] == "2026-11-05"
    assert open_one["period_start"] == "2026-07-06"
    assert open_one["period_end"] == "2026-10-05"


def test_hmrc_error_does_not_break_response():
    """If HMRC returns 5xx we still return 200 with demo=False and an error
    string so the UI can render gracefully."""
    from hmrc.services.client import HmrcApiError

    client, _, user = _client_with_user()
    _connect_hmrc(user["id"])

    def _raise_500(**kwargs):
        raise HmrcApiError(503, {"code": "SERVICE_UNAVAILABLE", "message": "Down"})

    with patch("hmrc.services.obligations._client.request", side_effect=_raise_500):
        r = client.get("/api/hmrc/obligations")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["error"] is not None
    assert "503" in body["error"]


def test_multiple_businesses_call_hmrc_once_each():
    client, _, user = _client_with_user()
    _connect_hmrc(user["id"], businesses=[
        {"business_id": "XAIS00000000001", "type_of_business": "self-employment",
         "label": "SE biz"},
        {"business_id": "XPIS00000000002", "type_of_business": "property",
         "label": "Property biz"},
    ])
    mock_resp = MagicMock(status_code=200, json=_SANDBOX_RESPONSE, headers={}, audit_id="x")
    with patch("hmrc.services.obligations._client.request", return_value=mock_resp) as mock_call:
        r = client.get("/api/hmrc/obligations")
    assert r.status_code == 200
    assert mock_call.call_count == 2
    paths = [c.kwargs["path"] for c in mock_call.call_args_list]
    assert any("/self-employment/" in p for p in paths)
    assert any("/property/" in p for p in paths)


# ---------------------------------------------------------------------------
# Business setup endpoint
# ---------------------------------------------------------------------------

def test_business_setup_requires_authentication():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        csrf = _seed_csrf(client)
        r = client.post("/api/hmrc/obligations/business-setup",
                        json={"nino": "AB123456C", "businesses": []},
                        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401


def test_business_setup_rejects_malformed_nino():
    client, csrf, user = _client_with_user()
    _connect_hmrc(user["id"], with_nino=False)
    r = client.post("/api/hmrc/obligations/business-setup",
                    json={"nino": "NOTANINO", "businesses": [
                        {"business_id": "XAIS001", "type_of_business": "self-employment"}
                    ]},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_business_setup_requires_oauth_connection_first():
    client, csrf, _ = _client_with_user()  # no _connect_hmrc → no tokens
    r = client.post("/api/hmrc/obligations/business-setup",
                    json={"nino": "AB123456C", "businesses": [
                        {"business_id": "XAIS001", "type_of_business": "self-employment"}
                    ]},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409


def test_business_setup_persists_nino_and_businesses():
    client, csrf, user = _client_with_user()
    _connect_hmrc(user["id"], with_nino=False)
    r = client.post("/api/hmrc/obligations/business-setup",
                    json={"nino": "ab123456c", "businesses": [
                        {"business_id": "XAIS001", "type_of_business": "self-employment",
                         "label": "  Sole trader  "},
                        {"business_id": "", "type_of_business": "self-employment"},  # dropped
                        {"business_id": "XPIS002", "type_of_business": "property"},
                    ]},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["businesses_saved"] == 2

    from hmrc.repositories import tokens as _tokens
    info = _tokens.get_tokens(user["id"])
    assert info["nino"] == "AB123456C"
    assert len(info["businesses"]) == 2
    assert info["businesses"][0]["label"] == "Sole trader"
