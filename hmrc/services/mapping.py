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

# Canonical HMRC category names live in `hmrc/schemas/categories.py` — this
# module re-exports them so existing call sites (`mapping.SE_INCOME` etc.)
# keep working unchanged, but there is only ONE source of truth.
from ..schemas.categories import (  # noqa: F401  (re-exports)
    SE_INCOME, SE_OTHER_INCOME,
    SE_EXPENSE_COST_OF_GOODS, SE_EXPENSE_CIS, SE_EXPENSE_STAFF,
    SE_EXPENSE_TRAVEL, SE_EXPENSE_PREMISES, SE_EXPENSE_REPAIRS,
    SE_EXPENSE_ADMIN, SE_EXPENSE_ADVERTISING, SE_EXPENSE_ENTERTAINMENT,
    SE_EXPENSE_INTEREST, SE_EXPENSE_FINANCIAL, SE_EXPENSE_BAD_DEBT,
    SE_EXPENSE_PROFESSIONAL, SE_EXPENSE_DEPRECIATION, SE_EXPENSE_OTHER,
    PROP_INCOME_RENT, PROP_INCOME_PREMIUMS, PROP_INCOME_OTHER,
    PROP_EXPENSE_PREMISES, PROP_EXPENSE_REPAIRS, PROP_EXPENSE_FINANCIAL,
    PROP_EXPENSE_PROFESSIONAL, PROP_EXPENSE_SERVICES, PROP_EXPENSE_TRAVEL,
    PROP_EXPENSE_OTHER, PROP_EXPENSE_RESIDENTIAL_FINANCIAL,
    EXCLUDE_OWNER_TRANSFER,
)


# Strip bank-statement prefixes before regex matching.
# Real-world UK statements lead descriptions with codes like "BP" (Bill
# Payment), "DD" (Direct Debit), "SO" (Standing Order), "CR" (Credit),
# "FPI"/"FPO" (Faster Payment In/Out), "VIS" (Visa), "POS", "ATM", "CHQ",
# "TFR"/"TRF" (Transfer). These collide with merchant names — e.g. "BP" the
# prefix collides with "BP" the petrol station — and lead to confidently
# wrong categorisations. Strip them once before matching.
_BANK_PREFIX_RE = re.compile(
    # NB. 'bp' is intentionally NOT stripped here — the strict BP-fuel rule
    # below needs to see "BP <fuel-word>" in the original description, and
    # the person-name fallback handles 'BP <person-name>' just fine because
    # 'BP James Okeh Gift' reads as four capitalised words anyway.
    r"^\s*(?:dd|so|cr|dr|vis|atm|chq|pos|fpi|fpo|tfr|trf|"
    r"\)+|\(+)\s+",
    re.I,
)

# A description that's essentially just "<First> <Last>" — common shape for
# personal transfers (gifts, paying friends back, etc.) — has no business
# signal so we route it to AI / low-confidence rather than guess wrong.
_LIKELY_PERSON_NAME = re.compile(
    r"^\s*[A-Z][a-zA-Z'\-]{1,15}(?:\s+[A-Z][a-zA-Z'\-\.]{0,15}){1,3}\s*$"
)


def _strip_prefix_for_matching(description: str) -> str:
    """Remove the leading bank-noise prefix so the rest of the description
    can be matched against merchant rules without bank-prefix collisions."""
    return _BANK_PREFIX_RE.sub("", description or "")


def _looks_like_personal_name(stripped: str) -> bool:
    """After prefix removal, does the description look like just a person's
    name (no obvious merchant or business signal)? If yes, low-confidence
    'other' — let user override or AI take a swing."""
    return bool(_LIKELY_PERSON_NAME.match(stripped))


@dataclass(frozen=True)
class Classification:
    category: str
    confidence: float          # 0..1
    is_income: bool
    reasoning: str             # plain-English explanation for the UI


# Sentinel `EXCLUDE_OWNER_TRANSFER` is re-exported from `schemas.categories`
# at the top of this file — these transactions are NOT a tax expense or
# income (the user moving money between their own accounts). The UI hides
# them from category totals but still shows them on the table.


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

# Reference-only rules — fire ONLY when the strong signal appears in the
# customer-supplied reference (memo / invoice id / "RENT FEB" line), not
# in the merchant name. These get a confidence boost because a memo
# saying "INV-2026-001" is much more diagnostic than the same string
# buried inside a merchant description. Each rule is (pattern, category,
# is_income, confidence, why).
_SE_REFERENCE_RULES: list[tuple[re.Pattern[str], str, bool, float, str]] = [
    # ----- invoice numbers → turnover (when also a credit) -----
    # NB. no trailing \b — references like "INV-2026-001" end on a digit
    # which has no boundary against the preceding digit, so requiring a
    # word boundary at the end would silently fail to match every typical
    # invoice number.
    (re.compile(r"\b(inv[-_\s/]?\d|invoice[-_\s]?\d|invoice\s+no|"
                r"inv\s*[#:]\s*\d|sales\s+inv|customer\s+inv|client\s+inv)", re.I),
     SE_INCOME, True, 0.92,
     "Reference contains an invoice number — typical sales receipt"),

    # ----- staff payroll references -----
    (re.compile(r"\b(payroll|wages?|salary|net\s+pay|paye|"
                r"month\s+end\s+pay)\b", re.I),
     SE_EXPENSE_STAFF, False, 0.88,
     "Reference looks like a wages / payroll line"),

    # ----- HMRC self-assessment / PAYE / VAT / corp tax payments
    # These are NOT deductible business expenses — they're a tax
    # liability settlement. Route to "other" with explicit reasoning so
    # the user can flag-and-exclude on review.
    (re.compile(r"\b(hmrc|hmrc\s+nps|self[-\s]?assessment|"
                r"sa\s+payment|paye\s+tax|class\s*[12]\s*ni|"
                r"vat\s+(return|payment|q[1-4])|"
                r"corporation\s+tax|ct600)\b", re.I),
     SE_EXPENSE_OTHER, False, 0.85,
     "Reference identifies an HMRC tax payment — not a business expense; "
     "review and mark personal/excluded if appropriate"),

    # ----- standing-order rent payments (sole-trader paying business rent) -----
    (re.compile(r"\b(rent\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|month)|"
                r"rent\s+payment|monthly\s+rent|business\s+rent)\b", re.I),
     SE_EXPENSE_PREMISES, False, 0.90,
     "Reference describes a rent payment — premises running cost"),

    # ----- utility account references -----
    (re.compile(r"\b(elec(t(ric(ity)?)?)?\s+(account|bill|q[1-4])|"
                r"gas\s+(account|bill|q[1-4])|"
                r"water\s+(account|bill|rates))\b", re.I),
     SE_EXPENSE_PREMISES, False, 0.85,
     "Reference identifies a utility bill"),

    # ----- subcontractor CIS deductions -----
    (re.compile(r"\b(cis\s+(?:payment|deduction)|"
                r"contractor\s+payment|subbie\s+pay)\b", re.I),
     SE_EXPENSE_CIS, False, 0.92,
     "Reference describes a CIS subcontractor payment"),

    # ----- ad-platform references -----
    (re.compile(r"\b(google\s+ads|facebook\s+ads|meta\s+ads|"
                r"linkedin\s+campaign|tiktok\s+campaign|"
                r"adwords\s+account)\b", re.I),
     SE_EXPENSE_ADVERTISING, False, 0.92,
     "Reference identifies a paid-ads platform charge"),

    # ----- professional-services invoice patterns -----
    (re.compile(r"\b(accountancy\s+fee|legal\s+fee|"
                r"professional\s+services|consultancy\s+fee|"
                r"audit\s+fee|tax\s+advice)\b", re.I),
     SE_EXPENSE_PROFESSIONAL, False, 0.90,
     "Reference describes a professional-services fee"),
]


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
    # NB. `bp` is intentionally NOT here — too many UK statements use it as
    # the "Bill Payment" prefix (e.g. "BP James Okeh Gift"). The fuel-station
    # rule below requires "BP" + a fuel-context word.
    (re.compile(r"\b(ncp|q-?park|euro car park|apcoa|justpark|"
                r"mipermit|ringgo|paybyphone|parkmobile|"
                r"tfl|transport for london|trainline|"
                r"national rail|cross country|lner|gwr|south western|"
                r"chiltern|northern rail|tpe|"
                r"uber|lyft|bolt|via|free now|gett|addison lee|"
                r"easyjet|ryanair|british airways|jet2|"
                r"shell|esso|texaco|sainsbury'?s petrol|tesco fuel|asda fuel|"
                r"morrisons fuel|costco fuel|gulf|jet petrol|fuel|petrol|diesel|"
                r"dvla|congestion charge|dartford crossing|"
                r"national express|megabus|stagecoach|first bus)\b", re.I),
     SE_EXPENSE_TRAVEL, False, 0.90, "Travel / fuel / parking merchant"),
    # Tight BP fuel rule — only fires when BP is followed by a fuel-context word.
    (re.compile(r"\bbp\s+(forecourt|petrol|service|station|garage|fuel|express)\b", re.I),
     SE_EXPENSE_TRAVEL, False, 0.90, "BP fuel station"),

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
    reference: str | None = None,
) -> Classification:
    """Map one bank transaction to a self-employment HMRC category.

    `amount` is the signed bank amount: positive = credit (money in),
    negative = debit (money out). We use the sign to bias toward income vs
    expense classification when the description is ambiguous.

    `user_full_name` (optional) lets us spot transfers to/from the user's
    own accounts and exclude them from category totals — these are owner
    draws, not taxable events.

    `reference` (optional) is the customer-supplied memo / invoice id
    extracted by the parser. When present it gets two boosts:
      1. A set of reference-only rules fire first (e.g. "INV-2026-001"
         → turnover with 0.92 confidence) — these are strictly more
         diagnostic than merchant-name matches.
      2. The merchant-name rules also scan the reference text, so a
         vague description like "FPI Acme Ltd" with reference "Plumbing
         invoice 0042" still hits the repairs rule.
    """
    desc = (description or "").strip()
    ref = (reference or "").strip()
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

    # Reference-only rules first — strongest signal we have.
    if ref:
        for pat, cat, is_income_rule, conf, why in _SE_REFERENCE_RULES:
            if pat.search(ref):
                if is_income_rule != is_credit:
                    # Direction mismatch — still trust the bank sign but
                    # downgrade. e.g. "wages" reference on a CREDIT is
                    # almost certainly the user RECEIVING a wage from
                    # employment (not a self-employment expense).
                    return Classification(
                        SE_INCOME if is_credit else SE_EXPENSE_OTHER,
                        confidence=0.40,
                        is_income=is_credit,
                        reasoning=(
                            f"Reference '{ref[:40]}' suggests {why}, but the "
                            f"transaction is a {'credit' if is_credit else 'debit'} — "
                            f"flagged for review."
                        ),
                    )
                return Classification(cat, conf, is_income_rule, why)

    # Strip the bank-statement prefix ('BP', 'DD', 'CR', etc.) before matching
    # rules. Without this, descriptions like 'BP James Okeh Gift' wrongly hit
    # the 'BP' fuel rule. Keep the original for reasoning text.
    stripped = _strip_prefix_for_matching(desc).strip()
    # Merchant rules scan description + reference. Reference is appended
    # after a space so word-boundary matches still work cleanly.
    combined = (stripped + " " + ref).strip() if ref else stripped

    for pat, cat, is_income_rule, conf, why in _SE_RULES:
        if pat.search(combined):
            # If rule says income but bank says debit (or vice versa), trust
            # the bank direction and downgrade confidence.
            if is_income_rule != is_credit:
                return Classification(
                    SE_INCOME if is_credit else SE_EXPENSE_OTHER,
                    confidence=0.35,
                    is_income=is_credit,
                    reasoning=f"Direction mismatch — {why}, but transaction is a {'credit' if is_credit else 'debit'}",
                )
            # When the merchant name itself didn't match but the rule
            # fired off the reference, surface that in the reasoning.
            if ref and pat.search(ref) and not pat.search(stripped):
                why = f"{why} (matched on reference '{ref[:40]}')"
            return Classification(cat, conf, is_income_rule, why)

    # No rule matched. If the stripped description looks like just a person's
    # name (no LTD/PLC/place/digits), it's almost certainly a personal transfer
    # rather than a business merchant — flag with explicit reasoning so the
    # UI nudges the user to confirm and AI can pick it up.
    if _looks_like_personal_name(stripped):
        return Classification(
            SE_OTHER_INCOME if is_credit else SE_EXPENSE_OTHER,
            confidence=0.20,
            is_income=is_credit,
            reasoning=(
                f"Looks like a personal transfer ({'received from' if is_credit else 'sent to'} "
                f"'{stripped[:40]}') — no business merchant matched. Please review or enable AI."
            ),
        )

    # Fallback: route by sign, low confidence.
    if is_credit:
        return Classification(SE_OTHER_INCOME, 0.30, True,
                              "No category match — falling back to 'other income' for review")
    return Classification(SE_EXPENSE_OTHER, 0.30, False,
                          "No category match — falling back to 'other expense' for review")


# ---------------------------------------------------------------------------
# UK property rules
# ---------------------------------------------------------------------------

# Reference-only rules for UK property. References are the killer signal
# for landlords: "RENT FEB 32 HIGH ST", "DEPOSIT 14 OAK LANE", "BOILER
# SERVICE FLAT 3" tell us category + property in one shot.
_PROPERTY_REFERENCE_RULES: list[tuple[re.Pattern[str], str, bool, float, str]] = [
    # --- rent received from tenant (very common pattern) ---
    (re.compile(r"\b(rent\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|month|week)|"
                r"monthly\s+rent|weekly\s+rent|tenant\s+rent|"
                r"rent\s+for|rent\s+\d|"
                r"rental\s+income)\b", re.I),
     PROP_INCOME_RENT, True, 0.95,
     "Reference describes a rent payment"),

    # --- tenant deposit (NOT taxable income — held in protection scheme) ---
    (re.compile(r"\b(deposit\s+(?:scheme|protection|held|"
                r"received|tenant)|"
                r"dps\s+ref|tds\s+ref|mydeposits)\b", re.I),
     PROP_INCOME_OTHER, True, 0.55,
     "Reference suggests a tenant deposit — review; deposits aren't taxable income"),

    # --- property repairs / boiler / heating refs ---
    (re.compile(r"\b(boiler|heating|plumbing|electric|"
                r"repair\s+(?:flat|unit|property)|"
                r"property\s+repair|maintenance\s+(?:flat|unit|property))\b", re.I),
     PROP_EXPENSE_REPAIRS, False, 0.92,
     "Reference describes a property repair"),

    # --- letting-agent / management fees ---
    (re.compile(r"\b(letting\s+(?:fee|agent)|"
                r"management\s+fee|agency\s+fee|"
                r"rightmove\s+listing|zoopla\s+listing)\b", re.I),
     PROP_EXPENSE_SERVICES, False, 0.92,
     "Reference describes a letting-agent fee"),

    # --- mortgage interest -> residential financial cost -----
    (re.compile(r"\b(btl\s+mortgage|buy[-\s]?to[-\s]?let|"
                r"residential\s+mortgage)\b", re.I),
     PROP_EXPENSE_RESIDENTIAL_FINANCIAL, False, 0.92,
     "Reference identifies a BTL/residential mortgage payment"),
]


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
    reference: str | None = None,
) -> Classification:
    """Map one bank transaction to a UK property HMRC category.

    See ``classify_self_employment`` for the role of ``reference`` —
    same idea: reference-only rules fire first, then merchant rules
    scan description+reference. Critical for landlords because the
    description on inbound rent payments is often just the tenant's
    name (which our merchant rules can't categorise) but the reference
    "RENT FEB 32 OAK LANE" is unambiguous."""
    desc = (description or "").strip()
    ref = (reference or "").strip()
    is_credit = amount > 0

    if _is_likely_owner_transfer(desc, user_full_name):
        return Classification(
            EXCLUDE_OWNER_TRANSFER,
            confidence=0.95,
            is_income=is_credit,
            reasoning=f"Looks like a transfer to/from your own account ('{desc[:40]}…') — excluded from tax totals",
        )

    # Reference-only rules first (the killer signal for landlords).
    if ref:
        for pat, cat, is_income_rule, conf, why in _PROPERTY_REFERENCE_RULES:
            if pat.search(ref):
                if is_income_rule != is_credit:
                    return Classification(
                        PROP_INCOME_OTHER if is_credit else PROP_EXPENSE_OTHER,
                        confidence=0.40,
                        is_income=is_credit,
                        reasoning=(
                            f"Reference '{ref[:40]}' suggests {why}, but the "
                            f"transaction is a {'credit' if is_credit else 'debit'} — "
                            f"flagged for review."
                        ),
                    )
                return Classification(cat, conf, is_income_rule, why)

    stripped = _strip_prefix_for_matching(desc).strip()
    combined = (stripped + " " + ref).strip() if ref else stripped

    for pat, cat, is_income_rule, conf, why in _PROPERTY_RULES:
        if pat.search(combined):
            if is_income_rule != is_credit:
                return Classification(
                    PROP_INCOME_OTHER if is_credit else PROP_EXPENSE_OTHER,
                    confidence=0.35,
                    is_income=is_credit,
                    reasoning=f"Direction mismatch — {why}, but transaction is a {'credit' if is_credit else 'debit'}",
                )
            if ref and pat.search(ref) and not pat.search(stripped):
                why = f"{why} (matched on reference '{ref[:40]}')"
            return Classification(cat, conf, is_income_rule, why)

    if _looks_like_personal_name(stripped):
        return Classification(
            PROP_INCOME_OTHER if is_credit else PROP_EXPENSE_OTHER,
            confidence=0.20,
            is_income=is_credit,
            reasoning=(
                f"Looks like a personal transfer ({'received from' if is_credit else 'sent to'} "
                f"'{stripped[:40]}') — no property merchant matched. Please review or enable AI."
            ),
        )

    if is_credit:
        return Classification(PROP_INCOME_OTHER, 0.30, True,
                              "No category match — falling back to 'other income' for review")
    return Classification(PROP_EXPENSE_OTHER, 0.30, False,
                          "No category match — falling back to 'other expense' for review")


# ---------------------------------------------------------------------------
# Aggregation — bank-transaction rows → HMRC quarterly submission payload.
# ---------------------------------------------------------------------------

def _aggregate(
    rows: Iterable[dict], classify_fn, low_conf_income: str, low_conf_expense: str,
    business_type: str,
    user_full_name: str | None = None,
) -> dict:
    """Shared aggregator. Owner-transfer rows are excluded from category totals
    but returned in `excluded` so the UI can show them clearly.

    Bucket routing uses the category's INTRINSIC HMRC meaning
    (see `hmrc.schemas.categories.is_income_category`), NOT the per-row
    `is_income` flag. This protects against the classifier mis-flagging a
    credit as an expense category like `other` and the row landing under
    Income — which would render as the nonsensical "Other expense" inside
    the Income column in the UI/XLSX.
    """
    # Local import keeps the top of the module clean and avoids re-importing
    # ourselves via the re-export chain.
    from ..schemas.categories import is_income_category

    income: dict[str, float] = {}
    expenses: dict[str, float] = {}
    flagged: list[dict] = []
    excluded: list[dict] = []
    for r in rows:
        c = classify_fn(
            r.get("description", ""),
            float(r.get("amount") or 0),
            user_full_name,
            reference=(r.get("reference") or None),
        )
        if c.category == EXCLUDE_OWNER_TRANSFER:
            excluded.append({"row": r, "classification": c.__dict__})
            continue
        # Route by the category's canonical income/expense status, not the
        # classifier's per-row guess.
        category_is_income = is_income_category(c.category, business_type)
        bucket = income if category_is_income else expenses
        # Low-confidence rows fall back to the 'other' bucket of the SAME
        # side as the canonical category, not the classifier's guess.
        key = c.category if c.confidence >= 0.5 else (low_conf_income if category_is_income else low_conf_expense)
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
    return _aggregate(
        rows, classify_self_employment, SE_OTHER_INCOME, SE_EXPENSE_OTHER,
        business_type="se",
        user_full_name=user_full_name,
    )


def aggregate_property(rows: Iterable[dict], user_full_name: str | None = None) -> dict:
    """Same shape as `aggregate_self_employment` but for UK property."""
    return _aggregate(
        rows, classify_property, PROP_INCOME_OTHER, PROP_EXPENSE_OTHER,
        business_type="property",
        user_full_name=user_full_name,
    )
