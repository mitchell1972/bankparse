"""
BankParse — Billing service (card-on-file trial flow).

This module owns the Stripe-backed 7-day free trial:

    register → verify email → /start-trial → Stripe Checkout (card on file)
                                              ↓
                            Subscription created in `trialing` state, no charge
                                              ↓ 7 days later
                                Stripe auto-bills; webhook drives state

The *legacy* trial (7 days from `users.created_at`, no card required) remains
in effect for users registered before this flow shipped — they carry
`grandfathered_trial = 1` and `core.is_trial_active` continues to honour them.
Only NEW signups go through Stripe Checkout for card collection.

Stripe is the source of truth. Webhooks (`checkout.session.completed`,
`customer.subscription.updated`, `customer.subscription.deleted`,
`invoice.payment_failed`) drive `users.subscription_status`,
`users.stripe_subscription_id` and `users.trial_end_at`. Webhook idempotency
is enforced via the `processed_webhooks` table (Stripe retries failed
webhooks for up to 3 days).
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("bankparse.billing")

TRIAL_DAYS = 7


def _stripe():
    """Lazy-import stripe so importing this module never fails when the SDK
    isn't installed (CI, tests, fresh dev clones)."""
    import stripe as _s
    _s.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    return _s


def _starter_price_id() -> str:
    """The default plan a new user trials toward.

    Per the product spec the user is auto-enrolled in Starter and can change
    tier later via the Stripe customer portal — choice paralysis kills
    conversion at the signup wall.
    """
    return os.environ.get("STRIPE_STARTER_PRICE_ID", "")


# --- Checkout session creation ------------------------------------------------

def create_trial_checkout_session(
    *,
    user: dict,
    success_url: str,
    cancel_url: str,
) -> str:
    """Create a Stripe Checkout session in subscription mode with a 7-day trial.

    Returns the hosted Checkout URL the caller should redirect to. Raises
    ValueError on missing config; lets stripe SDK exceptions propagate.

    Idempotency: passes an ``idempotency_key`` derived from user_id so a
    double-click can't spawn duplicate sessions. Stripe still produces one
    session, the caller redirects, and we rely on webhook delivery (not the
    success redirect) to mark the trial active.
    """
    stripe = _stripe()
    price_id = _starter_price_id()
    if not stripe.api_key:
        raise ValueError("STRIPE_SECRET_KEY not configured")
    if not price_id:
        raise ValueError("STRIPE_STARTER_PRICE_ID not configured")

    customer_id = _get_or_create_customer(user)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        payment_method_types=["card"],
        # CRITICAL anti-abuse setting: force card collection up front. Without
        # this Stripe allows trials to start without a payment method, which
        # defeats the whole point of the card-on-file gate.
        payment_method_collection="always",
        line_items=[{"price": price_id, "quantity": 1}],
        subscription_data={
            "trial_period_days": TRIAL_DAYS,
            # Belt-and-suspenders: if the card is detached mid-trial, the
            # subscription auto-cancels at trial end instead of trying to
            # charge nothing and ending up in `incomplete`.
            "trial_settings": {
                "end_behavior": {"missing_payment_method": "cancel"},
            },
            "metadata": {"user_id": str(user["id"])},
        },
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=str(user["id"]),
        # `idempotency_key` keyed on user_id+v1 — bumping suffix lets us
        # invalidate cached sessions if Stripe changes API behaviour.
        idempotency_key=f"trial-checkout-{user['id']}-v1",
    )
    logger.info("Trial checkout session %s created for user %s", session.id, user["id"])
    return session.url


def _get_or_create_customer(user: dict) -> str:
    """Reuse the existing Stripe Customer for this user, or create one.

    Email is the only identifying field — we never push PII beyond what's
    already in `users.email`.
    """
    existing = user.get("stripe_customer_id")
    if existing:
        return existing

    stripe = _stripe()
    # Don't trust user-provided email until verified; this service is only
    # called from a route that already gates on email_verified.
    customer = stripe.Customer.create(
        email=user["email"],
        metadata={"user_id": str(user["id"]), "source": "bankparse"},
    )
    from database import update_user
    update_user(user["id"], stripe_customer_id=customer.id)
    return customer.id


# --- Webhook event handlers ---------------------------------------------------

def handle_checkout_completed(event_data: dict) -> None:
    """Fired when the user completes Checkout (card entered, sub created).

    Sets `subscription_status='trialing'`, captures the subscription ID and
    its trial_end timestamp. Idempotent — re-running this with the same data
    yields the same row state.
    """
    from database import get_user_by_stripe_customer, update_user

    if event_data.get("mode") != "subscription":
        return  # ignore — this handler is only for subscription checkouts

    customer_id = event_data.get("customer")
    subscription_id = event_data.get("subscription")
    client_ref = event_data.get("client_reference_id")
    if not customer_id or not subscription_id:
        logger.warning("checkout.session.completed missing customer/subscription: %s", event_data.get("id"))
        return

    user = get_user_by_stripe_customer(customer_id)
    if not user and client_ref:
        # Customer wasn't linked yet — fall back to user_id from client_reference_id.
        try:
            from database import get_user_by_id
            user = get_user_by_id(int(client_ref))
        except (TypeError, ValueError):
            user = None

    if not user:
        logger.warning(
            "checkout.session.completed cannot resolve user (customer=%s, ref=%s)",
            customer_id, client_ref,
        )
        return

    # Fetch the subscription so we have authoritative trial_end / status.
    stripe = _stripe()
    sub = stripe.Subscription.retrieve(subscription_id)
    update_user(
        user["id"],
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        subscription_status=sub.status,            # 'trialing'
        trial_end_at=float(sub.trial_end) if sub.trial_end else None,
        subscription_checked_at=time.time(),
    )
    logger.info(
        "User %s entered trial via subscription %s (trial_end=%s)",
        user["id"], subscription_id, sub.trial_end,
    )


def handle_subscription_lifecycle(event_data: dict) -> None:
    """Sync `subscription_status` and `trial_end_at` from Stripe truth.

    Fires on `customer.subscription.updated`, `customer.subscription.deleted`.
    Stripe sends one of these whenever status flips (trialing → active,
    active → past_due, anything → canceled). We mirror.
    """
    from database import get_user_by_stripe_customer, update_user

    customer_id = event_data.get("customer")
    if not customer_id:
        return

    user = get_user_by_stripe_customer(customer_id)
    if not user:
        logger.warning("subscription lifecycle: no user for customer=%s", customer_id)
        return

    status = event_data.get("status", "")
    trial_end = event_data.get("trial_end")
    update_user(
        user["id"],
        stripe_subscription_id=event_data.get("id"),
        subscription_status=status,
        trial_end_at=float(trial_end) if trial_end else None,
        subscription_checked_at=time.time(),
    )
    logger.info("User %s subscription %s → status=%s", user["id"], event_data.get("id"), status)


def handle_payment_failed(event_data: dict) -> None:
    """First payment attempt at trial end failed.

    Stripe will retry via dunning; in the meantime mark the user as past_due
    so the gate engages. The `customer.subscription.updated` event that
    follows will overwrite with the canonical status.
    """
    from database import get_user_by_stripe_customer, update_user
    customer_id = event_data.get("customer")
    if not customer_id:
        return
    user = get_user_by_stripe_customer(customer_id)
    if user:
        update_user(user["id"], subscription_status="past_due", subscription_checked_at=time.time())


# --- Webhook idempotency ------------------------------------------------------
# Stripe retries failed webhook deliveries for up to 3 days. We dedupe by
# event.id in a tiny `processed_webhooks` table to make every handler safely
# replayable. Caller wraps the dispatch:
#
#     if was_processed(event["id"]):
#         return
#     dispatch(event)
#     mark_processed(event["id"], event["type"])

def was_processed(event_id: str) -> bool:
    if not event_id:
        return False
    from database import _fetchone_dict
    row = _fetchone_dict(
        "SELECT event_id FROM processed_webhooks WHERE event_id = ?",
        (event_id,),
    )
    return row is not None


def mark_processed(event_id: str, event_type: str) -> None:
    if not event_id:
        return
    from database import _execute
    try:
        _execute(
            "INSERT INTO processed_webhooks (event_id, event_type, received_at) VALUES (?, ?, ?)",
            (event_id, event_type, time.time()),
        )
    except Exception:
        # Race / replay — another worker already inserted. Safe to ignore.
        logger.debug("processed_webhooks insert race for event %s", event_id)
