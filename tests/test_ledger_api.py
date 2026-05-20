"""
Tests for /api/ledger/* endpoints and the ledger_ingest service.

Covers:
  - /api/ledger returns the unified shape
  - /api/ledger/link manually attaches a receipt to a transaction
  - /api/ledger/unlink reverses it
  - /api/ledger/transaction/status updates exclusion / business_pct / is_capital
  - Auth + ownership are enforced
  - ingest_statement_rows + ingest_receipt_and_match + rematch_user_unmatched_receipts
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_ledger_api.db"


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


def _make_client_for_user(email: str) -> tuple:
    """Register + verify + login, return (client, user, csrf_token)."""
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    r = client.post(
        "/api/register",
        json={"email": email, "password": "password12345"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    # Give them a Stripe trial so the paywall doesn't redirect
    _db.update_user(
        user["id"],
        subscription_status="trialing",
        stripe_subscription_id="sub_test_ledger",
        trial_end_at=time.time() + 7 * 86400,
    )
    return client, user, csrf


def _seed_tx(user_id: int, **kwargs) -> int:
    import database as _db
    defaults = {
        "extracted_data_id": None,
        "date_iso": "2026-08-04",
        "description": "AMAZON UK",
        "amount": -42.99,
    }
    defaults.update(kwargs)
    return _db.insert_ledger_transaction(user_id, **defaults)


def _seed_rc(user_id: int, **kwargs) -> int:
    import database as _db
    defaults = {
        "extracted_data_id": None,
        "file_path": None,
        "source_filename": "r.pdf",
        "store_name": "Amazon",
        "date_iso": "2026-08-04",
        "total_amount": 42.99,
        "tax_amount": 7.16,
    }
    defaults.update(kwargs)
    return _db.insert_ledger_receipt(user_id, **defaults)


# ---------------------------------------------------------------------------
# GET /api/ledger
# ---------------------------------------------------------------------------


def test_ledger_requires_auth():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/api/ledger")
    assert r.status_code == 401


def test_ledger_returns_empty_shape_for_new_user():
    client, user, _ = _make_client_for_user("empty@example.com")
    r = client.get("/api/ledger")
    assert r.status_code == 200
    body = r.json()
    assert body["transactions"] == []
    assert body["orphan_receipts"] == []
    assert body["counts"]["transactions"] == 0


def test_ledger_inlines_linked_receipts():
    client, user, _ = _make_client_for_user("inlined@example.com")
    tx_id = _seed_tx(user["id"])
    rc_id = _seed_rc(user["id"])
    import database as _db
    _db.insert_ledger_link(
        transaction_id=tx_id, receipt_id=rc_id,
        match_strategy="exact", confidence=100,
    )

    r = client.get("/api/ledger")
    assert r.status_code == 200
    body = r.json()
    assert len(body["transactions"]) == 1
    tx = body["transactions"][0]
    assert tx["receipt_status"] == "matched"
    assert abs(tx["vat_amount"] - 7.16) < 0.01
    assert len(tx["linked_receipts"]) == 1
    assert tx["linked_receipts"][0]["receipt_id"] == rc_id
    assert tx["linked_receipts"][0]["match_strategy"] == "exact"
    assert body["counts"]["with_receipt"] == 1
    assert body["counts"]["orphan_receipts"] == 0


def test_ledger_shows_orphan_receipts_separately():
    client, user, _ = _make_client_for_user("orphan@example.com")
    _seed_rc(user["id"], store_name="LonelyStore", total_amount=12.50)
    r = client.get("/api/ledger")
    assert r.status_code == 200
    body = r.json()
    assert len(body["orphan_receipts"]) == 1
    assert body["orphan_receipts"][0]["store_name"] == "LonelyStore"
    assert body["counts"]["orphan_receipts"] == 1


# ---------------------------------------------------------------------------
# /api/ledger/link + unlink
# ---------------------------------------------------------------------------


def test_link_creates_relationship():
    client, user, csrf = _make_client_for_user("linker@example.com")
    tx_id = _seed_tx(user["id"])
    rc_id = _seed_rc(user["id"])
    r = client.post(
        "/api/ledger/link",
        json={"transaction_id": tx_id, "receipt_id": rc_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    import database as _db
    links = _db.get_links_for_transaction(tx_id)
    assert len(links) == 1
    assert links[0]["match_strategy"] == "manual"
    assert links[0]["user_confirmed"] == 1


def test_link_rejects_someone_elses_transaction():
    """User A cannot link User B's transaction to anything."""
    import database as _db
    client_a, user_a, csrf_a = _make_client_for_user("attacker@example.com")
    client_b, user_b, _ = _make_client_for_user("victim@example.com")
    tx_b = _seed_tx(user_b["id"])
    rc_a = _seed_rc(user_a["id"])
    r = client_a.post(
        "/api/ledger/link",
        json={"transaction_id": tx_b, "receipt_id": rc_a},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 404  # Not found from user A's perspective


def test_unlink_removes_relationship():
    client, user, csrf = _make_client_for_user("unlinker@example.com")
    tx_id = _seed_tx(user["id"])
    rc_id = _seed_rc(user["id"])
    import database as _db
    _db.insert_ledger_link(
        transaction_id=tx_id, receipt_id=rc_id,
        match_strategy="exact", confidence=100,
    )
    r = client.post(
        "/api/ledger/unlink",
        json={"transaction_id": tx_id, "receipt_id": rc_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert _db.get_links_for_transaction(tx_id) == []


def test_link_validates_body():
    client, user, csrf = _make_client_for_user("validator@example.com")
    r = client.post(
        "/api/ledger/link",
        json={"transaction_id": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/ledger/rematch-all  — clear auto-links and re-run the matcher
# ---------------------------------------------------------------------------


def test_rematch_all_clears_auto_links_keeps_user_confirmed():
    """The button that lets Mitchell wipe nonsense matches and start over.
    Auto-created links (user_confirmed=0) go; manual ones stay."""
    client, user, csrf = _make_client_for_user("rematcher@example.com")
    import database as _db

    tx1 = _seed_tx(user["id"], description="TESCO STORES", amount=-50.0)
    tx2 = _seed_tx(user["id"], description="AMAZON UK",    amount=-30.0)
    rc_auto = _seed_rc(user["id"], store_name="Wrong Match", total_amount=99.99)
    rc_manual = _seed_rc(user["id"], store_name="Amazon", total_amount=30.0)

    # Auto-link (matcher-created): should be wiped
    _db.insert_ledger_link(
        transaction_id=tx1, receipt_id=rc_auto,
        match_strategy="exact", confidence=85, user_confirmed=False,
    )
    # Manual link (user drag-drop): should survive
    _db.insert_ledger_link(
        transaction_id=tx2, receipt_id=rc_manual,
        match_strategy="manual", confidence=100, user_confirmed=True,
    )

    r = client.post(
        "/api/ledger/rematch-all",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["cleared"] >= 1

    # tx1 should no longer have a link; tx2's manual link survives
    assert _db.get_links_for_transaction(tx1) == [], \
        "auto link must be cleared"
    surviving = _db.get_links_for_transaction(tx2)
    assert len(surviving) == 1
    assert surviving[0]["match_strategy"] == "manual"


def test_rematch_all_does_not_link_credits_to_receipts():
    """Regression: the Mitchell-bug from 2026-05-20. A £1,700 salary credit
    must not get linked to a £20.68 burger receipt no matter how attractive
    the date+merchant signals are."""
    client, user, csrf = _make_client_for_user("nocreditmatch@example.com")
    import database as _db

    # Salary credit on the same day with the same merchant string as the receipt
    salary_tx = _seed_tx(
        user["id"],
        description="Love Thy Burger PAYMENT IN",
        amount=1700.00,  # POSITIVE = income
    )
    receipt = _seed_rc(
        user["id"], store_name="Love Thy Burger", total_amount=20.68,
    )
    # Set the receipt's date to the same day as the salary tx so the matcher's
    # date signal would fire if it were going to.
    _db._execute(
        "UPDATE ledger_receipts SET date_iso = (SELECT date_iso FROM ledger_transactions WHERE id = ?) WHERE id = ?",
        (salary_tx, receipt),
    )

    r = client.post(
        "/api/ledger/rematch-all",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    assert r.status_code == 200

    # The salary credit must NOT have a receipt linked
    assert _db.get_links_for_transaction(salary_tx) == [], \
        "credit must never be linked to an expense receipt"
    # The receipt must still be marked as unmatched
    rc_row = _db.get_receipt_by_id(receipt, user["id"])
    assert rc_row["match_status"] == "unmatched"


def test_diagnostic_links_returns_per_user_rows():
    """The diagnostic endpoint lets the user see what matches actually exist."""
    client, user, csrf = _make_client_for_user("diag@example.com")
    import database as _db

    tx = _seed_tx(user["id"], description="TESCO 0123", amount=-12.50)
    rc = _seed_rc(user["id"], store_name="Tesco", total_amount=12.50)
    _db.insert_ledger_link(
        transaction_id=tx, receipt_id=rc,
        match_strategy="exact", confidence=98, user_confirmed=False,
        reason="amount + merchant match",
    )
    r = client.get("/api/ledger/_diagnostic-links")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    link = body["links"][0]
    assert link["tx_desc"] == "TESCO 0123"
    assert link["rc_store"] == "Tesco"
    assert link["match_strategy"] == "exact"


# ---------------------------------------------------------------------------
# /api/ledger/transaction/status
# ---------------------------------------------------------------------------


def test_status_update_mark_personal_excludes_from_totals():
    client, user, csrf = _make_client_for_user("personalmarker@example.com")
    tx_id = _seed_tx(user["id"], description="TESCO 0123 LONDON", amount=-45.00)
    r = client.post(
        "/api/ledger/transaction/status",
        json={"transaction_id": tx_id, "exclusion_reason": "personal"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    import database as _db
    fresh = _db.get_transaction_by_id(tx_id, user["id"])
    assert fresh["exclusion_reason"] == "personal"
    assert fresh["receipt_status"] == "excluded"


def test_status_update_business_split():
    client, user, csrf = _make_client_for_user("splitter@example.com")
    tx_id = _seed_tx(user["id"])
    r = client.post(
        "/api/ledger/transaction/status",
        json={"transaction_id": tx_id, "business_pct": 60},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    import database as _db
    fresh = _db.get_transaction_by_id(tx_id, user["id"])
    assert fresh["business_pct"] == 60


def test_status_update_mark_capital():
    client, user, csrf = _make_client_for_user("capitalmarker@example.com")
    tx_id = _seed_tx(user["id"], amount=-350.00, description="DELL UK LTD")
    r = client.post(
        "/api/ledger/transaction/status",
        json={"transaction_id": tx_id, "is_capital": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    import database as _db
    fresh = _db.get_transaction_by_id(tx_id, user["id"])
    assert fresh["is_capital"] == 1


def test_status_update_rejects_invalid_exclusion():
    client, user, csrf = _make_client_for_user("invalid@example.com")
    tx_id = _seed_tx(user["id"])
    r = client.post(
        "/api/ledger/transaction/status",
        json={"transaction_id": tx_id, "exclusion_reason": "bogus"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_status_update_rejects_out_of_range_business_pct():
    client, user, csrf = _make_client_for_user("range@example.com")
    tx_id = _seed_tx(user["id"])
    r = client.post(
        "/api/ledger/transaction/status",
        json={"transaction_id": tx_id, "business_pct": 150},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_status_update_rejects_someone_elses_transaction():
    client_a, user_a, csrf_a = _make_client_for_user("a@example.com")
    client_b, user_b, _ = _make_client_for_user("b@example.com")
    tx_b = _seed_tx(user_b["id"])
    r = client_a.post(
        "/api/ledger/transaction/status",
        json={"transaction_id": tx_b, "business_pct": 50},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# ledger_ingest service
# ---------------------------------------------------------------------------


def test_ingest_statement_rows_writes_one_per_row():
    """The bridge from parse_statement_ai's output → ledger_transactions."""
    from services.ledger_ingest import ingest_statement_rows
    import database as _db

    user_id = _db.create_user("ingest_stmt@example.com", "pwhash")
    extracted_id = _db.save_extracted_data(
        user_id, "statement", "test.pdf", [], source_size_bytes=0,
    )
    rows = [
        {"date": "2026-08-04", "description": "AMAZON UK", "amount": -42.99, "type": "debit"},
        {"date": "2026-08-05", "description": "STARBUCKS", "amount": -5.40, "type": "debit"},
    ]
    new_ids = ingest_statement_rows(user_id, extracted_id, rows)
    assert len(new_ids) == 2

    txs = _db.get_user_ledger_transactions(user_id)
    assert len(txs) == 2
    assert {t["amount"] for t in txs} == {-42.99, -5.40}


def test_ingest_receipt_auto_links_on_exact_match():
    """The headline interaction — upload a receipt, see it auto-link to its bank line."""
    from services.ledger_ingest import (
        ingest_receipt_and_match, ingest_statement_rows,
    )
    import database as _db

    user_id = _db.create_user("ingest_rc@example.com", "pwhash")
    extracted_id = _db.save_extracted_data(
        user_id, "statement", "stmt.pdf", [], source_size_bytes=0,
    )
    ingest_statement_rows(user_id, extracted_id, [
        {"date": "2026-08-04", "description": "AMAZON UK MARKETPLACE",
         "amount": -42.99, "type": "debit"},
    ])

    receipt_parsed = {
        "items": [{"description": "USB Hub", "quantity": 1, "unit_price": 42.99, "total_price": 42.99}],
        "totals": {"subtotal": 35.83, "tax": 7.16, "total": 42.99},
        "summary": {"store_name": "Amazon", "date": "2026-08-04",
                    "currency": "GBP", "payment_method": "card"},
    }
    extracted_rid = _db.save_extracted_data(
        user_id, "receipt", "rc.pdf", [], source_size_bytes=0,
    )

    outcome = ingest_receipt_and_match(
        user_id, extracted_rid, receipt_parsed,
        file_path="/tmp/r.pdf", source_filename="rc.pdf",
        enable_ai=False,
    )
    assert outcome["match"]["strategy"] == "exact"
    assert outcome["match"]["auto_link"] is True

    # The transaction now reports matched and has VAT inherited.
    txs = _db.get_user_ledger_transactions(user_id)
    assert txs[0]["receipt_status"] == "matched"
    assert abs(txs[0]["vat_amount"] - 7.16) < 0.01


def test_rematch_after_statement_upload_resolves_orphan_receipts():
    """Receipt arrives BEFORE the bank statement. When the statement
    arrives later, the orphan should auto-resolve."""
    from services.ledger_ingest import (
        ingest_receipt_and_match, ingest_statement_rows,
        rematch_user_unmatched_receipts,
    )
    import database as _db

    user_id = _db.create_user("orphan_first@example.com", "pwhash")
    extracted_rid = _db.save_extracted_data(
        user_id, "receipt", "rc.pdf", [], source_size_bytes=0,
    )
    receipt_parsed = {
        "items": [],
        "totals": {"subtotal": 35.83, "tax": 7.16, "total": 42.99},
        "summary": {"store_name": "Amazon", "date": "2026-08-04",
                    "currency": "GBP"},
    }
    outcome = ingest_receipt_and_match(
        user_id, extracted_rid, receipt_parsed, enable_ai=False,
    )
    # No bank lines yet — must be orphan
    assert outcome["match"]["strategy"] == "orphan"

    # Now bank statement arrives
    extracted_sid = _db.save_extracted_data(
        user_id, "statement", "stmt.pdf", [], source_size_bytes=0,
    )
    ingest_statement_rows(user_id, extracted_sid, [
        {"date": "2026-08-04", "description": "AMAZON UK", "amount": -42.99, "type": "debit"},
    ])
    results = rematch_user_unmatched_receipts(user_id, enable_ai=False)
    assert len(results) == 1
    assert results[0]["strategy"] == "exact"
    assert results[0]["transaction_id"] is not None

    txs = _db.get_user_ledger_transactions(user_id)
    assert txs[0]["receipt_status"] == "matched"
