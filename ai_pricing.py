"""
BankParse — AI Pricing & Spend Caps
Single source of truth for Anthropic token prices, cost calculation,
and tier spend budgets.

When Anthropic prices change, update MODEL_PRICES below. Everything else
in the app reads from this module.
"""

import os
import logging

logger = logging.getLogger("bankparse.ai_pricing")


# ---------------------------------------------------------------------------
# Anthropic pricing (verified 2026-04-08 from platform.claude.com/docs pricing)
# Prices in USD per million tokens.
# ---------------------------------------------------------------------------

MODEL_PRICES = {
    # Claude Haiku 4.5 — current default parser model
    "claude-haiku-4-5-20251001": {
        "input_usd_per_mtok": 1.00,
        "output_usd_per_mtok": 5.00,
    },
    "claude-haiku-4-5": {
        "input_usd_per_mtok": 1.00,
        "output_usd_per_mtok": 5.00,
    },
    # Sonnet 4.6 — in case model is swapped
    "claude-sonnet-4-6": {
        "input_usd_per_mtok": 3.00,
        "output_usd_per_mtok": 15.00,
    },
    # Opus 4.6 — in case model is swapped
    "claude-opus-4-6": {
        "input_usd_per_mtok": 5.00,
        "output_usd_per_mtok": 25.00,
    },
}

# Fallback prices if an unknown model is used — use the most expensive so
# we over-estimate cost and fail-safe.
FALLBACK_INPUT_USD_PER_MTOK = 5.00
FALLBACK_OUTPUT_USD_PER_MTOK = 25.00

# USD → GBP conversion. Read from env so it can be updated without a deploy.
# Default is a conservative 0.80 (1 USD = 0.80 GBP). Over-estimating keeps
# us safe against exchange drift.
USD_TO_GBP = float(os.environ.get("USD_TO_GBP_RATE", "0.80"))


def calculate_cost_gbp(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute the GBP cost of a single Anthropic API call.

    Rounds to 6 decimal places (micro-pence) to preserve accuracy when
    summed across thousands of calls. Callers that display cost should
    round further for presentation.
    """
    prices = MODEL_PRICES.get(model)
    if prices:
        in_rate = prices["input_usd_per_mtok"]
        out_rate = prices["output_usd_per_mtok"]
    else:
        logger.warning("Unknown model %s — using fail-safe fallback pricing", model)
        in_rate = FALLBACK_INPUT_USD_PER_MTOK
        out_rate = FALLBACK_OUTPUT_USD_PER_MTOK

    cost_usd = (input_tokens / 1_000_000.0) * in_rate + (output_tokens / 1_000_000.0) * out_rate
    cost_gbp = cost_usd * USD_TO_GBP
    return round(cost_gbp, 6)


# ---------------------------------------------------------------------------
# Tier monthly AI-spend budgets (GBP)
# 40% of the subscription price converted to GBP at USD_TO_GBP rate.
# Free tier is file-count capped, not spend-capped.
#
# Subscription prices (USD, from stripe_config.py):
#   starter     $9.99   → £7.99   → 40% = £3.20
#   pro         $24.99  → £19.99  → 40% = £8.00
#   business    $59.99  → £47.99  → 40% = £19.20
#   enterprise  $149    → £119.20 → 40% = £47.68
# ---------------------------------------------------------------------------

TIER_MONTHLY_AI_BUDGET_GBP = {
    "free": 0.0,        # free tier is file-count gated, not spend gated
    "starter": 3.20,
    "pro": 8.00,
    "business": 19.20,
    "enterprise": 47.68,
}

# Free tier monthly file counts (exact limits — not budget-based)
FREE_MONTHLY_STATEMENTS = 1
FREE_MONTHLY_RECEIPTS = 1


# ---------------------------------------------------------------------------
# Global safeguards — panic brakes
# Configurable via env vars so they can be tightened without a deploy.
# ---------------------------------------------------------------------------

# Global hard ceiling: total AI spend across ALL users per UTC day.
# If exceeded, /api/parse and /api/parse-receipt return 503 until next day.
AI_DAILY_BUDGET_GBP = float(os.environ.get("AI_DAILY_BUDGET_GBP", "30.0"))

# Per-user hard ceiling: single user's AI spend per UTC day.
# Prevents a single compromised/abusive account from burning through budget
# in one day, regardless of their monthly allowance.
AI_USER_DAILY_CAP_GBP = float(os.environ.get("AI_USER_DAILY_CAP_GBP", "5.0"))


# ---------------------------------------------------------------------------
# Pre-flight cost estimates (for pre-call budget checks)
# These are intentionally pessimistic — it's better to slightly over-estimate
# pre-flight cost than allow a call that breaches a cap post-hoc.
# ---------------------------------------------------------------------------

# Worst-case per-call estimates based on observed token usage:
#   Receipt: ~1500 in + 500 out ≈ 0.3p GBP
#   Statement page: ~2000 in + 2000 out ≈ 0.9p GBP
# We add 50% headroom.
ESTIMATED_RECEIPT_COST_GBP = 0.005    # 0.5p per receipt (pessimistic)
ESTIMATED_STATEMENT_PAGE_COST_GBP = 0.015  # 1.5p per page (pessimistic)


def estimated_call_cost_gbp(mode: str, num_pages: int = 1) -> float:
    """Return a pessimistic pre-flight cost estimate.

    Used to reject calls BEFORE they happen if the estimate would breach
    the user's monthly budget, user's daily cap, or the global daily ceiling.
    """
    if mode == "receipt":
        return ESTIMATED_RECEIPT_COST_GBP * max(1, num_pages)
    if mode == "statement":
        return ESTIMATED_STATEMENT_PAGE_COST_GBP * max(1, num_pages)
    return 0.01


# ---------------------------------------------------------------------------
# Overage credit packs — pre-purchased one-time top-ups for paid users
# who've exhausted their monthly AI budget.
#
# Pricing is a flat £->credit mapping (no discount tiers for now so users can
# size up in small increments). Each pack is a Stripe one-time checkout
# (mode="payment") which credits `ai_credit_balance_gbp` on webhook.
# ---------------------------------------------------------------------------

CREDIT_PACKS = {
    "small":  {"amount_gbp": 10.0, "label": "£10 credit pack"},
    "medium": {"amount_gbp": 25.0, "label": "£25 credit pack"},
    "large":  {"amount_gbp": 50.0, "label": "£50 credit pack"},
}

# GBP pence value for Stripe (amount_gbp * 100). Stripe uses the smallest
# currency unit, so £10 → 1000.
def credit_pack_stripe_amount(pack_id: str) -> int | None:
    pack = CREDIT_PACKS.get(pack_id)
    if not pack:
        return None
    return int(round(pack["amount_gbp"] * 100))
