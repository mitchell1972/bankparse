"""
Integration tests for the /api/hmrc/categorise* endpoints + override repo
against a real isolated SQLite DB (same pattern as test_billing_trial.py).
"""

import base64
import os
import secrets
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_categorise.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    """Encryption key + AI disabled."""
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY",
                      base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("HMRC_AI_CATEGORISE", "0")
    monkeypatch.setenv("ENVIRONMENT", "development")
    yield


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
    """CSRFMiddleware only sets the cookie on GETs — hit /login to get one."""
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_user(email="cat-test@example.com"):
    """Register + email-verify + grandfather a user, return (client, csrf)."""
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
    return client, csrf


# --- Endpoint tests --------------------------------------------------------

def test_categorise_returns_one_hmrc_block_per_row():
    client, csrf = _client_with_user()
    rows = [
        {"date": "2025-12-22", "description": "DD TV LICENCE MBP", "amount": -14.95},
        {"date": "2025-12-22", "description": "MIPERMIT LTD CHIPPENHAM", "amount": -2.60},
        {"date": "2025-12-22", "description": "STRIPE PAYOUT", "amount": 1500.0},
    ]
    r = client.post(
        "/api/hmrc/categorise",
        json={"business_type": "se", "rows": rows},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["rows"]) == 3
    for row in body["rows"]:
        assert "hmrc" in row
        assert "category" in row["hmrc"]
        assert "confidence" in row["hmrc"]
        assert "source" in row["hmrc"]
    cats = [row["hmrc"]["category"] for row in body["rows"]]
    assert "adminCosts" in cats         # TV licence
    assert "travelCosts" in cats        # MiPermit
    assert "turnover" in cats           # Stripe


def test_override_is_remembered_next_time():
    """After saving an override for a merchant, subsequent categorise calls
    must return that category with source='override'."""
    client, csrf = _client_with_user()

    # First categorise — TACO BELL falls to entertainment by default.
    first = client.post(
        "/api/hmrc/categorise",
        json={"business_type": "se", "rows": [{"description": "TACO BELL NACTON", "amount": -13.37}]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert first["rows"][0]["hmrc"]["category"] == "businessEntertainmentCosts"

    # User says: actually treat this as 'adminCosts' for me.
    r = client.post(
        "/api/hmrc/categorise/override",
        json={"description": "TACO BELL NACTON",
              "business_type": "se", "category": "adminCosts"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200

    # Now categorise again — should return the override.
    second = client.post(
        "/api/hmrc/categorise",
        json={"business_type": "se", "rows": [{"description": "TACO BELL NACTON", "amount": -13.37}]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert second["rows"][0]["hmrc"]["category"] == "adminCosts"
    assert second["rows"][0]["hmrc"]["source"] == "override"
    assert second["rows"][0]["hmrc"]["confidence"] == 1.0


def test_override_keys_normalise_so_repeat_merchants_match():
    """Saved override for 'MIPERMIT LTD CHIPPENHAM 14/03' must also fire on
    'MIPERMIT LTD CHIPPENHAM' with no trailing date."""
    client, csrf = _client_with_user()
    client.post(
        "/api/hmrc/categorise/override",
        json={"description": ")))  MIPERMIT LTD CHIPPENHAM 14/03",
              "business_type": "se", "category": "premisesRunningCosts"},
        headers={"X-CSRF-Token": csrf},
    )
    out = client.post(
        "/api/hmrc/categorise",
        json={"business_type": "se", "rows": [
            {"description": "VIS MIPERMIT LTD CHIPPENHAM", "amount": -3.20},
        ]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert out["rows"][0]["hmrc"]["category"] == "premisesRunningCosts"
    assert out["rows"][0]["hmrc"]["source"] == "override"


def test_summary_endpoint_aggregates_by_category():
    client, csrf = _client_with_user()
    rows = [
        {"description": "STRIPE PAYOUT", "amount": 1500.0},
        {"description": "STRIPE PAYOUT", "amount": 800.0},
        {"description": "MIPERMIT LTD CHIPPENHAM", "amount": -2.60},
        {"description": "DD TV LICENCE MBP", "amount": -14.95},
    ]
    r = client.post(
        "/api/hmrc/categorise/summary",
        json={"business_type": "se", "rows": rows},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    s = r.json()["summary"]
    assert s["income"]["turnover"] == 2300.0
    assert s["expenses"]["travelCosts"] == 2.60
    assert s["expenses"]["adminCosts"] == 14.95


def test_unauthenticated_categorise_returns_401():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        csrf = _seed_csrf(client)
        r = client.post(
            "/api/hmrc/categorise",
            json={"business_type": "se", "rows": []},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 401
