"""
Tests for the annual MTD ITSA finalisation flow:

  EOPS submit → Calculation trigger → Calculation get → Final declaration submit

HMRC mocked at services.client.request — no real sandbox traffic. Each
test pins the exact URL/body/headers we send so any future regression
that breaks the wire contract trips a test.
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

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_annual.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")

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


def _client_with_user(email="annual-test@example.com"):
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
        expires_in_seconds=14400, scope="write:self-assessment",
    )
    _tokens.save_nino_and_businesses(
        user_id, nino,
        [{"business_id": "XAIS00000999001",
          "type_of_business": "self-employment",
          "label": "Test SE"}],
    )


# ---------------------------------------------------------------------------
# Auth gates (apply to every endpoint)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,body", [
    ("/api/hmrc/eops/submit",
     {"business_id": "XAIS001", "type_of_business": "self-employment",
      "period_start": "2026-04-06", "period_end": "2027-04-05", "finalised": True}),
    ("/api/hmrc/calculation/trigger", {"tax_year": "2026-27"}),
    ("/api/hmrc/calculation/get",
     {"tax_year": "2026-27", "calculation_id": "CALC-1"}),
    ("/api/hmrc/final-declaration/submit",
     {"tax_year": "2026-27", "calculation_id": "CALC-1", "finalised": True}),
])
def test_endpoint_requires_authentication(path, body):
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        csrf = _seed_csrf(client)
        r = client.post(path, json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# EOPS submit
# ---------------------------------------------------------------------------

def test_eops_submit_hits_correct_hmrc_url():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    mock_resp = MagicMock(
        status_code=200, json={"transactionReference": "EOPS-1"},
        headers={}, audit_id="audit-eops-1",
    )
    with patch("hmrc.services.annual._client.request",
               return_value=mock_resp) as mock_call:
        r = client.post(
            "/api/hmrc/eops/submit",
            json={"business_id": "XAIS00000999001",
                  "type_of_business": "self-employment",
                  "period_start": "2026-04-06",
                  "period_end": "2027-04-05",
                  "finalised": True},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200, r.text
    kwargs = mock_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["path"] == (
        "/individuals/business/CX139207A/XAIS00000999001/end-of-period-statements"
    )
    # HMRC contract: finalised MUST be true; accountingPeriod object present.
    body = kwargs["json_body"]
    assert body["finalised"] is True
    assert body["typeOfBusiness"] == "self-employment"
    assert body["accountingPeriod"] == {
        "startDate": "2026-04-06", "endDate": "2027-04-05",
    }
    assert kwargs["idempotency_key"]


def test_eops_submit_rejects_finalised_false():
    """Pydantic Literal[True] should reject a request that doesn't confirm."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    r = client.post(
        "/api/hmrc/eops/submit",
        json={"business_id": "XAIS001", "type_of_business": "self-employment",
              "period_start": "2026-04-06", "period_end": "2027-04-05",
              "finalised": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_eops_submit_requires_nino_saved():
    client, csrf, user = _client_with_user()
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user["id"], access_token="x", refresh_token="y",
        expires_in_seconds=14400, scope="",
    )
    r = client.post(
        "/api/hmrc/eops/submit",
        json={"business_id": "XAIS001",
              "type_of_business": "self-employment",
              "period_start": "2026-04-06", "period_end": "2027-04-05",
              "finalised": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Calculation: trigger
# ---------------------------------------------------------------------------

def test_calculation_trigger_hits_correct_url_with_idempotency_key():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    mock_resp = MagicMock(
        status_code=202,
        json={"calculationId": "00000000-0000-1000-8000-000000000001"},
        headers={}, audit_id="audit-trigger-1",
    )
    with patch("hmrc.services.annual._client.request",
               return_value=mock_resp) as mock_call:
        r = client.post(
            "/api/hmrc/calculation/trigger",
            json={"tax_year": "2026-27"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200, r.text
    kwargs = mock_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["path"] == (
        "/individuals/calculations/CX139207A/self-assessment/2026-27"
    )
    assert kwargs["idempotency_key"]
    body = r.json()
    assert body["calculation_id"] == "00000000-0000-1000-8000-000000000001"
    assert body["tax_year"] == "2026-27"


def test_calculation_trigger_rejects_malformed_tax_year():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    r = client.post(
        "/api/hmrc/calculation/trigger",
        json={"tax_year": "2026/27"},  # wrong format
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Calculation: get + summary
# ---------------------------------------------------------------------------

_CALCULATION_RESPONSE_FIXTURE = {
    "calculation": {
        "taxCalculation": {
            "incomeTax": {
                "payPensionsProfit": {
                    "incomeTaxAmount": 4250.00,
                },
            },
            "nics": {
                "class4Nics": {"nicsAmount": 612.50},
            },
            "totalTaxAmount": 4862.50,
        },
        "totalIncome": {
            "totalIncomeReceived": 28600.00,
        },
    },
}


def test_calculation_get_hits_correct_url():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    mock_resp = MagicMock(
        status_code=200, json=_CALCULATION_RESPONSE_FIXTURE,
        headers={}, audit_id="audit-get-1",
    )
    with patch("hmrc.services.annual._client.request",
               return_value=mock_resp) as mock_call:
        r = client.post(
            "/api/hmrc/calculation/get",
            json={"tax_year": "2026-27",
                  "calculation_id": "CALC-ABC-123"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200, r.text
    kwargs = mock_call.call_args.kwargs
    assert kwargs["method"] == "GET"
    assert kwargs["path"] == (
        "/individuals/calculations/CX139207A/self-assessment/2026-27/CALC-ABC-123"
    )


def test_calculation_get_extracts_headline_numbers_into_summary():
    """The UI gets four flat numbers — not the raw 50-field HMRC body."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    mock_resp = MagicMock(
        status_code=200, json=_CALCULATION_RESPONSE_FIXTURE,
        headers={}, audit_id="x",
    )
    with patch("hmrc.services.annual._client.request", return_value=mock_resp):
        r = client.post(
            "/api/hmrc/calculation/get",
            json={"tax_year": "2026-27", "calculation_id": "CALC-1"},
            headers={"X-CSRF-Token": csrf},
        )
    body = r.json()
    assert body["income_tax_amount"] == 4250.00
    assert body["nics_amount"] == 612.50
    assert body["total_amount_payable"] == 4862.50
    assert body["total_taxable_income"] == 28600.00
    # Raw HMRC body still available for debug / power-user
    assert body["raw"] == _CALCULATION_RESPONSE_FIXTURE


def test_calculation_get_handles_missing_fields_gracefully():
    """In-progress calculations have an empty body — must not crash."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    mock_resp = MagicMock(
        status_code=200, json={"calculation": {}},
        headers={}, audit_id="x",
    )
    with patch("hmrc.services.annual._client.request", return_value=mock_resp):
        r = client.post(
            "/api/hmrc/calculation/get",
            json={"tax_year": "2026-27", "calculation_id": "CALC-2"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["income_tax_amount"] is None
    assert body["total_amount_payable"] is None


# ---------------------------------------------------------------------------
# Final Declaration
# ---------------------------------------------------------------------------

def test_final_declaration_hits_correct_hmrc_url():
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    mock_resp = MagicMock(
        status_code=204, json={},
        headers={"X-CorrelationId": "abc-123"}, audit_id="audit-fd-1",
    )
    with patch("hmrc.services.annual._client.request",
               return_value=mock_resp) as mock_call:
        r = client.post(
            "/api/hmrc/final-declaration/submit",
            json={"tax_year": "2026-27",
                  "calculation_id": "CALC-XYZ",
                  "finalised": True},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200, r.text
    kwargs = mock_call.call_args.kwargs
    assert kwargs["method"] == "POST"
    assert kwargs["path"] == (
        "/individuals/calculations/CX139207A/self-assessment/"
        "2026-27/CALC-XYZ/final-declaration"
    )
    assert kwargs["idempotency_key"]
    # HMRC expects no body for final declaration — only headers.
    assert kwargs["json_body"] is None


def test_final_declaration_rejects_finalised_false():
    """Misclick defence — no `finalised: True` → no submit."""
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    r = client.post(
        "/api/hmrc/final-declaration/submit",
        json={"tax_year": "2026-27", "calculation_id": "CALC-1",
              "finalised": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_final_declaration_surfaces_hmrc_validation_error():
    from hmrc.services.client import HmrcApiError
    client, csrf, user = _client_with_user()
    _connect_with_nino(user["id"])
    with patch(
        "hmrc.services.annual._client.request",
        side_effect=HmrcApiError(
            403, {"code": "RULE_FINAL_DECLARATION_RECEIVED",
                  "message": "Already filed"},
        ),
    ):
        r = client.post(
            "/api/hmrc/final-declaration/submit",
            json={"tax_year": "2026-27", "calculation_id": "CALC-1",
                  "finalised": True},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 400
    assert "RULE_FINAL_DECLARATION_RECEIVED" in r.json()["detail"]
