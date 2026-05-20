"""
Tests for the HMRC audit-readiness summary — the per-category scorecard
that proves to HMRC (and to the user) which expenses are evidenced.
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

TEST_DB_PATH = "/tmp/test_bankparse_audit_summary.db"


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


def _seed_user(email: str = "u@example.com") -> int:
    import database as _db
    _db.create_user(email, "pwhash")
    return _db.get_user_by_email(email)["id"]


def _add_tx(user_id: int, **kwargs) -> int:
    import database as _db
    base = {"extracted_data_id": None,
            "date_iso": "2026-08-04",
            "description": "AMAZON UK",
            "amount": -42.99}
    base.update(kwargs)
    return _db.insert_ledger_transaction(user_id, **base)


def _add_rc(user_id: int, **kwargs) -> int:
    import database as _db
    base = {"extracted_data_id": None,
            "file_path": None, "source_filename": "r.pdf",
            "store_name": "Amazon", "date_iso": "2026-08-04",
            "total_amount": 42.99, "tax_amount": 7.16}
    base.update(kwargs)
    return _db.insert_ledger_receipt(user_id, **base)


# ---------------------------------------------------------------------------
# summarise_audit_readiness — service layer
# ---------------------------------------------------------------------------


def test_empty_user_returns_zeros():
    from services.audit_summary import summarise_audit_readiness
    uid = _seed_user()
    s = summarise_audit_readiness(uid)
    assert s["categories"] == []
    assert s["totals"]["expenses"] == 0
    assert s["totals"]["audit_ready_pct"] == 0


def test_single_matched_expense_is_100_percent_audit_ready():
    from services.audit_summary import summarise_audit_readiness
    import database as _db
    uid = _seed_user()
    tx = _add_tx(uid, hmrc_category="se_office_expenses")
    rc = _add_rc(uid)
    _db.insert_ledger_link(transaction_id=tx, receipt_id=rc,
                           match_strategy="exact", confidence=100)

    s = summarise_audit_readiness(uid)
    cats = {c["category"]: c for c in s["categories"]}
    assert "se_office_expenses" in cats
    c = cats["se_office_expenses"]
    assert c["audit_ready_pct"] == 100
    assert abs(c["total_gross_gbp"] - 42.99) < 0.01
    assert abs(c["total_vat_gbp"] - 7.16) < 0.01
    assert s["totals"]["audit_ready_pct"] == 100


def test_mix_of_matched_and_unmatched_is_partial_score():
    from services.audit_summary import summarise_audit_readiness
    import database as _db
    uid = _seed_user()
    # 3 office expenses, 1 backed by a receipt
    tx1 = _add_tx(uid, hmrc_category="se_office_expenses", amount=-100.0)
    tx2 = _add_tx(uid, hmrc_category="se_office_expenses", amount=-100.0)
    tx3 = _add_tx(uid, hmrc_category="se_office_expenses", amount=-100.0)
    rc = _add_rc(uid, total_amount=100.0)
    _db.insert_ledger_link(transaction_id=tx1, receipt_id=rc,
                           match_strategy="exact", confidence=100)

    s = summarise_audit_readiness(uid)
    c = {x["category"]: x for x in s["categories"]}["se_office_expenses"]
    assert c["audit_ready_pct"] == 33  # 1/3 rounded
    assert s["totals"]["audit_ready_pct"] == 33


def test_excluded_personal_transactions_dont_count_in_totals():
    from services.audit_summary import summarise_audit_readiness
    import database as _db
    uid = _seed_user()
    tx_business = _add_tx(uid, hmrc_category="se_office_expenses", amount=-100.0)
    _add_tx(uid, hmrc_category="se_office_expenses", amount=-50.0)
    _db.update_transaction_status(_, receipt_status="excluded", exclusion_reason="personal") if False else None
    # Get the second tx ID via query and mark it excluded
    txs = _db.get_user_ledger_transactions(uid)
    personal_tx = [t for t in txs if t["amount"] == -50.0][0]
    _db.update_transaction_status(
        personal_tx["id"],
        receipt_status="excluded",
        exclusion_reason="personal",
    )

    s = summarise_audit_readiness(uid)
    c = {x["category"]: x for x in s["categories"]}["se_office_expenses"]
    # Only the £100 business tx counts; the £50 personal is excluded
    assert abs(c["total_gross_gbp"] - 100.0) < 0.01
    assert c["transaction_count"] == 1


def test_business_pct_split_proportionally_reduces_total():
    """A transaction marked 60% business contributes only 60% of its
    amount toward the HMRC total — the kind of detail accountants love."""
    from services.audit_summary import summarise_audit_readiness
    import database as _db
    uid = _seed_user()
    tx = _add_tx(uid, hmrc_category="se_motor_expenses", amount=-100.0)
    _db.update_transaction_status(tx, business_pct=60)
    s = summarise_audit_readiness(uid)
    c = {x["category"]: x for x in s["categories"]}["se_motor_expenses"]
    assert abs(c["total_gross_gbp"] - 60.0) < 0.01


def test_uncategorised_transactions_are_flagged_for_attention():
    """If a transaction hasn't been categorised, it shows in its own
    'uncategorised' bucket marked needs_attention=True — that nudges
    the user to fix it before they submit to HMRC."""
    from services.audit_summary import summarise_audit_readiness
    uid = _seed_user()
    _add_tx(uid, hmrc_category=None, amount=-25.00)
    s = summarise_audit_readiness(uid)
    cats = {c["category"]: c for c in s["categories"]}
    assert "uncategorised" in cats
    assert cats["uncategorised"]["needs_attention"] is True
    # And the audit_ready_pct counts these uncategorised as missing.
    # Since the only expense bucket is "uncategorised" with 0 matched,
    # overall should be 0%.
    assert s["totals"]["audit_ready_pct"] == 0


def test_income_categories_are_marked_and_excluded_from_audit_pct():
    """Income (e.g. se_turnover) doesn't need receipts. Should be
    flagged is_income=True and NOT pull the overall audit % down."""
    from services.audit_summary import summarise_audit_readiness
    uid = _seed_user()
    _add_tx(uid, hmrc_category="se_turnover", amount=2000.00)
    _add_tx(uid, hmrc_category="se_office_expenses", amount=-100.00)
    s = summarise_audit_readiness(uid)
    cats = {c["category"]: c for c in s["categories"]}
    assert cats["se_turnover"]["is_income"] is True
    # Expense bucket is 0% (no receipt) — overall = 0%
    assert s["totals"]["audit_ready_pct"] == 0
    # But income total IS captured separately
    assert abs(s["totals"]["income"] - 2000.00) < 0.01


def test_spend_weighted_overall_pct():
    """Heavy categories count more in the overall %. £900 of office
    expenses with 100% receipts + £100 of motor with 0% receipts =
    overall 90%, not 50%."""
    from services.audit_summary import summarise_audit_readiness
    import database as _db
    uid = _seed_user()
    tx_office = _add_tx(uid, hmrc_category="se_office_expenses", amount=-900.0)
    _add_tx(uid, hmrc_category="se_motor_expenses", amount=-100.0)
    rc = _add_rc(uid, total_amount=900.0)
    _db.insert_ledger_link(transaction_id=tx_office, receipt_id=rc,
                           match_strategy="exact", confidence=100)
    s = summarise_audit_readiness(uid)
    # office=100% over £900, motor=0% over £100 → spend-weighted = 90%
    assert s["totals"]["audit_ready_pct"] == 90


def test_capital_count_separated():
    from services.audit_summary import summarise_audit_readiness
    import database as _db
    uid = _seed_user()
    tx = _add_tx(uid, hmrc_category="se_office_expenses", amount=-350.0)
    _db.update_transaction_status(tx, is_capital=1)
    s = summarise_audit_readiness(uid)
    c = {x["category"]: x for x in s["categories"]}["se_office_expenses"]
    assert c["capital_count"] == 1


# ---------------------------------------------------------------------------
# /api/audit-summary endpoint
# ---------------------------------------------------------------------------


def _make_client(email: str) -> tuple:
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
                    stripe_subscription_id="sub_audit",
                    trial_end_at=time.time() + 7*86400)
    return client, user, csrf


def test_endpoint_requires_auth():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/api/audit-summary")
    assert r.status_code == 401


def test_endpoint_returns_summary_shape():
    client, user, _ = _make_client("api@example.com")
    import database as _db
    tx = _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="X", amount=-100.0,
        hmrc_category="se_office_expenses",
    )
    r = client.get("/api/audit-summary")
    assert r.status_code == 200
    body = r.json()
    assert "categories" in body
    assert "totals" in body
    assert body["totals"]["expenses"] == 100.0
