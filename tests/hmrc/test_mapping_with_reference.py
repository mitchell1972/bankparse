"""
Tests for the reference-aware bank-tx → HMRC category mapper.

The bank statement parser now extracts the customer-supplied REFERENCE
(memo / invoice id / "RENT FEB" line) as a separate field from the
merchant description. These tests pin the behaviour:

  - Reference-only rules fire when the strong signal is in the
    reference (not the description).
  - Merchant rules also scan the reference text — a vague description
    plus a strong reference still classifies correctly.
  - Direction mismatches (e.g. "wages" reference on a credit) get
    downgraded confidence, not a wrong-bucket classification.
  - When the description is already definitive, the reference doesn't
    change the outcome (no regression on existing rules).
  - Both classifiers (self-employment + UK property) honour the
    parameter, including back-compat when called without it.

If you change the rule order in mapping.py, run these tests — they
are the contract.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Self-employment: reference-only rules fire when the merchant is vague
# ---------------------------------------------------------------------------


def test_se_inbound_payment_with_invoice_reference_is_turnover():
    """'Acme Ltd' alone wouldn't classify confidently. With reference
    'INV-2026-001' it must land in turnover with high confidence."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="FPI Acme Engineering Ltd",
        amount=2500.00,
        reference="INV-2026-001",
    )
    assert c.category == m.SE_INCOME
    assert c.is_income is True
    assert c.confidence >= 0.9


def test_se_payment_with_wages_reference_is_staff_costs():
    """Standing order to a person + reference 'WAGES' / 'PAYROLL' is
    unambiguous staff cost — without the reference it would have been
    a low-confidence personal-name guess."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="SO J Smith",
        amount=-1850.00,
        reference="WAGES NOV",
    )
    assert c.category == m.SE_EXPENSE_STAFF
    assert c.confidence >= 0.85


def test_se_hmrc_self_assessment_reference_routes_to_other_not_expense_category():
    """Self Assessment payment is a TAX LIABILITY, not a deductible
    expense. Reference 'SELF ASSESSMENT 9999999999K' must NOT land in
    any expense category — it routes to 'other' with explicit warning."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="HMRC NDDS",
        amount=-650.00,
        reference="SELF ASSESSMENT 9999999999K",
    )
    assert c.category == m.SE_EXPENSE_OTHER
    assert "HMRC tax payment" in c.reasoning
    assert "not a business expense" in c.reasoning


def test_se_hmrc_vat_reference_also_flagged():
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="HMRC VAT",
        amount=-1200.00,
        reference="VAT Q1 2026",
    )
    assert c.category == m.SE_EXPENSE_OTHER
    assert "not a business expense" in c.reasoning


def test_se_corporation_tax_reference_flagged():
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="HMRC CT",
        amount=-3200.00,
        reference="CORPORATION TAX 2025",
    )
    assert c.category == m.SE_EXPENSE_OTHER
    assert "not a business expense" in c.reasoning


def test_se_rent_reference_routes_to_premises():
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="SO LANDLORD LTD",
        amount=-800.00,
        reference="RENT FEB OFFICE",
    )
    assert c.category == m.SE_EXPENSE_PREMISES
    assert c.confidence >= 0.85


def test_se_google_ads_reference_routes_to_advertising():
    """Without reference, payment processor sees 'FPI Google Ireland Ltd'
    and might misclassify. With 'Google Ads' reference: unambiguous."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="FPI Google Ireland Ltd",
        amount=-150.00,
        reference="Google Ads campaign Q1",
    )
    assert c.category == m.SE_EXPENSE_ADVERTISING
    assert c.confidence >= 0.9


def test_se_accountancy_fee_reference_routes_to_professional():
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="DD Smith Bookkeeping",
        amount=-180.00,
        reference="Accountancy fee Q4",
    )
    assert c.category == m.SE_EXPENSE_PROFESSIONAL
    assert c.confidence >= 0.85


def test_se_cis_reference_routes_to_cis():
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="FPO Mike Plumbing",
        amount=-650.00,
        reference="CIS payment Nov",
    )
    assert c.category == m.SE_EXPENSE_CIS
    assert c.confidence >= 0.9


# ---------------------------------------------------------------------------
# Self-employment: merchant rules scan reference too
# ---------------------------------------------------------------------------


def test_se_vague_merchant_but_petrol_reference_is_travel():
    """Description 'CARD PAYMENT 1234' is uninformative. Reference
    'shell fuel' triggers the travel-merchant rule (which scans both
    fields)."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="CARD PAYMENT 1234",
        amount=-65.00,
        reference="Shell fuel A1 services",
    )
    assert c.category == m.SE_EXPENSE_TRAVEL


def test_se_repairs_via_reference_match_says_so_in_reasoning():
    """When a merchant rule matches only because of the reference, the
    reasoning string mentions it — so the user sees WHY."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="FPO J Williams",
        amount=-280.00,
        reference="Plumber emergency boiler repair",
    )
    assert c.category == m.SE_EXPENSE_REPAIRS
    assert "matched on reference" in c.reasoning


# ---------------------------------------------------------------------------
# Direction mismatches — bank sign always wins
# ---------------------------------------------------------------------------


def test_se_wages_reference_on_a_credit_is_downgraded_not_misclassified():
    """A 'wages' reference on a CREDIT (money in) is almost certainly the
    user RECEIVING a wage from employment — not a self-employment
    expense. We must NOT classify it as SE_EXPENSE_STAFF."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="CR PAYROLL ACME LTD",
        amount=1800.00,
        reference="WAGES NOV",
    )
    assert c.category == m.SE_INCOME  # routed to income because credit
    assert c.confidence < 0.5
    assert "flagged for review" in c.reasoning


def test_se_invoice_reference_on_a_debit_is_downgraded():
    """Invoice number on a DEBIT is unusual (paying a supplier's invoice?).
    Downgrade confidence but don't claim it's turnover."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="DD ANY MERCHANT",
        amount=-450.00,
        reference="Their INV-2026-099",
    )
    assert c.category != m.SE_INCOME  # never income on a debit
    assert c.confidence < 0.5


# ---------------------------------------------------------------------------
# Back-compat: reference=None / not supplied works unchanged
# ---------------------------------------------------------------------------


def test_se_reference_not_supplied_existing_rules_still_fire():
    """Old call sites that pass no reference get identical behaviour."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment("STRIPE PAYOUT BANKSCAN AI", amount=450.0)
    assert c.category == m.SE_INCOME
    assert c.is_income is True
    assert c.confidence >= 0.8


def test_se_reference_explicitly_none_works():
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        "SHELL FUEL STATION", amount=-65.0, reference=None,
    )
    assert c.category == m.SE_EXPENSE_TRAVEL


def test_se_empty_string_reference_is_treated_as_no_reference():
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        "TRAVIS PERKINS", amount=-142.50, reference="",
    )
    assert c.category == m.SE_EXPENSE_REPAIRS


# ---------------------------------------------------------------------------
# Definitive merchant wins — reference doesn't override a strong match
# ---------------------------------------------------------------------------


def test_se_strong_merchant_match_not_overridden_by_unrelated_reference():
    """Description 'Stripe payout' is definitive turnover. A reference
    saying 'Refund issued' (an English word salad) should NOT change
    the bucket — reference-only rules only fire on specific patterns."""
    from hmrc.services import mapping as m
    c = m.classify_self_employment(
        description="STRIPE PAYOUT",
        amount=450.00,
        reference="Refund issued to customer",  # not an INV/HMRC/RENT pattern
    )
    assert c.category == m.SE_INCOME


# ---------------------------------------------------------------------------
# Property — reference-only rules
# ---------------------------------------------------------------------------


def test_property_rent_reference_classifies_inbound_payment_as_rent():
    """The killer landlord case. Tenant J Smith pays in £950 with
    description 'FPI J Smith' (a personal name — would normally fail
    every rule) and reference 'RENT FEB 32 OAK LANE'."""
    from hmrc.services import mapping as m
    c = m.classify_property(
        description="FPI J Smith",
        amount=950.00,
        reference="RENT FEB 32 OAK LANE",
    )
    assert c.category == m.PROP_INCOME_RENT
    assert c.is_income is True
    assert c.confidence >= 0.9


def test_property_letting_agent_fee_reference_routes_to_services():
    from hmrc.services import mapping as m
    c = m.classify_property(
        description="DD ANY AGENCY",
        amount=-95.00,
        reference="Letting agent fee Q1",
    )
    assert c.category == m.PROP_EXPENSE_SERVICES
    assert c.confidence >= 0.9


def test_property_btl_mortgage_reference_routes_to_residential_financial():
    from hmrc.services import mapping as m
    c = m.classify_property(
        description="DD Nationwide BS",
        amount=-650.00,
        reference="BTL mortgage 32 oak lane",
    )
    assert c.category == m.PROP_EXPENSE_RESIDENTIAL_FINANCIAL
    assert c.confidence >= 0.9


def test_property_boiler_repair_reference_routes_to_repairs():
    """Description is just a person's name, but the reference
    "boiler service flat 3" is unambiguous repair."""
    from hmrc.services import mapping as m
    c = m.classify_property(
        description="FPO J Williams",
        amount=-180.00,
        reference="Boiler service flat 3",
    )
    assert c.category == m.PROP_EXPENSE_REPAIRS
    assert c.confidence >= 0.9


def test_property_back_compat_no_reference():
    """Existing call without reference: unchanged behaviour."""
    from hmrc.services import mapping as m
    c = m.classify_property("AIRBNB PAYMENTS", amount=1450.0)
    assert c.category == m.PROP_INCOME_RENT


def test_property_rent_reference_with_debit_is_downgraded():
    """'Rent FEB' reference on a DEBIT (money out) — likely the user
    paying their own rent. Mark for review, don't claim income."""
    from hmrc.services import mapping as m
    c = m.classify_property(
        description="DD Landlord",
        amount=-1100.00,
        reference="Rent FEB",  # matches property-rent-income reference rule
    )
    assert c.is_income is False
    assert c.confidence < 0.5
    # Direction-mismatch path on the reference-rule branch sets a
    # "flagged for review" reasoning.
    assert "flagged for review" in c.reasoning


# ---------------------------------------------------------------------------
# Aggregation still works when reference is in the row dicts
# ---------------------------------------------------------------------------


def test_se_aggregate_with_reference_field_in_rows():
    """The batch aggregator should accept rows with `reference` and
    classify them with that field included. Verifies the dict-input path
    (not just the function-call path) understands reference."""
    from hmrc.services import mapping as m
    rows = [
        # Vague description, strong reference -> turnover
        {"description": "FPI Acme Ltd", "amount": 1500.0,
         "reference": "INV-2026-001"},
        # No reference -> existing behaviour
        {"description": "STRIPE PAYOUT", "amount": 500.0},
        # Strong reference: HMRC tax payment -> NOT a deductible expense
        {"description": "HMRC NDDS", "amount": -300.0,
         "reference": "Self Assessment payment"},
    ]
    result = m.aggregate_self_employment(rows)
    # Both invoiced inbound payments should be turnover (£2,000 total)
    assert result["income"][m.SE_INCOME] == 2000.0
    # HMRC tax payment must NOT be in any allowable expense category
    # (it's flagged as "other" with low-ish confidence)
    assert m.SE_EXPENSE_OTHER in result["expenses"]
    assert result["expenses"][m.SE_EXPENSE_OTHER] == 300.0
