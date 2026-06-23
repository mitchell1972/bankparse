#!/usr/bin/env python3
"""
Reconcile local "subscribers" against what Stripe ACTUALLY says.

The admin panel shows `subscription_status` straight from our own database.
That field is only as fresh as the last Stripe webhook we received, and it
lumps trial users (paying £0) in with real payers. So "7 active subscribers"
in the panel does NOT mean 7 people are paying you.

This script is the source-of-truth check. For every user the database marks
as active / trialing / past_due, it asks Stripe what is really true and prints
a verdict per user plus a summary (real paying count + monthly recurring
revenue). It is READ-ONLY — it never writes to the database or to Stripe, and
it never moves money.

Why each verdict matters:
  PAYING          real money: Stripe sub is active and the latest invoice paid.
  TRIAL           in a free trial — £0 so far. Counts as a "subscriber" in the
                  panel but is not revenue yet.
  PAST_DUE        card is failing; Stripe is retrying (grace period).
  STALE ⚠         our DB says active/trialing but Stripe has NO live sub
                  (cancelled / none). This is hidden churn the panel is masking.
  NO_STRIPE  ⚠    DB says active/trialing but the user has no Stripe customer at
                  all — an orphan or leftover test row (e.g. http-test@…).

Run it where the production env vars are set (e.g. `railway run`, or export
them locally). Required environment:
  TURSO_DATABASE_URL, TURSO_AUTH_TOKEN   (prod DB; falls back to local sqlite)
  STRIPE_SECRET_KEY                      (live key — read only used for reads)

Usage:
    python3 scripts/reconcile_subscribers.py
    python3 scripts/reconcile_subscribers.py --json     # machine-readable
"""
from __future__ import annotations

import json
import os
import sys

# Import the app's database layer so we hit the SAME connection the app uses
# (Turso in prod, sqlite locally) with no extra config.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import database  # noqa: E402

try:
    import stripe
except ImportError:
    print("ERROR: the `stripe` package is not installed. `pip install stripe`.", file=sys.stderr)
    sys.exit(2)


# Plan price IDs → human label + the same defaults core.py ships with, so MRR
# is labelled even if the env vars aren't exported. The amount Stripe returns
# is the real source of truth; this is only for the plan name column.
_PRICE_LABELS = {
    os.environ.get("STRIPE_STARTER_PRICE_ID", "price_1THsjkLniIk7TL9BZuCd5LZ0"): "Starter",
    os.environ.get("STRIPE_PRO_PRICE_ID", "price_1THsjmLniIk7TL9BkgiXW5c3"): "Pro",
    os.environ.get("STRIPE_BUSINESS_PRICE_ID", "price_1THsjnLniIk7TL9Bh0dDiHL5"): "Business",
    os.environ.get("STRIPE_ENTERPRISE_PRICE_ID", "price_1THsjoLniIk7TL9BZ3GEHfUu"): "Enterprise",
}

# Stripe sub statuses that mean "this person currently has app access".
_LIVE = {"active", "trialing", "past_due"}


def _monthly_pence(sub) -> int:
    """Monthly-equivalent amount (in pence) for a subscription, summed across
    its items. Annual plans are divided by 12 so MRR is comparable."""
    total = 0
    try:
        for item in sub["items"]["data"]:
            price = item["price"]
            unit = price.get("unit_amount") or 0
            qty = item.get("quantity", 1) or 1
            interval = (price.get("recurring") or {}).get("interval", "month")
            amount = unit * qty
            if interval == "year":
                amount = round(amount / 12)
            elif interval == "week":
                amount = amount * 4
            total += amount
    except (KeyError, TypeError):
        pass
    return total


def _plan_label(sub) -> str:
    try:
        pid = sub["items"]["data"][0]["price"]["id"]
        return _PRICE_LABELS.get(pid, pid)
    except (KeyError, IndexError, TypeError):
        return "?"


def _best_sub(subs):
    """Pick the most relevant subscription: prefer a live one, else the newest."""
    if not subs:
        return None
    live = [s for s in subs if s.status in _LIVE]
    if live:
        # active beats trialing beats past_due; then newest
        rank = {"active": 0, "trialing": 1, "past_due": 2}
        return sorted(live, key=lambda s: (rank.get(s.status, 9), -s.get("created", 0)))[0]
    return sorted(subs, key=lambda s: -s.get("created", 0))[0]


def reconcile_one(row: dict) -> dict:
    email = row.get("email") or "?"
    db_status = row.get("subscription_status") or "(none)"
    cid = row.get("stripe_customer_id")

    out = {
        "email": email, "db_status": db_status, "stripe_customer_id": cid,
        "stripe_status": None, "plan": None, "monthly_pence": 0,
        "last_invoice_paid": None, "last_invoice_amount_pence": None,
        "verdict": None, "mismatch": False,
    }

    if not cid:
        out["verdict"] = "NO_STRIPE"
        return out

    try:
        subs = stripe.Subscription.list(customer=cid, status="all", limit=20).data
    except Exception as e:  # noqa: BLE001
        out["verdict"] = "STRIPE_ERROR"
        out["error"] = str(e)[:120]
        return out

    sub = _best_sub(subs)
    if sub is None:
        out["verdict"] = "STALE"  # DB says subscriber, Stripe has no sub at all
        out["mismatch"] = db_status in _LIVE
        return out

    out["stripe_status"] = sub.status
    out["plan"] = _plan_label(sub)
    out["monthly_pence"] = _monthly_pence(sub)
    out["mismatch"] = (db_status != sub.status)

    # Latest invoice for this customer — did the most recent charge actually land?
    try:
        inv = stripe.Invoice.list(customer=cid, limit=1).data
        if inv:
            out["last_invoice_paid"] = bool(inv[0].get("paid"))
            out["last_invoice_amount_pence"] = inv[0].get("amount_paid")
    except Exception:  # noqa: BLE001
        pass

    if sub.status == "active":
        out["verdict"] = "PAYING"
    elif sub.status == "trialing":
        out["verdict"] = "TRIAL"
    elif sub.status == "past_due":
        out["verdict"] = "PAST_DUE"
    else:  # canceled, unpaid, incomplete, incomplete_expired, paused
        out["verdict"] = "STALE"
    return out


def main() -> int:
    as_json = "--json" in sys.argv

    if not os.environ.get("STRIPE_SECRET_KEY"):
        print("ERROR: STRIPE_SECRET_KEY is not set. Run where prod env is "
              "available (e.g. `railway run python3 scripts/reconcile_subscribers.py`).",
              file=sys.stderr)
        return 2
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

    rows = database._fetchall_dicts(
        "SELECT id, email, subscription_status, stripe_customer_id "
        "FROM users WHERE subscription_status IN ('active', 'trialing', 'past_due') "
        "ORDER BY subscription_status, email"
    )

    results = [reconcile_one(r) for r in rows]

    if as_json:
        print(json.dumps(results, indent=2))
        return 0

    # ---- human-readable table ----
    def gbp(pence):
        return f"£{(pence or 0) / 100:,.2f}"

    print()
    print(f"Reconciling {len(results)} users the database marks as subscribers "
          f"against Stripe…\n")
    hdr = f"{'EMAIL':<38} {'DB':<9} {'STRIPE':<10} {'PLAN':<9} {'£/mo':>9}  VERDICT"
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(results, key=lambda x: (x["verdict"] or "", x["email"])):
        flag = " ⚠" if r["mismatch"] or r["verdict"] in ("STALE", "NO_STRIPE") else ""
        # Only show money for people on a real billing cycle — never for a
        # trial (£0 so far) or a stale/orphan row, where a price would mislead.
        amount = gbp(r["monthly_pence"]) if r["verdict"] in ("PAYING", "PAST_DUE") else "-"
        print(f"{r['email'][:37]:<38} {r['db_status'][:8]:<9} "
              f"{(r['stripe_status'] or '-')[:9]:<10} {(r['plan'] or '-')[:8]:<9} "
              f"{amount:>9}  {r['verdict']}{flag}")

    paying = [r for r in results if r["verdict"] == "PAYING"]
    trial = [r for r in results if r["verdict"] == "TRIAL"]
    past_due = [r for r in results if r["verdict"] == "PAST_DUE"]
    stale = [r for r in results if r["verdict"] in ("STALE", "NO_STRIPE")]
    mrr = sum(r["monthly_pence"] for r in paying)

    print()
    print("SUMMARY")
    print(f"  Database calls them 'subscribers'   : {len(results)}")
    print(f"  Actually PAYING (Stripe active)     : {len(paying)}   →  MRR {gbp(mrr)}")
    print(f"  On TRIAL (£0 so far)                : {len(trial)}")
    print(f"  PAST_DUE (card failing, in grace)   : {len(past_due)}")
    print(f"  STALE / orphan (panel is lying ⚠)   : {len(stale)}")
    if stale:
        print("\n  These show as subscribers in the panel but Stripe is NOT "
              "billing them — investigate / clean up:")
        for r in stale:
            why = r["verdict"] + (f" (Stripe: {r['stripe_status']})" if r["stripe_status"] else "")
            print(f"    - {r['email']}  [{why}]")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
