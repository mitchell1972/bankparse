"""Unit tests for hmrc.services.mapping — bank-tx → HMRC category."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# --- Self-employment ---------------------------------------------------------

def test_se_stripe_credit_classified_as_turnover():
    from hmrc.services import mapping as m
    c = m.classify_self_employment("STRIPE PAYOUT BANKSCAN AI", amount=450.0)
    assert c.category == m.SE_INCOME
    assert c.is_income is True
    assert c.confidence >= 0.8


def test_se_petrol_classified_as_travel():
    from hmrc.services import mapping as m
    c = m.classify_self_employment("SHELL FUEL STATION LONDON", amount=-65.0)
    assert c.category == m.SE_EXPENSE_TRAVEL
    assert c.is_income is False


def test_se_travis_perkins_repairs():
    from hmrc.services import mapping as m
    c = m.classify_self_employment("TRAVIS PERKINS BUILDING SUPPLIES", amount=-142.50)
    assert c.category == m.SE_EXPENSE_REPAIRS


def test_se_unknown_debit_low_confidence_other():
    from hmrc.services import mapping as m
    c = m.classify_self_employment("ZXZX OBSCURE MERCHANT", amount=-12.50)
    assert c.category == m.SE_EXPENSE_OTHER
    assert c.confidence < 0.5


def test_se_unknown_credit_low_confidence_other_income():
    from hmrc.services import mapping as m
    c = m.classify_self_employment("ZXZX OBSCURE INFLOW", amount=350.0)
    assert c.category == m.SE_OTHER_INCOME
    assert c.is_income is True


def test_se_aggregation_sums_by_category():
    from hmrc.services import mapping as m
    rows = [
        {"description": "STRIPE PAYOUT", "amount": 1000.0},
        {"description": "STRIPE PAYOUT", "amount": 500.0},
        {"description": "TRAVIS PERKINS", "amount": -100.0},
        {"description": "ZZZ MYSTERY", "amount": -50.0},
    ]
    result = m.aggregate_self_employment(rows)
    assert result["income"][m.SE_INCOME] == 1500.0
    assert result["expenses"][m.SE_EXPENSE_REPAIRS] == 100.0
    # Low-confidence unknown is bucketed as 'other' and flagged for review.
    assert result["expenses"][m.SE_EXPENSE_OTHER] == 50.0
    assert len(result["flagged_for_review"]) == 1


# --- Property ----------------------------------------------------------------

def test_property_rent_credit():
    from hmrc.services import mapping as m
    c = m.classify_property("RENT FROM J SMITH FEB", amount=1200.0)
    assert c.category == m.PROP_INCOME_RENT
    assert c.is_income is True


def test_property_mortgage_interest_restricted_relief():
    from hmrc.services import mapping as m
    c = m.classify_property("HALIFAX BUY TO LET MORTGAGE INTEREST", amount=-450.0)
    # Residential mortgage interest goes to the special restricted bucket.
    assert c.category == m.PROP_EXPENSE_RESIDENTIAL_FINANCIAL


def test_property_repair_via_screwfix():
    from hmrc.services import mapping as m
    c = m.classify_property("SCREWFIX TOOLS", amount=-40.0)
    assert c.category == m.PROP_EXPENSE_REPAIRS


def test_property_aggregation_separates_residential_finance():
    from hmrc.services import mapping as m
    rows = [
        {"description": "RENT FROM J SMITH", "amount": 1200.0},
        {"description": "HALIFAX BUY TO LET MORTGAGE INTEREST", "amount": -450.0},
        {"description": "SCREWFIX", "amount": -40.0},
        {"description": "BRITISH GAS HOME", "amount": -80.0},
    ]
    result = m.aggregate_property(rows)
    assert result["income"][m.PROP_INCOME_RENT] == 1200.0
    assert result["expenses"][m.PROP_EXPENSE_RESIDENTIAL_FINANCIAL] == 450.0
    assert result["expenses"][m.PROP_EXPENSE_REPAIRS] == 40.0
    assert result["expenses"][m.PROP_EXPENSE_PREMISES] == 80.0


# --- Expanded UK merchant coverage (the cases from a real statement) -------

def test_se_uk_parking_merchants_to_travel():
    """NCP, MiPermit, RingGo etc. — straight to travelCosts."""
    from hmrc.services import mapping as m
    for desc in ["NCP IPSWICH TOWER", "MIPERMIT LTD CHIPPENHAM", "RINGGO LONDON", "JUSTPARK"]:
        c = m.classify_self_employment(desc, amount=-3.50)
        assert c.category == m.SE_EXPENSE_TRAVEL, f"expected travelCosts for '{desc}', got {c.category}"
        assert c.confidence >= 0.8


def test_se_tv_licence_is_admin_not_other():
    from hmrc.services import mapping as m
    c = m.classify_self_employment("DD TV LICENCE MBP", amount=-14.95)
    assert c.category == m.SE_EXPENSE_ADMIN


def test_se_home_bargains_admin():
    from hmrc.services import mapping as m
    c = m.classify_self_employment(")))  HOME BARGAINS IPSWICH", amount=-1.99)
    assert c.category == m.SE_EXPENSE_ADMIN


def test_se_taco_bell_entertainment_low_confidence():
    """Fast-food chains map to entertainment but confidence stays modest because
    HMRC heavily restricts business entertainment."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(")))  TACO BELL NACTON R IPSWICH", amount=-13.37)
    assert c.category == m.SE_EXPENSE_ENTERTAINMENT
    assert c.confidence < 0.7  # nudges the user to review


# --- Owner-transfer detection ---------------------------------------------

def test_owner_transfer_excluded_when_name_matches():
    """A bank transfer with the user's own name in the description should be
    excluded — it's an owner draw, not a tax expense."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        "BP Augusta k Chukwuma Mitchell",
        amount=-100.0,
        user_full_name="Augusta Chukwuma Mitchell",
    )
    assert c.category == m.EXCLUDE_OWNER_TRANSFER
    assert c.confidence >= 0.9


def test_owner_transfer_not_triggered_without_name():
    """Without the user's name, the same row shouldn't be excluded — fall
    through to normal classification."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        "BP Augusta k Chukwuma Mitchell",
        amount=-100.0,
        user_full_name=None,
    )
    assert c.category != m.EXCLUDE_OWNER_TRANSFER


def test_aggregation_excluded_rows_kept_separate_from_totals():
    from hmrc.services import mapping as m
    rows = [
        {"description": "STRIPE PAYOUT", "amount": 1000.0},
        {"description": "Augusta Mitchell transfer", "amount": -200.0},  # owner transfer
        {"description": "TRAVIS PERKINS", "amount": -50.0},
    ]
    result = m.aggregate_self_employment(rows, user_full_name="Augusta Mitchell")
    assert result["income"][m.SE_INCOME] == 1000.0
    assert result["expenses"][m.SE_EXPENSE_REPAIRS] == 50.0
    assert len(result["excluded"]) == 1
    # The £200 owner transfer must NOT have leaked into expenses
    assert m.SE_EXPENSE_OTHER not in result["expenses"]


# --- Merchant overrides ----------------------------------------------------

def test_merchant_key_normalises_bank_noise():
    from hmrc.repositories import overrides as o
    assert o.merchant_key(")))  MIPERMIT LTD CHIPPENHAM 14/03") == "mipermit ltd chippenham"
    assert o.merchant_key("DD TV LICENCE MBP") == "tv licence mbp"
    assert o.merchant_key("BP Augusta k Chukwuma Mitchell 22DEC") == "augusta k chukwuma mitchell"


def test_merchant_key_idempotent():
    from hmrc.repositories import overrides as o
    raw = "VIS MIPERMIT LTD CHIPPENHAM   2026-01-21"
    once = o.merchant_key(raw)
    assert once == o.merchant_key(once)

