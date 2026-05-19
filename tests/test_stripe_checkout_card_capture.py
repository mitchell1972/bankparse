"""
Regression tests for the Stripe Checkout config that captures the user's
card during the 7-day trial.

The single most important Stripe parameter here is:

    payment_method_collection="always"

Without it, Stripe lets a subscription enter `trialing` status WITHOUT a
saved payment method — so the user gets 7 days of free product access
and Stripe has nothing to charge on day 8. That defeats the whole point
of the card-on-file gate. These tests pin the exact Stripe Session.create
arguments so any future config drift fails CI.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_user(user_id: int = 42, email: str = "trial-card@example.com") -> dict:
    return {
        "id": user_id,
        "email": email,
        "stripe_customer_id": "cus_existing_test",
    }


def _patched_stripe(no_existing_subs: bool = True):
    """Build a mock stripe module that returns a fake Session + Customer."""
    fake_stripe = MagicMock()
    fake_stripe.api_key = "sk_test_dummy"

    fake_stripe.Customer.retrieve.return_value = MagicMock(
        id="cus_existing_test", deleted=False,
    )
    fake_stripe.Subscription.list.return_value = MagicMock(
        data=[] if no_existing_subs else [
            MagicMock(status="active", id="sub_existing"),
        ],
    )
    fake_session = MagicMock(
        id="cs_test_abc123",
        url="https://checkout.stripe.com/c/pay/cs_test_abc123",
    )
    fake_stripe.checkout.Session.create.return_value = fake_session
    return fake_stripe


# ---------------------------------------------------------------------------
# Card-collection contract
# ---------------------------------------------------------------------------

def test_checkout_session_forces_card_collection_during_trial(monkeypatch):
    """REGRESSION — `payment_method_collection="always"` MUST be present in
    the Stripe Session.create call. If it isn't, Stripe lets trials start
    without a card and we have nothing to charge on day 8."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_test_starter")

    fake_stripe = _patched_stripe()
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        url = billing.create_trial_checkout_session(
            user=_fake_user(),
            success_url="https://bankscanai.com/?trial=started",
            cancel_url="https://bankscanai.com/start-trial?canceled=1",
        )

    assert url == "https://checkout.stripe.com/c/pay/cs_test_abc123"
    call_kwargs = fake_stripe.checkout.Session.create.call_args.kwargs

    assert call_kwargs["payment_method_collection"] == "always", (
        "Card-on-file trial REQUIRES payment_method_collection='always'. "
        "Without it Stripe will start a trial without saving a card and "
        "the day-8 charge silently no-ops."
    )


def test_checkout_session_uses_subscription_mode_with_7_day_trial(monkeypatch):
    """The Stripe Session must be in subscription mode (so a Subscription
    object exists to attach the card to) AND set trial_period_days=7."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_test_starter")

    fake_stripe = _patched_stripe()
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        billing.create_trial_checkout_session(
            user=_fake_user(),
            success_url="https://bankscanai.com/?trial=started",
            cancel_url="https://bankscanai.com/start-trial?canceled=1",
        )

    kwargs = fake_stripe.checkout.Session.create.call_args.kwargs
    assert kwargs["mode"] == "subscription"
    assert kwargs["payment_method_types"] == ["card"]
    assert kwargs["subscription_data"]["trial_period_days"] == 7


def test_checkout_session_cancels_if_card_detached_mid_trial(monkeypatch):
    """If the user detaches the card mid-trial, the subscription must
    auto-cancel at trial end rather than ending up in `incomplete` status
    (which would let them keep parsing for free)."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_test_starter")

    fake_stripe = _patched_stripe()
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        billing.create_trial_checkout_session(
            user=_fake_user(),
            success_url="https://x", cancel_url="https://y",
        )

    kwargs = fake_stripe.checkout.Session.create.call_args.kwargs
    trial_settings = kwargs["subscription_data"]["trial_settings"]
    assert trial_settings["end_behavior"]["missing_payment_method"] == "cancel"


def test_checkout_session_uses_starter_price_id_by_default(monkeypatch):
    """New trials enrol the user on the Starter plan. They can change via
    the Stripe customer portal later — keeping the signup wall to one click."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_test_starter")

    fake_stripe = _patched_stripe()
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        billing.create_trial_checkout_session(
            user=_fake_user(),
            success_url="https://x", cancel_url="https://y",
        )

    kwargs = fake_stripe.checkout.Session.create.call_args.kwargs
    assert kwargs["line_items"] == [
        {"price": "price_test_starter", "quantity": 1},
    ]


# ---------------------------------------------------------------------------
# Anti-abuse / safety
# ---------------------------------------------------------------------------

def test_checkout_session_refuses_when_user_already_has_active_sub(monkeypatch):
    """Belt-and-suspenders: even if our local DB thinks the user has no
    subscription, the Stripe customer might already be subscribed (e.g. a
    missed webhook). Refuse so we never double-bill a paying customer."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_test_starter")

    fake_stripe = _patched_stripe(no_existing_subs=False)
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        with pytest.raises(ValueError, match="already has an active subscription"):
            billing.create_trial_checkout_session(
                user=_fake_user(),
                success_url="https://x", cancel_url="https://y",
            )


def test_checkout_session_idempotency_key_is_user_specific(monkeypatch):
    """Double-clicking the 'Start trial' button must NOT spawn two
    checkout sessions. Stripe dedupes via Idempotency-Key — we key it on
    user_id so a single user's double-click is collapsed, but two
    different users still get separate sessions."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_test_starter")

    fake_stripe = _patched_stripe()
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        billing.create_trial_checkout_session(
            user=_fake_user(user_id=99),
            success_url="https://x", cancel_url="https://y",
        )

    kwargs = fake_stripe.checkout.Session.create.call_args.kwargs
    assert kwargs["idempotency_key"] == "trial-checkout-99-v1"


def test_checkout_session_attaches_user_id_to_subscription_metadata(monkeypatch):
    """Subscription metadata.user_id lets us route webhook events to the
    right user even if the customer record gets re-keyed."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_test_starter")

    fake_stripe = _patched_stripe()
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        billing.create_trial_checkout_session(
            user=_fake_user(user_id=777),
            success_url="https://x", cancel_url="https://y",
        )

    kwargs = fake_stripe.checkout.Session.create.call_args.kwargs
    assert kwargs["subscription_data"]["metadata"]["user_id"] == "777"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_checkout_session_raises_when_stripe_key_missing(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("STRIPE_STARTER_PRICE_ID", "price_test_starter")
    fake_stripe = MagicMock()
    fake_stripe.api_key = ""
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        with pytest.raises(ValueError, match="STRIPE_SECRET_KEY"):
            billing.create_trial_checkout_session(
                user=_fake_user(),
                success_url="https://x", cancel_url="https://y",
            )


def test_checkout_session_raises_when_price_id_missing(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
    monkeypatch.delenv("STRIPE_STARTER_PRICE_ID", raising=False)
    fake_stripe = _patched_stripe()
    from services import billing
    with patch.object(billing, "_stripe", return_value=fake_stripe):
        with pytest.raises(ValueError, match="STRIPE_STARTER_PRICE_ID"):
            billing.create_trial_checkout_session(
                user=_fake_user(),
                success_url="https://x", cancel_url="https://y",
            )
