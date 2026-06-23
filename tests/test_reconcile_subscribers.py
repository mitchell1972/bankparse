"""
Tests for scripts/reconcile_subscribers.py — the script that checks who the
database calls a "subscriber" against what Stripe ACTUALLY says.

The whole point of the script is to not trust the local subscription_status
field, so these tests pin the classification: a trialing user is not revenue,
a DB-active user whose Stripe sub is gone is STALE (hidden churn), an active
DB row with no Stripe customer is an orphan, and MRR counts only real payers
(with annual plans prorated to monthly).
"""

from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
recon = importlib.import_module("reconcile_subscribers")
import stripe  # noqa: E402


class _S(dict):
    """dict that also allows attribute access, like a Stripe object."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


def _sub(status, amount=600, interval="month", created=100):
    return _S(
        status=status, created=created,
        items=_S(data=[_S(
            price=_S(id="price_x", unit_amount=amount,
                     recurring=_S(interval=interval)),
            quantity=1,
        )]),
    )


@pytest.fixture()
def fake_stripe(monkeypatch):
    """Route Stripe list calls to in-memory fixtures keyed by customer id."""
    subs: dict = {}
    invoices: dict = {}
    monkeypatch.setattr(stripe.Subscription, "list",
                        staticmethod(lambda **k: _S(data=subs.get(k["customer"], []))))
    monkeypatch.setattr(stripe.Invoice, "list",
                        staticmethod(lambda **k: _S(data=invoices.get(k["customer"], []))))
    return subs, invoices


def test_active_with_paid_invoice_is_paying(fake_stripe):
    subs, invoices = fake_stripe
    subs["cus_1"] = [_sub("active")]
    invoices["cus_1"] = [_S(paid=True, amount_paid=600)]
    r = recon.reconcile_one({"email": "a@x.com", "subscription_status": "active",
                             "stripe_customer_id": "cus_1"})
    assert r["verdict"] == "PAYING"
    assert r["monthly_pence"] == 600
    assert r["mismatch"] is False


def test_trialing_is_not_revenue(fake_stripe):
    subs, _ = fake_stripe
    subs["cus_2"] = [_sub("trialing")]
    r = recon.reconcile_one({"email": "t@x.com", "subscription_status": "trialing",
                             "stripe_customer_id": "cus_2"})
    assert r["verdict"] == "TRIAL"


def test_annual_plan_prorated_to_monthly(fake_stripe):
    subs, _ = fake_stripe
    subs["cus_3"] = [_sub("active", amount=6000, interval="year")]
    r = recon.reconcile_one({"email": "y@x.com", "subscription_status": "active",
                             "stripe_customer_id": "cus_3"})
    assert r["verdict"] == "PAYING"
    assert r["monthly_pence"] == 500  # 6000 / 12


def test_db_active_but_stripe_cancelled_is_stale_mismatch(fake_stripe):
    """The hidden-churn case: panel says active, Stripe says cancelled."""
    subs, _ = fake_stripe
    subs["cus_4"] = [_sub("canceled")]
    r = recon.reconcile_one({"email": "old@x.com", "subscription_status": "active",
                             "stripe_customer_id": "cus_4"})
    assert r["verdict"] == "STALE"
    assert r["mismatch"] is True


def test_db_active_but_no_stripe_subscription_is_stale(fake_stripe):
    """Customer exists but has no subscriptions at all → stale."""
    fake_stripe  # customer id present, no subs registered → empty list
    r = recon.reconcile_one({"email": "ghost@x.com", "subscription_status": "active",
                             "stripe_customer_id": "cus_none"})
    assert r["verdict"] == "STALE"
    assert r["mismatch"] is True


def test_active_with_no_stripe_customer_is_orphan(fake_stripe):
    """The http-test@bankparse.com case: active in DB, never had Stripe."""
    r = recon.reconcile_one({"email": "http-test@bankparse.com",
                             "subscription_status": "active",
                             "stripe_customer_id": None})
    assert r["verdict"] == "NO_STRIPE"


def test_past_due_classified_as_grace(fake_stripe):
    subs, _ = fake_stripe
    subs["cus_5"] = [_sub("past_due")]
    r = recon.reconcile_one({"email": "pd@x.com", "subscription_status": "past_due",
                             "stripe_customer_id": "cus_5"})
    assert r["verdict"] == "PAST_DUE"


def test_best_sub_prefers_live_over_cancelled(fake_stripe):
    """A customer with an old cancelled sub AND a current active one must read
    as active — not be dragged down by the dead record."""
    subs, _ = fake_stripe
    subs["cus_6"] = [_sub("canceled", created=1), _sub("active", created=2)]
    r = recon.reconcile_one({"email": "multi@x.com", "subscription_status": "active",
                             "stripe_customer_id": "cus_6"})
    assert r["verdict"] == "PAYING"
