"""
Bank transaction → HMRC MTD ITSA category mapping.

This is the bridge between BankParse's existing parser output (stored in
`user_extracted_data.rows_json`) and the HMRC quarterly submission payload.

Two flows, two taxonomies:

  - **Self-employment (sole trader)**: turnover + expense categories from
    `Self Employment Business (MTD)` API. Used by all sole traders, freelancers,
    consultants, contractors.

  - **UK property (landlord)**: rent income + property-specific expense
    categories from `Property Business (MTD)` API. Different schema, different
    allowable categories (e.g. mortgage interest restriction, residential
    financial costs carried forward).

The same bank statement can produce rows for BOTH streams when a user is a
sole trader who also owns rental property. Each user's `business_type` per
transaction is set in the UI; this mapper only handles category-within-stream.

Current implementation: **regex/keyword starter**. Each rule has a
`confidence` 0..1 so the UI can ask for user confirmation when the model is
unsure. The next iteration (Tier 1 of CLAUDE.md priority) will replace this
with an AI-powered classifier (Claude Haiku) that learns per-user merchant →
category mappings.

Design notes:
  - All amounts are positive in the returned category sums; `is_income`
    distinguishes income vs expense.
  - Unknown / low-confidence transactions fall into `other` (HMRC category)
    and are flagged for user review.
  - Rules are ordered: first match wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


# ---------------------------------------------------------------------------
# Self-Employment Business (MTD) — categories per HMRC v5.0 API
# ---------------------------------------------------------------------------

SE_INCOME = "turnover"
SE_OTHER_INCOME = "otherIncome"
SE_EXPENSE_COST_OF_GOODS = "costOfGoodsBought"
SE_EXPENSE_CIS = "cisPaymentsToSubcontractors"
SE_EXPENSE_STAFF = "staffCosts"
SE_EXPENSE_TRAVEL = "travelCosts"
SE_EXPENSE_PREMISES = "premisesRunningCosts"
SE_EXPENSE_REPAIRS = "maintenanceCosts"
SE_EXPENSE_ADMIN = "adminCosts"
SE_EXPENSE_ADVERTISING = "advertisingCosts"
SE_EXPENSE_ENTERTAINMENT = "businessEntertainmentCosts"
SE_EXPENSE_INTEREST = "interest"
SE_EXPENSE_FINANCIAL = "financialCharges"
SE_EXPENSE_BAD_DEBT = "badDebt"
SE_EXPENSE_PROFESSIONAL = "professionalFees"
SE_EXPENSE_DEPRECIATION = "depreciation"
SE_EXPENSE_OTHER = "other"


# ---------------------------------------------------------------------------
# Property Business (MTD) — UK property categories per HMRC v6.0 API
# ---------------------------------------------------------------------------

PROP_INCOME_RENT = "rentIncome"
PROP_INCOME_PREMIUMS = "premiumsOfLeaseGrant"
PROP_INCOME_OTHER = "otherIncome"
PROP_EXPENSE_PREMISES = "premisesRunningCosts"
PROP_EXPENSE_REPAIRS = "repairsAndMaintenance"
PROP_EXPENSE_FINANCIAL = "financialCosts"
PROP_EXPENSE_PROFESSIONAL = "professionalFees"
PROP_EXPENSE_SERVICES = "costOfServices"
PROP_EXPENSE_TRAVEL = "travelCosts"
PROP_EXPENSE_OTHER = "other"
# Residential mortgage interest is a special restricted-relief category;
# we keep it separate from financialCosts:
PROP_EXPENSE_RESIDENTIAL_FINANCIAL = "residentialFinancialCost"


@dataclass(frozen=True)
class Classification:
    category: str
    confidence: float          # 0..1
    is_income: bool
    reasoning: str             # plain-English explanation for the UI


# ---------------------------------------------------------------------------
# Self-employment rules
# Ordering matters — earlier rules trump later. Keep narrow merchants ahead
# of broad keyword catches.
# ---------------------------------------------------------------------------

_SE_RULES: list[tuple[re.Pattern[str], str, bool, float, str]] = [
    # --- income ---
    (re.compile(r"\b(stripe|paypal|gocardless|sumup|square|takepayments)\b", re.I),
     SE_INCOME, True, 0.85, "Payment processor deposit — typically customer takings"),
    (re.compile(r"\bsalary|payroll|wages received|fee for service\b", re.I),
     SE_INCOME, True, 0.75, "Looks like an incoming fee or salary deposit"),

    # --- subcontractor / CIS ---
    (re.compile(r"\bcis\b|construction industry scheme", re.I),
     SE_EXPENSE_CIS, False, 0.90, "Mentions CIS — Construction Industry Scheme payment"),

    # --- staff ---
    (re.compile(r"\b(payroll|wages|salary|hmrc paye|paye|pension contribution)\b", re.I),
     SE_EXPENSE_STAFF, False, 0.85, "Looks like a staff cost (wages / PAYE / pension)"),

    # --- travel ---
    (re.compile(r"\b(uber|lyft|bolt|trainline|sncf|tfl|transport for london|"
                r"national rail|easyjet|ryanair|british airways|"
                r"shell|bp|esso|texaco|fuel|petrol|diesel)\b", re.I),
     SE_EXPENSE_TRAVEL, False, 0.90, "Travel / fuel merchant"),

    # --- premises (rent, utilities, council tax) ---
    (re.compile(r"\b(rent|business rates|council tax|electric ireland|"
                r"british gas|edf|eon|octopus energy|sse|scottish power|"
                r"thames water|severn trent|anglian water|virgin media business|"
                r"bt business|sky business|openreach)\b", re.I),
     SE_EXPENSE_PREMISES, False, 0.85, "Premises running cost (rent/utilities/internet)"),

    # --- repairs ---
    (re.compile(r"\b(travis perkins|wickes|screwfix|b&q|toolstation|"
                r"plumber|electrician|repair|maintenance|boiler service)\b", re.I),
     SE_EXPENSE_REPAIRS, False, 0.85, "Repairs / maintenance supplier"),

    # --- admin (stationery, software, subscriptions) ---
    (re.compile(r"\b(amazon|amzn|royal mail|dpd|hermes|evri|"
                r"google workspace|microsoft|office365|m365|"
                r"zoom|notion|slack|github|aws|azure|cloudflare|"
                r"xero|quickbooks|freeagent|sage|stripe billing)\b", re.I),
     SE_EXPENSE_ADMIN, False, 0.80, "Office / admin / software subscription"),

    # --- advertising ---
    (re.compile(r"\b(facebook ads|meta ads|google ads|adwords|"
                r"linkedin ads|tiktok ads|x ads|twitter ads|"
                r"advertising|marketing)\b", re.I),
     SE_EXPENSE_ADVERTISING, False, 0.90, "Advertising / marketing spend"),

    # --- entertainment ---
    (re.compile(r"\b(restaurant|pub|hotel|deliveroo|uber eats|just eat)\b", re.I),
     SE_EXPENSE_ENTERTAINMENT, False, 0.60,
     "Possibly business entertainment — note HMRC restricts this category"),

    # --- interest / financial charges ---
    (re.compile(r"\b(interest charge|loan interest|mortgage interest)\b", re.I),
     SE_EXPENSE_INTEREST, False, 0.85, "Interest payment on borrowing"),
    (re.compile(r"\b(bank charge|overdraft fee|transaction fee|foreign exchange)\b", re.I),
     SE_EXPENSE_FINANCIAL, False, 0.85, "Bank / financial charge"),

    # --- professional fees ---
    (re.compile(r"\b(solicitor|lawyer|accountant|bookkeeper|"
                r"insurance|aviva|axa|hiscox|simply business|"
                r"companies house|ico|insolvency)\b", re.I),
     SE_EXPENSE_PROFESSIONAL, False, 0.85, "Professional / legal / insurance fees"),

    # --- cost of goods ---
    (re.compile(r"\b(makro|booker|alibaba|wholesale|cash & carry)\b", re.I),
     SE_EXPENSE_COST_OF_GOODS, False, 0.80, "Wholesale goods purchase"),
]


def classify_self_employment(description: str, amount: float) -> Classification:
    """Map one bank transaction to a self-employment HMRC category.

    `amount` is the signed bank amount: positive = credit (money in),
    negative = debit (money out). We use the sign to bias toward income vs
    expense classification when the description is ambiguous.
    """
    desc = (description or "").strip()
    is_credit = amount > 0
    for pat, cat, is_income_rule, conf, why in _SE_RULES:
        if pat.search(desc):
            # If rule says income but bank says debit (or vice versa), trust
            # the bank direction and downgrade confidence.
            if is_income_rule != is_credit:
                return Classification(
                    SE_INCOME if is_credit else SE_EXPENSE_OTHER,
                    confidence=0.35,
                    is_income=is_credit,
                    reasoning=f"Direction mismatch — {why}, but transaction is a {'credit' if is_credit else 'debit'}",
                )
            return Classification(cat, conf, is_income_rule, why)

    # Fallback: route by sign, low confidence.
    if is_credit:
        return Classification(SE_OTHER_INCOME, 0.30, True,
                              "No category match — falling back to 'other income' for review")
    return Classification(SE_EXPENSE_OTHER, 0.30, False,
                          "No category match — falling back to 'other expense' for review")


# ---------------------------------------------------------------------------
# UK property rules
# ---------------------------------------------------------------------------

_PROPERTY_RULES: list[tuple[re.Pattern[str], str, bool, float, str]] = [
    # --- rent income ---
    (re.compile(r"\b(rent|tenant|rental|airbnb|booking\.com|sykes cottages|"
                r"vrbo|holidu)\b", re.I),
     PROP_INCOME_RENT, True, 0.90, "Looks like rent or short-let income"),

    # --- mortgage interest (residential = restricted relief) ---
    (re.compile(r"\b(mortgage|buy to let|btl interest)\b", re.I),
     PROP_EXPENSE_RESIDENTIAL_FINANCIAL, False, 0.85,
     "Residential mortgage interest — restricted to basic-rate tax relief"),

    # --- repairs / maintenance ---
    (re.compile(r"\b(travis perkins|wickes|screwfix|b&q|toolstation|"
                r"plumber|electrician|repair|boiler|gas safe|epc|"
                r"painter|decorator|carpet|locksmith)\b", re.I),
     PROP_EXPENSE_REPAIRS, False, 0.90, "Property repairs / maintenance"),

    # --- premises running costs ---
    (re.compile(r"\b(council tax|tv licence|british gas|edf|eon|octopus|"
                r"thames water|severn trent|anglian water|sse|"
                r"electric ireland|tv licence)\b", re.I),
     PROP_EXPENSE_PREMISES, False, 0.85, "Premises running cost (council tax / utility)"),

    # --- services (cleaning, gardening, letting agent) ---
    (re.compile(r"\b(cleaner|cleaning|gardener|gardening|letting agent|"
                r"property management|rightmove|zoopla|openrent)\b", re.I),
     PROP_EXPENSE_SERVICES, False, 0.85, "Cost of services (cleaning/agent/listing)"),

    # --- insurance / professional ---
    (re.compile(r"\b(landlord insurance|building insurance|contents insurance|"
                r"solicitor|conveyancing|tenancy deposit|dps|tds|mydeposits)\b", re.I),
     PROP_EXPENSE_PROFESSIONAL, False, 0.85, "Insurance or professional fees"),

    # --- financial (non-residential mortgage interest, fees) ---
    (re.compile(r"\b(commercial mortgage interest|loan interest|"
                r"foreign exchange|bank charge|arrangement fee)\b", re.I),
     PROP_EXPENSE_FINANCIAL, False, 0.80, "Commercial finance or fees"),
]


def classify_property(description: str, amount: float) -> Classification:
    """Map one bank transaction to a UK property HMRC category."""
    desc = (description or "").strip()
    is_credit = amount > 0
    for pat, cat, is_income_rule, conf, why in _PROPERTY_RULES:
        if pat.search(desc):
            if is_income_rule != is_credit:
                return Classification(
                    PROP_INCOME_OTHER if is_credit else PROP_EXPENSE_OTHER,
                    confidence=0.35,
                    is_income=is_credit,
                    reasoning=f"Direction mismatch — {why}, but transaction is a {'credit' if is_credit else 'debit'}",
                )
            return Classification(cat, conf, is_income_rule, why)

    if is_credit:
        return Classification(PROP_INCOME_OTHER, 0.30, True,
                              "No category match — falling back to 'other income' for review")
    return Classification(PROP_EXPENSE_OTHER, 0.30, False,
                          "No category match — falling back to 'other expense' for review")


# ---------------------------------------------------------------------------
# Aggregation — bank-transaction rows → HMRC quarterly submission payload.
# ---------------------------------------------------------------------------

def aggregate_self_employment(rows: Iterable[dict]) -> dict:
    """Sum bank-transaction rows into the HMRC SE quarterly payload shape.

    Each input row must contain at minimum:
        - "description" (str)
        - "amount" (float, signed: + credit, - debit)

    Returns a dict with `income.turnover`, `income.other`, and
    `expenses.<category>` keys ready to be assembled into the
    Self-Employment Business (MTD) period-summary POST body.

    Low-confidence rows (< 0.5) are bucketed into 'other' under each side.
    """
    income: dict[str, float] = {}
    expenses: dict[str, float] = {}
    flagged: list[dict] = []
    for r in rows:
        c = classify_self_employment(r.get("description", ""), float(r.get("amount") or 0))
        bucket = income if c.is_income else expenses
        key = c.category if c.confidence >= 0.5 else (
            SE_OTHER_INCOME if c.is_income else SE_EXPENSE_OTHER
        )
        bucket[key] = round(bucket.get(key, 0.0) + abs(float(r.get("amount") or 0)), 2)
        if c.confidence < 0.5:
            flagged.append({"row": r, "classification": c.__dict__})
    return {"income": income, "expenses": expenses, "flagged_for_review": flagged}


def aggregate_property(rows: Iterable[dict]) -> dict:
    """Same shape as `aggregate_self_employment` but for UK property."""
    income: dict[str, float] = {}
    expenses: dict[str, float] = {}
    flagged: list[dict] = []
    for r in rows:
        c = classify_property(r.get("description", ""), float(r.get("amount") or 0))
        bucket = income if c.is_income else expenses
        key = c.category if c.confidence >= 0.5 else (
            PROP_INCOME_OTHER if c.is_income else PROP_EXPENSE_OTHER
        )
        bucket[key] = round(bucket.get(key, 0.0) + abs(float(r.get("amount") or 0)), 2)
        if c.confidence < 0.5:
            flagged.append({"row": r, "classification": c.__dict__})
    return {"income": income, "expenses": expenses, "flagged_for_review": flagged}
