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

# --- Stripe keys ---
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "")
STRIPE_BUSINESS_PRICE_ID = os.environ.get("STRIPE_BUSINESS_PRICE_ID", "")

# Initialize Stripe
if STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# --- Free tier limits ---
FREE_STATEMENT_LIMIT = 1
FREE_RECEIPT_LIMIT = 1

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


def check_can_use(user: dict, mode: str) -> tuple[bool, bool]:
    """Check if user can use the service. Returns (allowed, is_subscriber)."""
    if user.get("stripe_customer_id"):
        if verify_subscription(user):
            return True, True

    if mode == "statement" and user.get("statements_used", 0) >= FREE_STATEMENT_LIMIT:
        return False, False
    if mode == "receipt" and user.get("receipts_used", 0) >= FREE_RECEIPT_LIMIT:
        return False, False

    return True, False
