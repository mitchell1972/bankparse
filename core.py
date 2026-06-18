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

# Load .env for local dev. In production (Vercel/Railway) env vars come from
# the platform, so this silently no-ops if dotenv isn't installed or .env
# is missing.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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

# Cookie security flag — see security_headers.cookies_must_be_secure().
# Imported via alias so existing call sites can read it as a local lookup
# and tests can monkeypatch a single name. Defaults to True so cookies are
# Secure on any deployment that doesn't explicitly opt out (COOKIES_SECURE=0).
from security_headers import cookies_must_be_secure as _cookies_must_be_secure  # noqa: E402

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

# --- Intuit / QuickBooks Online OAuth ---
# Created in the Mitoba Consulting workspace at developer.intuit.com.
# INTUIT_ENVIRONMENT controls which Intuit base URL is used:
#   sandbox    → sandbox-quickbooks.api.intuit.com (dev / testing)
#   production → quickbooks.api.intuit.com (live customer data)
INTUIT_CLIENT_ID = os.environ.get("INTUIT_CLIENT_ID", "")
INTUIT_CLIENT_SECRET = os.environ.get("INTUIT_CLIENT_SECRET", "")
INTUIT_ENVIRONMENT = os.environ.get("INTUIT_ENVIRONMENT", "sandbox").lower()
INTUIT_REDIRECT_URI = os.environ.get(
    "INTUIT_REDIRECT_URI",
    "https://bankscanai.com/api/qbo/callback" if IS_PRODUCTION else "http://localhost:8000/api/qbo/callback",
)
# Minimum scopes for posting bank transactions / journal entries.
# `com.intuit.quickbooks.accounting` covers Accounts, BankTransaction, JournalEntry.
INTUIT_SCOPES = "com.intuit.quickbooks.accounting"
INTUIT_AVAILABLE = bool(INTUIT_CLIENT_ID and INTUIT_CLIENT_SECRET)

# --- Tier limits ---
# Free tier is file-count gated (1 statement + 1 receipt per calendar month).
# Paid tiers are SPEND-gated — each tier gets ~40% of its subscription price
# as a monthly AI budget in GBP (see ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP).
# Paid users can exceed the budget by pre-purchasing credit packs (one-time
# Stripe checkouts that top up `ai_credit_balance_gbp`), after which each
# overage parse deducts the exact Anthropic token cost.
# Free tier is now a 7-day trial from registration. The per-month file
# count limits are kept as None — gating happens via TRIAL_DAYS in
# check_can_use, not by counting files.
TRIAL_DAYS = 7

# Total bytes a user can have accumulated in their session store before they
# must hit "Clear & Upload New". Designed to be tight enough that a few
# parsed statements + receipts fit, loose enough that an honest quarterly
# workflow doesn't trip it.
SESSION_MAX_BYTES = 25 * 1024 * 1024  # 25 MB

TIER_LIMITS = {
    "free": {
        "monthly_statements": None,
        "monthly_receipts": None,
        "monthly_ai_budget_gbp": ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP["free"],
        "bulk_max_files": 10,  # free trial: 10 files per batch (overridden to 0 after trial)
        "ai_parsing": True,   # AI-only parsing everywhere
        "auto_insights": False,
        "pre_built_reports": False,
        "chat_per_day": 0,
        "trial_days": TRIAL_DAYS,
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

# --- Admin-feature accounts (bypass FEATURE gates, NOT the paywall) ---
# UNLIMITED_EMAILS bypasses the FEATURE gates: email verification, the
# free-tier file caps, the auto-enterprise-tier assignment — i.e. things
# that affect the user EXPERIENCE inside the app once they're already in.
# It does NOT bypass the paywall. The paywall is enforced via the
# separate PAYWALL_BYPASS_EMAILS set below.
_ADMIN_DEFAULTS = {"mitchell_agoma@yahoo.co.uk"}
UNLIMITED_EMAILS = _ADMIN_DEFAULTS | {
    e.strip().lower() for e in
    os.environ.get("UNLIMITED_EMAILS", "").split(",")
    if e.strip()
}

# --- Paywall bypass — HARDCODED SINGLETON, no env override ---
# ONLY this exact email may reach the dashboard without going through
# Stripe Checkout. Hardcoded on purpose: previously the paywall bypass
# read from UNLIMITED_EMAILS which is env-extendable, so if Railway had
# UNLIMITED_EMAILS="mitchell_agoma@live.co.uk,..." that account silently
# bypassed and masked what real customers see.
#
# DO NOT make this env-extendable. If you need to grant another account
# permanent paywall bypass, edit this file and re-deploy. An env var would
# defeat the point.
PAYWALL_BYPASS_EMAILS: frozenset[str] = frozenset({"mitchell_agoma@yahoo.co.uk"})

# --- Auth cookie config ---
AUTH_COOKIE = "bp_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

# --- Legacy session cookie (kept for backward compat) ---
COOKIE_NAME = "bp_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year

# --- Subscription cache ---
SUBSCRIPTION_CACHE_TTL = 3600  # 1 hour

# Subscription statuses that grant access to the app. `past_due` is included
# as a GRACE state: the card failed but Stripe is still retrying (dunning).
# Cutting a customer off the instant a payment fails churns them over a single
# expired card. The grace period is self-bounding — when Stripe gives up,
# `customer.subscription.updated`/`deleted` fires and the lifecycle webhook
# (services/billing.py::handle_subscription_lifecycle) writes the real status
# (`canceled`/`unpaid`), which is NOT in this set, so access ends automatically.
# This set MUST stay in sync across has_active_subscription / verify_subscription
# / get_user_tier — a past mismatch let past_due users reach the dashboard but
# blocked them at the parse gate as "trial_expired".
ACCESS_GRANTING_STATUSES = ("trialing", "active", "past_due")

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
        secure=_cookies_must_be_secure(),
    )
    return response


def clear_auth_cookie(response):
    """Clear the bp_auth cookie."""
    response.delete_cookie(key=AUTH_COOKIE, samesite="lax", secure=_cookies_must_be_secure())
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
        secure=_cookies_must_be_secure(),
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
        return status in ACCESS_GRANTING_STATUSES

    # Cache is stale -- check Stripe
    stripe_customer_id = user.get("stripe_customer_id")
    if not stripe_customer_id or not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        return status in ACCESS_GRANTING_STATUSES  # Fall back to cache if Stripe unavailable

    try:
        # status="all" (not "active") so we see the REAL status — a past_due or
        # trialing sub is excluded by status="active" and would be wrongly
        # collapsed to "cancelled", flipping a grace-period customer to no-access
        # after the cache TTL. We mirror Stripe's truth instead.
        subs = stripe.Subscription.list(customer=stripe_customer_id, status="all", limit=1)
        real_status = subs.data[0].status if subs.data else "canceled"
        is_active = real_status in ACCESS_GRANTING_STATUSES
        # Update cache with the actual Stripe status (not a binary active/cancelled)
        if user.get("id"):
            update_user(user["id"], subscription_status=real_status, subscription_checked_at=time.time())
        return is_active
    except Exception:
        # Stripe unreachable -- fall back to cached status (don't lock out paying users)
        return status in ACCESS_GRANTING_STATUSES


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

    # Subscriber — determine tier from Stripe price ID. status="all" (not
    # "active") so a past_due grace-period customer's price is still found —
    # status="active" would miss it and silently drop a paid Pro/Business user
    # to the "starter" fallback below (right access, wrong limits).
    stripe_customer_id = user.get("stripe_customer_id")
    if STRIPE_AVAILABLE and STRIPE_SECRET_KEY and stripe_customer_id:
        try:
            subs = stripe.Subscription.list(customer=stripe_customer_id, status="all", limit=1)
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


def _legacy_trial_days_remaining(user: dict) -> int:
    """Pre-card-on-file rule: 7 days from users.created_at.

    Only used for users with grandfathered_trial=1 (the 105 users registered
    before the Stripe-driven trial shipped). New signups use the Stripe
    subscription `trialing` status instead.
    """
    import time

    created_at = user.get("created_at")
    if not created_at:
        return TRIAL_DAYS
    elapsed_seconds = time.time() - float(created_at)
    elapsed_days = elapsed_seconds / 86400.0
    return max(TRIAL_DAYS - int(elapsed_days), 0)


def trial_days_remaining(user: dict) -> int:
    """Days left on the trial — Stripe-driven for new users, legacy for grandfathered.

    Returns 0 once the trial has ended. UNLIMITED_EMAILS admins always get
    TRIAL_DAYS (their gate bypass happens elsewhere; this is just a display
    value).
    """
    import time

    if user.get("grandfathered_trial"):
        return _legacy_trial_days_remaining(user)

    # Stripe-driven trial: trialing status + trial_end_at in the future.
    if user.get("subscription_status") == "trialing":
        end = user.get("trial_end_at")
        if end and float(end) > time.time():
            remaining_seconds = float(end) - time.time()
            return max(int(remaining_seconds / 86400.0) + 1, 1)
        return 0

    # Active subscribers don't have a meaningful "trial days remaining" — return 0,
    # the UI distinguishes via subscription_status.
    return 0


def is_trial_active(user: dict) -> bool:
    """True if the user is within their Stripe-backed free trial window.

    Requires THREE conditions, all of which must hold:
      - subscription_status == 'trialing'
      - trial_end_at in the future
      - stripe_subscription_id is non-empty (proves the user actually
        completed Stripe Checkout — a bare 'trialing' status without an
        attached Stripe subscription is a stale orphan record and must
        NOT grant access. This was the live.co.uk production bug.)

    Grandfathered users no longer get free access — they go through
    Stripe Checkout like everyone else.
    """
    import time

    if user.get("subscription_status") != "trialing":
        return False
    if not user.get("stripe_subscription_id"):
        return False
    end = user.get("trial_end_at")
    return bool(end and float(end) > time.time())


def has_active_subscription(user: dict) -> bool:
    """True if the user has a real Stripe subscription in a paywall-bypassing
    state. Requires BOTH a valid subscription_status AND a Stripe
    subscription_id — a bare status without the id is a stale orphan that
    must not grant access (live.co.uk production bug)."""
    if user.get("subscription_status") not in ("trialing", "active", "past_due"):
        return False
    return bool(user.get("stripe_subscription_id"))


def check_can_use(user: dict, mode: str, num_pages: int = 1) -> tuple[bool, str, str, float]:
    """Pre-flight gate for an AI parse request.

    Checks, in order:
      1. Email verification (required for any AI usage)
      2. Global daily budget ceiling (all users, panic brake)
      3. Per-user daily cap (panic brake for single abusive account)
      4. Free tier: 7-day trial from registration
      5. Paid-tier monthly spend budget — if exhausted, credit balance must
         cover the pre-flight estimate.

    ``mode`` is ``'statement'`` or ``'receipt'``. ``num_pages`` is the
    estimated number of pages for this request (pessimistic: use the actual
    PDF page count for statements, 1 for receipts).

    Returns ``(allowed, tier, reason, estimated_cost_gbp)``:
        - ``allowed`` — bool, True if the request should proceed
        - ``tier`` — the user's tier ('free'/'starter'/.../'enterprise')
        - ``reason`` — short machine-readable code; 'ok' if allowed, otherwise
          one of: 'email_unverified', 'trial_expired',
          'monthly_budget_exhausted', 'user_daily_cap', 'global_daily_cap'
        - ``estimated_cost_gbp`` — the pre-flight (pessimistic) cost for
          this specific call
    """
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

    # 4. Free tier: 7-day Stripe trial required.
    #
    # Only PAYWALL_BYPASS_EMAILS (hardcoded yahoo founder) skips the gate
    # entirely. Every other user — including grandfathered legacy accounts
    # and UNLIMITED_EMAILS-only admins — must complete Stripe Checkout to
    # enter `trialing` status. Without a `stripe_subscription_id` →
    # 'payment_method_required' so the UI can show the card-on-file CTA.
    # With a sub but trial window over → 'trial_expired'.
    if tier == "free":
        if email in PAYWALL_BYPASS_EMAILS:
            return True, tier, "ok", estimated_cost
        if is_trial_active(user):
            return True, tier, "ok", estimated_cost
        if not user.get("stripe_subscription_id"):
            return False, tier, "payment_method_required", estimated_cost
        return False, tier, "trial_expired", estimated_cost

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
    "trial_expired": "TRIAL_EXPIRED",
    "payment_method_required": "PAYMENT_METHOD_REQUIRED",
    # Kept for backward compat with any client JS that branches on the old codes
    "free_statements_cap": "FREE_LIMIT_REACHED",
    "free_receipts_cap": "FREE_LIMIT_REACHED",
    "monthly_budget_exhausted": "MONTHLY_BUDGET_EXHAUSTED",
    "user_daily_cap": "DAILY_CAP_REACHED",
    "global_daily_cap": "SERVICE_BUSY_TRY_TOMORROW",
}
