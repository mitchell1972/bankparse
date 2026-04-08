"""
BankParse — Shared Core Logic
All constants, auth helpers, session helpers, subscription verification,
and Stripe initialization extracted from app.py and api/index.py.
"""

import os
import secrets
import time

import bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from fastapi import Request
from fastapi.responses import JSONResponse

from database import (
    get_user_by_id, update_user,
    get_monthly_scans, increment_monthly_scans,
    get_monthly_ai_spend, get_user_today_spend, get_global_daily_ai_spend,
    get_credit_balance, is_email_verified,
)
import ai_pricing

# --- Optional Stripe import ---
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False

# --- Environment ---
IS_PRODUCTION = os.environ.get("ENVIRONMENT", "development") == "production"

# --- Secret key for auth token signing ---
SECRET_KEY = os.environ.get("SECRET_KEY", "bankparse-dev-secret-change-me")

# --- Anthropic API key (AI-powered parsing) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# --- Stripe keys ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_STARTER_PRICE_ID = os.environ.get("STRIPE_STARTER_PRICE_ID", "price_1THsjkLniIk7TL9BZuCd5LZ0")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "price_1THsjmLniIk7TL9BkgiXW5c3")
STRIPE_BUSINESS_PRICE_ID = os.environ.get("STRIPE_BUSINESS_PRICE_ID", "price_1THsjnLniIk7TL9Bh0dDiHL5")
STRIPE_ENTERPRISE_PRICE_ID = os.environ.get("STRIPE_ENTERPRISE_PRICE_ID", "price_1THsjoLniIk7TL9BZ3GEHfUu")

# Initialize Stripe
if STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# --- Tier limits ---
# Free tier is file-count gated (1 statement + 1 receipt per calendar month).
# Paid tiers are SPEND-gated — each tier gets ~40% of its subscription price
# as a monthly AI budget in GBP (see ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP).
# Paid users can exceed the budget by pre-purchasing credit packs (one-time
# Stripe checkouts that top up `ai_credit_balance_gbp`), after which each
# overage parse deducts the exact Anthropic token cost.
TIER_LIMITS = {
    "free": {
        "monthly_statements": ai_pricing.FREE_MONTHLY_STATEMENTS,
        "monthly_receipts": ai_pricing.FREE_MONTHLY_RECEIPTS,
        "monthly_ai_budget_gbp": ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP["free"],
        "bulk_max_files": 0,
        "ai_parsing": True,   # AI-only parsing everywhere — even the 1 free use is AI
        "auto_insights": False,
        "pre_built_reports": False,
        "chat_per_day": 0,
    },
    "starter": {
        "monthly_statements": None,  # no file-count cap — spend-capped instead
        "monthly_receipts": None,
        "monthly_ai_budget_gbp": ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP["starter"],
        "bulk_max_files": 5,
        "ai_parsing": True,
        "auto_insights": True,
        "pre_built_reports": True,
        "chat_per_day": 0,
    },
    "pro": {
        "monthly_statements": None,
        "monthly_receipts": None,
        "monthly_ai_budget_gbp": ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP["pro"],
        "bulk_max_files": 20,
        "ai_parsing": True,
        "auto_insights": True,
        "pre_built_reports": True,
        "chat_per_day": 0,
    },
    "business": {
        "monthly_statements": None,
        "monthly_receipts": None,
        "monthly_ai_budget_gbp": ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP["business"],
        "bulk_max_files": 50,
        "ai_parsing": True,
        "auto_insights": True,
        "pre_built_reports": True,
        "chat_per_day": 50,
    },
    "enterprise": {
        "monthly_statements": None,
        "monthly_receipts": None,
        "monthly_ai_budget_gbp": ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP["enterprise"],
        "bulk_max_files": 100,
        "ai_parsing": True,
        "auto_insights": True,
        "pre_built_reports": True,
        "chat_per_day": None,        # unlimited
    },
}

# Legacy constants (kept for backward compat in case external code references them)
FREE_STATEMENT_LIMIT = ai_pricing.FREE_MONTHLY_STATEMENTS
FREE_RECEIPT_LIMIT = ai_pricing.FREE_MONTHLY_RECEIPTS

# --- Test/admin accounts (bypass free tier limits) ---
UNLIMITED_EMAILS = set(
    e.strip().lower() for e in
    os.environ.get("UNLIMITED_EMAILS", "mitchell_agoma@yahoo.co.uk").split(",")
    if e.strip()
)

# --- Auth cookie config ---
AUTH_COOKIE = "bp_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# --- Legacy session cookie (kept for backward compat) ---
COOKIE_NAME = "bp_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year

# --- Subscription cache ---
SUBSCRIPTION_CACHE_TTL = 3600  # 1 hour

# --- File type constants ---
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp", ".heic", ".heif"]
RECEIPT_EXTENSIONS = [".pdf"] + IMAGE_EXTENSIONS


# ==========================================================================
# Auth helpers
# ==========================================================================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False


def get_serializer():
    return URLSafeTimedSerializer(SECRET_KEY)


def make_auth_token(user_id: int) -> str:
    s = get_serializer()
    return s.dumps({"uid": user_id})


def verify_auth_token(token: str) -> int | None:
    s = get_serializer()
    try:
        data = s.loads(token, max_age=60 * 60 * 24 * 30)  # 30 days
        return data.get("uid")
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request) -> dict | None:
    """Read bp_auth cookie, verify token, look up user in DB."""
    token = request.cookies.get(AUTH_COOKIE, "")
    if not token:
        return None
    user_id = verify_auth_token(token)
    if user_id is None:
        return None
    return get_user_by_id(user_id)


def set_auth_cookie(response, user_id: int):
    token = make_auth_token(user_id)
    response.set_cookie(
        key=AUTH_COOKIE,
        value=token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
    )
    return response


def clear_auth_cookie(response):
    """Clear the bp_auth cookie."""
    response.delete_cookie(key=AUTH_COOKIE, samesite="lax", secure=IS_PRODUCTION)
    return response


# ==========================================================================
# Session helpers (legacy cookie-based sessions)
# ==========================================================================

def get_session_id(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME, "")


def ensure_session(request: Request) -> str:
    sid = get_session_id(request)
    if sid:
        return sid
    return f"bp_{secrets.token_urlsafe(24)}"


def set_session_cookie(response: JSONResponse, session_id: str) -> JSONResponse:
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
    )
    return response


# ==========================================================================
# Subscription verification
# ==========================================================================

def verify_subscription(user: dict) -> bool:
    """Check subscription using cached status first, falling back to Stripe API."""
    status = user.get("subscription_status")
    checked_at = user.get("subscription_checked_at") or 0

    # Use cached status if fresh enough
    if time.time() - checked_at < SUBSCRIPTION_CACHE_TTL:
        return status in ("active", "trialing")

    # Cache is stale -- check Stripe
    stripe_customer_id = user.get("stripe_customer_id")
    if not stripe_customer_id or not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        return status in ("active", "trialing")  # Fall back to cache if Stripe unavailable

    try:
        subs = stripe.Subscription.list(customer=stripe_customer_id, status="active", limit=1)
        is_active = len(subs.data) > 0
        new_status = "active" if is_active else "cancelled"
        # Update cache
        if user.get("id"):
            update_user(user["id"], subscription_status=new_status, subscription_checked_at=time.time())
        return is_active
    except Exception:
        # Stripe unreachable -- fall back to cached status (don't lock out paying users)
        return status in ("active", "trialing")


def get_user_tier(user: dict) -> str:
    """Determine user's subscription tier.

    Tiers: 'free', 'starter', 'pro', 'business', 'enterprise'.

    Logic:
      - UNLIMITED_EMAILS always get 'enterprise'
      - Active subscribers: check Stripe price ID to determine tier
      - Legacy subscribers with unrecognised price IDs: map to 'starter'
      - Everyone else: 'free'
    """
    email = (user.get("email") or "").lower()
    if email in UNLIMITED_EMAILS:
        return "enterprise"

    if not user.get("stripe_customer_id"):
        return "free"

    if not verify_subscription(user):
        return "free"

    # Subscriber — determine tier from Stripe price ID
    stripe_customer_id = user.get("stripe_customer_id")
    if STRIPE_AVAILABLE and STRIPE_SECRET_KEY and stripe_customer_id:
        try:
            subs = stripe.Subscription.list(customer=stripe_customer_id, status="active", limit=1)
            if subs.data:
                sub = subs.data[0]
                try:
                    price_id = sub["items"]["data"][0]["price"]["id"]
                except (KeyError, IndexError, TypeError):
                    price_id = ""

                if price_id == STRIPE_ENTERPRISE_PRICE_ID and STRIPE_ENTERPRISE_PRICE_ID:
                    return "enterprise"
                if price_id == STRIPE_BUSINESS_PRICE_ID and STRIPE_BUSINESS_PRICE_ID:
                    return "business"
                if price_id == STRIPE_PRO_PRICE_ID and STRIPE_PRO_PRICE_ID:
                    return "pro"
                if price_id == STRIPE_STARTER_PRICE_ID and STRIPE_STARTER_PRICE_ID:
                    return "starter"

                # Unrecognised price ID (e.g. legacy "pro" at old £9.99 price) -> map to starter
                return "starter"
        except Exception:
            # Stripe unreachable — default active subscribers to starter (don't lock out paying users)
            return "starter"

    # Fallback: active subscription but can't determine tier
    return "starter"


def check_can_use(user: dict, mode: str, num_pages: int = 1) -> tuple[bool, str, str, float]:
    """Pre-flight gate for an AI parse request.

    Checks, in order:
      1. Email verification (required for any AI usage)
      2. Global daily budget ceiling (all users, panic brake)
      3. Per-user daily cap (panic brake for single abusive account)
      4. Free-tier file-count cap (statements/receipts this month)
      5. Paid-tier monthly spend budget — if exhausted, credit balance must
         cover the pre-flight estimate.

    ``mode`` is ``'statement'`` or ``'receipt'``. ``num_pages`` is the
    estimated number of pages for this request (pessimistic: use the actual
    PDF page count for statements, 1 for receipts).

    Returns ``(allowed, tier, reason, estimated_cost_gbp)``:
        - ``allowed`` — bool, True if the request should proceed
        - ``tier`` — the user's tier ('free'/'starter'/.../'enterprise')
        - ``reason`` — short machine-readable code; 'ok' if allowed, otherwise
          one of: 'email_unverified', 'free_statements_cap',
          'free_receipts_cap', 'monthly_budget_exhausted', 'user_daily_cap',
          'global_daily_cap'
        - ``estimated_cost_gbp`` — the pre-flight (pessimistic) cost for
          this specific call
    """
    from database import get_monthly_statements, get_monthly_receipts

    tier = get_user_tier(user)
    limits = TIER_LIMITS[tier]
    user_id = user["id"]

    estimated_cost = ai_pricing.estimated_call_cost_gbp(mode, num_pages)

    # 1. Email verification — applies to EVERY tier including free.
    #    UNLIMITED_EMAILS (admin) bypass this.
    email = (user.get("email") or "").lower()
    if email not in UNLIMITED_EMAILS:
        if not is_email_verified(user_id):
            return False, tier, "email_unverified", estimated_cost

    # 2. Global daily ceiling — hard fail across the whole service.
    global_today = get_global_daily_ai_spend()
    if global_today + estimated_cost > ai_pricing.AI_DAILY_BUDGET_GBP:
        return False, tier, "global_daily_cap", estimated_cost

    # 3. Per-user daily cap — single compromised/abusive account.
    user_today = get_user_today_spend(user_id)
    if user_today + estimated_cost > ai_pricing.AI_USER_DAILY_CAP_GBP:
        return False, tier, "user_daily_cap", estimated_cost

    # 4. Free tier: file-count cap (1 statement + 1 receipt per month).
    #    Spend budget is £0 so the paid-tier check would always fail; we
    #    short-circuit here for a better UX error code.
    if tier == "free":
        if mode == "statement":
            if get_monthly_statements(user_id) >= ai_pricing.FREE_MONTHLY_STATEMENTS:
                return False, tier, "free_statements_cap", estimated_cost
        elif mode == "receipt":
            if get_monthly_receipts(user_id) >= ai_pricing.FREE_MONTHLY_RECEIPTS:
                return False, tier, "free_receipts_cap", estimated_cost
        return True, tier, "ok", estimated_cost

    # 5. Paid tier: spend-based budget check.
    #    If monthly budget is exhausted, the user's credit balance must cover
    #    the pre-flight estimate.
    monthly_spend = get_monthly_ai_spend(user_id)
    budget = limits["monthly_ai_budget_gbp"] or 0.0

    if monthly_spend + estimated_cost <= budget:
        return True, tier, "ok", estimated_cost

    # Over budget — fall through to credit balance
    credit_balance = get_credit_balance(user_id)
    if credit_balance >= estimated_cost:
        return True, tier, "ok", estimated_cost

    return False, tier, "monthly_budget_exhausted", estimated_cost


def record_ai_spend(
    user_id: int,
    mode: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    success: bool = True,
) -> dict:
    """Record a completed AI call's exact cost against the user.

    Writes to ``ai_usage_log`` and updates the running monthly spend. If the
    user's monthly budget is already exhausted the cost is deducted from
    their credit balance instead (so the budget never "over-charges").

    Returns a dict describing what happened::

        {
            "cost_gbp": 0.0042,
            "billed_to": "budget" | "credit",
            "input_tokens": 1234,
            "output_tokens": 567,
        }
    """
    from database import log_ai_usage, add_to_monthly_ai_spend, deduct_credit_balance

    cost_gbp = ai_pricing.calculate_cost_gbp(model, input_tokens, output_tokens)
    # Always log the call — the log is the source of truth for audit.
    log_ai_usage(
        user_id=user_id,
        mode=mode,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_gbp=cost_gbp,
        success=success,
    )

    if user_id is None:
        return {"cost_gbp": cost_gbp, "billed_to": "none",
                "input_tokens": input_tokens, "output_tokens": output_tokens}

    # Decide where to bill: running monthly budget first, credit balance
    # only when the budget would be exceeded. We compute this after the
    # call because only here do we know the exact cost.
    user = get_user_by_id(user_id)
    if not user:
        return {"cost_gbp": cost_gbp, "billed_to": "none",
                "input_tokens": input_tokens, "output_tokens": output_tokens}

    tier = get_user_tier(user)
    limits = TIER_LIMITS[tier]
    budget = limits["monthly_ai_budget_gbp"] or 0.0
    monthly_spend = get_monthly_ai_spend(user_id)

    billed_to = "budget"
    if tier == "free":
        # Free tier has no budget to deduct from — cost is recorded in the
        # log only; the file-count cap is what gates usage.
        billed_to = "free_cap"
    elif monthly_spend + cost_gbp <= budget:
        add_to_monthly_ai_spend(user_id, cost_gbp)
        billed_to = "budget"
    else:
        # Budget exceeded → try credit balance. Any shortfall is absorbed by
        # the budget column (we don't want a failed deduction to leave the
        # call un-accounted).
        if deduct_credit_balance(user_id, cost_gbp):
            billed_to = "credit"
        else:
            # Should not happen because check_can_use gated this, but fall
            # back to the running budget so the call is recorded somewhere.
            add_to_monthly_ai_spend(user_id, cost_gbp)
            billed_to = "budget_overflow"

    return {
        "cost_gbp": cost_gbp,
        "billed_to": billed_to,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


# Machine-readable reason codes returned to the frontend. These keys live
# forever in the client JS — don't rename casually.
QUOTA_REASON_MESSAGES = {
    "ok": "OK",
    "email_unverified": "EMAIL_VERIFICATION_REQUIRED",
    "free_statements_cap": "FREE_LIMIT_REACHED",
    "free_receipts_cap": "FREE_LIMIT_REACHED",
    "monthly_budget_exhausted": "MONTHLY_BUDGET_EXHAUSTED",
    "user_daily_cap": "DAILY_CAP_REACHED",
    "global_daily_cap": "SERVICE_BUSY_TRY_TOMORROW",
}
