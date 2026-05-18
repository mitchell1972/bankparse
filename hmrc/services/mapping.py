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


# Sentinel category — these transactions are NOT a tax expense or income at
# all. They're typically the user moving money between their own accounts.
# The UI hides these from category totals but still shows them on the table.
EXCLUDE_OWNER_TRANSFER = "_owner_transfer"


def _is_likely_owner_transfer(description: str, user_full_name: str | None) -> bool:
    """Heuristic: is this a transfer the user made to/from themselves?

    Triggers if the description contains the user's first or last name, OR
    obvious self-transfer keywords. Helps avoid mis-classifying personal
    cash movements as taxable income/expenses.
    """
    desc = (description or "").lower()
    if any(k in desc for k in (" own transfer", " to savings", " from savings", " transfer to ", " transfer from ")):
        return True
    if not user_full_name:
        return False
    parts = [p.strip().lower() for p in re.split(r"[\s,]+", user_full_name) if p.strip()]
    parts = [p for p in parts if len(p) >= 3]  # skip initials
    if not parts:
        return False
    # Need at least two name parts to match — single common first name alone
    # is too noisy (e.g. "James" shouldn't trip).
    hits = sum(1 for p in parts if p in desc)
    return hits >= 2


# ---------------------------------------------------------------------------
# Self-employment rules
# Ordering matters — earlier rules trump later. Keep narrow merchants ahead
# of broad keyword catches.
# ---------------------------------------------------------------------------

_SE_RULES: list[tuple[re.Pattern[str], str, bool, float, str]] = [
    # ----- income -----
    (re.compile(r"\b(stripe|paypal|gocardless|sumup|square|takepayments|zettle|tide payments)\b", re.I),
     SE_INCOME, True, 0.85, "Payment processor deposit — typically customer takings"),
    (re.compile(r"\b(invoice|inv\d|sales receipt|fee for service|consulting fee)\b", re.I),
     SE_INCOME, True, 0.80, "Looks like an invoice / fee payment"),

    # ----- subcontractor / CIS -----
    (re.compile(r"\bcis\b|construction industry scheme", re.I),
     SE_EXPENSE_CIS, False, 0.90, "Mentions CIS — Construction Industry Scheme payment"),

    # ----- staff -----
    (re.compile(r"\b(payroll|wages|salary paid|hmrc paye|paye payment|nest pension|"
                r"the people's pension|smart pension|aviva pension|standard life)\b", re.I),
     SE_EXPENSE_STAFF, False, 0.85, "Looks like a staff cost (wages / PAYE / pension)"),

    # ----- travel: parking, fuel, public transport, taxis, flights -----
    (re.compile(r"\b(ncp|q-?park|euro car park|apcoa|justpark|"
                r"mipermit|ringgo|paybyphone|parkmobile|"
                r"tfl|transport for london|trainline|"
                r"national rail|cross country|lner|gwr|south western|"
                r"chiltern|northern rail|tpe|"
                r"uber|lyft|bolt|via|free now|gett|addison lee|"
                r"easyjet|ryanair|british airways|jet2|"
                r"shell|bp\b|esso|texaco|sainsbury'?s petrol|tesco fuel|asda fuel|"
                r"morrisons fuel|costco fuel|gulf|jet petrol|fuel|petrol|diesel|"
                r"dvla|congestion charge|dartford crossing|"
                r"national express|megabus|stagecoach|first bus)\b", re.I),
     SE_EXPENSE_TRAVEL, False, 0.90, "Travel / fuel / parking merchant"),

    # ----- premises (rent, utilities, council tax) -----
    (re.compile(r"\b(rent payment|business rates|council tax|"
                r"british gas|edf|eon|octopus energy|sse|ovo|scottish power|"
                r"bulb|so energy|utilita|shell energy|good energy|"
                r"thames water|severn trent|anglian water|yorkshire water|"
                r"southern water|south west water|northumbrian water|"
                r"affinity water|wessex water|"
                r"virgin media|bt business|bt internet|sky business|openreach|"
                r"plusnet|talktalk business|vodafone business|three business|"
                r"ee business)\b", re.I),
     SE_EXPENSE_PREMISES, False, 0.85, "Premises running cost (rent/utilities/internet)"),

    # ----- repairs -----
    (re.compile(r"\b(travis perkins|wickes|screwfix|b&q|toolstation|"
                r"jewson|howdens|builders merchants?|"
                r"plumber|electrician|repair|maintenance|boiler service|"
                r"locksmith|glazier|gas safe)\b", re.I),
     SE_EXPENSE_REPAIRS, False, 0.85, "Repairs / maintenance supplier"),

    # ----- admin (stationery, software, subscriptions, postage, shopping) -----
    (re.compile(r"\b(amazon|amzn|royal mail|dpd|hermes|evri|yodel|fedex|ups|dhl|"
                r"google workspace|microsoft|office365|m365|outlook|"
                r"zoom|notion|slack|github|aws|amazon web services|azure|cloudflare|"
                r"xero|quickbooks|qbo|freeagent|sage|stripe billing|"
                r"adobe|canva|figma|miro|"
                r"openrouter|openai|anthropic|claude|gemini|"
                r"home bargains|argos|wilko|wilkinson|b&m|poundland|"
                r"staples|ryman|paperchase|whsmith|"
                r"vodafone|three\.co|three uk|ee\.co|o2|giffgaff|"
                r"home office|companies house filing)\b", re.I),
     SE_EXPENSE_ADMIN, False, 0.80, "Office / admin / postage / software / shopping"),

    # ----- advertising -----
    (re.compile(r"\b(facebook ads|meta ads|google ads|adwords|"
                r"linkedin ads|tiktok ads|x ads|twitter ads|"
                r"reddit ads|youtube ads|snapchat ads|"
                r"mailchimp|hubspot|klaviyo|sendgrid|"
                r"advertising|marketing|promoted)\b", re.I),
     SE_EXPENSE_ADVERTISING, False, 0.90, "Advertising / marketing spend"),

    # ----- entertainment (subsistence + restaurants — restricted by HMRC) -----
    (re.compile(r"\b(restaurant|pub\b|hotel|bar|cafe|coffee|"
                r"deliveroo|uber eats|just eat|"
                r"greggs|pret|starbucks|costa|caffe nero|leon|wasabi|itsu|"
                r"mcdonald'?s|kfc|burger king|subway|taco bell|nando'?s|"
                r"five guys|wagamama|pizza express|domino'?s|papa john'?s|"
                r"premier inn|travelodge|ibis|holiday inn)\b", re.I),
     SE_EXPENSE_ENTERTAINMENT, False, 0.55,
     "Subsistence / hospitality — note HMRC restricts business entertainment"),

    # ----- interest / financial charges -----
    (re.compile(r"\b(interest charge|loan interest|mortgage interest|"
                r"overdraft interest)\b", re.I),
     SE_EXPENSE_INTEREST, False, 0.85, "Interest payment on borrowing"),
    (re.compile(r"\b(bank charge|overdraft fee|transaction fee|"
                r"foreign exchange|fx fee|non-?sterling fee|"
                r"international fee|atm fee)\b", re.I),
     SE_EXPENSE_FINANCIAL, False, 0.85, "Bank / financial charge"),

    # ----- professional fees -----
    (re.compile(r"\b(solicitor|lawyer|accountant|bookkeeper|"
                r"insurance|aviva|axa|hiscox|simply business|direct line|"
                r"admiral|churchill|lv=|esure|"
                r"companies house|ico|insolvency|cipa)\b", re.I),
     SE_EXPENSE_PROFESSIONAL, False, 0.85, "Professional / legal / insurance fees"),

    # ----- cost of goods -----
    (re.compile(r"\b(makro|booker|alibaba|aliexpress|wholesale|cash & carry|"
                r"costco|bookers cash and carry)\b", re.I),
     SE_EXPENSE_COST_OF_GOODS, False, 0.80, "Wholesale goods purchase"),

    # ----- TV licence (admin) — special case -----
    (re.compile(r"\b(tv licen[cs]e|tv licensing|bbc licen[cs]e|dd tv licence)\b", re.I),
     SE_EXPENSE_ADMIN, False, 0.80, "TV licence — included under admin"),
]


def classify_self_employment(
    description: str,
    amount: float,
    user_full_name: str | None = None,
) -> Classification:
    """Map one bank transaction to a self-employment HMRC category.

    `amount` is the signed bank amount: positive = credit (money in),
    negative = debit (money out). We use the sign to bias toward income vs
    expense classification when the description is ambiguous.

    `user_full_name` (optional) lets us spot transfers to/from the user's
    own accounts and exclude them from category totals — these are owner
    draws, not taxable events.
    """
    desc = (description or "").strip()
    is_credit = amount > 0

    # Own-name transfer? Exclude before any other rule. We can't know
    # whether it's income vs expense without business context; the UI
    # marks them clearly so the user can re-categorise if needed.
    if _is_likely_owner_transfer(desc, user_full_name):
        return Classification(
            EXCLUDE_OWNER_TRANSFER,
            confidence=0.95,
            is_income=is_credit,
            reasoning=f"Looks like a transfer to/from your own account ('{desc[:40]}…') — excluded from tax totals",
        )

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


def classify_property(
    description: str,
    amount: float,
    user_full_name: str | None = None,
) -> Classification:
    """Map one bank transaction to a UK property HMRC category."""
    desc = (description or "").strip()
    is_credit = amount > 0

    if _is_likely_owner_transfer(desc, user_full_name):
        return Classification(
            EXCLUDE_OWNER_TRANSFER,
            confidence=0.95,
            is_income=is_credit,
            reasoning=f"Looks like a transfer to/from your own account ('{desc[:40]}…') — excluded from tax totals",
        )

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

def _aggregate(rows: Iterable[dict], classify_fn, low_conf_income: str, low_conf_expense: str,
               user_full_name: str | None = None) -> dict:
    """Shared aggregator. Owner-transfer rows are excluded from category totals
    but returned in `excluded` so the UI can show them clearly."""
    income: dict[str, float] = {}
    expenses: dict[str, float] = {}
    flagged: list[dict] = []
    excluded: list[dict] = []
    for r in rows:
        c = classify_fn(r.get("description", ""), float(r.get("amount") or 0), user_full_name)
        if c.category == EXCLUDE_OWNER_TRANSFER:
            excluded.append({"row": r, "classification": c.__dict__})
            continue
        bucket = income if c.is_income else expenses
        key = c.category if c.confidence >= 0.5 else (low_conf_income if c.is_income else low_conf_expense)
        bucket[key] = round(bucket.get(key, 0.0) + abs(float(r.get("amount") or 0)), 2)
        if c.confidence < 0.5:
            flagged.append({"row": r, "classification": c.__dict__})
    return {
        "income": income,
        "expenses": expenses,
        "flagged_for_review": flagged,
        "excluded": excluded,
    }


def aggregate_self_employment(rows: Iterable[dict], user_full_name: str | None = None) -> dict:
    """Sum bank-transaction rows into the HMRC SE quarterly payload shape.

    Each input row must contain at minimum:
        - "description" (str)
        - "amount" (float, signed: + credit, - debit)

    Returns a dict with `income.turnover`, `income.other`, and
    `expenses.<category>` keys ready to be assembled into the
    Self-Employment Business (MTD) period-summary POST body. Also includes
    `flagged_for_review` (low-confidence) and `excluded` (owner transfers).
    """
    return _aggregate(rows, classify_self_employment, SE_OTHER_INCOME, SE_EXPENSE_OTHER, user_full_name)


def aggregate_property(rows: Iterable[dict], user_full_name: str | None = None) -> dict:
    """Same shape as `aggregate_self_employment` but for UK property."""
    return _aggregate(rows, classify_property, PROP_INCOME_OTHER, PROP_EXPENSE_OTHER, user_full_name)
