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
