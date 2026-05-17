"""
BankParse -- Card-on-file 7-day trial unit tests.

Covers the new Stripe-driven trial path that replaces "7 days from
registration with no card" for users created after the migration cutoff:

  - is_trial_active honours grandfathered_trial=1 with legacy rule
  - is_trial_active honours subscription_status='trialing' + trial_end_at
  - check_can_use returns 'payment_method_required' for new free users
    without a Stripe subscription
  - check_can_use returns 'trial_expired' for grandfathered users past the
    legacy 7-day window
  - Webhook idempotency: was_processed / mark_processed dedupes on event_id
  - handle_checkout_completed sets subscription_id, trial_end_at, status
    from a mocked Stripe subscription object
  - handle_subscription_lifecycle mirrors status updates
  - handle_payment_failed flips to past_due

Stripe SDK is mocked at the boundary; no network calls.
"""

import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_billing_trial.db"


@pytest.fixture(autouse=True)
def clean_db():
    """Fresh sqlite test db per test (same pattern as test_usage_budget.py)."""
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


# --- helpers -----------------------------------------------------------------

def _make_user(email="t@example.com", verified=True, grandfathered=False, **fields):
    import database
    user_id = database.create_user(email, "pwhash")
    if verified:
        database.mark_email_verified(user_id)
    if grandfathered or fields:
        updates = dict(fields)
        if grandfathered:
            updates["grandfathered_trial"] = 1
        if updates:
            database.update_user(user_id, **updates)
    return database.get_user_by_id(user_id)


# --- is_trial_active ---------------------------------------------------------

def test_grandfathered_user_active_within_legacy_window():
    """A grandfathered user signed up today still has the legacy 7-day trial."""
    import core
    user = _make_user("legacy@example.com", grandfathered=True)
    assert core.is_trial_active(user) is True


def test_grandfathered_user_expired_after_legacy_window():
    """Grandfathered user with created_at older than 7 days is no longer trialling."""
    import core, database
    user = _make_user("oldlegacy@example.com", grandfathered=True)
    # Backdate created_at by 8 days
    database._execute(
        "UPDATE users SET created_at = ? WHERE id = ?",
        (time.time() - 8 * 86400, user["id"]),
    )
    user = database.get_user_by_id(user["id"])
    assert core.is_trial_active(user) is False


def test_new_user_without_subscription_is_not_trialling():
    """A new (non-grandfathered) user with no subscription has no trial at all."""
    import core
    user = _make_user("brandnew@example.com", grandfathered=False)
    assert core.is_trial_active(user) is False


def test_new_user_in_stripe_trialing_status_is_active():
    """A new user with subscription_status='trialing' and a future trial_end_at is in trial."""
    import core
    user = _make_user(
        "trialing@example.com",
        subscription_status="trialing",
        trial_end_at=time.time() + 6 * 86400,
    )
    assert core.is_trial_active(user) is True


def test_new_user_with_expired_trial_end_at_is_not_active():
    """Even if status is still 'trialing', a past trial_end_at means trial is over."""
    import core
    user = _make_user(
        "expired@example.com",
        subscription_status="trialing",
        trial_end_at=time.time() - 60,
    )
    assert core.is_trial_active(user) is False


# --- check_can_use distinguishes 'never started' vs 'expired' ----------------

def test_check_can_use_new_user_without_card_gets_payment_method_required():
    """Brand-new free user without subscription → 'payment_method_required'."""
    import core
    user = _make_user("nocard@example.com", grandfathered=False)
    with patch("core.get_user_tier", return_value="free"):
        allowed, tier, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is False
        assert reason == "payment_method_required"
        assert tier == "free"


def test_check_can_use_grandfathered_expired_gets_trial_expired():
    """Grandfathered user past 7-day window → 'trial_expired' (not 'payment_method_required')."""
    import core, database
    user = _make_user("oldlegacy@example.com", grandfathered=True)
    database._execute(
        "UPDATE users SET created_at = ? WHERE id = ?",
        (time.time() - 8 * 86400, user["id"]),
    )
    user = database.get_user_by_id(user["id"])
    with patch("core.get_user_tier", return_value="free"):
        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is False
        assert reason == "trial_expired"


def test_check_can_use_active_trial_allowed():
    """User mid-trial with card on file → allowed."""
    import core
    user = _make_user(
        "midtrial@example.com",
        subscription_status="trialing",
        trial_end_at=time.time() + 4 * 86400,
    )
    with patch("core.get_user_tier", return_value="free"):
        allowed, _, reason, _ = core.check_can_use(user, "receipt")
        assert allowed is True
        assert reason == "ok"


# --- webhook idempotency -----------------------------------------------------

def test_webhook_idempotency_dedupes_by_event_id():
    """Replaying the same event.id is a no-op after the first mark_processed."""
    from services import billing
    assert billing.was_processed("evt_test_1") is False
    billing.mark_processed("evt_test_1", "checkout.session.completed")
    assert billing.was_processed("evt_test_1") is True
    # Re-marking is safe (race / replay)
    billing.mark_processed("evt_test_1", "checkout.session.completed")
    assert billing.was_processed("evt_test_1") is True


def test_webhook_idempotency_empty_event_id_returns_false():
    """Defensive: empty event_id never claims 'already processed'."""
    from services import billing
    assert billing.was_processed("") is False
    assert billing.was_processed(None) is False
    # mark_processed with empty id is a no-op (silent skip).
    billing.mark_processed("", "x")


# --- handle_checkout_completed ----------------------------------------------

def test_handle_checkout_completed_sets_trial_state_from_stripe_subscription():
    """checkout.session.completed (mode=subscription) writes sub_id, status, trial_end."""
    from services import billing
    import database

    user = _make_user("checkout@example.com")
    database.update_user(user["id"], stripe_customer_id="cus_TEST_123")

    # Mock the Stripe SDK to return a controlled Subscription object
    future_trial_end = int(time.time() + 7 * 86400)
    fake_sub = MagicMock(
        status="trialing",
        trial_end=future_trial_end,
    )
    with patch("services.billing._stripe") as mock_stripe_factory:
        mock_stripe = MagicMock()
        mock_stripe.Subscription.retrieve.return_value = fake_sub
        mock_stripe_factory.return_value = mock_stripe

        billing.handle_checkout_completed({
            "id": "cs_test_xyz",
            "mode": "subscription",
            "customer": "cus_TEST_123",
            "subscription": "sub_TEST_456",
            "client_reference_id": str(user["id"]),
        })

    fresh = database.get_user_by_id(user["id"])
    assert fresh["stripe_subscription_id"] == "sub_TEST_456"
    assert fresh["subscription_status"] == "trialing"
    assert fresh["trial_end_at"] == float(future_trial_end)


def test_handle_checkout_completed_ignores_non_subscription_mode():
    """checkout.session.completed with mode=payment is ignored here (credit pack path)."""
    from services import billing
    import database
    user = _make_user("payment@example.com")
    database.update_user(user["id"], stripe_customer_id="cus_TEST_payment")
    # Should NOT touch the user row.
    billing.handle_checkout_completed({
        "id": "cs_test_payment",
        "mode": "payment",
        "customer": "cus_TEST_payment",
    })
    fresh = database.get_user_by_id(user["id"])
    assert fresh.get("subscription_status") is None
    assert fresh.get("stripe_subscription_id") is None


# --- handle_subscription_lifecycle ------------------------------------------

def test_subscription_updated_to_active_mirrors_status():
    """When Stripe says 'active', we mirror that and update trial_end_at."""
    from services import billing
    import database
    user = _make_user("activate@example.com", subscription_status="trialing")
    database.update_user(user["id"], stripe_customer_id="cus_active", stripe_subscription_id="sub_a")

    billing.handle_subscription_lifecycle({
        "id": "sub_a",
        "customer": "cus_active",
        "status": "active",
        "trial_end": None,
    })
    fresh = database.get_user_by_id(user["id"])
    assert fresh["subscription_status"] == "active"
    assert fresh["trial_end_at"] is None


def test_subscription_deleted_sets_canceled():
    """customer.subscription.deleted flips status to canceled."""
    from services import billing
    import database
    user = _make_user("cancel@example.com", subscription_status="active")
    database.update_user(user["id"], stripe_customer_id="cus_cancel", stripe_subscription_id="sub_c")

    billing.handle_subscription_lifecycle({
        "id": "sub_c",
        "customer": "cus_cancel",
        "status": "canceled",
        "trial_end": None,
    })
    fresh = database.get_user_by_id(user["id"])
    assert fresh["subscription_status"] == "canceled"


# --- handle_payment_failed --------------------------------------------------

def test_payment_failed_flips_to_past_due():
    """invoice.payment_failed marks the user past_due."""
    from services import billing
    import database
    user = _make_user("pastdue@example.com", subscription_status="active")
    database.update_user(user["id"], stripe_customer_id="cus_pastdue")

    billing.handle_payment_failed({"customer": "cus_pastdue"})
    fresh = database.get_user_by_id(user["id"])
    assert fresh["subscription_status"] == "past_due"


# --- stale customer id defense ----------------------------------------------

def test_get_or_create_customer_recreates_on_stale_id():
    """If users.stripe_customer_id points at a customer that no longer exists
    in this Stripe account (e.g. left over from test-mode keys), we null it
    out and create a fresh one. Without this defense the live Stripe checkout
    creation fails with 'No such customer' and we wrongly surface "Could not
    start trial" to the user."""
    from services import billing
    import database
    import stripe as stripe_sdk

    user = _make_user("stale@example.com")
    database.update_user(user["id"], stripe_customer_id="cus_STALE_test_mode")
    user = database.get_user_by_id(user["id"])

    fresh_customer = MagicMock(id="cus_FRESH_live")

    with patch("services.billing._stripe") as factory:
        mock_stripe = MagicMock()
        # First retrieve raises InvalidRequestError "No such customer"
        mock_stripe.Customer.retrieve.side_effect = stripe_sdk.error.InvalidRequestError(
            "No such customer: 'cus_STALE_test_mode'", param="customer"
        )
        mock_stripe.Customer.create.return_value = fresh_customer
        # Expose the SDK's error classes on the mock so the SUT can `except`
        mock_stripe.error = stripe_sdk.error
        factory.return_value = mock_stripe

        result = billing._get_or_create_customer(user)

    assert result == "cus_FRESH_live"
    persisted = database.get_user_by_id(user["id"])
    assert persisted["stripe_customer_id"] == "cus_FRESH_live"
