"""
Tests for the /api/extracted-data response shape that the dashboard uses
to *rehydrate* the parsed-results panel after a page navigation (e.g.
Back to dashboard from /hmrc/connect).

Before this change the endpoint returned per-file metadata only —
filename, row_count, parsed_at. The dashboard had no way to re-render
the transaction table without the actual rows, so navigating away and
back showed an empty upload zone even though the session totals banner
still said '860 transactions across 10 statements'.

Now the endpoint also returns each file's `transactions` and a top-level
`summary` for each mode, so the dashboard can call its existing
showBulkStatementResults() function with no further server calls.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_extracted_rehydration.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")

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


def _client_with_user(email="rehydration@example.com"):
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


# ---------------------------------------------------------------------------

def test_summary_helper_handles_mixed_amounts():
    """The summary sums credits, debits and net in the same shape the
    bulk-statement panel expects."""
    from app import _summarise_transactions
    s = _summarise_transactions([
        {"amount": 100.0},
        {"amount": -45.20},
        {"amount": -29.99},
        {"amount": 0},
        {"amount": None},      # robust to missing / nullable amount
        {"amount": "not-a-number"},
    ])
    assert s["total_transactions"] == 6
    assert s["total_credits"] == 100.0
    assert s["total_debits"] == -75.19
    assert s["net"] == 24.81


def test_summary_helper_handles_empty_list():
    from app import _summarise_transactions
    s = _summarise_transactions([])
    assert s == {"total_transactions": 0, "total_credits": 0.0,
                 "total_debits": 0.0, "net": 0.0}


def test_extracted_data_endpoint_returns_per_file_transactions():
    """Each file in the statements.files array must include the
    transactions list — that's what the dashboard uses to render the
    per-statement page after rehydration."""
    client, csrf, user = _client_with_user()
    import database as _db
    _db.save_extracted_data(
        user["id"], "statement", "natwest-jan.pdf",
        [{"date": "2026-01-01", "description": "Starbucks", "amount": -4.50},
         {"date": "2026-01-02", "description": "Payroll", "amount": 2500.0}],
        source_size_bytes=12345,
    )
    _db.save_extracted_data(
        user["id"], "statement", "natwest-feb.pdf",
        [{"date": "2026-02-15", "description": "Rent", "amount": -1200.0}],
        source_size_bytes=67890,
    )

    r = client.get("/api/extracted-data")
    assert r.status_code == 200, r.text
    body = r.json()

    files = body["statements"]["files"]
    assert len(files) == 2
    assert files[0]["filename"] == "natwest-jan.pdf"
    assert len(files[0]["transactions"]) == 2
    assert files[0]["transactions"][0]["description"] == "Starbucks"
    assert files[1]["filename"] == "natwest-feb.pdf"
    assert len(files[1]["transactions"]) == 1


def test_extracted_data_endpoint_returns_summary():
    """The top-level statements.summary must match the shape that
    showBulkStatementResults expects (total_transactions/total_credits/
    total_debits/net)."""
    client, csrf, user = _client_with_user()
    import database as _db
    _db.save_extracted_data(
        user["id"], "statement", "s.pdf",
        [{"date": "2026-01-01", "amount": 100.0},
         {"date": "2026-01-02", "amount": -45.20},
         {"date": "2026-01-03", "amount": -29.99}],
        source_size_bytes=100,
    )

    r = client.get("/api/extracted-data")
    assert r.status_code == 200, r.text
    summary = r.json()["statements"]["summary"]
    assert summary["total_transactions"] == 3
    assert summary["total_credits"] == 100.0
    assert summary["total_debits"] == -75.19
    assert summary["net"] == 24.81


def test_extracted_data_endpoint_summary_handles_zero_state():
    """No statements stored ⇒ summary is zeroed out, not absent."""
    client, csrf, user = _client_with_user()

    r = client.get("/api/extracted-data")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["statements"]["summary"] == {
        "total_transactions": 0, "total_credits": 0.0,
        "total_debits": 0.0, "net": 0.0,
    }
    assert body["statements"]["files"] == []


def test_extracted_data_endpoint_returns_receipts_with_transactions_too():
    """Receipts rehydrate via the same shape — items live in
    `transactions` so the bulk-receipt renderer can find them without
    a second endpoint."""
    client, csrf, user = _client_with_user()
    import database as _db
    _db.save_extracted_data(
        user["id"], "receipt", "tesco.jpg",
        [{"item": "Milk", "total_price": 1.99},
         {"item": "Bread", "total_price": 2.40}],
        source_size_bytes=2048,
    )

    r = client.get("/api/extracted-data")
    assert r.status_code == 200, r.text
    body = r.json()
    files = body["receipts"]["files"]
    assert len(files) == 1
    assert files[0]["filename"] == "tesco.jpg"
    assert len(files[0]["transactions"]) == 2
    assert files[0]["transactions"][0]["item"] == "Milk"
