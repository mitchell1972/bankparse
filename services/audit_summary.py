"""
HMRC audit-readiness summary.

Aggregates the user's structured ledger (ledger_transactions + ledger_links
+ ledger_receipts) by HMRC category and computes the per-category metrics
that competitors don't have:

  - total gross spend
  - VAT reclaimable (from matched receipts only)
  - count + % of transactions backed by an attached receipt
  - count of excluded transactions (personal / cash / dd / sub)
  - count of capital-flagged transactions (separated from revenue)

The overall ``audit_ready_pct`` for the user is the spend-weighted
average of per-category percentages — heavy categories count more.

This is the feature the homepage screenshot is built around.
"""
from __future__ import annotations

from typing import Iterable

import database


_INCOME_CATEGORIES = {
    "se_turnover",
    "se_other_income",
    "property_rent_income",
    "property_other_income",
}


def _is_income(category: str | None) -> bool:
    if not category:
        return False
    return category in _INCOME_CATEGORIES


def _bucket_key(tx: dict) -> str:
    """The label we group on. Falls back to 'uncategorised' for transactions
    that haven't been categorised yet — the UI shows them in their own
    bucket so the user can review."""
    return tx.get("hmrc_category") or "uncategorised"


def summarise_audit_readiness(
    user_id: int,
    *,
    include_excluded: bool = False,
) -> dict:
    """Return the HMRC audit-readiness summary for the user.

    Schema:
        {
          "categories": [
            {
              "category": "office_expenses",
              "is_income": False,
              "transaction_count": 12,
              "matched_count": 9,            # receipt-backed
              "audit_ready_pct": 75,
              "total_gross_gbp": 482.13,
              "total_vat_gbp": 80.14,
              "excluded_count": 0,
              "capital_count": 1,
              "needs_attention": False,      # True if any tx is 'uncategorised'
                                              # or any expense category has 0% receipts
            },
            ...
          ],
          "totals": {
            "income": 18400.00,
            "expenses": 4730.50,
            "expenses_matched": 3984.20,
            "vat_total": 646.30,
            "audit_ready_pct": 84,           # spend-weighted across expense categories
            "transactions_total": 124,
            "transactions_matched": 87,
            "transactions_missing": 35,
            "transactions_excluded": 2,
          }
        }
    """
    txs = database.get_user_ledger_transactions(user_id, limit=10000)

    buckets: dict[str, dict] = {}
    for tx in txs:
        # Excluded transactions don't count in HMRC totals unless asked for.
        if tx.get("receipt_status") == "excluded" and not include_excluded:
            continue
        key = _bucket_key(tx)
        b = buckets.setdefault(key, {
            "category": key,
            "is_income": _is_income(key),
            "transaction_count": 0,
            "matched_count": 0,
            "total_gross_gbp": 0.0,
            "total_vat_gbp": 0.0,
            "excluded_count": 0,
            "capital_count": 0,
        })
        b["transaction_count"] += 1
        amt = float(tx.get("amount") or 0)
        # business_pct lets the user say "this is 60% business" — only the
        # business portion counts toward HMRC.
        bpct = (tx.get("business_pct") or 100) / 100.0
        gross = abs(amt) * bpct
        b["total_gross_gbp"] += gross
        if tx.get("receipt_status") == "matched":
            b["matched_count"] += 1
            vat = float(tx.get("vat_amount") or 0)
            b["total_vat_gbp"] += vat * bpct
        if tx.get("receipt_status") == "excluded":
            b["excluded_count"] += 1
        if tx.get("is_capital"):
            b["capital_count"] += 1

    categories = []
    for key, b in buckets.items():
        ready = 0
        if b["transaction_count"]:
            ready = int(round(100 * b["matched_count"] / b["transaction_count"]))
        b["audit_ready_pct"] = ready
        b["needs_attention"] = (
            key == "uncategorised"
            or (not b["is_income"] and ready == 0 and b["transaction_count"] > 0)
        )
        # Round monetary values to pence so the JSON is sane.
        b["total_gross_gbp"] = round(b["total_gross_gbp"], 2)
        b["total_vat_gbp"] = round(b["total_vat_gbp"], 2)
        categories.append(b)

    # Sort: needs_attention first, then by gross spend descending.
    categories.sort(key=lambda c: (not c["needs_attention"], -c["total_gross_gbp"]))

    # Totals across all categories.
    income_total = sum(
        c["total_gross_gbp"] for c in categories if c["is_income"]
    )
    expense_categories = [c for c in categories if not c["is_income"]]
    expense_total = sum(c["total_gross_gbp"] for c in expense_categories)
    expense_matched = sum(
        c["total_gross_gbp"] * (c["audit_ready_pct"] / 100.0)
        for c in expense_categories
    )
    vat_total = sum(c["total_vat_gbp"] for c in expense_categories)

    overall_ready_pct = 0
    if expense_total > 0:
        overall_ready_pct = int(round(100 * expense_matched / expense_total))

    txs_total = sum(c["transaction_count"] for c in categories)
    txs_matched = sum(c["matched_count"] for c in categories)
    txs_excluded = sum(c["excluded_count"] for c in categories)

    return {
        "categories": categories,
        "totals": {
            "income": round(income_total, 2),
            "expenses": round(expense_total, 2),
            "expenses_matched": round(expense_matched, 2),
            "vat_total": round(vat_total, 2),
            "audit_ready_pct": overall_ready_pct,
            "transactions_total": txs_total,
            "transactions_matched": txs_matched,
            "transactions_missing": txs_total - txs_matched - txs_excluded,
            "transactions_excluded": txs_excluded,
        },
    }
