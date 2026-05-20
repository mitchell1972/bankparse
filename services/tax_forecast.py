"""
Live tax-due forecast.

Reads the user's structured ledger and produces a quarter-by-quarter
estimated tax bill — across both Self-Employment and Property income,
on the same dashboard. Hammock shows property only; Coconut shows SE
only; we're the first UK tool to do both side-by-side.

The numbers are pessimistic / panic-brake. Real tax due depends on the
user's full circumstances (other income, allowances, marriage
allowance, etc.) which only an end-of-year calculation can resolve.
This is the "if today were the deadline" view — not the final figure.

UK tax bands (2025-26 used for now; refresh annually):

  Personal allowance:     £12,570
  Basic rate band:        £37,700  @ 20%
  Higher rate band:       up to £125,140 @ 40%
  Additional rate:        £125,140+ @ 45%

  Class 2 NI:             Removed Apr 2024
  Class 4 NI:             Lower limit £12,570
                          6% between £12,570 - £50,270
                          2% above £50,270

The forecast model is deliberately conservative and documented inline.
Income → expenses → profit → tax. No reliefs assumed.
"""
from __future__ import annotations

import datetime as _dt

import database


# Frozen for 2025-26 — annual refresh required
PERSONAL_ALLOWANCE = 12_570.0
BASIC_RATE_TOP = 50_270.0      # PA + basic rate band 37,700
HIGHER_RATE_TOP = 125_140.0

BASIC_RATE = 0.20
HIGHER_RATE = 0.40
ADDITIONAL_RATE = 0.45

NI_LOWER = 12_570.0
NI_UPPER = 50_270.0
NI_MAIN_RATE = 0.06
NI_UPPER_RATE = 0.02


def _income_tax_due(taxable_profit: float) -> float:
    """Compute UK income tax due on a given annual taxable profit, after
    deducting the personal allowance."""
    after_pa = max(0.0, taxable_profit - PERSONAL_ALLOWANCE)
    if after_pa <= 0:
        return 0.0
    basic_band = min(after_pa, BASIC_RATE_TOP - PERSONAL_ALLOWANCE)
    higher_band = max(0.0, min(after_pa - basic_band, HIGHER_RATE_TOP - BASIC_RATE_TOP))
    add_band = max(0.0, after_pa - basic_band - higher_band)
    return (
        basic_band * BASIC_RATE
        + higher_band * HIGHER_RATE
        + add_band * ADDITIONAL_RATE
    )


def _class_4_ni_due(taxable_profit: float) -> float:
    """Self-employed Class 4 NI. Doesn't apply to property income."""
    if taxable_profit <= NI_LOWER:
        return 0.0
    main_band = min(taxable_profit, NI_UPPER) - NI_LOWER
    upper_band = max(0.0, taxable_profit - NI_UPPER)
    return main_band * NI_MAIN_RATE + upper_band * NI_UPPER_RATE


def _current_tax_year_start() -> _dt.date:
    """UK tax year runs 6 April to 5 April. Returns the 6 April date of
    the current tax year."""
    today = _dt.date.today()
    if today.month < 4 or (today.month == 4 and today.day < 6):
        return _dt.date(today.year - 1, 4, 6)
    return _dt.date(today.year, 4, 6)


_SE_INCOME_CATEGORIES = {"se_turnover", "se_other_income"}
_PROPERTY_INCOME_CATEGORIES = {"property_rent_income", "property_other_income"}
_INCOME_CATEGORIES = _SE_INCOME_CATEGORIES | _PROPERTY_INCOME_CATEGORIES


def _is_se_category(cat: str) -> bool:
    return bool(cat) and cat.startswith("se_")


def _is_property_category(cat: str) -> bool:
    return bool(cat) and cat.startswith("property_")


def forecast_tax_due(user_id: int) -> dict:
    """Compute the current-year-to-date forecast.

    Returns:
        {
          "tax_year": "2025-26",
          "as_of": "2026-08-04",
          "self_employment": {
            "income": 18400.00,
            "expenses": 4730.00,
            "profit": 13670.00,
            "income_tax_due": 220.00,
            "class_4_ni_due": 66.00,
          },
          "property": {
            "income": 12000.00,
            "expenses": 3200.00,
            "profit": 8800.00,
            "income_tax_due": 0.00,
          },
          "combined": {
            "profit": 22470.00,
            "income_tax_due": 1980.00,
            "class_4_ni_due": 66.00,
            "total_due": 2046.00,
          },
          "notes": [...],
        }
    """
    txs = database.get_user_ledger_transactions(user_id, limit=20000)
    ty_start = _current_tax_year_start()

    se = {"income": 0.0, "expenses": 0.0}
    prop = {"income": 0.0, "expenses": 0.0}
    # Uncategorised bucket — counted as PROVISIONAL SE income/expenses so
    # the user gets a useful "if-everything-were-business" forecast even
    # before the categoriser runs. Surfaced separately in the response so
    # the UI can show it as a caveat.
    uncat = {"income": 0.0, "expenses": 0.0}

    for tx in txs:
        if tx.get("receipt_status") == "excluded":
            continue
        date_iso = tx.get("date_iso")
        if not date_iso:
            continue
        try:
            d = _dt.datetime.strptime(date_iso, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < ty_start:
            continue
        cat = tx.get("hmrc_category") or ""
        amt = float(tx.get("amount") or 0)
        bpct = (tx.get("business_pct") or 100) / 100.0

        if cat in _SE_INCOME_CATEGORIES:
            se["income"] += abs(amt) * bpct
        elif cat in _PROPERTY_INCOME_CATEGORIES:
            prop["income"] += abs(amt) * bpct
        elif _is_se_category(cat) and amt < 0:
            # Capital items aren't an expense in the year they're bought —
            # they're a capital allowance. Skip from the expense total here.
            if tx.get("is_capital"):
                continue
            se["expenses"] += abs(amt) * bpct
        elif _is_property_category(cat) and amt < 0:
            if tx.get("is_capital"):
                continue
            prop["expenses"] += abs(amt) * bpct
        elif not cat:
            # Uncategorised — credit → provisional income, debit → provisional
            # expense. Counted into the SE bucket on the conservative
            # assumption that this is a self-employment account until the
            # categoriser reclassifies. The user sees a clear caveat note.
            if amt > 0:
                uncat["income"] += amt * bpct
                se["income"] += amt * bpct
            elif amt < 0:
                uncat["expenses"] += abs(amt) * bpct
                se["expenses"] += abs(amt) * bpct

    se_profit = max(0.0, se["income"] - se["expenses"])
    prop_profit = max(0.0, prop["income"] - prop["expenses"])
    combined_profit = se_profit + prop_profit

    income_tax = _income_tax_due(combined_profit)
    # Apportion the income tax pro-rata between SE / property so the user
    # sees how each stream contributes. Class 4 NI is SE-only.
    if combined_profit > 0:
        se_share = se_profit / combined_profit
    else:
        se_share = 0.0
    se_income_tax = round(income_tax * se_share, 2)
    prop_income_tax = round(income_tax - se_income_tax, 2)
    class_4_ni = _class_4_ni_due(se_profit)

    notes: list[str] = []
    if combined_profit < PERSONAL_ALLOWANCE:
        notes.append(
            f"Profit is below the £{PERSONAL_ALLOWANCE:,.0f} personal allowance — "
            "no income tax due yet."
        )
    if uncat["income"] > 0 or uncat["expenses"] > 0:
        notes.append(
            f"£{uncat['income']:,.0f} of credits and £{uncat['expenses']:,.0f} of "
            "debits are still uncategorised — they're counted provisionally as "
            "self-employment income/expenses. Run the categoriser for an accurate forecast."
        )
    elif any(tx.get("hmrc_category") is None for tx in txs):
        # Defensive — if uncat both 0 but there are categoriless txs (e.g.
        # all zero-amount), still show the caveat.
        notes.append(
            "Some transactions are not yet categorised — actual tax may differ."
        )

    return {
        "tax_year": f"{ty_start.year}-{str(ty_start.year + 1)[-2:]}",
        "as_of": _dt.date.today().isoformat(),
        "self_employment": {
            "income": round(se["income"], 2),
            "expenses": round(se["expenses"], 2),
            "profit": round(se_profit, 2),
            "income_tax_due": se_income_tax,
            "class_4_ni_due": round(class_4_ni, 2),
        },
        "property": {
            "income": round(prop["income"], 2),
            "expenses": round(prop["expenses"], 2),
            "profit": round(prop_profit, 2),
            "income_tax_due": prop_income_tax,
        },
        "combined": {
            "profit": round(combined_profit, 2),
            "income_tax_due": round(income_tax, 2),
            "class_4_ni_due": round(class_4_ni, 2),
            "total_due": round(income_tax + class_4_ni, 2),
        },
        "provisional": {
            # Portion of the forecast that depends on uncategorised txs —
            # the user can subtract this if their account is actually
            # personal, or commit to it once the categoriser runs.
            "uncategorised_income": round(uncat["income"], 2),
            "uncategorised_expenses": round(uncat["expenses"], 2),
        },
        "notes": notes,
        "disclaimer": (
            "This is a pessimistic year-to-date estimate based on your current "
            "ledger. Final tax depends on allowances, reliefs and other income "
            "and is only known at end-of-year calculation."
        ),
    }


# ---------------------------------------------------------------------------
# Capital allowance prompt
# ---------------------------------------------------------------------------

CAPITAL_THRESHOLD_GBP = 200.0


def should_prompt_capital(tx: dict) -> bool:
    """Decide whether the upload UI should ask 'Is this a capital item?'.

    Conservative: only prompt for negative amounts (purchases) above the
    £200 threshold AND in an SE expense category. Property has different
    rules. The user can always answer 'no, expense' — the prompt is a
    nudge, not a hard categorisation."""
    amt = float(tx.get("amount") or 0)
    if amt >= 0:  # income / refund — never a capital item
        return False
    if abs(amt) < CAPITAL_THRESHOLD_GBP:
        return False
    if tx.get("is_capital"):  # already decided
        return False
    cat = tx.get("hmrc_category") or ""
    if not _is_se_category(cat):
        return False
    return True
