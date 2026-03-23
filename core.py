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
)

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
STRIPE_STARTER_PRICE_ID = os.environ.get("STRIPE_STARTER_PRICE_ID", "")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "")
STRIPE_BUSINESS_PRICE_ID = os.environ.get("STRIPE_BUSINESS_PRICE_ID", "")
STRIPE_ENTERPRISE_PRICE_ID = os.environ.get("STRIPE_ENTERPRISE_PRICE_ID", "")

# Initialize Stripe
if STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# --- Tier limits ---
TIER_LIMITS = {
    "free": {
        "monthly_scans": 2,  # 1 statement + 1 receipt
        "bulk_max_files": 0,
        "ai_parsing": False,
        "auto_insights": False,
        "pre_built_reports": False,
        "chat_per_day": 0,
    },
    "starter": {
        "monthly_scans": 100,
        "bulk_max_files": 5,
        "ai_parsing": True,
        "auto_insights": True,
        "pre_built_reports": True,
        "chat_per_day": 0,
    },
    "pro": {
        "monthly_scans": 500,
        "bulk_max_files": 20,
        "ai_parsing": True,
        "auto_insights": True,
        "pre_built_reports": True,
        "chat_per_day": 0,
    },
    "business": {
        "monthly_scans": 2000,
        "bulk_max_files": 50,
        "ai_parsing": True,
        "auto_insights": True,
        "pre_built_reports": True,
        "chat_per_day": 50,
    },
    "enterprise": {
        "monthly_scans": None,  # unlimited
        "bulk_max_files": 100,
        "ai_parsing": True,
        "auto_insights": True,
        "pre_built_reports": True,
        "chat_per_day": None,  # unlimited
    },
}

# Legacy constants (kept for backward compat in case external code references them)
FREE_STATEMENT_LIMIT = 1
FREE_RECEIPT_LIMIT = 1

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


def check_can_use(user: dict, mode: str) -> tuple[bool, str]:
    """Check if user can use the service based on monthly scan limits.

    Returns (allowed, tier).
    """
    tier = get_user_tier(user)
    limits = TIER_LIMITS[tier]

    monthly_limit = limits["monthly_scans"]
    if monthly_limit is not None:
        scans_used = get_monthly_scans(user["id"])
        if scans_used >= monthly_limit:
            return False, tier

    return True, tier
