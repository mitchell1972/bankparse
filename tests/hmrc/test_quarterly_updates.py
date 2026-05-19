"""
Tests for the Quarterly Updates endpoints — first HMRC write integration.

Covers BOTH self-employment and property streams since they share the
same shape. HMRC is mocked at `services.client.request` so no real
sandbox calls happen. Tests pin the EXACT HMRC URL + body we send so any
future refactor that breaks the wire contract trips a test.
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

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_quarterly.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Default to AI OFF so categorisation goes through the rules path and
    # we don't need to mock Anthropic in every test. Individual tests can
    # override.
    monkeypatch.setenv("HMRC_AI_CATEGORISE", "0")

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


def _client_with_user(email="quarterly-test@example.com"):
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


def _connect_with_nino(user_id: int, nino: str = "CX139207A",
                      businesses: list | None = None):
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user_id, access_token="sandbox-access-token",
        refresh_token="sandbox-refresh-token",
        expires_in_seconds=14400, scope="write:self-assessment",
    )
    _tokens.save_nino_and_businesses(
        user_id, nino,
        businesses or [
            {"business_id": "XAIS00000999001",
             "type_of_business": "self-employment",
             "label": "Test SE"},
            {"business_id": "XPIS00000999002",
             "type_of_business": "property",
             "label": "Test Property"},
        ],
    )


# Sample rows that classify to known SE categories under the regex rules.
_SE_ROWS = [
    {"description": "STRIPE PAYOUT", "amount": 1500.00},              # turnover
    {"description": "STRIPE PAYOUT", "amount": 800.00},               # turnover
    {"description": "MIPERMIT LTD CHIPPENHAM", "amount": -2.60},      # travelCosts
    {"description": "NCP IPSWICH", "amount": -2.50},                  # travelCosts
    {"description": "DD TV LICENCE MBP", "amount": -14.95},           # adminCosts
    {"description": "AWS", "amount": -42.10},                         # adminCosts
]

_PROPERTY_ROWS = [
    {"description": "RENT TENANT PAYMENT", "amount": 1200.00},        # rentIncome
    {"description": "RENT TENANT PAYMENT", "amount": 1200.00},
    {"description": "BRITISH GAS", "amount": -85.00},                 # premisesRunningCosts
    {"description": "PLUMBER CALLOUT", "amount": -120.00},            # repairsAndMaintenance
]


# ---------------------------------------------------------------------------
# /preview — Self-Employment
# ---------------------------------------------------------------------------

def test_preview_se_requires_authentication():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        csrf = _seed_csrf(client)
        r = client.post(
            "/api/hmrc/quarterly-updates/se/preview",
            json={"business_id": "XAIS00000999001",
                  "period_start": "2026-04-06", "period_end": "2026-07-05",
                  "rows": _SE_ROWS},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 401


def test_preview_se_builds_payload_with_correct_categories():
    """The dashboard previews the exact payload that would go to HMRC.
    Numbers MUST match what the user sees on the categorise summary panel.
    """
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    r = client.post(
        "/api/hmrc/quarterly-updates/se/preview",
        json={"business_id": "XAIS00000999001",
              "period_start": "2026-04-06", "period_end": "2026-07-05",
              "rows": _SE_ROWS},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["business_id"] == "XAIS00000999001"
    payload = body["payload"]
    assert payload["periodDates"]["periodStartDate"] == "2026-04-06"
    assert payload["periodDates"]["periodEndDate"] == "2026-07-05"
    # Turnover should be the sum of the two STRIPE PAYOUTs.
    assert payload["periodIncome"]["turnover"] == 2300.0
    # travelCosts = NCP + MiPermit
    assert payload["periodExpenses"]["travelCosts"] == 5.10
    # adminCosts = TV licence + AWS
    assert payload["periodExpenses"]["adminCosts"] == 57.05


def test_preview_se_rejects_invalid_date_format():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    r = client.post(
        "/api/hmrc/quarterly-updates/se/preview",
        json={"business_id": "XAIS001", "period_start": "06-04-2026",
              "period_end": "2026-07-05", "rows": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_preview_se_does_not_call_hmrc():
    """The preview step must NEVER hit HMRC. Otherwise a curious user could
    rack up sandbox calls without ever clicking submit."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    with patch("hmrc.services.client.request") as mock_call:
        r = client.post(
            "/api/hmrc/quarterly-updates/se/preview",
            json={"business_id": "XAIS00000999001",
                  "period_start": "2026-04-06", "period_end": "2026-07-05",
                  "rows": _SE_ROWS},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200
    mock_call.assert_not_called()


# ---------------------------------------------------------------------------
# /submit — Self-Employment
# ---------------------------------------------------------------------------

def test_submit_se_hits_correct_hmrc_url_with_idempotency_key():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    mock_resp = MagicMock(
        status_code=200,
        json={"transactionReference": "TXN-12345"},
        headers={}, audit_id="audit-uuid-1",
    )
    with patch("hmrc.services.quarterly_updates._client.request",
               return_value=mock_resp) as mock_call:
        r = client.post(
            "/api/hmrc/quarterly-updates/se/submit",
            json={"business_id": "XAIS00000999001",
                  "period_start": "2026-04-06", "period_end": "2026-07-05",
                  "rows": _SE_ROWS},
            headers={"X-CSRF-Token": csrf},
        )

    assert r.status_code == 200, r.text
    kwargs = mock_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["path"] == (
        "/individuals/business/self-employment/"
        "CX139207A/XAIS00000999001/period-summaries"
    )
    # Idempotency-Key MUST be present and a UUID string (HMRC will reject
    # duplicate submissions; this is the contract).
    assert kwargs["idempotency_key"]
    assert len(kwargs["idempotency_key"]) >= 32

    body = kwargs["json_body"]
    assert body["periodDates"]["periodStartDate"] == "2026-04-06"
    assert body["periodIncome"]["turnover"] == 2300.0
    assert body["periodExpenses"]["travelCosts"] == 5.10

    # Response shape — HMRC's body is echoed and we include our audit id.
    out = r.json()
    assert out["status"] == "ok"
    assert out["hmrc_response"]["transactionReference"] == "TXN-12345"
    assert out["audit_id"] == "audit-uuid-1"


def test_submit_se_honours_caller_supplied_idempotency_key():
    """Background jobs need replay safety — pass through caller's key."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    mock_resp = MagicMock(status_code=200, json={}, headers={}, audit_id="x")
    with patch("hmrc.services.quarterly_updates._client.request",
               return_value=mock_resp) as mock_call:
        client.post(
            "/api/hmrc/quarterly-updates/se/submit",
            json={"business_id": "XAIS00000999001",
                  "period_start": "2026-04-06", "period_end": "2026-07-05",
                  "rows": _SE_ROWS,
                  "idempotency_key": "MY-FIXED-KEY-12345678901234567890"},
            headers={"X-CSRF-Token": csrf},
        )
    assert mock_call.call_args.kwargs["idempotency_key"] == (
        "MY-FIXED-KEY-12345678901234567890"
    )


def test_submit_se_requires_nino_saved():
    client, csrf, user = _client_with_user()
    # OAuth tokens only, no NINO.
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user["id"], access_token="x", refresh_token="y",
        expires_in_seconds=14400, scope="",
    )
    r = client.post(
        "/api/hmrc/quarterly-updates/se/submit",
        json={"business_id": "XAIS001",
              "period_start": "2026-04-06", "period_end": "2026-07-05",
              "rows": _SE_ROWS},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409


def test_submit_se_surfaces_hmrc_validation_errors():
    """HMRC's typical first-failure on quarterlies is RULE_DUPLICATE_SUBMISSION.
    The user MUST see that — not a generic 500."""
    from hmrc.services.client import HmrcApiError
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    with patch(
        "hmrc.services.quarterly_updates._client.request",
        side_effect=HmrcApiError(
            403, {"code": "RULE_DUPLICATE_SUBMISSION",
                  "message": "An update already exists for this period."},
        ),
    ):
        r = client.post(
            "/api/hmrc/quarterly-updates/se/submit",
            json={"business_id": "XAIS001",
                  "period_start": "2026-04-06", "period_end": "2026-07-05",
                  "rows": _SE_ROWS},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 400
    assert "RULE_DUPLICATE_SUBMISSION" in r.json()["detail"]


def test_submit_se_returns_502_on_network_error():
    from hmrc.services.client import HmrcApiError
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    with patch(
        "hmrc.services.quarterly_updates._client.request",
        side_effect=HmrcApiError(0, {"network_error": "connection reset"}),
    ):
        r = client.post(
            "/api/hmrc/quarterly-updates/se/submit",
            json={"business_id": "XAIS001",
                  "period_start": "2026-04-06", "period_end": "2026-07-05",
                  "rows": _SE_ROWS},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 502


def test_submit_se_omits_empty_categories_from_payload():
    """HMRC's schema rejects null values for unused expense categories — we
    must strip them via model_dump(exclude_none=True)."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    mock_resp = MagicMock(status_code=200, json={}, headers={}, audit_id="x")
    with patch("hmrc.services.quarterly_updates._client.request",
               return_value=mock_resp) as mock_call:
        client.post(
            "/api/hmrc/quarterly-updates/se/submit",
            json={"business_id": "XAIS001",
                  "period_start": "2026-04-06", "period_end": "2026-07-05",
                  "rows": [{"description": "STRIPE PAYOUT", "amount": 100.0}]},
            headers={"X-CSRF-Token": csrf},
        )
    sent = mock_call.call_args.kwargs["json_body"]
    # Only the categories that actually had values should appear.
    assert "turnover" in sent["periodIncome"]
    # `cisPaymentsToSubcontractors` was untouched -> must be absent.
    assert "cisPaymentsToSubcontractors" not in sent.get("periodExpenses", {})


# ---------------------------------------------------------------------------
# /preview + /submit — UK Property (smoke tests; same shape as SE)
# ---------------------------------------------------------------------------

def test_preview_property_builds_payload():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    r = client.post(
        "/api/hmrc/quarterly-updates/property/preview",
        json={"business_id": "XPIS00000999002",
              "period_start": "2026-04-06", "period_end": "2026-07-05",
              "rows": _PROPERTY_ROWS},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    payload = body["payload"]
    assert payload["periodIncome"]["rentIncome"] == 2400.0
    assert payload["periodExpenses"]["premisesRunningCosts"] == 85.0
    assert payload["periodExpenses"]["repairsAndMaintenance"] == 120.0


def test_submit_property_hits_correct_uk_endpoint():
    """UK property is a separate URL — `/property/{nino}/{biz}/uk/period-summaries`."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])

    mock_resp = MagicMock(status_code=200, json={"transactionReference": "PROP-TXN-1"},
                          headers={}, audit_id="audit-prop-1")
    with patch("hmrc.services.quarterly_updates._client.request",
               return_value=mock_resp) as mock_call:
        r = client.post(
            "/api/hmrc/quarterly-updates/property/submit",
            json={"business_id": "XPIS00000999002",
                  "period_start": "2026-04-06", "period_end": "2026-07-05",
                  "rows": _PROPERTY_ROWS},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200, r.text
    kwargs = mock_call.call_args.kwargs
    assert kwargs["path"] == (
        "/individuals/business/property/"
        "CX139207A/XPIS00000999002/uk/period-summaries"
    )
    assert "Idempotency-Key" not in kwargs  # passed as named param, not header in client call
    assert kwargs["idempotency_key"]
