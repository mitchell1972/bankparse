"""
Regression test for the sign-bug Mitchell spotted on 2026-05-20:

  /api/audit-summary was reporting Total Expenses £19,139.33 for a bank
  statement whose real debits totalled £8,439.72. The £10,699.61 of
  credits were being lumped into the "uncategorised" expense bucket —
  because the old _bucket_key() lumped EVERY uncategorised transaction
  into one bucket regardless of sign, and the bucket-summing used
  abs(amount).

  Fix: split by sign — credits go to 'uncategorised_income', debits
  stay in 'uncategorised'.

The numbers in the test below are the EXACT figures from the real
bank statement that surfaced the bug (PDF totals report):
  Total Transactions: 86
  Total Credits:      £10,699.61
  Total Debits:       £8,439.72
  Net:                £2,259.89
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_sign_audit.db"


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


def _seed(amounts: list[float]) -> int:
    """Seed a user with N transactions of given amounts (NO hmrc_category)."""
    import database as _db
    _db.create_user("sign@example.com", "h")
    uid = _db.get_user_by_email("sign@example.com")["id"]
    for i, amt in enumerate(amounts):
        _db.insert_ledger_transaction(
            uid, extracted_data_id=None,
            date_iso="2026-05-15",
            description=f"TX_{i}",
            amount=float(amt),
            hmrc_category=None,  # The bug condition: uncategorised
        )
    return uid


# ---------------------------------------------------------------------------
# THE BUG MITCHELL FOUND
# ---------------------------------------------------------------------------


def test_uncategorised_credits_do_not_count_as_expenses():
    """The real-data scenario: an 86-transaction bank statement uploaded
    with no categorisation. Credits and debits BOTH ended up as 'expenses'
    in the dashboard total. After the fix they bucket separately."""
    from services.audit_summary import summarise_audit_readiness

    # Simulate the user's actual file: many small txs whose absolute
    # totals match the bank's own report.
    credits = [1000.0, 5000.0, 4699.61]   # sum = 10_699.61
    debits =  [-3000.0, -4000.0, -1439.72]  # sum (abs) = 8_439.72
    uid = _seed(credits + debits)

    s = summarise_audit_readiness(uid)

    # Expenses MUST equal the sum of |debits| only, NOT the sum of all |amts|.
    assert abs(s["totals"]["expenses"] - 8_439.72) < 0.01, (
        f"Expected expenses £8,439.72, got £{s['totals']['expenses']}. "
        "The credits are being miscounted as expenses again."
    )
    # Income equals sum of credits.
    assert abs(s["totals"]["income"] - 10_699.61) < 0.01

    # Net should reconcile to the bank's own number.
    net = s["totals"]["income"] - s["totals"]["expenses"]
    assert abs(net - 2_259.89) < 0.01


def test_uncategorised_split_into_two_buckets():
    from services.audit_summary import summarise_audit_readiness
    uid = _seed([100.0, -50.0])
    s = summarise_audit_readiness(uid)
    keys = {c["category"] for c in s["categories"]}
    assert "uncategorised_income" in keys
    assert "uncategorised" in keys


def test_uncategorised_income_is_marked_is_income():
    from services.audit_summary import summarise_audit_readiness
    uid = _seed([100.0, -50.0])
    s = summarise_audit_readiness(uid)
    by_cat = {c["category"]: c for c in s["categories"]}
    assert by_cat["uncategorised_income"]["is_income"] is True
    assert by_cat["uncategorised"]["is_income"] is False


def test_overall_audit_ready_pct_unaffected_by_credits():
    """Spend-weighted % must be computed against EXPENSE total only —
    credits in 'uncategorised_income' shouldn't be in the denominator."""
    from services.audit_summary import summarise_audit_readiness
    # 1 expense fully receipted + lots of credits → 100% audit-ready
    import database as _db
    _db.create_user("pct@example.com", "h")
    uid = _db.get_user_by_email("pct@example.com")["id"]

    # Big credit (income) — shouldn't pull % down
    _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="BIG CREDIT",
        amount=10_000.0, hmrc_category=None,
    )
    # Small expense, matched
    tx_exp = _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="EXPENSE",
        amount=-100.0, hmrc_category="se_office_expenses",
    )
    rc = _db.insert_ledger_receipt(
        uid, extracted_data_id=None, file_path=None,
        source_filename="r.pdf", store_name="A",
        date_iso="2026-05-15", total_amount=100.0, tax_amount=16.67,
    )
    _db.insert_ledger_link(transaction_id=tx_exp, receipt_id=rc,
                           match_strategy="exact", confidence=100)

    s = summarise_audit_readiness(uid)
    # The £10k credit doesn't drag the overall % down — only expenses count.
    assert s["totals"]["audit_ready_pct"] == 100
    assert s["totals"]["expenses"] == 100.0
    assert s["totals"]["income"] == 10_000.0
