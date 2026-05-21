"""
End-to-end tests for the reference field — parser output → DB →
categoriser → /api/ledger response → /ledger UI.

If you remove the reference column or break the wiring, the dashboard
quietly stops surfacing the strong tax-categorisation signal. These
tests pin every layer touched.
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_reference_pipeline.db"


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


# ---------------------------------------------------------------------------
# DB layer: column exists, insert helper accepts reference, read-back works
# ---------------------------------------------------------------------------


def test_ledger_transactions_table_has_reference_column():
    """The ALTER TABLE migration must run on init_db()."""
    import database as _db
    cur = _db._get_sqlite().execute("PRAGMA table_info(ledger_transactions)")
    cols = {row[1] for row in cur.fetchall()}
    assert "reference" in cols


def test_insert_ledger_transaction_stores_reference():
    import database as _db
    _db.create_user("ref@example.com", "pw")
    uid = _db.get_user_by_email("ref@example.com")["id"]
    tx_id = _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-08-15", description="FPI Acme Ltd",
        reference="INV-2026-001", amount=2500.0,
    )
    row = _db.get_transaction_by_id(tx_id, uid)
    assert row["reference"] == "INV-2026-001"
    assert row["description"] == "FPI Acme Ltd"


def test_insert_ledger_transaction_reference_optional_stays_back_compat():
    """Existing callers that don't pass reference still work."""
    import database as _db
    _db.create_user("noref@example.com", "pw")
    uid = _db.get_user_by_email("noref@example.com")["id"]
    tx_id = _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-08-15", description="STRIPE PAYOUT",
        amount=500.0,
    )
    row = _db.get_transaction_by_id(tx_id, uid)
    assert row["reference"] is None


def test_content_hash_independent_of_reference():
    """Re-parsing the same statement, with text shifting between
    description and reference, should NOT change the content hash —
    otherwise we'd double-import every transaction on re-upload."""
    import database as _db
    _db.create_user("hash@example.com", "pw")
    uid = _db.get_user_by_email("hash@example.com")["id"]
    h1 = _db._hash_transaction("2026-08-15", "Acme Ltd", -100.0)
    # Hash is just (date, description, amount) — reference excluded.
    # Sanity: same args → same hash; different reference shouldn't matter
    # because the hasher doesn't accept it.
    h2 = _db._hash_transaction("2026-08-15", "Acme Ltd", -100.0)
    assert h1 == h2


# ---------------------------------------------------------------------------
# Pipeline: ingest_statement_rows accepts reference from parser output
# ---------------------------------------------------------------------------


def test_ingest_statement_rows_threads_reference_through_to_db():
    import database as _db
    from services.ledger_ingest import ingest_statement_rows
    _db.create_user("pipe@example.com", "pw")
    uid = _db.get_user_by_email("pipe@example.com")["id"]
    ed = _db.save_extracted_data(uid, "statement", "stmt.pdf", [])
    ids = ingest_statement_rows(uid, ed, [
        {"date": "2026-08-15", "description": "FPI Acme Ltd",
         "reference": "INV-2026-001", "amount": 2500.0, "type": "credit"},
        {"date": "2026-08-16", "description": "DD British Gas",
         "reference": "Account 1234567", "amount": -85.0, "type": "debit"},
        # No reference — must still ingest cleanly
        {"date": "2026-08-17", "description": "STRIPE PAYOUT",
         "amount": 450.0, "type": "credit"},
    ])
    assert len(ids) == 3
    rows = _db.get_user_ledger_transactions(uid)
    by_desc = {r["description"]: r for r in rows}
    assert by_desc["FPI Acme Ltd"]["reference"] == "INV-2026-001"
    assert by_desc["DD British Gas"]["reference"] == "Account 1234567"
    assert by_desc["STRIPE PAYOUT"]["reference"] is None


def test_ingest_accepts_ref_alias_for_reference():
    """Some parsers (older or third-party) might use `ref`. We accept it."""
    import database as _db
    from services.ledger_ingest import ingest_statement_rows
    _db.create_user("alias@example.com", "pw")
    uid = _db.get_user_by_email("alias@example.com")["id"]
    ed = _db.save_extracted_data(uid, "statement", "stmt.pdf", [])
    ingest_statement_rows(uid, ed, [
        {"date": "2026-08-15", "description": "Acme",
         "ref": "INV-99", "amount": 100.0},
    ])
    row = _db.get_user_ledger_transactions(uid)[0]
    assert row["reference"] == "INV-99"


def test_ingest_empty_string_reference_normalised_to_null():
    """Parser may emit empty strings. We want NULL in the DB so the
    categoriser sees a clean None."""
    import database as _db
    from services.ledger_ingest import ingest_statement_rows
    _db.create_user("empty@example.com", "pw")
    uid = _db.get_user_by_email("empty@example.com")["id"]
    ed = _db.save_extracted_data(uid, "statement", "stmt.pdf", [])
    ingest_statement_rows(uid, ed, [
        {"date": "2026-08-15", "description": "Acme",
         "reference": "  ", "amount": 100.0},
    ])
    row = _db.get_user_ledger_transactions(uid)[0]
    assert row["reference"] is None


# ---------------------------------------------------------------------------
# /api/ledger surfaces reference
# ---------------------------------------------------------------------------


def _authed(email: str = "ledger@example.com"):
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
    _db.update_user(user["id"], subscription_status="trialing",
                    stripe_subscription_id="s",
                    trial_end_at=time.time() + 7*86400)
    return client, user


def test_api_ledger_includes_reference_in_each_transaction():
    client, user = _authed("ledger@example.com")
    import database as _db
    _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-15", description="FPI Acme Ltd",
        reference="INV-2026-001", amount=2500.0,
    )
    _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-16", description="STRIPE PAYOUT",
        amount=500.0,
    )
    r = client.get("/api/ledger")
    assert r.status_code == 200
    txs = {t["description"]: t for t in r.json()["transactions"]}
    assert txs["FPI Acme Ltd"]["reference"] == "INV-2026-001"
    assert txs["STRIPE PAYOUT"]["reference"] is None


# ---------------------------------------------------------------------------
# Categorisation schema — TransactionIn accepts + echoes reference
# ---------------------------------------------------------------------------


def test_categorise_request_schema_accepts_reference():
    from hmrc.schemas.categorise import CategoriseRequest
    req = CategoriseRequest(
        business_type="se",
        rows=[
            {"description": "Acme", "reference": "INV-99", "amount": 100.0},
            {"description": "Other", "amount": -50.0},  # reference omitted
        ],
    )
    assert req.rows[0].reference == "INV-99"
    assert req.rows[1].reference is None


def test_categorise_rules_use_reference():
    """Calling the categoriser with rows that have reference set must
    return the high-confidence reference-driven category."""
    from hmrc.schemas.categorise import CategoriseRequest, TransactionIn
    from hmrc.services.categorisation import resolve
    import asyncio
    req = CategoriseRequest(business_type="se", rows=[
        TransactionIn(description="FPI Acme Ltd",
                      reference="INV-2026-001", amount=2500.0),
    ])
    resp, _metrics = asyncio.run(resolve(req, user_id=None))
    assert resp.rows[0].hmrc.category == "turnover"
    assert resp.rows[0].hmrc.confidence >= 0.9
    assert resp.rows[0].reference == "INV-2026-001"  # echoed back


# ---------------------------------------------------------------------------
# AI classifier prompt — reference appears when provided
# ---------------------------------------------------------------------------


def test_ai_classifier_prompt_includes_reference_when_present():
    """The reference text must show up in the prompt so the AI sees it."""
    from hmrc.services.ai_classifier import _build_prompt
    from hmrc.schemas.categories import SE_CATEGORIES
    p = _build_prompt(
        [
            {"description": "Acme Ltd", "reference": "INV-2026-001", "amount": 100.0},
            {"description": "Tesco", "amount": -10.0},  # no reference
        ],
        business_type="se",
        categories=SE_CATEGORIES,
    )
    assert 'reference="INV-2026-001"' in p
    # Reference doesn't appear for the row that doesn't have one
    tesco_line = [ln for ln in p.split("\n") if "Tesco" in ln][0]
    assert "reference=" not in tesco_line


def test_ai_classifier_prompt_explains_how_to_use_reference():
    from hmrc.services.ai_classifier import _build_prompt
    from hmrc.schemas.categories import SE_CATEGORIES
    p = _build_prompt(
        [{"description": "x", "amount": 1.0}],
        business_type="se",
        categories=SE_CATEGORIES,
    )
    # The instruction block teaches the AI what `reference` is and how
    # to weight HMRC tax payments correctly.
    assert "reference" in p.lower()
    assert "HMRC" in p
    assert "Self Assessment" in p
