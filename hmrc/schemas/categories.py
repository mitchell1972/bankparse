"""
Canonical HMRC MTD ITSA category names (single source of truth).

Why this file exists
--------------------
Before this module existed, the HMRC category names were defined as
constants in `hmrc/services/mapping.py` AND duplicated as the
`_SE_CATEGORIES` / `_PROP_CATEGORIES` lists in `hmrc/services/ai_classifier.py`.
If someone added a category to one and forgot the other, the AI returned
"valid"-looking values that the HMRC validator rejected.

Both the regex classifier and the AI classifier now import from here, so
the list can only drift in one place.

Source of truth: HMRC developer hub
  - Self-Employment Business (MTD) API v5.0 — period summary expenses block
    https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/self-employment-business-api
  - Property Business (MTD) API v6.0 — period summary expenses block
    https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/property-business-api

When HMRC publishes a new version, update the constants here and the rest of
the codebase picks it up.
"""

from __future__ import annotations

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

SE_CATEGORIES: tuple[str, ...] = (
    SE_INCOME, SE_OTHER_INCOME,
    SE_EXPENSE_COST_OF_GOODS, SE_EXPENSE_CIS, SE_EXPENSE_STAFF,
    SE_EXPENSE_TRAVEL, SE_EXPENSE_PREMISES, SE_EXPENSE_REPAIRS,
    SE_EXPENSE_ADMIN, SE_EXPENSE_ADVERTISING, SE_EXPENSE_ENTERTAINMENT,
    SE_EXPENSE_INTEREST, SE_EXPENSE_FINANCIAL, SE_EXPENSE_BAD_DEBT,
    SE_EXPENSE_PROFESSIONAL, SE_EXPENSE_DEPRECIATION, SE_EXPENSE_OTHER,
)


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
# kept separate from `financialCosts` because HMRC reports it on a different
# line of the return.
PROP_EXPENSE_RESIDENTIAL_FINANCIAL = "residentialFinancialCost"

PROPERTY_CATEGORIES: tuple[str, ...] = (
    PROP_INCOME_RENT, PROP_INCOME_PREMIUMS, PROP_INCOME_OTHER,
    PROP_EXPENSE_PREMISES, PROP_EXPENSE_REPAIRS, PROP_EXPENSE_FINANCIAL,
    PROP_EXPENSE_PROFESSIONAL, PROP_EXPENSE_SERVICES, PROP_EXPENSE_TRAVEL,
    PROP_EXPENSE_OTHER, PROP_EXPENSE_RESIDENTIAL_FINANCIAL,
)


# ---------------------------------------------------------------------------
# Sentinel — not a real HMRC category. Used internally to mark transactions
# the user moved between their own accounts (owner draws etc.); the UI hides
# these from category totals but still displays them on the table.
# ---------------------------------------------------------------------------

EXCLUDE_OWNER_TRANSFER = "_owner_transfer"


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

BusinessType = str  # "se" | "property"


def categories_for(business_type: BusinessType) -> tuple[str, ...]:
    """Return the canonical category list for a business type."""
    return PROPERTY_CATEGORIES if business_type == "property" else SE_CATEGORIES


def fallback_other(business_type: BusinessType) -> str:
    """Return the 'other expense' category for unmatched debits."""
    return PROP_EXPENSE_OTHER if business_type == "property" else SE_EXPENSE_OTHER


def fallback_other_income(business_type: BusinessType) -> str:
    """Return the 'other income' category for unmatched credits."""
    return PROP_INCOME_OTHER if business_type == "property" else SE_OTHER_INCOME


def is_valid_category(category: str, business_type: BusinessType) -> bool:
    """Is `category` a real HMRC category for this business type?"""
    return category in categories_for(business_type)


# Income categories per HMRC's published spec. The income/expense split is
# an INTRINSIC property of the category name — `turnover` is always income,
# `other` is always an expense — so we treat it as the source of truth and
# IGNORE the upstream `is_income` flag during aggregation.
_SE_INCOME_CATEGORIES = frozenset({SE_INCOME, SE_OTHER_INCOME})
_PROPERTY_INCOME_CATEGORIES = frozenset({
    PROP_INCOME_RENT, PROP_INCOME_PREMIUMS, PROP_INCOME_OTHER,
})


def is_income_category(category: str, business_type: BusinessType) -> bool:
    """True if the HMRC category code is an INCOME line for this business.

    Used by the summary aggregator to decide which bucket a transaction
    belongs to. Critically, this routes by the category's intrinsic
    HMRC meaning — not by the AI classifier's `is_income` boolean — so an
    AI mis-classification (e.g. tagging a credit as `other` with
    `is_income=True`) lands the row in the correct expense bucket on the
    summary, instead of polluting Income with a category called
    "Other expense".

    Note: this is about the category's TAX classification, not the
    transaction's direction. A refund showing up under `travelCosts`
    will still aggregate under expenses; the negative sign on that
    refund row will reduce the total. That matches what HMRC wants.
    """
    if business_type == "property":
        return category in _PROPERTY_INCOME_CATEGORIES
    return category in _SE_INCOME_CATEGORIES
