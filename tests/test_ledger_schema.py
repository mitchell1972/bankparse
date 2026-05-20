"""
Schema + CRUD tests for the structured ledger tables.

Pins:
  - Tables exist with the right columns
  - Hash audit trail is computed at insert time and is content-addressable
  - Inserting a link cascades cached match_status updates on both sides
  - Removing a link rolls back the cache correctly
  - Foreign-key CASCADE deletes work end-to-end
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_ledger_schema.db"


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
    # Seed a user we can attach data to.
    database.create_user("ledger@example.com", "pwhash")
    yield database.get_user_by_email("ledger@example.com")["id"]
    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_ledger_tables_exist(clean_db):
    import database
    conn = database._get_sqlite()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('ledger_transactions','ledger_receipts','ledger_links') "
        "ORDER BY name"
    )
    names = [row[0] for row in cursor.fetchall()]
    assert names == ["ledger_links", "ledger_receipts", "ledger_transactions"]


def test_indexes_present_for_match_lookups(clean_db):
    import database
    conn = database._get_sqlite()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    idx_names = {row[0] for row in cursor.fetchall()}
    expected = {
        "idx_ledger_tx_user_date",
        "idx_ledger_rc_user_date",
        "idx_ledger_links_tx",
        "idx_ledger_links_rc",
    }
    assert expected.issubset(idx_names)


# ---------------------------------------------------------------------------
# Insertion + hash audit
# ---------------------------------------------------------------------------


def test_insert_transaction_computes_content_hash(clean_db):
    import database
    user_id = clean_db
    tx_id = database.insert_ledger_transaction(
        user_id,
        extracted_data_id=None,
        date_iso="2026-08-04",
        description="AMAZON UK MARKETPLACE",
        amount=-42.99,
    )
    fresh = database.get_transaction_by_id(tx_id, user_id)
    assert fresh is not None
    assert len(fresh["content_hash"]) == 64  # SHA-256 hex
    # Same inputs → same hash
    assert fresh["content_hash"] == database._hash_transaction(
        "2026-08-04", "AMAZON UK MARKETPLACE", -42.99,
    )


def test_hash_is_case_insensitive_and_whitespace_tolerant(clean_db):
    """Same merchant under different casing/whitespace must hash identically.
    Critical for receipt-vs-bank deduplication later."""
    import database
    h1 = database._hash_transaction("2026-08-04", "AMAZON UK", -42.99)
    h2 = database._hash_transaction("2026-08-04", "  amazon uk  ", -42.99)
    assert h1 == h2


def test_amount_precision_in_hash_is_pence(clean_db):
    """42.999 and 43.00 should hash the same; 42.99 should differ."""
    import database
    h1 = database._hash_transaction("2026-08-04", "AMAZON", 42.999)
    h2 = database._hash_transaction("2026-08-04", "AMAZON", 43.00)
    h3 = database._hash_transaction("2026-08-04", "AMAZON", 42.99)
    assert h1 == h2
    assert h1 != h3


def test_insert_receipt_computes_content_hash(clean_db):
    import database
    user_id = clean_db
    rc_id = database.insert_ledger_receipt(
        user_id,
        extracted_data_id=None,
        file_path="/tmp/receipt.pdf",
        source_filename="receipt.pdf",
        store_name="Amazon",
        date_iso="2026-08-04",
        total_amount=42.99,
        tax_amount=7.16,
    )
    fresh = database.get_receipt_by_id(rc_id, user_id)
    assert fresh is not None
    assert len(fresh["content_hash"]) == 64


# ---------------------------------------------------------------------------
# Linking — cache propagation
# ---------------------------------------------------------------------------


def test_linking_updates_cached_match_status_both_sides(clean_db):
    import database
    user_id = clean_db
    tx_id = database.insert_ledger_transaction(
        user_id, extracted_data_id=None,
        date_iso="2026-08-04", description="AMAZON UK", amount=-42.99,
    )
    rc_id = database.insert_ledger_receipt(
        user_id, extracted_data_id=None,
        file_path=None, source_filename="r.pdf",
        store_name="Amazon", date_iso="2026-08-04",
        total_amount=42.99, tax_amount=7.16,
    )
    # Before linking
    tx_before = database.get_transaction_by_id(tx_id, user_id)
    rc_before = database.get_receipt_by_id(rc_id, user_id)
    assert tx_before["receipt_status"] == "missing"
    assert rc_before["match_status"] == "unmatched"
    assert tx_before["vat_amount"] is None

    database.insert_ledger_link(
        transaction_id=tx_id, receipt_id=rc_id,
        match_strategy="exact", confidence=100,
        reason="Test link",
    )

    tx_after = database.get_transaction_by_id(tx_id, user_id)
    rc_after = database.get_receipt_by_id(rc_id, user_id)
    assert tx_after["receipt_status"] == "matched"
    assert rc_after["match_status"] == "matched"
    # VAT is inherited from the receipt for fast HMRC summary lookups.
    assert abs(tx_after["vat_amount"] - 7.16) < 0.01


def test_unlinking_rolls_back_status_when_no_other_links(clean_db):
    import database
    user_id = clean_db
    tx_id = database.insert_ledger_transaction(
        user_id, extracted_data_id=None,
        date_iso="2026-08-04", description="AMAZON UK", amount=-42.99,
    )
    rc_id = database.insert_ledger_receipt(
        user_id, extracted_data_id=None,
        file_path=None, source_filename="r.pdf",
        store_name="Amazon", date_iso="2026-08-04",
        total_amount=42.99, tax_amount=7.16,
    )
    database.insert_ledger_link(
        transaction_id=tx_id, receipt_id=rc_id,
        match_strategy="exact", confidence=100,
    )
    database.remove_ledger_link(tx_id, rc_id)

    tx_after = database.get_transaction_by_id(tx_id, user_id)
    rc_after = database.get_receipt_by_id(rc_id, user_id)
    assert tx_after["receipt_status"] == "missing"
    assert tx_after["vat_amount"] is None
    assert rc_after["match_status"] == "unmatched"


def test_idempotent_link_does_not_duplicate(clean_db):
    """Re-linking the same pair is a no-op (PK enforces uniqueness)."""
    import database
    user_id = clean_db
    tx_id = database.insert_ledger_transaction(
        user_id, extracted_data_id=None,
        date_iso="2026-08-04", description="AMAZON", amount=-42.99,
    )
    rc_id = database.insert_ledger_receipt(
        user_id, extracted_data_id=None,
        file_path=None, source_filename="r.pdf",
        store_name="Amazon", date_iso="2026-08-04",
        total_amount=42.99,
    )
    database.insert_ledger_link(
        transaction_id=tx_id, receipt_id=rc_id,
        match_strategy="exact", confidence=100,
    )
    database.insert_ledger_link(
        transaction_id=tx_id, receipt_id=rc_id,
        match_strategy="manual", confidence=100,
    )
    links = database.get_links_for_transaction(tx_id)
    assert len(links) == 1
    # The newer call overwrote the strategy
    assert links[0]["match_strategy"] == "manual"


# ---------------------------------------------------------------------------
# CASCADE deletes
# ---------------------------------------------------------------------------


def test_clear_user_ledger_removes_everything(clean_db):
    import database
    user_id = clean_db
    tx_id = database.insert_ledger_transaction(
        user_id, extracted_data_id=None,
        date_iso="2026-08-04", description="AMAZON", amount=-42.99,
    )
    rc_id = database.insert_ledger_receipt(
        user_id, extracted_data_id=None,
        file_path=None, source_filename="r.pdf",
        store_name="Amazon", date_iso="2026-08-04",
        total_amount=42.99,
    )
    database.insert_ledger_link(
        transaction_id=tx_id, receipt_id=rc_id,
        match_strategy="exact", confidence=100,
    )

    count = database.clear_user_ledger(user_id)
    assert count == 1
    assert database.get_user_ledger_transactions(user_id) == []
    assert database.get_user_ledger_receipts(user_id) == []
    assert database.get_links_for_transaction(tx_id) == []


def test_update_transaction_status_only_overwrites_provided_fields(clean_db):
    """Calling update with one field must NOT clear other columns."""
    import database
    user_id = clean_db
    tx_id = database.insert_ledger_transaction(
        user_id, extracted_data_id=None,
        date_iso="2026-08-04", description="AMAZON", amount=-42.99,
        hmrc_category="office_expenses",
        hmrc_category_confidence=92,
        hmrc_category_reason="Office consumables",
    )
    database.update_transaction_status(
        tx_id, business_pct=50, is_capital=1,
    )
    fresh = database.get_transaction_by_id(tx_id, user_id)
    assert fresh["business_pct"] == 50
    assert fresh["is_capital"] == 1
    # Other columns untouched
    assert fresh["hmrc_category"] == "office_expenses"
    assert fresh["hmrc_category_confidence"] == 92
    assert fresh["hmrc_category_reason"] == "Office consumables"


def test_get_user_ledger_receipts_filters_unmatched(clean_db):
    import database
    user_id = clean_db
    matched_rc = database.insert_ledger_receipt(
        user_id, extracted_data_id=None, file_path=None, source_filename="m.pdf",
        store_name="A", date_iso="2026-08-04", total_amount=10.0,
    )
    unmatched_rc = database.insert_ledger_receipt(
        user_id, extracted_data_id=None, file_path=None, source_filename="u.pdf",
        store_name="B", date_iso="2026-08-05", total_amount=20.0,
    )
    tx_id = database.insert_ledger_transaction(
        user_id, extracted_data_id=None,
        date_iso="2026-08-04", description="A", amount=-10.0,
    )
    database.insert_ledger_link(
        transaction_id=tx_id, receipt_id=matched_rc,
        match_strategy="exact", confidence=100,
    )

    only_unmatched = database.get_user_ledger_receipts(user_id, only_unmatched=True)
    assert len(only_unmatched) == 1
    assert only_unmatched[0]["id"] == unmatched_rc
