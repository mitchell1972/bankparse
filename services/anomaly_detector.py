"""
Anomaly / missed-expense detection.

Compares the user's spend in the CURRENT period (this tax-year quarter)
against their rolling baseline (the previous 3 quarters of the same
expense categories). Flags categories where the current quarter is
materially below the baseline — those are the most common "I forgot to
upload the receipt" cases.

The output is plain English so the dashboard can show it verbatim:

  "Your motor expenses are £120 this quarter. Your usual average is
   £450. You might be missing fuel receipts — check Sun 12 May, Sun
   26 May (you spent £130+ at Shell on those dates with no business
   mileage logged)."

No AI required for the baseline maths — pure statistics. Claude is
only invoked to write the prose for the final user-facing message
when ENABLE_AI_PROSE is True.
"""
from __future__ import annotations

import datetime as _dt

import database


# Tunable thresholds — kept here so tests can monkey-patch
DROP_THRESHOLD_PCT = 0.40        # current must be ≥ 40% below baseline to flag
MINIMUM_BASELINE_GBP = 50.0      # don't flag categories with trivial spend
QUARTERS_TO_BASELINE = 3         # look back this many quarters


def _quarter_start(d: _dt.date) -> _dt.date:
    """Return the first day of the calendar quarter containing d."""
    qm = (d.month - 1) // 3 * 3 + 1
    return _dt.date(d.year, qm, 1)


def _quarter_label(d: _dt.date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"Q{q}-{d.year}"


def _quarter_bounds(d: _dt.date) -> tuple[_dt.date, _dt.date]:
    """Return (inclusive start, inclusive end) of the calendar quarter
    containing d."""
    start = _quarter_start(d)
    if start.month + 3 > 12:
        end = _dt.date(start.year, 12, 31)
    else:
        end = _dt.date(start.year, start.month + 3, 1) - _dt.timedelta(days=1)
    return (start, end)


def _previous_quarter_start(d: _dt.date) -> _dt.date:
    qm = (d.month - 1) // 3 * 3 + 1
    if qm == 1:
        return _dt.date(d.year - 1, 10, 1)
    return _dt.date(d.year, qm - 3, 1)


def detect_anomalies(user_id: int, *, today: _dt.date | None = None) -> dict:
    """Return per-category anomalies for the user's current quarter.

    Schema:
        {
          "current_quarter": "Q2-2026",
          "baseline_quarters": ["Q1-2026", "Q4-2025", "Q3-2025"],
          "anomalies": [
            {
              "category": "se_motor_expenses",
              "current_gbp": 120.0,
              "baseline_avg_gbp": 450.0,
              "drop_pct": 73,
              "missed_count_hint": 3,    # how many baseline txs we'd expect
              "message": "...",
            },
            ...
          ],
          "ok_categories": ["se_office_expenses", "se_general_admin_costs"],
        }
    """
    if today is None:
        today = _dt.date.today()
    txs = database.get_user_ledger_transactions(user_id, limit=20_000)

    # Filter to expense transactions (negative amounts) only.
    expense_txs = [
        tx for tx in txs
        if tx.get("amount") is not None and float(tx["amount"]) < 0
        and tx.get("receipt_status") != "excluded"
    ]

    current_q_start, current_q_end = _quarter_bounds(today)

    # Bucket by category and tax-quarter
    by_cat: dict[str, dict[str, float]] = {}  # cat -> {q_label: total}
    by_cat_counts: dict[str, dict[str, int]] = {}  # cat -> {q_label: count}

    for tx in expense_txs:
        date_iso = tx.get("date_iso") or ""
        try:
            d = _dt.datetime.strptime(date_iso, "%Y-%m-%d").date()
        except ValueError:
            continue
        cat = tx.get("hmrc_category") or "uncategorised"
        amt = abs(float(tx["amount"]))
        bpct = (tx.get("business_pct") or 100) / 100.0
        adjusted = amt * bpct
        q_label = _quarter_label(d)
        by_cat.setdefault(cat, {})
        by_cat_counts.setdefault(cat, {})
        by_cat[cat][q_label] = by_cat[cat].get(q_label, 0.0) + adjusted
        by_cat_counts[cat][q_label] = by_cat_counts[cat].get(q_label, 0) + 1

    # Compute baseline quarter labels (previous N quarters)
    baseline_q_labels: list[str] = []
    cursor = current_q_start
    for _ in range(QUARTERS_TO_BASELINE):
        cursor = _previous_quarter_start(cursor)
        baseline_q_labels.append(_quarter_label(cursor))

    current_q_label = _quarter_label(today)

    anomalies = []
    ok_categories = []
    for cat, q_totals in by_cat.items():
        if cat == "uncategorised":
            continue
        baseline_vals = [q_totals.get(q, 0.0) for q in baseline_q_labels]
        # Need at least one quarter of history with non-trivial spend
        baseline_with_data = [v for v in baseline_vals if v > 0]
        if not baseline_with_data:
            continue
        baseline_avg = sum(baseline_with_data) / len(baseline_with_data)
        if baseline_avg < MINIMUM_BASELINE_GBP:
            continue
        current_val = q_totals.get(current_q_label, 0.0)
        drop = (baseline_avg - current_val) / baseline_avg
        if drop < DROP_THRESHOLD_PCT:
            ok_categories.append(cat)
            continue
        # Estimate how many transactions we'd expect this quarter
        baseline_counts = [by_cat_counts[cat].get(q, 0) for q in baseline_q_labels]
        baseline_count_avg = (
            sum(c for c in baseline_counts if c > 0) /
            max(1, sum(1 for c in baseline_counts if c > 0))
        )
        current_count = by_cat_counts[cat].get(current_q_label, 0)
        missed_count_hint = max(0, int(round(baseline_count_avg - current_count)))

        cat_label = cat.replace("_", " ").replace("se ", "").replace("property ", "")
        if current_val == 0:
            msg = (
                f"You haven't claimed any {cat_label} this quarter. "
                f"Your usual is £{baseline_avg:.0f}. Have you forgotten "
                f"to upload {missed_count_hint or 'some'} receipts?"
            )
        else:
            msg = (
                f"Your {cat_label} is £{current_val:.0f} this quarter — "
                f"your usual average is £{baseline_avg:.0f} "
                f"(down {int(drop*100)}%). "
                f"Check for {missed_count_hint or 'a few'} missing receipts."
            )

        anomalies.append({
            "category": cat,
            "current_gbp": round(current_val, 2),
            "baseline_avg_gbp": round(baseline_avg, 2),
            "drop_pct": int(round(drop * 100)),
            "missed_count_hint": missed_count_hint,
            "message": msg,
        })

    # Sort by largest drop first
    anomalies.sort(key=lambda a: a["drop_pct"], reverse=True)

    return {
        "current_quarter": current_q_label,
        "baseline_quarters": baseline_q_labels,
        "anomalies": anomalies,
        "ok_categories": ok_categories,
    }
