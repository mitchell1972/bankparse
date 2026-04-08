"""
BankParse -- AI pricing unit tests

Pure-function tests for ai_pricing.py: cost calculation from real
Anthropic token counts, pessimistic pre-flight estimates, credit-pack
lookups, and the global daily / per-user daily ceilings.

These must pass before every deploy -- a bug here directly affects how
much money we can lose to AI spend.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_pricing


# ---------------------------------------------------------------------------
# calculate_cost_gbp
# ---------------------------------------------------------------------------

HAIKU = "claude-haiku-4-5-20251001"


def test_calculate_cost_zero_tokens_is_zero():
    """No tokens consumed => £0.00 cost."""
    assert ai_pricing.calculate_cost_gbp(HAIKU, 0, 0) == 0.0


def test_calculate_cost_haiku_exact_math():
    """Claude Haiku 4.5: $1/MTok in, $5/MTok out, USD->GBP 0.80 default.

    1,000,000 input + 1,000,000 output = $1 + $5 = $6 * 0.80 = £4.80
    """
    # Force the conversion rate so the test is deterministic regardless of env.
    original_rate = ai_pricing.USD_TO_GBP
    try:
        ai_pricing.USD_TO_GBP = 0.80
        cost = ai_pricing.calculate_cost_gbp(HAIKU, 1_000_000, 1_000_000)
        assert cost == pytest.approx(4.80, abs=1e-6)
    finally:
        ai_pricing.USD_TO_GBP = original_rate


def test_calculate_cost_realistic_receipt_pennies():
    """Realistic receipt call (~1500 in, ~500 out) should be ~0.3p GBP."""
    original_rate = ai_pricing.USD_TO_GBP
    try:
        ai_pricing.USD_TO_GBP = 0.80
        cost = ai_pricing.calculate_cost_gbp(HAIKU, 1500, 500)
        # 1500 * 1/1e6 + 500 * 5/1e6 = 0.0015 + 0.0025 = $0.004 USD
        # 0.004 * 0.80 = £0.0032 (0.32p)
        assert cost == pytest.approx(0.0032, abs=1e-6)
    finally:
        ai_pricing.USD_TO_GBP = original_rate


def test_calculate_cost_unknown_model_uses_failsafe_pricing():
    """Unknown model must fall back to the MOST expensive prices so we
    overestimate rather than underestimate spend."""
    original_rate = ai_pricing.USD_TO_GBP
    try:
        ai_pricing.USD_TO_GBP = 0.80
        cost_unknown = ai_pricing.calculate_cost_gbp("some-new-model-2027", 1_000_000, 1_000_000)
        cost_opus = ai_pricing.calculate_cost_gbp("claude-opus-4-6", 1_000_000, 1_000_000)
        assert cost_unknown == cost_opus
        # And confirm it's actually more expensive than Haiku
        cost_haiku = ai_pricing.calculate_cost_gbp(HAIKU, 1_000_000, 1_000_000)
        assert cost_unknown > cost_haiku
    finally:
        ai_pricing.USD_TO_GBP = original_rate


def test_calculate_cost_opus_is_more_expensive_than_haiku():
    """Opus should be strictly more expensive than Haiku for identical token counts."""
    haiku = ai_pricing.calculate_cost_gbp(HAIKU, 10_000, 10_000)
    sonnet = ai_pricing.calculate_cost_gbp("claude-sonnet-4-6", 10_000, 10_000)
    opus = ai_pricing.calculate_cost_gbp("claude-opus-4-6", 10_000, 10_000)
    assert haiku < sonnet < opus


def test_calculate_cost_rounding_preserves_microprecision():
    """Cost is rounded to 6dp so thousands of calls still sum accurately."""
    cost = ai_pricing.calculate_cost_gbp(HAIKU, 1, 1)
    # This is a tiny number -- the key invariant is it's not truncated to 0.
    assert cost > 0
    assert isinstance(cost, float)


# ---------------------------------------------------------------------------
# estimated_call_cost_gbp (pre-flight pessimism)
# ---------------------------------------------------------------------------

def test_estimated_receipt_is_pessimistic_vs_realistic():
    """Estimated receipt cost should exceed a realistic measured receipt cost."""
    original_rate = ai_pricing.USD_TO_GBP
    try:
        ai_pricing.USD_TO_GBP = 0.80
        realistic = ai_pricing.calculate_cost_gbp(HAIKU, 1500, 500)
        estimated = ai_pricing.estimated_call_cost_gbp("receipt")
        assert estimated > realistic
    finally:
        ai_pricing.USD_TO_GBP = original_rate


def test_estimated_statement_scales_with_page_count():
    """Estimated statement cost should scale linearly with num_pages."""
    one = ai_pricing.estimated_call_cost_gbp("statement", num_pages=1)
    five = ai_pricing.estimated_call_cost_gbp("statement", num_pages=5)
    ten = ai_pricing.estimated_call_cost_gbp("statement", num_pages=10)
    assert five == pytest.approx(5 * one, abs=1e-9)
    assert ten == pytest.approx(10 * one, abs=1e-9)


def test_estimated_cost_zero_pages_treated_as_one():
    """num_pages=0 must not zero out the estimate (would bypass pre-flight)."""
    zero_pages = ai_pricing.estimated_call_cost_gbp("statement", num_pages=0)
    one_page = ai_pricing.estimated_call_cost_gbp("statement", num_pages=1)
    assert zero_pages == one_page
    assert zero_pages > 0


def test_estimated_cost_unknown_mode_has_nonzero_fallback():
    """Unknown modes must return > 0 so callers can't bypass the cap."""
    cost = ai_pricing.estimated_call_cost_gbp("unknown_mode")
    assert cost > 0


# ---------------------------------------------------------------------------
# Tier budgets
# ---------------------------------------------------------------------------

def test_tier_budgets_are_40_percent_of_price_order():
    """Budgets are ordered free < starter < pro < business < enterprise."""
    b = ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP
    assert b["free"] == 0.0
    assert b["free"] < b["starter"] < b["pro"] < b["business"] < b["enterprise"]


def test_tier_budgets_exact_values():
    """Pinned tier budgets (40% of subscription price converted to GBP).

    These are load-bearing — changing them changes how much AI the user gets.
    """
    b = ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP
    assert b["starter"] == 3.20
    assert b["pro"] == 8.00
    assert b["business"] == 19.20
    assert b["enterprise"] == 47.68


def test_free_tier_file_caps_are_one_of_each():
    assert ai_pricing.FREE_MONTHLY_STATEMENTS == 1
    assert ai_pricing.FREE_MONTHLY_RECEIPTS == 1


# ---------------------------------------------------------------------------
# Global safeguards
# ---------------------------------------------------------------------------

def test_global_daily_budget_is_nonzero():
    """A zero or missing global ceiling would mean no panic brake at all."""
    assert ai_pricing.AI_DAILY_BUDGET_GBP > 0


def test_user_daily_cap_is_nonzero_and_lower_than_global():
    """Per-user cap must be strictly less than the global ceiling."""
    assert ai_pricing.AI_USER_DAILY_CAP_GBP > 0
    assert ai_pricing.AI_USER_DAILY_CAP_GBP < ai_pricing.AI_DAILY_BUDGET_GBP


# ---------------------------------------------------------------------------
# Credit packs
# ---------------------------------------------------------------------------

def test_credit_pack_amounts_in_pence_for_stripe():
    """Stripe expects the smallest currency unit (pence for GBP)."""
    assert ai_pricing.credit_pack_stripe_amount("small") == 1000   # £10
    assert ai_pricing.credit_pack_stripe_amount("medium") == 2500  # £25
    assert ai_pricing.credit_pack_stripe_amount("large") == 5000   # £50


def test_credit_pack_unknown_returns_none():
    """Unknown pack IDs must return None so the checkout endpoint 404s."""
    assert ai_pricing.credit_pack_stripe_amount("huge") is None
    assert ai_pricing.credit_pack_stripe_amount("") is None


def test_credit_pack_labels_exist():
    """Each pack must have a human-readable label for the /credits page."""
    for pack_id, pack in ai_pricing.CREDIT_PACKS.items():
        assert "label" in pack
        assert "amount_gbp" in pack
        assert pack["amount_gbp"] > 0
        assert isinstance(pack["label"], str)
        assert pack["label"]
