"""
Tests for /api/transaction/{id}/explain and /api/audit-certificate —
the printable HTML pages that turn the audit-ready % into evidence the
user can show HMRC.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_defence_cert.db"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")


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


def _client(email: str) -> tuple:
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    client.post("/api/register",
                json={"email": email, "password": "password12345"},
                headers={"X-CSRF-Token": csrf})
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"],
                    subscription_status="trialing",
                    stripe_subscription_id="sub_def",
                    trial_end_at=time.time() + 7*86400)
    return client, user, csrf


# ---------------------------------------------------------------------------
# /api/transaction/{id}/explain
# ---------------------------------------------------------------------------


def test_explain_requires_auth():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/api/transaction/1/explain")
    assert r.status_code == 401


def test_explain_returns_404_for_unknown_transaction():
    client, user, _ = _client("dave@example.com")
    r = client.get("/api/transaction/99999/explain")
    assert r.status_code == 404


def test_explain_renders_html_with_claim_and_hash():
    client, user, _ = _client("sara@example.com")
    import database as _db
    tx_id = _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="AMAZON UK MARKETPLACE",
        amount=-42.99,
        hmrc_category="se_general_admin_costs",
        hmrc_category_confidence=92,
        hmrc_category_reason="Stationery and office consumables — recognised office admin spend.",
    )
    r = client.get(f"/api/transaction/{tx_id}/explain")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "HMRC defence sheet" in html
    assert "AMAZON UK MARKETPLACE" in html
    assert "£42.99" in html
    # The HMRC manual ref MUST be cited for the category
    assert "BIM47800" in html
    # The transaction's hash must be printed verbatim as the audit trail
    expected_hash = _db._hash_transaction("2026-08-04", "AMAZON UK MARKETPLACE", -42.99)
    assert expected_hash in html
    # The AI's reason must appear in the body
    assert "Stationery and office consumables" in html
    # The sheet hash field exists
    assert "Sheet hash" in html


def test_explain_with_attached_receipt_shows_vat_and_receipt_row():
    client, user, _ = _client("paul@example.com")
    import database as _db
    tx_id = _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="AMAZON UK", amount=-42.99,
        hmrc_category="se_general_admin_costs",
    )
    rc_id = _db.insert_ledger_receipt(
        user["id"], extracted_data_id=None,
        file_path=None, source_filename="r.pdf",
        store_name="Amazon", date_iso="2026-08-04",
        total_amount=42.99, tax_amount=7.16,
    )
    _db.insert_ledger_link(transaction_id=tx_id, receipt_id=rc_id,
                           match_strategy="exact", confidence=100)
    r = client.get(f"/api/transaction/{tx_id}/explain")
    assert r.status_code == 200
    html = r.text
    assert "Amazon" in html
    assert "£7.16" in html  # VAT visible


def test_explain_warns_when_no_receipts():
    client, user, _ = _client("warn@example.com")
    import database as _db
    tx_id = _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="STARBUCKS",
        amount=-5.40,
        hmrc_category="se_business_travel_costs",
    )
    r = client.get(f"/api/transaction/{tx_id}/explain")
    assert r.status_code == 200
    assert "No receipts attached" in r.text


def test_explain_marks_capital_when_flagged():
    client, user, _ = _client("cap@example.com")
    import database as _db
    tx_id = _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="DELL UK",
        amount=-350.0,
        hmrc_category="se_office_expenses",
    )
    _db.update_transaction_status(tx_id, is_capital=1)
    r = client.get(f"/api/transaction/{tx_id}/explain")
    assert "Capital item" in r.text
    assert "CA20000" in r.text


def test_explain_shows_business_pct_block():
    client, user, _ = _client("split@example.com")
    import database as _db
    tx_id = _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="EE MOBILE",
        amount=-100.0,
        hmrc_category="se_general_admin_costs",
    )
    _db.update_transaction_status(tx_id, business_pct=60)
    r = client.get(f"/api/transaction/{tx_id}/explain")
    html = r.text
    assert "60% business use" in html


def test_explain_does_not_leak_other_users_transactions():
    client_a, user_a, _ = _client("a@example.com")
    client_b, user_b, _ = _client("b@example.com")
    import database as _db
    tx_b = _db.insert_ledger_transaction(
        user_b["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="PRIVATE",
        amount=-1000.0,
    )
    r = client_a.get(f"/api/transaction/{tx_b}/explain")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/audit-certificate
# ---------------------------------------------------------------------------


def test_certificate_requires_auth():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/api/audit-certificate")
    assert r.status_code == 401


def test_certificate_defaults_period_to_current_quarter():
    client, user, _ = _client("default@example.com")
    r = client.get("/api/audit-certificate")
    assert r.status_code == 200
    # Should mention the current year somewhere
    import datetime
    yr = datetime.datetime.utcnow().year
    assert str(yr) in r.text


def test_certificate_honours_period_query_param():
    client, user, _ = _client("period@example.com")
    r = client.get("/api/audit-certificate?period=Q2-2026")
    assert r.status_code == 200
    assert "Q2-2026" in r.text


def test_certificate_shows_overall_audit_ready_pct_prominently():
    client, user, _ = _client("ready@example.com")
    import database as _db
    tx_id = _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="A", amount=-100.0,
        hmrc_category="se_office_expenses",
    )
    rc_id = _db.insert_ledger_receipt(
        user["id"], extracted_data_id=None,
        file_path=None, source_filename="r.pdf",
        store_name="A", date_iso="2026-08-04",
        total_amount=100.0, tax_amount=16.67,
    )
    _db.insert_ledger_link(transaction_id=tx_id, receipt_id=rc_id,
                           match_strategy="exact", confidence=100)
    r = client.get("/api/audit-certificate?period=Q2-2026")
    html = r.text
    # 100% audit-ready overall
    assert "100%" in html
    assert "Excellent" in html  # the band label


def test_certificate_includes_sha256_hash_stamp():
    client, user, _ = _client("hash@example.com")
    r = client.get("/api/audit-certificate?period=Q1-2026")
    html = r.text
    # Find the 64-char hex hash in the page
    assert re.search(r"[a-f0-9]{64}", html)
    assert "Certificate hash" in html


def test_certificate_hash_changes_when_data_changes():
    """The certificate hash must change if any underlying figure changes —
    this is the tamper-evidence claim."""
    client, user, _ = _client("change@example.com")
    import database as _db
    # Snapshot 1: no transactions
    r1 = client.get("/api/audit-certificate?period=Q1-2026")
    hashes = re.findall(r"([a-f0-9]{64})", r1.text)
    assert len(hashes) >= 1
    h1 = hashes[0]

    # Snapshot 2: add a transaction
    _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-01-04", description="X", amount=-100.0,
        hmrc_category="se_office_expenses",
    )
    r2 = client.get("/api/audit-certificate?period=Q1-2026")
    hashes2 = re.findall(r"([a-f0-9]{64})", r2.text)
    h2 = hashes2[0]
    assert h1 != h2, "Certificate hash must change when underlying data changes"


def test_certificate_lists_expense_categories_only_in_table():
    """Income categories are surfaced separately; the expense table
    is the audit-ready scorecard."""
    client, user, _ = _client("incomesplit@example.com")
    import database as _db
    _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-01-04", description="invoice paid",
        amount=2000.0,
        hmrc_category="se_turnover",
    )
    _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-01-05", description="AMAZON",
        amount=-50.0,
        hmrc_category="se_office_expenses",
    )
    r = client.get("/api/audit-certificate?period=Q1-2026")
    html = r.text
    # Income box outside the table
    assert "Income reported" in html
    # Expenses table shows the Office Expenses line
    assert "Office Expenses" in html or "office expenses" in html.lower()
