"""
BankParse -- Usage / budget / global-ceiling unit tests

Covers the hot-path budget gate `core.check_can_use` and the `record_ai_spend`
bookkeeping. These tests use a real sqlite test database (same harness as
test_stripe_billing.py) and patch `core.get_user_tier` + the email-verified
check so we can exercise each tier in isolation without touching Stripe.

Invariants under test:
  - email unverified => blocked regardless of tier
  - free tier: 1 statement + 1 receipt per month, file-count gated
  - paid tier: blocked once monthly spend would exceed tier budget
  - paid tier: credit balance covers overage when budget exhausted
  - per-user daily cap: short-circuits even a user with budget remaining
  - global daily ceiling: short-circuits even a user within their own caps
"""

import os
import sys
import datetime
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_usage_budget.db"


@pytest.fixture(autouse=True)
def clean_db():
    """Fresh sqlite test db per test (same pattern as test_stripe_billing.py)."""
    import database

    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)

    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
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
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(email: str = "user@example.com", verified: bool = True) -> dict:
    """Insert a verified user and return the fresh user dict."""
    import database
    user_id = database.create_user(email, "pwhash")
    if verified:
        database.mark_email_verified(user_id)
    return database.get_user_by_id(user_id)


# ---------------------------------------------------------------------------
# Email verification gate
# ---------------------------------------------------------------------------

def test_unverified_user_is_blocked_regardless_of_tier():
    """A non-admin unverified user must be blocked on every tier."""
    import core
    user = _make_user("unverified@example.com", verified=False)
    with patch("core.get_user_tier", return_value="pro"):
        allowed, tier, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is False
        assert reason == "email_unverified"
        assert tier == "pro"


def test_unlimited_admin_bypasses_verification():
    """UNLIMITED_EMAILS entries skip the verification check even when unverified."""
    import core
    # Use an email that is in the default UNLIMITED_EMAILS set
    admin_email = next(iter(core.UNLIMITED_EMAILS))
    user = _make_user(admin_email, verified=False)
    # get_user_tier will classify this user as enterprise naturally, but we
    # still patch get_monthly_ai_spend via a fresh db so nothing is owed.
    allowed, tier, reason, _ = core.check_can_use(user, "receipt")
    assert allowed is True
    assert reason == "ok"
    assert tier == "enterprise"


# ---------------------------------------------------------------------------
# Free tier: file-count caps
# ---------------------------------------------------------------------------

def test_free_tier_allows_one_statement_then_blocks():
    """Free tier = 1 statement / month. Second call same month = blocked."""
    import core
    import database
    user = _make_user("free1@example.com")
    with patch("core.get_user_tier", return_value="free"):
        allowed, _, reason, _ = core.check_can_use(user, "statement")
        assert allowed is True
        assert reason == "ok"

        # Simulate the statement having been consumed
        database.increment_monthly_statements(user["id"], 1)

        allowed, _, reason, _ = core.check_can_use(user, "statement")
        assert allowed is False
        assert reason == "free_statements_cap"


def test_free_tier_allows_one_receipt_then_blocks():
    """Free tier = 1 receipt / month. Second call same month = blocked."""
    import core
    import database
    user = _make_user("free2@example.com")
    with patch("core.get_user_tier", return_value="free"):
        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is True

        database.increment_monthly_receipts(user["id"], 1)

        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is False
        assert reason == "free_receipts_cap"


def test_free_tier_statements_and_receipts_are_independent():
    """Using the one free statement does not consume the one free receipt."""
    import core
    import database
    user = _make_user("free3@example.com")
    with patch("core.get_user_tier", return_value="free"):
        # Use the one free statement
        database.increment_monthly_statements(user["id"], 1)

        # Statement is blocked
        allowed, _, reason, _ = core.check_can_use(user, "statement")
        assert allowed is False
        assert reason == "free_statements_cap"

        # But receipt is still allowed
        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is True
        assert reason == "ok"


# ---------------------------------------------------------------------------
# Paid tier: monthly spend budget
# ---------------------------------------------------------------------------

def test_paid_tier_allowed_while_under_budget():
    """A starter user who has spent £0 is under their £3.20 budget."""
    import core
    user = _make_user("starter1@example.com")
    with patch("core.get_user_tier", return_value="starter"):
        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is True
        assert reason == "ok"


def test_paid_tier_blocked_when_budget_exhausted_with_no_credit():
    """Starter user who has spent £3.20 should be blocked when the
    pessimistic estimate would push them over budget and they have no
    credit balance."""
    import core
    import database
    user = _make_user("starter2@example.com")
    # Push them right up to the starter budget
    database.add_to_monthly_ai_spend(user["id"], 3.20)
    with patch("core.get_user_tier", return_value="starter"):
        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is False
        assert reason == "monthly_budget_exhausted"


def test_paid_tier_overage_covered_by_credit_balance():
    """Over budget + sufficient credit => allowed, reason=ok."""
    import core
    import database
    user = _make_user("starter3@example.com")
    database.add_to_monthly_ai_spend(user["id"], 3.20)
    database.add_credit_balance(user["id"], 10.00)
    with patch("core.get_user_tier", return_value="starter"):
        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is True
        assert reason == "ok"


def test_paid_tier_overage_insufficient_credit_still_blocks():
    """Over budget + credit less than the pre-flight estimate => blocked."""
    import core
    import database
    user = _make_user("starter4@example.com")
    database.add_to_monthly_ai_spend(user["id"], 3.20)
    # A tiny credit that can't cover even the pessimistic receipt estimate (~0.5p)
    database.add_credit_balance(user["id"], 0.0001)
    with patch("core.get_user_tier", return_value="starter"):
        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is False
        assert reason == "monthly_budget_exhausted"


# ---------------------------------------------------------------------------
# Per-user daily cap (panic brake for one account)
# ---------------------------------------------------------------------------

def test_user_daily_cap_blocks_even_with_budget_remaining():
    """User within monthly budget but over their daily cap => blocked."""
    import core
    import ai_pricing
    user = _make_user("capuser@example.com")
    original_cap = ai_pricing.AI_USER_DAILY_CAP_GBP
    try:
        # Force the cap tiny so we can trip it
        ai_pricing.AI_USER_DAILY_CAP_GBP = 0.0001
        with patch("core.get_user_tier", return_value="enterprise"):
            allowed, _, reason, _ = core.check_can_use(user, "receipt")
            assert allowed is False
            assert reason == "user_daily_cap"
    finally:
        ai_pricing.AI_USER_DAILY_CAP_GBP = original_cap


# ---------------------------------------------------------------------------
# Global daily ceiling (panic brake for whole service)
# ---------------------------------------------------------------------------

def test_global_daily_ceiling_blocks_everyone():
    """If GLOBAL today's spend is already at/above the ceiling, everyone is
    blocked -- even a verified enterprise user well within their own cap."""
    import core
    import ai_pricing
    user = _make_user("global@example.com")
    original_global = ai_pricing.AI_DAILY_BUDGET_GBP
    try:
        # Tiny global budget: force ceiling trip
        ai_pricing.AI_DAILY_BUDGET_GBP = 0.0001
        with patch("core.get_user_tier", return_value="enterprise"):
            allowed, _, reason, _ = core.check_can_use(user, "receipt")
            assert allowed is False
            assert reason == "global_daily_cap"
    finally:
        ai_pricing.AI_DAILY_BUDGET_GBP = original_global


def test_global_ceiling_beats_user_cap_in_ordering():
    """When BOTH the global ceiling AND the user daily cap are tripped,
    the global code should win (it's checked first in check_can_use)."""
    import core
    import ai_pricing
    user = _make_user("order@example.com")
    orig_g = ai_pricing.AI_DAILY_BUDGET_GBP
    orig_u = ai_pricing.AI_USER_DAILY_CAP_GBP
    try:
        ai_pricing.AI_DAILY_BUDGET_GBP = 0.0001
        ai_pricing.AI_USER_DAILY_CAP_GBP = 0.0001
        with patch("core.get_user_tier", return_value="enterprise"):
            allowed, _, reason, _ = core.check_can_use(user, "receipt")
            assert allowed is False
            assert reason == "global_daily_cap"
    finally:
        ai_pricing.AI_DAILY_BUDGET_GBP = orig_g
        ai_pricing.AI_USER_DAILY_CAP_GBP = orig_u


# ---------------------------------------------------------------------------
# record_ai_spend: bookkeeping correctness
# ---------------------------------------------------------------------------

def test_record_ai_spend_charges_monthly_budget_first():
    """A user under their budget should have the cost deducted from the
    running monthly spend, not the credit balance."""
    import core
    import database
    user = _make_user("rec1@example.com")
    database.add_credit_balance(user["id"], 5.00)  # plenty of credit
    with patch("core.get_user_tier", return_value="starter"):
        result = core.record_ai_spend(
            user_id=user["id"],
            mode="receipt",
            model="claude-haiku-4-5-20251001",
            input_tokens=1500,
            output_tokens=500,
        )
    assert result["billed_to"] == "budget"
    assert result["cost_gbp"] > 0
    assert database.get_credit_balance(user["id"]) == pytest.approx(5.00, abs=1e-6)
    assert database.get_monthly_ai_spend(user["id"]) > 0


def test_record_ai_spend_falls_back_to_credit_when_budget_exhausted():
    """Once the monthly budget is exhausted, cost should deduct from credit."""
    import core
    import database
    user = _make_user("rec2@example.com")
    database.add_to_monthly_ai_spend(user["id"], 3.20)  # budget exhausted
    database.add_credit_balance(user["id"], 5.00)
    starting_credit = database.get_credit_balance(user["id"])
    with patch("core.get_user_tier", return_value="starter"):
        result = core.record_ai_spend(
            user_id=user["id"],
            mode="receipt",
            model="claude-haiku-4-5-20251001",
            input_tokens=1500,
            output_tokens=500,
        )
    assert result["billed_to"] == "credit"
    ending_credit = database.get_credit_balance(user["id"])
    assert ending_credit < starting_credit
    assert (starting_credit - ending_credit) == pytest.approx(result["cost_gbp"], abs=1e-6)


def test_record_ai_spend_writes_to_usage_log():
    """Every call must be persisted to ai_usage_log for audit."""
    import core
    import database
    user = _make_user("rec3@example.com")
    with patch("core.get_user_tier", return_value="pro"):
        core.record_ai_spend(
            user_id=user["id"],
            mode="statement",
            model="claude-haiku-4-5-20251001",
            input_tokens=2000,
            output_tokens=2000,
        )
    log = database.get_recent_ai_usage(limit=10)
    assert len(log) == 1
    assert log[0]["user_id"] == user["id"]
    assert log[0]["mode"] == "statement"
    assert log[0]["model"] == "claude-haiku-4-5-20251001"
    assert log[0]["input_tokens"] == 2000
    assert log[0]["output_tokens"] == 2000
    assert log[0]["cost_gbp"] > 0


def test_record_ai_spend_anonymous_user_still_logs():
    """A None user_id (anonymous/session call) must still land in the log
    so the global ceiling is enforceable."""
    import core
    import database
    core.record_ai_spend(
        user_id=None,
        mode="receipt",
        model="claude-haiku-4-5-20251001",
        input_tokens=1500,
        output_tokens=500,
    )
    log = database.get_recent_ai_usage(limit=10)
    assert len(log) == 1
    assert log[0]["user_id"] is None
    assert log[0]["cost_gbp"] > 0
