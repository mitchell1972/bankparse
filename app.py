"""
BankParse — FastAPI Application
Bank statement and receipt to spreadsheet converter with Stripe billing.
Identity: email/password auth with cookie-based sessions.
Storage: SQLite database. Email verification via OTP for subscription restore.
"""

import os
import re
import uuid
import asyncio
import logging
import sqlite3
import time
import json
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from parsers.csv_parser import parse_csv
from parsers.xlsx_exporter import export_to_xlsx, export_receipt_to_xlsx, export_bulk_receipts_to_xlsx, export_bulk_statements_to_xlsx
from parsers.ai_parser import parse_receipt_ai, parse_receipts_bulk, parse_statement_ai, parse_statements_bulk
from database import (
    get_usage, save_usage, increment_usage,
    store_otp, verify_otp, cleanup_expired_otps,
    track_output_file, get_stale_output_files, remove_output_file_record,
    create_user, get_user_by_email, get_user_by_id, update_user, delete_user, increment_user_usage,
    get_user_by_stripe_customer,
    get_chat_usage, increment_chat_usage,
    get_monthly_scans, increment_monthly_scans,
    get_monthly_statements, increment_monthly_statements,
    get_monthly_receipts, increment_monthly_receipts,
    get_credit_balance, mark_email_verified, is_email_verified,
    save_extracted_data, get_user_extracted_files, get_user_extracted_rows,
    get_user_extracted_summary, clear_user_extracted_data,
    get_user_extracted_total_bytes,
    find_users_due_trial_reminder, mark_trial_reminder_sent,
    _fetchall_dicts,
    list_user_accountant_pack_shares, revoke_accountant_pack_share,
)
# Module alias so endpoint code can reference `database.<helper>` without
# adding every new helper to the top-level import block. Tests + intra-app
# code both work.
import database
from otp import generate_otp, send_otp_email, send_trial_reminder_email
from seo_pages import SEO_PAGES

from core import (
    # Constants
    SECRET_KEY, IS_PRODUCTION,
    ANTHROPIC_API_KEY,
    STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET,
    STRIPE_STARTER_PRICE_ID, STRIPE_PRO_PRICE_ID, STRIPE_BUSINESS_PRICE_ID, STRIPE_ENTERPRISE_PRICE_ID,
    STRIPE_AVAILABLE,
    FREE_STATEMENT_LIMIT, FREE_RECEIPT_LIMIT,
    TIER_LIMITS,
    AUTH_COOKIE, AUTH_COOKIE_MAX_AGE,
    COOKIE_NAME, COOKIE_MAX_AGE,
    PAYWALL_BYPASS_EMAILS, SUBSCRIPTION_CACHE_TTL, UNLIMITED_EMAILS,
    IMAGE_EXTENSIONS, RECEIPT_EXTENSIONS,
    # Auth helpers
    hash_password, verify_password,
    make_auth_token, verify_auth_token,
    get_current_user, set_auth_cookie, clear_auth_cookie,
    # Session helpers
    get_session_id, ensure_session, set_session_cookie,
    # Subscription helpers
    verify_subscription, check_can_use, get_user_tier,
    record_ai_spend, QUOTA_REASON_MESSAGES,
    # Trial helpers
    trial_days_remaining, is_trial_active, has_active_subscription,
    # Session cap
    SESSION_MAX_BYTES,
)

# Re-import stripe if available (needed for direct Stripe API calls in routes)
try:
    import stripe
except ImportError:
    pass

logger = logging.getLogger("bankparse")

# --- Configuration ---
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

if IS_PRODUCTION and SECRET_KEY == "bankparse-dev-secret-change-me":
    raise RuntimeError("FATAL: SECRET_KEY must be set to a secure random value in production. Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")

ALLOWED_ORIGINS_REDIRECT = os.environ.get("ALLOWED_REDIRECT_ORIGINS", "https://bankscanai.com,http://localhost:8000").split(",")

def get_safe_origin(request: Request) -> str:
    origin = request.headers.get("origin", "")
    for allowed in ALLOWED_ORIGINS_REDIRECT:
        if origin == allowed.strip():
            return origin
    return ALLOWED_ORIGINS_REDIRECT[0].strip()

# Output file max age (1 hour)
OUTPUT_MAX_AGE = 3600


async def cleanup_output_files():
    """Periodically clean up stale output files and expired OTPs."""
    while True:
        try:
            stale = get_stale_output_files(OUTPUT_MAX_AGE)
            for filename in stale:
                filepath = OUTPUT_DIR / filename
                if filepath.exists():
                    filepath.unlink()
                remove_output_file_record(filename)
            if stale:
                logger.info("Cleaned up %d stale output files", len(stale))
            cleanup_expired_otps()
        except Exception:
            logger.exception("Error during cleanup")
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(cleanup_output_files())
    yield
    task.cancel()


# Initialise Sentry BEFORE the FastAPI app is constructed so the
# integrations can hook into it. No-op without SENTRY_DSN — see
# hmrc/services/monitoring.py.
from hmrc.services import monitoring as _monitoring  # noqa: E402
_monitoring.init_sentry()

# App
app = FastAPI(title="BankScan AI", version="2.3.0", lifespan=lifespan)

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type", "x-csrf-token"],
)

from csrf import CSRFMiddleware
app.add_middleware(CSRFMiddleware)

# Security headers — HSTS, CSP, X-Content-Type-Options, X-Frame-Options,
# Referrer-Policy, Permissions-Policy, COOP, CORP. Added after CSRF so the
# CSRF middleware's 403 response also gets the headers. See security_headers.py
# for the full policy + a justification for each directive.
from security_headers import SecurityHeadersMiddleware
app.add_middleware(SecurityHeadersMiddleware)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return PlainTextResponse("Rate limit exceeded. Please try again later.", status_code=429)


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/downloads", StaticFiles(directory=str(OUTPUT_DIR)), name="downloads")

# Serve /static/* (HMRC fraud-collect.js etc.) from the repo `static/` dir.
_STATIC_DIR = BASE_DIR / "static"
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# HMRC MTD ITSA integration — OAuth + fraud-context endpoints + connect page.
# All routes are no-ops at runtime unless HMRC env is configured.
try:
    from hmrc.routers import oauth as _hmrc_oauth_router
    from hmrc.routers import fraud_context as _hmrc_fraud_router
    from hmrc.routers import pages as _hmrc_pages_router
    from hmrc.routers import categorise as _hmrc_categorise_router
    from hmrc.routers import obligations as _hmrc_obligations_router
    from hmrc.routers import business_details as _hmrc_business_details_router
    from hmrc.routers import sandbox as _hmrc_sandbox_router
    from hmrc.routers import sandbox_seed as _hmrc_sandbox_seed_router
    from hmrc.routers import sandbox_test_user as _hmrc_sandbox_test_user_router
    from hmrc.routers import quarterly_updates_se as _hmrc_quarterly_se_router
    from hmrc.routers import quarterly_updates_property as _hmrc_quarterly_prop_router
    from hmrc.routers import eops as _hmrc_eops_router
    from hmrc.routers import calculation as _hmrc_calc_router
    from hmrc.routers import final_declaration as _hmrc_final_decl_router
    from hmrc.routers import submissions as _hmrc_submissions_router
    from hmrc.routers import penalties as _hmrc_penalties_router
    app.include_router(_hmrc_oauth_router.router)
    app.include_router(_hmrc_fraud_router.router)
    app.include_router(_hmrc_pages_router.router)
    app.include_router(_hmrc_categorise_router.router)
    app.include_router(_hmrc_obligations_router.router)
    app.include_router(_hmrc_business_details_router.router)
    app.include_router(_hmrc_sandbox_router.router)
    app.include_router(_hmrc_sandbox_seed_router.router)
    app.include_router(_hmrc_sandbox_test_user_router.router)
    app.include_router(_hmrc_quarterly_se_router.router)
    app.include_router(_hmrc_quarterly_prop_router.router)
    app.include_router(_hmrc_eops_router.router)
    app.include_router(_hmrc_calc_router.router)
    app.include_router(_hmrc_final_decl_router.router)
    app.include_router(_hmrc_submissions_router.router)
    app.include_router(_hmrc_penalties_router.router)
except Exception:
    logger.exception("Failed to register HMRC routers — continuing without HMRC routes")


# ==========================================================================
# Page Routes
# ==========================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the login/register page."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "login.html")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/landing", status_code=302)
    # Unverified users are routed to /verify-email first.
    if not is_email_verified(user["id"]):
        return RedirectResponse(url="/verify-email", status_code=302)
    # Users who haven't completed Stripe Checkout get sent to /start-trial
    # before they reach the dashboard. ONLY two ways to bypass the paywall:
    #   1. Email is in PAYWALL_BYPASS_EMAILS (hardcoded singleton — yahoo
    #      founder only, no env override). This is INTENTIONALLY a different
    #      set from UNLIMITED_EMAILS — see core.py for rationale.
    #   2. Stripe subscription_status is one of trialing / active /
    #      past_due (the user has already completed Checkout).
    # The ?trial=started query (returned from Stripe's success_url) lets
    # the user land on the dashboard immediately after Stripe confirms
    # checkout, even before the webhook updates subscription_status —
    # Stripe redirects are guaranteed but webhooks have a few seconds of
    # latency.
    email = (user.get("email") or "").lower()
    trial_started_flag = request.query_params.get("trial") == "started"
    if (
        not trial_started_flag
        and email not in PAYWALL_BYPASS_EMAILS
        and not has_active_subscription(user)
    ):
        return RedirectResponse(url="/start-trial", status_code=302)
    return templates.TemplateResponse(request, "index.html")


@app.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html")


@app.get("/compare", response_class=HTMLResponse)
async def compare_page(request: Request):
    return templates.TemplateResponse(request, "compare.html")


@app.get("/compare/docuclipper", response_class=HTMLResponse)
async def compare_docuclipper_page(request: Request):
    return templates.TemplateResponse(request, "compare-docuclipper.html")


@app.get("/compare/statement-desk", response_class=HTMLResponse)
async def compare_statement_desk_page(request: Request):
    return templates.TemplateResponse(request, "compare-statement-desk.html")


@app.get("/solutions/import-bank-statement-without-bank-feed", response_class=HTMLResponse)
async def solution_import_no_feed(request: Request):
    return templates.TemplateResponse(request, "solutions/import-bank-statement-without-bank-feed.html")


@app.get("/solutions/quickbooks-desktop-eol", response_class=HTMLResponse)
async def solution_qb_eol(request: Request):
    return templates.TemplateResponse(request, "solutions/quickbooks-desktop-eol.html")


@app.get("/solutions/xero-pdf-import", response_class=HTMLResponse)
async def solution_xero_pdf(request: Request):
    return templates.TemplateResponse(request, "solutions/xero-pdf-import.html")


@app.get("/solutions/import-bank-statements-into-quickbooks-online", response_class=HTMLResponse)
async def solution_qbo(request: Request):
    return templates.TemplateResponse(request, "solutions/import-bank-statements-into-quickbooks-online.html")


@app.get("/solutions/import-bank-statements-into-sage", response_class=HTMLResponse)
async def solution_sage(request: Request):
    return templates.TemplateResponse(request, "solutions/import-bank-statements-into-sage.html")


@app.get("/solutions/import-bank-statements-into-freeagent", response_class=HTMLResponse)
async def solution_freeagent(request: Request):
    return templates.TemplateResponse(request, "solutions/import-bank-statements-into-freeagent.html")


@app.get("/solutions/bank-statement-conversion-for-year-end", response_class=HTMLResponse)
async def solution_year_end(request: Request):
    return templates.TemplateResponse(request, "solutions/bank-statement-conversion-for-year-end.html")


@app.get("/solutions/receipt-scanner-for-accountants", response_class=HTMLResponse)
async def solution_receipt(request: Request):
    return templates.TemplateResponse(request, "solutions/receipt-scanner-for-accountants.html")


@app.get("/compare/lido", response_class=HTMLResponse)
async def compare_lido_page(request: Request):
    return templates.TemplateResponse(request, "compare-lido.html")


@app.get("/compare/capyparse", response_class=HTMLResponse)
async def compare_capyparse_page(request: Request):
    return templates.TemplateResponse(request, "compare-capyparse.html")


@app.get("/solutions/convert-bank-statements-for-mortgage-application", response_class=HTMLResponse)
async def solution_mortgage(request: Request):
    return templates.TemplateResponse(request, "solutions/convert-bank-statements-for-mortgage-application.html")


@app.get("/solutions/bank-statement-conversion-for-audit", response_class=HTMLResponse)
async def solution_audit(request: Request):
    return templates.TemplateResponse(request, "solutions/bank-statement-conversion-for-audit.html")


@app.get("/solutions/receipt-to-excel-guide", response_class=HTMLResponse)
async def solution_receipt_excel(request: Request):
    return templates.TemplateResponse(request, "solutions/receipt-to-excel-guide.html")


@app.get("/solutions/bank-statement-conversion-bookkeepers", response_class=HTMLResponse)
async def solution_bookkeepers(request: Request):
    return templates.TemplateResponse(request, "solutions/bank-statement-conversion-bookkeepers.html")


@app.get("/solutions/convert-multiple-bank-statements-bulk", response_class=HTMLResponse)
async def solution_bulk(request: Request):
    return templates.TemplateResponse(request, "solutions/convert-multiple-bank-statements-bulk.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Admin dashboard — restricted to UNLIMITED_EMAILS."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    email = (user.get("email") or "").lower()
    if email not in UNLIMITED_EMAILS:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "admin.html")


@app.get("/credits", response_class=HTMLResponse)
async def credits_page(request: Request):
    """AI credit pack purchase page. Logged-in users only. Non-indexed."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/credits", status_code=302)
    import ai_pricing
    balance = get_credit_balance(user["id"])
    return templates.TemplateResponse(
        request,
        "credits.html",
        {
            "credit_balance": balance,
            "packs": ai_pricing.CREDIT_PACKS,
        },
    )


@app.get("/start-trial", response_class=HTMLResponse)
async def start_trial_page(request: Request):
    """Card-on-file trial setup page.

    Shown after email verification for any user who isn't actively
    subscribed. Renders a single 'Start 7-day free trial' button which
    POSTs to ``/api/billing/start-trial-checkout``; the user is then
    bounced to Stripe-hosted Checkout.

    Already-subscribed users are routed straight to the dashboard so they
    don't see a paywall they've already passed.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/start-trial", status_code=302)
    if has_active_subscription(user):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "start_trial.html")


@app.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(request: Request):
    """Email verification page. Logged-in users only. Non-indexed.

    Shown after signup or whenever a user tries to parse while unverified.
    All users must verify their email before using the app.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/verify-email", status_code=302)
    if is_email_verified(user["id"]):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        request,
        "verify_email.html",
        {"email": user["email"]},
    )


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    """Privacy policy. Publicly accessible and indexable.

    Discloses Anthropic as the AI sub-processor that receives every uploaded
    bank statement and receipt. Required for GDPR/legal transparency now that
    all parsing runs through Claude.
    """
    return templates.TemplateResponse(
        request,
        "privacy.html",
        {"effective_date": "8 April 2026"},
    )


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    """Terms of Service. HMRC software recognition checks the URL resolves
    so this page must always return 200 with substantive content."""
    return templates.TemplateResponse(
        request,
        "terms.html",
        {"effective_date": "21 May 2026"},
    )


# ---------------------------------------------------------------------------
# Security disclosure — RFC 9116 + customer-facing reporting page.
# HMRC's recognition application explicitly asks for an easy contact
# method for customers/third parties to report security risks. Without
# these routes the answer to that question is honestly "No".
# ---------------------------------------------------------------------------

# RFC 9116 spec: https://datatracker.ietf.org/doc/html/rfc9116
# Served as text/plain at the well-known location researchers expect.
# Both contacts listed so the file works whether or not security@
# is set up on the mail server — Gmail fallback keeps it valid today.
_SECURITY_TXT = """\
Contact: mailto:security@bankscanai.com
Contact: mailto:mitchellagoma@gmail.com
Expires: 2027-12-31T23:59:59Z
Preferred-Languages: en
Canonical: https://bankscanai.com/.well-known/security.txt
Policy: https://bankscanai.com/security
"""


@app.get("/.well-known/security.txt", response_class=PlainTextResponse)
async def security_txt():
    """RFC 9116 security contact file. Security researchers + automated
    vulnerability scanners (e.g. CVE programs, bug-bounty platforms) look
    for this at the well-known location."""
    return _SECURITY_TXT


# Some scanners try the legacy /security.txt path before /.well-known/.
# Serve the same content there too — no harm, makes us findable.
@app.get("/security.txt", response_class=PlainTextResponse)
async def security_txt_legacy():
    return _SECURITY_TXT


@app.get("/security", response_class=HTMLResponse)
async def security_page(request: Request):
    """Public-facing security/vulnerability disclosure policy. Linked
    from the footer + referenced in security.txt's Policy field.
    HMRC's recognition reviewer follows this link from the application."""
    return templates.TemplateResponse(
        request,
        "security.html",
        {"effective_date": "26 May 2026"},
    )


# ==========================================================================
# Auth API — register, login, logout
# ==========================================================================

@app.post("/api/register")
@limiter.limit("10/minute")
async def register(request: Request):
    """Create a new user account."""
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    # Check if email already exists
    existing = get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    pw_hash = hash_password(password)
    try:
        user_id = create_user(email, pw_hash)
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower() or "constraint" in str(e).lower():
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        raise HTTPException(status_code=500, detail="Registration failed. Please try again.")

    # Send email verification OTP. Parsing is blocked until verified
    # (check_can_use enforces this). We still log the user in so they can
    # hit /verify-email from the same session.
    try:
        code = generate_otp()
        store_otp(email, code, session_id=f"verify:{user_id}")
        send_otp_email(email, code)
    except Exception:
        logger.exception("Failed to send signup OTP to %s", email)

    response = JSONResponse({
        "status": "ok",
        "email": email,
        "email_verification_required": True,
        "verify_url": "/verify-email",
    })
    set_auth_cookie(response, user_id)
    return response


@app.post("/api/login")
@limiter.limit("10/minute")
async def login(request: Request):
    """Sign in with email and password."""
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required.")

    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    payload = {"status": "ok", "email": user["email"]}

    # If the email isn't verified yet, send a fresh OTP and tell the frontend
    # to route to /verify-email. Mirrors /api/register so the user never has
    # to navigate there manually.
    if not is_email_verified(user["id"]):
        try:
            code = generate_otp()
            store_otp(user["email"], code, session_id=f"verify:{user['id']}")
            send_otp_email(user["email"], code)
        except Exception:
            logger.exception("Failed to send login OTP to %s", user["email"])
        payload["email_verification_required"] = True
        payload["verify_url"] = "/verify-email"

    response = JSONResponse(payload)
    set_auth_cookie(response, user["id"])
    return response


@app.post("/api/logout")
async def logout(request: Request):
    """Clear the auth cookie."""
    response = JSONResponse({"status": "ok"})
    clear_auth_cookie(response)
    return response


@app.post("/api/verify-email-code")
@limiter.limit("10/minute")
async def verify_email_code(request: Request):
    """Verify an OTP sent after signup and mark the email as verified."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if is_email_verified(user["id"]):
        return JSONResponse({"status": "ok", "already_verified": True})

    body = await request.json()
    code = (body.get("code") or "").strip()
    if not code or len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=400, detail="Enter the 6-digit code.")

    email = (user.get("email") or "").lower()
    session_id = verify_otp(email, code)
    if not session_id:
        raise HTTPException(status_code=400, detail="Invalid or expired code.")

    mark_email_verified(user["id"])

    # Where to send the user next?
    #   - Already subscribed (rare from this endpoint): dashboard.
    #   - Everyone else (including legacy grandfathered accounts): /start-trial
    #     so they enter a card and start the 7-day countdown under the same
    #     Stripe flow as every other user.
    redirect_to = "/"
    if not has_active_subscription(user):
        redirect_to = "/start-trial"

    return JSONResponse({"status": "ok", "verified": True, "redirect_to": redirect_to})


@app.post("/api/verify-email/resend")
@limiter.limit("3/minute")
async def verify_email_resend(request: Request):
    """Generate + email a fresh OTP to the currently-logged-in user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if is_email_verified(user["id"]):
        return JSONResponse({"status": "ok", "already_verified": True})

    email = (user.get("email") or "").lower()
    try:
        code = generate_otp()
        store_otp(email, code, session_id=f"verify:{user['id']}")
        send_otp_email(email, code)
    except Exception:
        logger.exception("Failed to resend verification OTP to %s", email)
        raise HTTPException(status_code=500, detail="Failed to send code. Please try again.")

    return JSONResponse({"status": "ok"})


# ==========================================================================
# Usage / Auth API
# ==========================================================================

@app.get("/api/usage")
async def get_usage_status(request: Request):
    user = get_current_user(request)
    if not user:
        free_limits = TIER_LIMITS["free"]
        return JSONResponse({
            "has_subscription": False,
            "tier": "free",
            "tier_limits": free_limits,
            "statements_used": 0,
            "statements_limit": free_limits["monthly_statements"],
            "receipts_used": 0,
            "receipts_limit": free_limits["monthly_receipts"],
            "chat_used_today": 0,
            "chat_limit": 0,
            "email": None,
            "stripe_configured": bool(STRIPE_PUBLISHABLE_KEY),
            "trial_days_remaining": None,
            "trial_active": None,
        })

    tier = get_user_tier(user)
    is_subscriber = tier != "free"
    limits = TIER_LIMITS[tier]
    statements_used = get_monthly_statements(user["id"])
    receipts_used = get_monthly_receipts(user["id"])
    chat_used = get_chat_usage(user["id"]) if limits["chat_per_day"] else 0

    # Trial state — only meaningful for free-tier users; surface for all so
    # the frontend can decide what banner (if any) to render.
    days_left = trial_days_remaining(user) if not is_subscriber else None
    in_trial = is_trial_active(user) if not is_subscriber else None

    return JSONResponse({
        "has_subscription": is_subscriber,
        "tier": tier,
        "tier_limits": limits,
        "statements_used": statements_used,
        "statements_limit": limits["monthly_statements"],
        "receipts_used": receipts_used,
        "receipts_limit": limits["monthly_receipts"],
        "chat_used_today": chat_used,
        "chat_limit": limits["chat_per_day"],
        "email": user["email"],
        "stripe_configured": bool(STRIPE_PUBLISHABLE_KEY),
        "trial_days_remaining": days_left,
        "trial_active": in_trial,
    })


# ---------------------------------------------------------------------------
# Unified ledger — bank transactions + linked receipts in one view.
# The differentiator the design doc calls "the single most demoable feature".
# ---------------------------------------------------------------------------

@app.get("/ledger", response_class=HTMLResponse)
async def ledger_page(request: Request):
    """The unified-ledger dashboard page. The single most demoable feature."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/ledger", status_code=302)
    # Gate on paywall like the main dashboard
    email = (user.get("email") or "").lower()
    if (
        email not in PAYWALL_BYPASS_EMAILS
        and not has_active_subscription(user)
    ):
        return RedirectResponse(url="/start-trial", status_code=302)
    return templates.TemplateResponse(request, "ledger.html")


@app.get("/api/ledger")
async def api_ledger(request: Request):
    """Return the unified ledger: every transaction with linked receipts,
    plus orphan (unmatched) receipts, plus summary counts."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from services.ledger_ingest import build_unified_ledger
    return JSONResponse(build_unified_ledger(user["id"]))


@app.post("/api/ledger/link")
@limiter.limit("60/minute")
async def api_ledger_link(request: Request):
    """Manually link a receipt to a transaction (the drag-and-drop or
    'Confirm' button in the inbox lands here).

    Body: ``{"transaction_id": 12, "receipt_id": 34}``
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    tx_id = body.get("transaction_id")
    rc_id = body.get("receipt_id")
    if not tx_id or not rc_id:
        raise HTTPException(
            status_code=400,
            detail="Both transaction_id and receipt_id are required.",
        )

    # Verify both belong to the caller — never trust client ids.
    from database import (
        get_transaction_by_id, get_receipt_by_id, insert_ledger_link,
    )
    tx = get_transaction_by_id(int(tx_id), user["id"])
    rc = get_receipt_by_id(int(rc_id), user["id"])
    if tx is None or rc is None:
        raise HTTPException(status_code=404, detail="Transaction or receipt not found.")

    insert_ledger_link(
        transaction_id=int(tx_id),
        receipt_id=int(rc_id),
        match_strategy="manual",
        confidence=100,
        user_confirmed=True,
        reason="User manually linked",
    )
    return JSONResponse({"status": "ok", "transaction_id": tx_id, "receipt_id": rc_id})


@app.post("/api/ledger/unlink")
@limiter.limit("60/minute")
async def api_ledger_unlink(request: Request):
    """Remove an existing link.

    Body: ``{"transaction_id": 12, "receipt_id": 34}``
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    tx_id = body.get("transaction_id")
    rc_id = body.get("receipt_id")
    if not tx_id or not rc_id:
        raise HTTPException(
            status_code=400,
            detail="Both transaction_id and receipt_id are required.",
        )
    from database import (
        get_transaction_by_id, get_receipt_by_id, remove_ledger_link,
    )
    tx = get_transaction_by_id(int(tx_id), user["id"])
    rc = get_receipt_by_id(int(rc_id), user["id"])
    if tx is None or rc is None:
        raise HTTPException(status_code=404, detail="Transaction or receipt not found.")
    remove_ledger_link(int(tx_id), int(rc_id))
    return JSONResponse({"status": "ok"})


@app.post("/api/ledger/rematch-all")
@limiter.limit("5/minute")
async def api_ledger_rematch_all(request: Request):
    """Clear every auto-created (non-user-confirmed) receipt link and re-run
    the matcher across all receipts. Useful when bad matches accumulate —
    e.g. after a parser bug saved receipts with NULL data and the matcher
    couldn't tell what they were."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    from database import clear_user_auto_links
    from services.ledger_ingest import rematch_user_unmatched_receipts

    cleared = clear_user_auto_links(user["id"])
    rematched = rematch_user_unmatched_receipts(user["id"], enable_ai=False)
    new_links = sum(1 for r in rematched if r.get("transaction_id"))
    return JSONResponse({
        "status": "ok",
        "cleared": cleared,
        "rematched_receipts": len(rematched),
        "new_links": new_links,
    })


@app.get("/api/ledger/diagnostic-links")
async def api_ledger_diagnostic_links(request: Request):
    """Diagnostic-only: list every ledger_links row for the current user
    with the linked transaction's amount/description and the receipt's
    store/total. Lets the user (and us) see exactly which links exist.

    Note: the leading-underscore variant of this path 404'd in the Railway
    edge layer (CDN convention strips _ prefixes). Plain hyphen works."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from database import _fetchall_dicts
    rows = _fetchall_dicts(
        "SELECT l.transaction_id, l.receipt_id, l.match_strategy, l.confidence, "
        "l.user_confirmed, l.reason, "
        "t.description AS tx_desc, t.amount AS tx_amount, t.date_iso AS tx_date, "
        "r.store_name AS rc_store, r.total_amount AS rc_total, r.date_iso AS rc_date "
        "FROM ledger_links l "
        "JOIN ledger_transactions t ON l.transaction_id = t.id "
        "JOIN ledger_receipts r ON l.receipt_id = r.id "
        "WHERE t.user_id = ? "
        "ORDER BY t.date_iso DESC",
        (user["id"],),
    )
    return JSONResponse({"links": rows, "count": len(rows)})


@app.post("/api/ledger/categorise-all")
@limiter.limit("5/minute")
async def api_ledger_categorise_all(request: Request):
    """Run the HMRC AI categoriser on every uncategorised transaction the
    user has in the ledger. Useful for accounts uploaded before auto-
    categorisation was wired in.

    Returns ``{"status": "ok", "categorised": N}``.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    business_type = body.get("business_type", "se")
    from services.ledger_ingest import auto_categorise_user_transactions
    try:
        n = await auto_categorise_user_transactions(
            user["id"], business_type=business_type, only_uncategorised=True,
            limit=2000,
        )
    except Exception as e:
        logger.exception("Manual auto-categorise failed for user %s", user["id"])
        raise HTTPException(status_code=500, detail="Categoriser failed.")
    return JSONResponse({"status": "ok", "categorised": n})


@app.get("/api/audit-summary")
async def api_audit_summary(request: Request):
    """The HMRC audit-readiness summary — per-category totals, VAT, and
    the receipt-backed % score that nobody else surfaces.

    Returns ``{categories: [...], totals: {...}}``. See
    ``services/audit_summary.py`` for the full schema."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from services.audit_summary import summarise_audit_readiness
    return JSONResponse(summarise_audit_readiness(user["id"]))


@app.get("/api/transaction/{transaction_id}/explain", response_class=HTMLResponse)
async def api_transaction_explain(request: Request, transaction_id: int):
    """One-page "Explain this to HMRC" defence sheet for a single
    transaction. Printable; the user's browser handles PDF rendering."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from database import get_transaction_by_id, get_links_for_transaction
    tx = get_transaction_by_id(transaction_id, user["id"])
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    linked = get_links_for_transaction(transaction_id)
    from services.hmrc_defence import build_defence_html
    html = build_defence_html(
        transaction=tx,
        linked_receipts=linked,
        user_email=user["email"],
    )
    return HTMLResponse(html)


@app.get("/api/tax-forecast")
async def api_tax_forecast(request: Request):
    """Live tax-due forecast — combined Self-Employment + Property.
    The dashboard widget that beats Hammock (property-only) and Coconut
    (SE-only) at the same time."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from services.tax_forecast import forecast_tax_due
    return JSONResponse(forecast_tax_due(user["id"]))


@app.get("/api/accountant-export")
async def api_accountant_export(
    request: Request,
    period: str | None = None,
    client_name: str | None = None,
):
    """Download the accountant-ready ZIP pack.

    Query params:
      ``period``       — Human label shown on the cover sheet (e.g. "Q2-2026-27",
                         "2025-26 tax year", "All time"). Does not filter rows
                         yet — the workbook always covers the full ledger.
      ``client_name``  — Optional business / client name for the cover sheet.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from services.accountant_export import build_export_zip
    zip_bytes = build_export_zip(
        user["id"], user["email"],
        period_label=period,
        client_name=client_name,
    )
    safe_period = (period or "current").replace(" ", "_").replace("/", "-")
    # Use the client name (if given) in the filename so the accountant's
    # download folder isn't 30 identical-looking ZIPs.
    safe_client = re.sub(r"[^A-Za-z0-9._-]+", "_", (client_name or "")).strip("_")[:40]
    name_part = f"{safe_client}_" if safe_client else ""
    filename = f"BankScan_Accountant_Pack_{name_part}{safe_period}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Send-to-accountant: shareable link + optional email invite
# ---------------------------------------------------------------------------


def _share_url(request: Request, token: str) -> str:
    """Build the public share URL using the inbound request's scheme + host.
    Behind Railway's edge so we trust X-Forwarded-Proto for HTTPS."""
    base = str(request.base_url).rstrip("/")
    return f"{base}/share/accountant-pack/{token}"


@app.post("/api/accountant-export/share")
@limiter.limit("10/minute")
async def api_accountant_export_share(request: Request):
    """Mint a shareable accountant-pack download link and (optionally)
    email it to the accountant. The token is the auth — anyone holding
    the URL can download until expiry (60 days default) or until the
    user revokes it from /ledger.

    Body:
      {
        "period": "Q2 2026-27 (Jul-Sep)" | null,
        "client_name": "Mitoba Property Services" | null,
        "accountant_email": "anna@cch-practice.co.uk" | null,
        "accountant_name": "Anna Reeves" | null,
        "send_email": true | false
      }

    Response:
      {
        "share_url": "https://bankscanai.com/share/accountant-pack/...",
        "token": "...",
        "expires_at": <epoch seconds>,
        "email_sent": true | false
      }
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        body = await request.json()
    except Exception:
        body = {}

    period = (body.get("period") or "").strip() or None
    client_name = (body.get("client_name") or "").strip() or None
    accountant_email = (body.get("accountant_email") or "").strip() or None
    accountant_name = (body.get("accountant_name") or "").strip() or None
    send_email = bool(body.get("send_email"))

    from services.accountant_share import create_share
    share = create_share(
        user_id=user["id"],
        period_label=period,
        client_name=client_name,
        accountant_email=accountant_email,
        accountant_name=accountant_name,
    )
    share_url = _share_url(request, share["token"])

    email_sent = False
    if send_email and accountant_email:
        # Build a fresh summary just for the email body's "at a glance" —
        # not the full ZIP yet, that gets built on download.
        from services.audit_summary import summarise_audit_readiness
        from services.tax_period import parse_period_label
        from otp import send_accountant_pack_email
        summary = summarise_audit_readiness(user["id"])
        # Human-readable expiry — "9 July 2026" reads better than the epoch
        import datetime as _dt
        try:
            exp = _dt.datetime.utcfromtimestamp(float(share["expires_at"]))
            expires_human = "on " + exp.strftime("%-d %B %Y")
        except Exception:
            expires_human = None
        email_sent = send_accountant_pack_email(
            accountant_email=accountant_email,
            accountant_name=accountant_name,
            share_url=share_url,
            client_name=client_name or user["email"],
            period_label=period or "All time",
            sender_email=user["email"],
            totals=summary.get("totals", {}),
            expires_human=expires_human,
        )

    return JSONResponse({
        "status": "ok",
        "share_url": share_url,
        "token": share["token"],
        "expires_at": share["expires_at"],
        "email_sent": email_sent,
    })


@app.get("/api/accountant-export/shares")
async def api_accountant_export_shares_list(request: Request):
    """List the user's recent shares so the /ledger panel can show
    "previously sent" with revoke buttons."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    rows = database.list_user_accountant_pack_shares(user["id"], limit=20)
    # Don't leak the raw token in the list — only return enough to identify
    # and revoke. Full URL is only shown at create-time.
    import time as _time
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "accountant_email": r.get("accountant_email"),
            "accountant_name": r.get("accountant_name"),
            "period_label": r.get("period_label"),
            "client_name": r.get("client_name"),
            "created_at": r.get("created_at"),
            "expires_at": r.get("expires_at"),
            "revoked_at": r.get("revoked_at"),
            "download_count": r.get("download_count") or 0,
            "last_downloaded_at": r.get("last_downloaded_at"),
            "is_active": (
                not r.get("revoked_at")
                and float(r.get("expires_at") or 0) > _time.time()
            ),
            "token_tail": (r.get("token") or "")[-6:],
        })
    return JSONResponse({"shares": out})


@app.post("/api/accountant-export/share/{share_id}/revoke")
@limiter.limit("30/minute")
async def api_accountant_export_share_revoke(share_id: int, request: Request):
    """Revoke a previously-issued share. Idempotent — already-revoked
    shares stay revoked."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    ok = database.revoke_accountant_pack_share(user["id"], share_id)
    if not ok:
        # Already-revoked or not-yours — return 404 so we don't leak
        # which IDs exist.
        raise HTTPException(status_code=404, detail="Share not found.")
    return JSONResponse({"status": "ok", "share_id": share_id})


@app.get("/share/accountant-pack/{token}", response_class=HTMLResponse)
async def share_accountant_pack_landing(token: str, request: Request):
    """Public landing page for the accountant. Shows totals, expiry,
    and a big Download button. Token IS the auth."""
    from services.accountant_share import resolve_share
    share = resolve_share(token)
    if not share:
        return HTMLResponse(
            content=_share_invalid_html(),
            status_code=404,
        )
    # Pull the underlying user for the "Sent by" line + summary
    sender = database.get_user_by_id(int(share["user_id"]))
    sender_email = sender["email"] if sender else "your client"

    from services.audit_summary import summarise_audit_readiness
    summary = summarise_audit_readiness(int(share["user_id"]))
    totals = summary.get("totals", {})

    import datetime as _dt
    try:
        exp = _dt.datetime.utcfromtimestamp(float(share["expires_at"]))
        expires_human = exp.strftime("%-d %B %Y")
    except Exception:
        expires_human = "60 days from issue"

    download_url = f"/share/accountant-pack/{token}/download"
    html = _share_landing_html(
        token=token,
        share=share,
        sender_email=sender_email,
        totals=totals,
        expires_human=expires_human,
        download_url=download_url,
    )
    return HTMLResponse(html)


@app.get("/share/accountant-pack/{token}/download")
async def share_accountant_pack_download(token: str, request: Request):
    """Public download endpoint. Returns the actual ZIP. No auth — the
    token IS the auth. Bumps download_count + last_downloaded_at."""
    from services.accountant_share import resolve_share, record_download
    from services.accountant_export import build_export_zip
    share = resolve_share(token)
    if not share:
        raise HTTPException(status_code=404, detail="Share is invalid, expired, or revoked.")

    user = database.get_user_by_id(int(share["user_id"]))
    if not user:
        raise HTTPException(status_code=404, detail="Share owner not found.")

    zip_bytes = build_export_zip(
        user["id"], user["email"],
        period_label=share.get("period_label"),
        client_name=share.get("client_name"),
    )
    record_download(int(share["id"]))

    safe_period = (share.get("period_label") or "current").replace(" ", "_").replace("/", "-")
    safe_client = re.sub(r"[^A-Za-z0-9._-]+", "_",
                          (share.get("client_name") or "")).strip("_")[:40]
    name_part = f"{safe_client}_" if safe_client else ""
    filename = f"BankScan_Accountant_Pack_{name_part}{safe_period}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _share_invalid_html() -> str:
    """Friendly 404 for missing/expired/revoked share tokens."""
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Pack unavailable — BankScan AI</title>
<style>
body { font-family: -apple-system, sans-serif; max-width: 480px; margin: 4rem auto;
       padding: 2rem; color: #2C3E50; text-align: center; }
h1 { color: #1B4F72; }
.card { background: #FDFEFE; border: 1px solid #D5DBDB; border-radius: 8px;
        padding: 2rem; margin-top: 1.5rem; }
.muted { color: #566573; font-size: 0.9rem; margin-top: 1.5rem; }
</style></head>
<body>
<h1>BankScan AI</h1>
<div class="card">
  <h2 style="color:#C0392B;">This link isn't valid.</h2>
  <p>It may have expired, been revoked, or never existed in the first place.</p>
  <p>Reach out to your client — they can re-issue a fresh link from the BankScan AI dashboard.</p>
</div>
<p class="muted">BankScan AI · accountant pack delivery</p>
</body></html>"""


def _share_landing_html(
    *, token: str, share: dict, sender_email: str, totals: dict,
    expires_human: str, download_url: str,
) -> str:
    """The page the accountant lands on. Shows totals up-front and a
    big Download button. Designed to be openable without login, on any
    device, including corporate sandbox browsers."""
    client = share.get("client_name") or sender_email
    period = share.get("period_label") or "All time"
    income = float(totals.get("income", 0) or 0)
    expenses = float(totals.get("expenses", 0) or 0)
    net = income - expenses
    audit_pct = totals.get("audit_ready_pct", 0)
    tx_count = totals.get("transactions_total", 0)
    missing = totals.get("transactions_missing", 0)

    # Lightweight HTML escape for the strings we render
    import html as _html
    client_safe = _html.escape(client)
    period_safe = _html.escape(period)
    sender_safe = _html.escape(sender_email)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Accountant pack from {client_safe} — BankScan AI</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 640px; margin: 2.5rem auto; padding: 0 1.25rem; color: #2C3E50;
          background: #F8F9F9; }}
  .card {{ background: #FFFFFF; border: 1px solid #E5E8E8; border-radius: 10px;
           padding: 2rem 2.25rem; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  h1 {{ color: #1B4F72; font-size: 1.4rem; margin: 0 0 0.25rem; }}
  .sub {{ color: #566573; margin: 0 0 1.5rem; font-size: 0.95rem; }}
  .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.6rem 1.5rem;
            background: #F4F6F7; padding: 1rem 1.25rem; border-radius: 6px;
            border-left: 4px solid #1B4F72; margin: 1rem 0 1.5rem; font-size: 0.95rem; }}
  .stats .label {{ color: #566573; }}
  .stats .val {{ text-align: right; font-weight: 600; }}
  .stats .val.warn {{ color: #B7950B; }}
  .cta {{ text-align: center; margin: 1.75rem 0 1rem; }}
  .cta a {{ display: inline-block; padding: 0.95rem 2rem; background: #1B4F72;
            color: #fff; text-decoration: none; border-radius: 6px;
            font-weight: 600; font-size: 1.05rem; }}
  .cta a:hover {{ background: #21618C; }}
  .expiry {{ font-size: 0.85rem; color: #566573; margin-top: 0.7rem; }}
  .contents {{ font-size: 0.9rem; color: #566573; }}
  .contents li {{ margin-bottom: 0.25rem; }}
  .disclaimer {{ font-size: 0.78rem; color: #95A5A6;
                  border-top: 1px solid #EAEDED; padding-top: 1rem;
                  margin-top: 1.5rem; }}
  .reply {{ font-size: 0.9rem; color: #566573; margin-top: 1rem; }}
  .reply a {{ color: #2874A6; }}
</style></head><body>
<div class="card">
  <h1>Accountant pack — ready for review</h1>
  <p class="sub">From <strong>{client_safe}</strong> · Period <strong>{period_safe}</strong></p>

  <div class="stats">
    <div class="label">Income (gross)</div>
    <div class="val">£{income:,.2f}</div>
    <div class="label">Expenses (gross)</div>
    <div class="val">£{expenses:,.2f}</div>
    <div class="label">Net</div>
    <div class="val">£{net:,.2f}</div>
    <div class="label">Transactions</div>
    <div class="val">{tx_count}</div>
    <div class="label">Receipt-backed</div>
    <div class="val">{audit_pct}%</div>
    <div class="label">Missing receipts</div>
    <div class="val {'warn' if missing else ''}">{missing}</div>
  </div>

  <div class="cta">
    <a href="{download_url}">Download pack (.zip)</a>
    <div class="expiry">Link expires on {expires_human}. No login required.</div>
  </div>

  <div class="contents">
    <strong>What's inside:</strong>
    <ul>
      <li><strong>Accountant_Pack.xlsx</strong> — Cover, Action Items, Tax Return Boxes
          (SA103/SA105), Trial Balance, Transactions, Missing Receipts, Receipt
          Inventory, VAT Register, Reasoning Log.</li>
      <li><strong>receipts/</strong> — grouped by HMRC category, plus _orphan and
          _missing-file sub-folders.</li>
      <li><strong>data/</strong> — raw CSVs for CCH / IRIS / TaxCalc / Sage import.</li>
      <li><strong>summary.html</strong> — Audit Confidence Certificate
          (print-to-PDF in your browser).</li>
      <li><strong>manifest.json</strong> — SHA-256 hashes for audit integrity.</li>
    </ul>
  </div>

  <p class="reply">
    Questions? Email <a href="mailto:{sender_safe}">{sender_safe}</a> — replies
    go straight back to the client.
  </p>

  <p class="disclaimer">
    AI-assisted draft. Requires sign-off by a qualified accountant or tax practitioner.
    BankScan AI does not provide accounting or tax advice.
  </p>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Mileage tracker
# ---------------------------------------------------------------------------

@app.get("/api/mileage")
async def api_mileage_summary(request: Request):
    """Per-tax-year mileage summary + every log line with HMRC rate applied."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from services.mileage import mileage_summary
    return JSONResponse(mileage_summary(user["id"]))


@app.post("/api/mileage")
@limiter.limit("60/minute")
async def api_mileage_add(request: Request):
    """Log a business journey. Body:
       {"date_iso":"YYYY-MM-DD","miles":12.5,"from_location":"...","to_location":"...","purpose":"...","vehicle":"car","business_pct":100}
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    if not body.get("date_iso"):
        raise HTTPException(status_code=400, detail="date_iso required.")
    miles = body.get("miles")
    if miles is None:
        raise HTTPException(status_code=400, detail="miles required.")
    try:
        miles = float(miles)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="miles must be a number.")
    if miles <= 0:
        raise HTTPException(status_code=400, detail="miles must be positive.")

    from services.mileage import add_mileage_log
    try:
        log_id = add_mileage_log(
            user["id"],
            date_iso=body["date_iso"],
            miles=miles,
            from_location=body.get("from_location"),
            to_location=body.get("to_location"),
            purpose=body.get("purpose"),
            vehicle=body.get("vehicle", "car"),
            business_pct=int(body.get("business_pct", 100)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse({"status": "ok", "log_id": log_id})


@app.delete("/api/mileage/{log_id}")
async def api_mileage_delete(request: Request, log_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from services.mileage import delete_mileage_log
    if not delete_mileage_log(user["id"], log_id):
        raise HTTPException(status_code=404, detail="Mileage log not found.")
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Anomaly / missed-expense detection
# ---------------------------------------------------------------------------

@app.get("/api/anomalies")
async def api_anomalies(request: Request):
    """Returns the user-facing list of categories that look like they're
    missing receipts this quarter, with human-readable messages."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    from services.anomaly_detector import detect_anomalies
    return JSONResponse(detect_anomalies(user["id"]))


# ---------------------------------------------------------------------------
# Email-in receipts — user's personal forwarding address
# ---------------------------------------------------------------------------

@app.post("/api/receipts/email-in")
@limiter.limit("30/minute")
async def api_email_in_receipt(request: Request):
    """Webhook for receipts forwarded to {user}@receipts.bankscanai.com.

    Accepts either:
      - JSON: {"to": "<userid>@receipts.bankscanai.com",
               "from": "<sender>", "subject": "...",
               "attachments": [{"filename":"...","content_b64":"..."}]}
      - Direct upload from the dashboard's "I just forwarded an invoice"
        button: same shape, the user_id is taken from session.

    The body is validated and the receipt(s) are queued for parsing in
    the same code path as a manual upload.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    # Identify the user. Three paths, tried in order:
    #   1. Session cookie (the in-app upload button case)
    #   2. `to` local-part is a per-user token (current scheme — 8 alnum chars)
    #   3. `to` local-part is an integer user-id (legacy scheme, kept for
    #      backwards-compat with any address that's already in the wild)
    user = get_current_user(request)
    if user is None:
        to_addr = (body.get("to") or "").lower()
        if "@receipts.bankscanai.com" not in to_addr:
            raise HTTPException(status_code=401, detail="Unrecognised recipient.")
        local = to_addr.split("@", 1)[0]
        from database import get_user_by_id, _fetchone_dict
        # Try the token route first
        user = _fetchone_dict(
            "SELECT id, email, receipts_token FROM users WHERE receipts_token = ?",
            (local,),
        )
        if user is None:
            # Fall back to integer id (legacy)
            try:
                uid = int(local)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid recipient local-part.")
            user = get_user_by_id(uid)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found.")

    attachments = body.get("attachments") or []
    if not attachments:
        raise HTTPException(status_code=400, detail="No attachments.")

    # Process every attachment through the same pipeline as the in-app
    # receipt upload button:
    #   1. Decode base64 → bytes
    #   2. Write to disk under UPLOAD_DIR
    #   3. Run parse_receipt_ai to extract store/date/total/items/VAT
    #   4. Persist to user_extracted_data + ingest_receipt_and_match
    #      which writes ledger_receipts AND auto-links to a bank tx
    #      on exact match.
    import base64
    from database import save_extracted_data
    from parsers.ai_parser import parse_receipt_ai
    from services.ledger_ingest import ingest_receipt_and_match

    saved = 0
    matched = 0
    parse_errors: list[str] = []
    match_summaries: list[dict] = []

    for att in attachments:
        fname = Path(att.get("filename") or "receipt.bin").name
        b64 = att.get("content_b64") or ""
        try:
            payload = base64.b64decode(b64)
        except Exception:
            continue
        if not payload:
            continue

        # 1. Write to disk so the AI parser can read it
        job_id = str(uuid.uuid4())[:8]
        upload_path = UPLOAD_DIR / f"emailin_{job_id}_{fname}"
        try:
            with open(upload_path, "wb") as f:
                f.write(payload)
        except OSError:
            logger.exception("email-in: failed to write %s", upload_path)
            parse_errors.append(fname)
            continue

        try:
            # 2. AI parse
            try:
                parsed = await asyncio.to_thread(parse_receipt_ai, str(upload_path))
            except Exception as e:
                logger.exception("email-in: parse_receipt_ai failed for %s", fname)
                parse_errors.append(f"{fname}: {type(e).__name__}")
                continue

            # 3. Persist + match
            extracted_id = save_extracted_data(
                user["id"], "receipt", fname,
                parsed.get("items") or [],
                source_size_bytes=len(payload),
            )
            outcome = ingest_receipt_and_match(
                user["id"],
                extracted_id,
                parsed,
                file_path=str(upload_path),
                source_filename=fname,
                enable_ai=False,  # rules + strong matching on the hot path
            )
            saved += 1
            if outcome["match"]["strategy"] == "exact":
                matched += 1
            match_summaries.append({
                "filename": fname,
                # parse_receipt_ai surfaces store/date under "metadata".
                "store": (parsed.get("metadata") or {}).get("store_name"),
                "total": (parsed.get("totals") or {}).get("total"),
                "match": outcome["match"],
            })
        finally:
            # Leave the file on disk — the matcher's defence sheet + accountant
            # ZIP both reference it via ledger_receipts.file_path. We clean it
            # up on receipt deletion, not here.
            pass

    token = _receipts_token_for(user)
    return JSONResponse({
        "status": "ok",
        "received": len(attachments),
        "saved": saved,
        "auto_matched": matched,
        "parse_errors": parse_errors,
        "receipts": match_summaries,
        "forwarding_address": f"{token}@receipts.bankscanai.com",
    })


def _receipts_token_for(user: dict) -> str:
    """Lazy-generate + return the user's per-user receipts forwarding token.

    Per-user random token (8 url-safe chars) used as the local-part of
    {token}@receipts.bankscanai.com. Stable for the lifetime of the user.
    Bare integer ids made the address look like a spam-bot endpoint
    (e.g. "4@receipts.bankscanai.com") — this gives it a proper-looking
    handle and removes the user-id enumeration vector.
    """
    import secrets
    token = (user or {}).get("receipts_token")
    if token:
        return token
    new = secrets.token_urlsafe(6).replace("_", "").replace("-", "").lower()[:8]
    # Guarantee 8 chars even after stripping non-alnum
    while len(new) < 8:
        new += secrets.token_urlsafe(2).replace("_", "").replace("-", "").lower()[:8 - len(new)]
    from database import update_user
    update_user(user["id"], receipts_token=new)
    return new


@app.get("/api/receipts/forwarding-address")
async def api_forwarding_address(request: Request):
    """Returns the user's personal receipts forwarding address.

    Format: ``{token}@receipts.bankscanai.com`` where token is an 8-char
    random handle stable per user. Lazy-generated on first call."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    token = _receipts_token_for(user)
    return JSONResponse({
        "address": f"{token}@receipts.bankscanai.com",
    })


# ---------------------------------------------------------------------------
# Accountant co-pilot — bulk approve transactions in a category
# ---------------------------------------------------------------------------

@app.post("/api/ledger/bulk-approve")
@limiter.limit("30/minute")
async def api_ledger_bulk_approve(request: Request):
    """Mark every transaction in a given category + amount range as
    user-confirmed (sets hmrc_category_confidence=100 on each).

    Body: ``{"hmrc_category": "se_office_expenses", "max_amount": 100}``
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    cat = body.get("hmrc_category")
    if not cat:
        raise HTTPException(status_code=400, detail="hmrc_category required.")
    max_amount = body.get("max_amount")

    from database import _execute, _fetchall_dicts
    if max_amount is not None:
        rows = _fetchall_dicts(
            "SELECT id FROM ledger_transactions "
            "WHERE user_id = ? AND hmrc_category = ? AND ABS(amount) <= ?",
            (user["id"], cat, float(max_amount)),
        )
    else:
        rows = _fetchall_dicts(
            "SELECT id FROM ledger_transactions "
            "WHERE user_id = ? AND hmrc_category = ?",
            (user["id"], cat),
        )
    approved = 0
    for r in rows:
        _execute(
            "UPDATE ledger_transactions SET hmrc_category_confidence = 100, "
            "updated_at = strftime('%s','now') WHERE id = ?",
            (r["id"],),
        )
        approved += 1
    return JSONResponse({"status": "ok", "approved": approved})


@app.get("/api/audit-certificate", response_class=HTMLResponse)
async def api_audit_certificate(request: Request, period: str | None = None):
    """Quarter-end Audit Confidence Certificate.

    Query param ``period`` is the human-readable label e.g. ``Q2-2026``.
    Defaults to the current quarter if not supplied."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not period:
        import datetime as _dt
        now = _dt.datetime.utcnow()
        q = (now.month - 1) // 3 + 1
        period = f"Q{q}-{now.year}"
    from services.audit_summary import summarise_audit_readiness
    from services.audit_certificate import build_certificate_html
    summary = summarise_audit_readiness(user["id"])
    html = build_certificate_html(
        user_email=user["email"],
        period_label=period,
        summary=summary,
    )
    return HTMLResponse(html)


@app.post("/api/ledger/transaction/status")
@limiter.limit("120/minute")
async def api_ledger_transaction_status(request: Request):
    """Update a transaction's user-controlled flags: exclusion reason
    (personal/cash/dd/subscription), business_pct (0-100), is_capital,
    or override HMRC category.

    Body: any subset of:
      {"transaction_id": 12,
       "exclusion_reason": "personal" | null,
       "business_pct": 60,
       "is_capital": 1,
       "hmrc_category": "office_expenses",
       "hmrc_category_reason": "User override"}
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    tx_id = body.get("transaction_id")
    if not tx_id:
        raise HTTPException(status_code=400, detail="transaction_id is required.")
    from database import get_transaction_by_id, update_transaction_status
    tx = get_transaction_by_id(int(tx_id), user["id"])
    if tx is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    # Validate enums
    excl = body.get("exclusion_reason")
    if excl is not None and excl not in (None, "personal", "cash", "dd", "subscription"):
        raise HTTPException(status_code=400, detail="Invalid exclusion_reason.")
    bpct = body.get("business_pct")
    if bpct is not None and not (0 <= int(bpct) <= 100):
        raise HTTPException(status_code=400, detail="business_pct must be 0-100.")

    # Treat exclusion as a state transition on receipt_status
    receipt_status = None
    if excl == "personal":
        receipt_status = "excluded"
    elif excl in ("cash", "dd", "subscription"):
        receipt_status = "na"
    elif excl is None and body.get("clear_exclusion"):
        receipt_status = "missing"

    update_transaction_status(
        int(tx_id),
        receipt_status=receipt_status,
        exclusion_reason=excl,
        business_pct=int(bpct) if bpct is not None else None,
        is_capital=int(body["is_capital"]) if body.get("is_capital") is not None else None,
        hmrc_category=body.get("hmrc_category"),
        hmrc_category_reason=body.get("hmrc_category_reason"),
    )
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Persisted cumulative extraction — survives logout, only cleared by the
# explicit "Clear & Upload New" button.
# ---------------------------------------------------------------------------

@app.get("/api/extracted-data")
async def get_extracted_data(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    statement_files = get_user_extracted_files(user["id"], "statement")
    receipt_files = get_user_extracted_files(user["id"], "receipt")
    statement_rows = [row for f in statement_files for row in f["rows"]]
    receipt_rows = [row for f in receipt_files for row in f["rows"]]
    total_bytes = get_user_extracted_total_bytes(user["id"])
    return JSONResponse({
        "statements": {
            "rows": statement_rows,
            # Include each file's transactions so the dashboard can
            # rehydrate the per-statement view after a page navigation
            # (e.g. back from /hmrc/connect) without re-uploading.
            "files": [
                {"id": f["id"], "filename": f["source_filename"],
                 "row_count": f["row_count"], "parsed_at": f["parsed_at"],
                 "transactions": f["rows"]}
                for f in statement_files
            ],
            "summary": _summarise_transactions(statement_rows),
        },
        "receipts": {
            "rows": receipt_rows,
            "files": [
                {"id": f["id"], "filename": f["source_filename"],
                 "row_count": f["row_count"], "parsed_at": f["parsed_at"],
                 "transactions": f["rows"]}
                for f in receipt_files
            ],
            "summary": _summarise_transactions(receipt_rows),
        },
        "total_size_bytes": total_bytes,
        "session_max_bytes": SESSION_MAX_BYTES,
        # Heuristically detect currency from the most recent parsed data.
        # British store names strongly suggest GBP.
        "currency": _guess_currency(receipt_rows, statement_rows),
    })


def _summarise_transactions(rows: list[dict]) -> dict:
    """Compute the credit/debit/net rollup used by the bulk-statement panel.

    Matches parsers/csv_parser.py + parsers/pdf_parser.py so rehydrated
    sessions display the same numbers a fresh upload would.
    """
    credits = 0.0
    debits = 0.0
    for r in rows:
        try:
            amt = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt > 0:
            credits += amt
        elif amt < 0:
            debits += amt
    return {
        "total_transactions": len(rows),
        "total_credits": round(credits, 2),
        "total_debits": round(debits, 2),
        "net": round(credits + debits, 2),
    }


@app.post("/api/extracted-data/clear")
async def clear_extracted_data(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    deleted = clear_user_extracted_data(user["id"])
    return JSONResponse({"status": "ok", "files_cleared": deleted})


def _build_hmrc_payload_for_rows(user: dict, rows: list[dict], business_type: str) -> dict:
    """Classify a list of statement rows and return them annotated with HMRC
    category data + an aggregate summary suitable for the XLSX exporter.

    Resolution order per row (same as /api/hmrc/categorise):
      1. User's saved override (★ saved)
      2. Static regex rule (rule)
      3. Fallback (low confidence 'other')
    AI fallback is deliberately NOT called in the download path — it would
    add seconds of latency to a click that's expected to be instant. The
    interactive dashboard path is where AI fires; the user's corrections
    saved there flow through to here via the overrides table.
    """
    from hmrc.repositories import overrides as _overrides
    from hmrc.schemas import categories as _cats
    from hmrc.services import mapping as _mapping

    bt = "property" if business_type == "property" else "se"

    classify_fn = (
        _mapping.classify_property if bt == "property"
        else _mapping.classify_self_employment
    )
    user_full_name = (user.get("email") or "").split("@")[0]

    annotated_rows = []
    income: dict[str, float] = {}
    expenses: dict[str, float] = {}
    flagged: list[dict] = []
    excluded: list[dict] = []

    for r in rows:
        desc = (r.get("description") or "").strip()
        try:
            amount = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        ov = _overrides.lookup(user["id"], desc, bt)
        if ov:
            category, confidence, source = ov, 1.0, "override"
            is_income = amount > 0
            reasoning = "Your saved category for this merchant"
        else:
            cl = classify_fn(desc, amount, user_full_name=user_full_name)
            category, confidence = cl.category, cl.confidence
            is_income, reasoning = cl.is_income, cl.reasoning
            source = "rule"

        annotated_rows.append({
            **r,
            "hmrc_category": category,
            "hmrc_confidence": confidence,
            "hmrc_source": source,
        })

        if category == _mapping.EXCLUDE_OWNER_TRANSFER:
            excluded.append({"description": desc, "amount": amount})
            continue

        # Bucket by the category's INTRINSIC HMRC meaning, not by the per-row
        # `is_income` flag — see hmrc.services.categorisation.summarise for
        # the rationale. Otherwise the downloaded XLSX shows "Other expense"
        # under Income for any credit the classifier mislabeled as `other`.
        bucket = income if _cats.is_income_category(category, bt) else expenses
        bucket[category] = round(bucket.get(category, 0.0) + abs(amount), 2)
        if confidence < 0.5:
            flagged.append({"description": desc, "amount": amount, "reasoning": reasoning})

    # Derive period from min/max date on the rows.
    dates = sorted([r.get("date") for r in rows if r.get("date")])
    period = {"start": dates[0], "end": dates[-1]} if dates else {}

    return {
        "rows": annotated_rows,
        "summary": {
            "business_type": bt,
            "period": period,
            "income": income,
            "expenses": expenses,
            "flagged_for_review": flagged,
            "excluded": excluded,
        },
    }


@app.get("/api/extracted-data/download")
async def download_cumulative_xlsx(
    request: Request,
    mode: str,
    currency: str = "",
    business_type: str = "",
):
    """Build a fresh XLSX from all the user's accumulated rows for one mode.

    Query params:
      - mode: 'statement' or 'receipt'
      - currency: e.g. 'GBP', 'USD' — for cell formatting only
      - business_type: 'se' | 'property' | '' — when set on a statement
        download we ALSO categorise each transaction against HMRC MTD ITSA
        categories (using the user's saved overrides where they exist) and
        add 'HMRC Category', 'Confidence', 'Source' columns + an
        'HMRC Summary' sheet to the workbook.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if mode not in ("statement", "receipt"):
        raise HTTPException(status_code=400, detail="mode must be 'statement' or 'receipt'.")

    rows = get_user_extracted_rows(user["id"], mode)
    if not rows:
        raise HTTPException(status_code=404, detail="No extracted data to download.")

    # Currency passed from the frontend (detected from AI response).
    # When nothing is known, leave blank — XLSX will use plain numbers.
    cur = currency.strip().upper()

    job_id = str(uuid.uuid4())[:8]
    if mode == "statement":
        output_filename = f"cumulative_statements_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename

        # Optional HMRC categorisation. Only fires when the caller asked for
        # a specific business_type. Uses the user's saved overrides where
        # they exist (same logic as the dashboard categorise endpoint), so
        # the spreadsheet reflects every correction they've made.
        hmrc_payload = None
        if business_type in ("se", "property"):
            hmrc_payload = _build_hmrc_payload_for_rows(user, rows, business_type)

        data = {
            "transactions": rows if not hmrc_payload else hmrc_payload["rows"],
            "summary": {},
            "metadata": {"bank_name": "Cumulative Export", "currency": cur},
        }
        if hmrc_payload:
            data["hmrc_summary"] = hmrc_payload["summary"]
        export_to_xlsx(data, str(output_path))
    else:
        output_filename = f"cumulative_receipts_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        grand_total = round(sum(float(r.get("total_price", 0) or 0) for r in rows), 2)
        export_receipt_to_xlsx(
            {
                "items": rows,
                "totals": {"total": grand_total, "tax": 0, "subtotal": grand_total},
                "metadata": {
                    "store_name": "Cumulative Receipts",
                    "item_count": len(rows),
                    "currency": cur,
                },
            },
            str(output_path),
        )

    track_output_file(output_filename)
    return JSONResponse({"download_url": f"/downloads/{output_filename}"})


# ---------------------------------------------------------------------------
# Cron: day-5 trial reminder
# ---------------------------------------------------------------------------
# Vercel hits this endpoint on the schedule in vercel.json (`crons`). The
# request carries Authorization: Bearer ${CRON_SECRET} — see Vercel docs.
# On Railway / local you can hit it manually with the same header.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test-mode endpoints — only enabled when TEST_MODE_ENABLED=1.
# Used by Playwright to read OTPs and simulate trial expiry without
# touching real email or waiting 7 days. Disabled in prod.
# ---------------------------------------------------------------------------

def _test_mode_guard():
    if os.environ.get("TEST_MODE_ENABLED", "") != "1":
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/api/test/peek-otp")
async def test_peek_otp(email: str):
    _test_mode_guard()
    from database import _fetchone_dict
    row = _fetchone_dict(
        "SELECT code FROM otp_codes WHERE email = ? ORDER BY created_at DESC LIMIT 1",
        (email.strip().lower(),),
    )
    if not row:
        raise HTTPException(status_code=404, detail="No OTP for this email")
    return JSONResponse({"email": email, "code": row["code"]})


@app.post("/api/test/age-user")
async def test_age_user(request: Request):
    _test_mode_guard()
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    days_ago = float(body.get("days_ago") or 0)
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    from database import _execute
    import time
    new_created = time.time() - days_ago * 86400.0
    _execute("UPDATE users SET created_at = ? WHERE email = ?", (new_created, email))
    return JSONResponse({"email": email, "days_ago": days_ago, "new_created_at": new_created})


@app.post("/api/test/grandfather-user")
async def test_grandfather_user(request: Request):
    """Test-only: flip grandfathered_trial on a user so e2e tests can opt into
    the legacy 7-days-from-signup trial path (skipping the Stripe Checkout
    requirement). Guarded by TEST_MODE_ENABLED."""
    _test_mode_guard()
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    grandfathered = 1 if body.get("grandfathered", True) else 0
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    from database import _execute
    _execute("UPDATE users SET grandfathered_trial = ? WHERE email = ?", (grandfathered, email))
    return JSONResponse({"email": email, "grandfathered_trial": grandfathered})


@app.post("/api/test/set-subscription-state")
async def test_set_subscription_state(request: Request):
    """Test-only: write subscription_status / stripe_subscription_id /
    trial_end_at directly on a user row, simulating a Stripe webhook delivery
    for the card-on-file trial flow. Lets e2e tests cover the
    post-Checkout-completed state without hitting real Stripe."""
    _test_mode_guard()
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    fields = {}
    if "subscription_status" in body:
        fields["subscription_status"] = body["subscription_status"]
    if "stripe_subscription_id" in body:
        fields["stripe_subscription_id"] = body["stripe_subscription_id"]
    if "trial_end_at" in body:
        fields["trial_end_at"] = float(body["trial_end_at"]) if body["trial_end_at"] is not None else None
    from database import get_user_by_email, update_user
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    if fields:
        update_user(user["id"], **fields)
    return JSONResponse({"email": email, "updated": fields})


@app.post("/api/test/seed-ledger-fixture")
async def test_seed_ledger_fixture(request: Request):
    """Test-only: in one shot, mark a user as trialing AND seed a single
    bank transaction + matched receipt + link in the structured ledger.

    Used by tests/e2e/test_ledger_journey.py to set up a known state
    without needing the real AI parser. Skips authentication checks
    because test mode is locked behind _test_mode_guard()."""
    _test_mode_guard()
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    from database import (
        get_user_by_email, update_user, save_extracted_data,
        insert_ledger_transaction, insert_ledger_receipt, insert_ledger_link,
    )
    import time as _time
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    update_user(
        user["id"],
        subscription_status="trialing",
        stripe_subscription_id="sub_e2e_fixture",
        stripe_customer_id="cus_e2e_fixture",
        trial_end_at=_time.time() + 7 * 86400,
    )

    ed_stmt = save_extracted_data(
        user["id"], "statement", "fixture_stmt.pdf", [], source_size_bytes=0,
    )
    ed_rc = save_extracted_data(
        user["id"], "receipt", "fixture_rc.pdf", [], source_size_bytes=0,
    )

    tx_id = insert_ledger_transaction(
        user["id"], extracted_data_id=ed_stmt,
        date_iso="2026-08-04", description="AMAZON UK MARKETPLACE",
        amount=-42.99,
        hmrc_category="se_general_admin_costs",
        hmrc_category_confidence=95,
        hmrc_category_reason=(
            "Office consumables — recognised general admin spend per BIM47800."
        ),
    )
    rc_id = insert_ledger_receipt(
        user["id"], extracted_data_id=ed_rc,
        file_path=None, source_filename="fixture_rc.pdf",
        store_name="Amazon", date_iso="2026-08-04",
        total_amount=42.99, currency="GBP",
        subtotal=35.83, tax_amount=7.16, payment_method="card",
    )
    insert_ledger_link(
        transaction_id=tx_id, receipt_id=rc_id,
        match_strategy="exact", confidence=100,
        user_confirmed=False, reason="E2E fixture",
    )
    return JSONResponse({
        "user_id": user["id"],
        "transaction_id": tx_id,
        "receipt_id": rc_id,
    })


@app.get("/api/test/peek-password-reset-token")
async def test_peek_password_reset_token(request: Request):
    """Test-only: return the latest unused password-reset token for an
    email. Used by the E2E test so we don't need a real inbox."""
    _test_mode_guard()
    email = request.query_params.get("email", "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    from database import get_user_by_email, _fetchone_dict
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    row = _fetchone_dict(
        "SELECT token FROM password_reset_tokens "
        "WHERE user_id = ? AND used = 0 "
        "ORDER BY created_at DESC LIMIT 1",
        (user["id"],),
    )
    if not row:
        raise HTTPException(status_code=404, detail="no reset token issued")
    return JSONResponse({"token": row["token"]})


@app.post("/api/test/mark-subscribed")
async def test_mark_subscribed(request: Request):
    """Test-only: flip a user into a Stripe-trialing state."""
    _test_mode_guard()
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    from database import get_user_by_email, update_user
    import time as _t
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    update_user(
        user["id"],
        email_verified=1,
        subscription_status="trialing",
        stripe_subscription_id="sub_test_seed",
        stripe_customer_id="cus_test_seed",
        trial_end_at=_t.time() + 7 * 86400,
    )
    return JSONResponse({"status": "ok", "user_id": user["id"]})


@app.post("/api/test/seed-ledger-rich")
async def test_seed_ledger_rich(request: Request):
    """Test-only: seed enough varied data to exercise every ledger UI:
       - 1 matched transaction + receipt
       - 1 unmatched bank transaction (so drag-drop has a target)
       - 1 orphan receipt (so drag-drop has a source — amount that
         WILL NOT auto-match the unmatched bank line)
       - 3 uncategorised transactions (for bulk-approve test)
       - Baseline transactions across 3 prior quarters for anomaly test

    Returns the IDs the test needs."""
    _test_mode_guard()
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    from database import (
        get_user_by_email, update_user, save_extracted_data,
        insert_ledger_transaction, insert_ledger_receipt, insert_ledger_link,
    )
    import time as _time, datetime as _dt
    user = get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")

    update_user(
        user["id"],
        subscription_status="trialing",
        stripe_subscription_id="sub_rich",
        stripe_customer_id="cus_rich",
        trial_end_at=_time.time() + 7 * 86400,
    )
    ed_stmt = save_extracted_data(user["id"], "statement", "rich.pdf", [], 0)
    ed_rc = save_extracted_data(user["id"], "receipt", "rich.pdf", [], 0)

    today = _dt.date.today()
    iso_today = today.isoformat()

    # Matched transaction + receipt
    tx_matched = insert_ledger_transaction(
        user["id"], extracted_data_id=ed_stmt,
        date_iso=iso_today, description="AMAZON UK MARKETPLACE",
        amount=-42.99, hmrc_category="se_general_admin_costs",
        hmrc_category_confidence=95,
        hmrc_category_reason="Office consumables per BIM47800.",
    )
    rc_matched = insert_ledger_receipt(
        user["id"], extracted_data_id=ed_rc, file_path=None,
        source_filename="rc1.pdf", store_name="Amazon",
        date_iso=iso_today, total_amount=42.99, tax_amount=7.16,
    )
    insert_ledger_link(transaction_id=tx_matched, receipt_id=rc_matched,
                       match_strategy="exact", confidence=100)

    # Unmatched bank tx (drag-drop target)
    tx_unmatched = insert_ledger_transaction(
        user["id"], extracted_data_id=ed_stmt,
        date_iso=iso_today, description="SHELL HOLLOWAY ROAD",
        amount=-58.20, hmrc_category="se_business_travel_costs",
        hmrc_category_confidence=80,
        hmrc_category_reason="Fuel — qualifies as business travel BIM45000.",
    )

    # Orphan receipt with amount NOT matching the unmatched tx
    # (so the auto-matcher leaves it alone — the user has to drag it)
    rc_orphan = insert_ledger_receipt(
        user["id"], extracted_data_id=ed_rc, file_path=None,
        source_filename="orphan.pdf", store_name="LonelyStore",
        date_iso=iso_today, total_amount=999.50, tax_amount=166.58,
    )

    # 3 uncategorised transactions for bulk approve seeding
    for i in range(3):
        insert_ledger_transaction(
            user["id"], extracted_data_id=ed_stmt,
            date_iso=iso_today, description=f"VENDOR_X_{i}",
            amount=-(15.0 + i),
            hmrc_category="se_office_expenses",
            hmrc_category_confidence=50,  # low confidence so bulk-approve has work
            hmrc_category_reason="Categorised by AI; awaiting user confirmation.",
        )

    # Baseline transactions for anomaly test: 3 prior quarters of motor expenses
    q_start = _dt.date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
    for q in range(1, 4):
        # Step back q quarters
        d = q_start
        for _ in range(q):
            if d.month == 1:
                d = _dt.date(d.year - 1, 10, 1)
            else:
                d = _dt.date(d.year, d.month - 3, 1)
        for i in range(3):
            insert_ledger_transaction(
                user["id"], extracted_data_id=ed_stmt,
                date_iso=(d + _dt.timedelta(days=i * 10)).isoformat(),
                description=f"SHELL Q{q}_{i}",
                amount=-(150.0 + i * 10),
                hmrc_category="se_motor_expenses",
                hmrc_category_confidence=90,
                hmrc_category_reason="Fuel for business travel — BIM45000.",
            )

    return JSONResponse({
        "user_id": user["id"],
        "tx_matched": tx_matched,
        "tx_unmatched": tx_unmatched,
        "rc_orphan": rc_orphan,
    })


@app.get("/api/cron/trial-reminders")
async def cron_trial_reminders(request: Request):
    expected = os.environ.get("CRON_SECRET", "")
    if expected:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    users = find_users_due_trial_reminder()
    sent = 0
    failed = 0
    for u in users:
        try:
            ok = send_trial_reminder_email(u["email"], days_left=2)
        except Exception:
            logger.exception("Trial reminder send raised for user %s", u["id"])
            ok = False
        if ok:
            mark_trial_reminder_sent(u["id"])
            sent += 1
        else:
            failed += 1

    return JSONResponse({"status": "ok", "candidates": len(users), "sent": sent, "failed": failed})


@app.get("/api/cron/hmrc-deadline-reminders")
async def cron_hmrc_deadline_reminders(request: Request):
    """Daily cron — emails any HMRC-connected user with an obligation due
    in exactly 7 or 1 days. Idempotent via hmrc_deadline_reminders table.

    Configure on Railway/Vercel to fire once a day. The cron secret guard
    matches the existing trial-reminders pattern."""
    expected = os.environ.get("CRON_SECRET", "")
    if expected:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    from hmrc.services import obligations as _obl_service
    from otp import send_hmrc_deadline_reminder

    LEAD_DAYS = (7, 1)
    candidates = database.list_users_with_hmrc_connection()
    base_url = str(request.base_url).rstrip("/")
    file_url = f"{base_url}/hmrc/file"

    sent = 0
    skipped_already_sent = 0
    failed = 0
    no_deadline = 0
    for user in candidates:
        try:
            # Reuse the in-app obligations fetch. It needs a request object
            # for fraud headers — we pass the cron request itself which
            # carries valid Vendor headers but no user-specific Client ones.
            # That's acceptable for a server-to-server cron.
            response = _obl_service.fetch_for_user(
                user_id=user["id"], request_obj=request,
            )
        except Exception:
            logger.exception("cron_hmrc_deadline_reminders: fetch failed for user %s", user["id"])
            failed += 1
            continue
        if not response or not getattr(response, "obligations", None):
            no_deadline += 1
            continue
        for obl in response.obligations:
            if obl.status not in ("open", "upcoming"):
                continue
            if obl.days_until_due not in LEAD_DAYS:
                continue
            # Idempotency: never double-send for this (user, deadline, lead).
            already = database.has_hmrc_deadline_reminder(
                user_id=user["id"], deadline_iso=obl.due, lead_days=obl.days_until_due,
            )
            if already:
                skipped_already_sent += 1
                continue
            ok = send_hmrc_deadline_reminder(
                to_email=user["email"],
                business_label=obl.business_label,
                period_start=obl.period_start, period_end=obl.period_end,
                due_iso=obl.due, days_until_due=obl.days_until_due,
                file_url=file_url,
            )
            if ok:
                database.mark_hmrc_deadline_reminder_sent(
                    user_id=user["id"], deadline_iso=obl.due,
                    lead_days=obl.days_until_due,
                    business_label=obl.business_label,
                )
                sent += 1
            else:
                failed += 1

    return JSONResponse({
        "status": "ok",
        "candidates": len(candidates),
        "sent": sent,
        "skipped_already_sent": skipped_already_sent,
        "no_deadline": no_deadline,
        "failed": failed,
    })


@app.post("/api/restore/request")
@limiter.limit("3/minute")
async def restore_request_otp(request: Request):
    """Step 1: User submits email, we send an OTP code."""
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    try:
        customers = stripe.Customer.search(query=f'email:"{email}"')
        if not customers.data:
            raise HTTPException(
                status_code=404,
                detail="No subscription found for this email."
            )

        active_customer = None
        for customer in customers.data:
            subs = stripe.Subscription.list(customer=customer.id, status="active", limit=1)
            if subs.data:
                active_customer = customer
                break

        if not active_customer:
            raise HTTPException(
                status_code=404,
                detail="No active subscription found."
            )

        session_id = ensure_session(request)
        code = generate_otp()
        store_otp(email, code, session_id)

        if not send_otp_email(email, code):
            raise HTTPException(status_code=500, detail="Failed to send verification email. Please try again.")

        response = JSONResponse({
            "status": "otp_sent",
            "message": "A verification code has been sent to your email.",
        })
        set_session_cookie(response, session_id)
        return response

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


@app.post("/api/restore/verify")
@limiter.limit("5/minute")
async def restore_verify_otp(request: Request):
    """Step 2: User submits OTP code to verify email and restore subscription."""
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    body = await request.json()
    email = body.get("email", "").strip().lower()
    code = body.get("code", "").strip()

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")
    if not code or len(code) != 6:
        raise HTTPException(status_code=400, detail="Please enter the 6-digit code from your email.")

    stored_session_id = verify_otp(email, code)
    if not stored_session_id:
        raise HTTPException(status_code=400, detail="Invalid or expired code. Please request a new one.")

    try:
        customers = stripe.Customer.search(query=f'email:"{email}"')
        active_customer = None
        for customer in customers.data:
            subs = stripe.Subscription.list(customer=customer.id, status="active", limit=1)
            if subs.data:
                active_customer = customer
                break

        if not active_customer:
            raise HTTPException(status_code=404, detail="No active subscription found.")

        # Update user record if logged in
        user = get_current_user(request)
        if user:
            update_user(user["id"], stripe_customer_id=active_customer.id)

        # Also update legacy session
        session_id = ensure_session(request)
        usage = get_usage(session_id)
        usage["stripe_customer_id"] = active_customer.id
        usage["email"] = email
        save_usage(session_id, usage)

        response = JSONResponse({
            "status": "restored",
            "email": email,
            "has_subscription": True,
        })
        set_session_cookie(response, session_id)
        return response

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


# ==========================================================================
# Parse API (with usage gating)
# ==========================================================================

@app.post("/api/parse")
@limiter.limit("10/minute")
async def parse_statement(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    allowed, tier, reason, est_cost = check_can_use(user, "statement")
    if not allowed:
        code = QUOTA_REASON_MESSAGES.get(reason, "FREE_LIMIT_REACHED")
        status = 503 if reason == "global_daily_cap" else 403
        raise HTTPException(status_code=status, detail=code)

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in [".pdf", ".csv", ".tsv", ".txt"]):
        raise HTTPException(status_code=400, detail="Unsupported file type. Please upload a PDF or CSV file.")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    # Cumulative session cap — total bytes already stored + this file must
    # stay under SESSION_MAX_BYTES. User can lift it by clicking "Clear &
    # Upload New" on the dashboard.
    existing_bytes = get_user_extracted_total_bytes(user["id"])
    if existing_bytes + len(contents) > SESSION_MAX_BYTES:
        raise HTTPException(status_code=413, detail="SESSION_LIMIT_EXCEEDED")

    if not ANTHROPIC_API_KEY and filename.endswith(".pdf"):
        raise HTTPException(status_code=501, detail="AI parsing is not configured on this server.")

    safe_filename = Path(file.filename).name  # Strip path components
    job_id = str(uuid.uuid4())[:8]
    upload_path = UPLOAD_DIR / f"{job_id}_{safe_filename}"

    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        # CSV files are parsed locally (no AI cost). PDFs always use Claude vision.
        # Run the synchronous parsers on a worker thread so they don't block
        # FastAPI's event loop. Without this, every concurrent upload queues
        # behind the previous one and Railway's edge eventually drops the
        # connection — surfaces as "Load failed" in Safari.
        if filename.endswith(".pdf"):
            result = await asyncio.to_thread(parse_statement_ai, str(upload_path))
        else:
            result = await asyncio.to_thread(parse_csv, str(upload_path))

        if not result["transactions"]:
            # Record failed AI call (tokens may still have been consumed).
            ai_usage = result.get("metadata", {}).get("ai_usage") or {}
            if ai_usage.get("input_tokens") or ai_usage.get("output_tokens"):
                record_ai_spend(
                    user["id"], "statement", ai_usage.get("model", ""),
                    int(ai_usage.get("input_tokens", 0)),
                    int(ai_usage.get("output_tokens", 0)),
                    success=False,
                )
            raise HTTPException(
                status_code=422,
                detail="No transactions found. The file format may not be supported yet, or the statement may be empty."
            )

        output_filename = f"bankparse_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        export_to_xlsx(result, str(output_path))
        track_output_file(output_filename)

        # Increment monthly statement count for all tiers
        increment_monthly_statements(user["id"], 1)

        # Persist the extracted rows on the user account so the dashboard can
        # show cumulative totals across uploads, surviving logout. Cleared
        # only when the user clicks "Clear & Upload New".
        extracted_data_id = None
        try:
            extracted_data_id = save_extracted_data(
                user["id"], "statement", safe_filename,
                result["transactions"], source_size_bytes=len(contents),
            )
        except Exception:
            logger.exception("Failed to persist extracted statement data for user %s", user["id"])

        # ALSO write structured ledger rows + try to re-match any orphan
        # receipts the user has already uploaded against these new bank lines.
        if extracted_data_id is not None:
            try:
                from services.ledger_ingest import (
                    ingest_statement_rows, rematch_user_unmatched_receipts,
                    auto_categorise_user_transactions,
                )
                ingest_statement_rows(
                    user["id"], extracted_data_id, result.get("transactions") or [],
                )
                # Run the HMRC categoriser on every freshly-ingested row so
                # the dashboard shows real categories straight away rather
                # than "Uncategorised". Best-effort: a failure here doesn't
                # block the upload. Only touches rows that don't already
                # have a category.
                try:
                    await auto_categorise_user_transactions(
                        user["id"], business_type="se",
                        only_uncategorised=True,
                    )
                except Exception:
                    logger.exception("Auto-categorise failed (user %s)", user["id"])
                # Brand-new bank data → existing orphan receipts might now match.
                rematch_user_unmatched_receipts(user["id"], enable_ai=False)
            except Exception:
                logger.exception("Ledger ingest failed for statement upload (user %s)", user["id"])

        # Deduct the exact AI cost from the user's monthly budget / credit balance.
        ai_usage = result.get("metadata", {}).get("ai_usage") or {}
        if ai_usage.get("input_tokens") or ai_usage.get("output_tokens"):
            record_ai_spend(
                user["id"], "statement", ai_usage.get("model", ""),
                int(ai_usage.get("input_tokens", 0)),
                int(ai_usage.get("output_tokens", 0)),
                success=True,
            )

        response = JSONResponse({
            "transactions": result["transactions"],
            "summary": result["summary"],
            "metadata": result["metadata"],
            "download_url": f"/downloads/{output_filename}",
        })
        return response

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")
    finally:
        if upload_path.exists():
            upload_path.unlink()


@app.post("/api/parse-receipt")
@limiter.limit("10/minute")
async def parse_receipt_endpoint(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    allowed, tier, reason, est_cost = check_can_use(user, "receipt")
    if not allowed:
        code = QUOTA_REASON_MESSAGES.get(reason, "FREE_LIMIT_REACHED")
        status = 503 if reason == "global_daily_cap" else 403
        raise HTTPException(status_code=status, detail=code)

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in RECEIPT_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload a PDF or image (PNG, JPG, TIFF) of your receipt."
        )

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    # Cumulative session cap (see /api/parse).
    existing_bytes = get_user_extracted_total_bytes(user["id"])
    if existing_bytes + len(contents) > SESSION_MAX_BYTES:
        raise HTTPException(status_code=413, detail="SESSION_LIMIT_EXCEEDED")

    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=501, detail="AI parsing is not configured on this server.")

    safe_filename = Path(file.filename).name  # Strip path components
    job_id = str(uuid.uuid4())[:8]
    upload_path = UPLOAD_DIR / f"{job_id}_{safe_filename}"

    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        # AI-only: parse every receipt with Claude vision. Off-load to a
        # thread so this blocking call doesn't pin the FastAPI event loop —
        # see /api/parse comment above for the failure mode.
        result = await asyncio.to_thread(parse_receipt_ai, str(upload_path))

        if not result.get("items"):
            # Record failed AI call (tokens may still have been consumed).
            ai_usage = result.get("metadata", {}).get("ai_usage") or {}
            if ai_usage.get("input_tokens") or ai_usage.get("output_tokens"):
                record_ai_spend(
                    user["id"], "receipt", ai_usage.get("model", ""),
                    int(ai_usage.get("input_tokens", 0)),
                    int(ai_usage.get("output_tokens", 0)),
                    success=False,
                )
            raise HTTPException(
                status_code=422,
                detail="No items found on the receipt. The format may not be supported, or the image may be unclear."
            )

        output_filename = f"receipt_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        export_receipt_to_xlsx(result, str(output_path))
        track_output_file(output_filename)

        # Increment monthly receipt count for all tiers
        increment_monthly_receipts(user["id"], 1)

        # Persist the extracted line items on the user account (cumulative).
        extracted_data_id = None
        try:
            extracted_data_id = save_extracted_data(
                user["id"], "receipt", safe_filename,
                result["items"], source_size_bytes=len(contents),
            )
        except Exception:
            logger.exception("Failed to persist extracted receipt data for user %s", user["id"])

        # Write the receipt to the structured ledger and try to auto-match
        # it against the user's existing bank transactions.
        match_meta = None
        if extracted_data_id is not None:
            try:
                from services.ledger_ingest import ingest_receipt_and_match
                outcome = ingest_receipt_and_match(
                    user["id"],
                    extracted_data_id,
                    result,
                    file_path=str(upload_path),
                    source_filename=safe_filename,
                    enable_ai=False,  # Heuristics only on the upload hot path
                )
                match_meta = outcome
            except Exception:
                logger.exception("Ledger ingest failed for receipt upload (user %s)", user["id"])

        # Deduct the exact AI cost from the user's monthly budget / credit balance.
        ai_usage = result.get("metadata", {}).get("ai_usage") or {}
        if ai_usage.get("input_tokens") or ai_usage.get("output_tokens"):
            record_ai_spend(
                user["id"], "receipt", ai_usage.get("model", ""),
                int(ai_usage.get("input_tokens", 0)),
                int(ai_usage.get("output_tokens", 0)),
                success=True,
            )

        response_body = {
            "items": result["items"],
            "totals": result["totals"],
            "metadata": result["metadata"],
            "download_url": f"/downloads/{output_filename}",
        }
        if match_meta is not None:
            # Expose the matcher's verdict so the dashboard can show the
            # green tick / "Awaiting check" inbox card immediately.
            response_body["receipt_id"] = match_meta["receipt_id"]
            response_body["match"] = match_meta["match"]
        response = JSONResponse(response_body)
        return response

    except HTTPException:
        raise
    except ImportError as e:
        logger.exception("Receipt parsing import error: %s", e)
        raise HTTPException(status_code=501, detail="A required dependency is not available. Please try again later.")
    except Exception as e:
        logger.exception("Receipt parsing error: %s", e)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")
    finally:
        if upload_path.exists():
            upload_path.unlink()


# ==========================================================================
# Bulk Receipt Parsing
# ==========================================================================

@app.post("/api/parse-receipts-bulk")
@limiter.limit("5/minute")
async def parse_receipts_bulk_endpoint(request: Request, files: list[UploadFile] = File(...)):
    """Parse multiple receipt files in a single batch.

    Accepts multiple files, processes each receipt, and returns combined
    results with individual receipt data and a merged spreadsheet.
    Counts as 1 receipt use for the whole batch.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    tier = get_user_tier(user)
    limits = TIER_LIMITS[tier]

    # Free tier cannot use bulk upload
    if limits["bulk_max_files"] == 0:
        raise HTTPException(status_code=403, detail="Bulk upload requires a paid subscription (Starter or above).")

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    if len(files) > limits["bulk_max_files"]:
        raise HTTPException(status_code=400, detail=f"Maximum {limits['bulk_max_files']} receipts per batch on your plan.")

    # Check monthly receipt limit for the batch
    monthly_limit = limits["monthly_receipts"]
    if monthly_limit is not None:
        receipts_used = get_monthly_receipts(user["id"])
        if receipts_used + len(files) > monthly_limit:
            remaining = max(0, monthly_limit - receipts_used)
            raise HTTPException(status_code=403, detail=f"Monthly receipt limit reached. You have {remaining} receipts remaining this month.")

    # Pre-flight spend check: pessimistic estimate for the whole batch.
    allowed, _tier, reason, _cost = check_can_use(user, "receipt", num_pages=len(files))
    if not allowed:
        code = QUOTA_REASON_MESSAGES.get(reason, "FREE_LIMIT_REACHED")
        status = 503 if reason == "global_daily_cap" else 403
        raise HTTPException(status_code=status, detail=code)

    # Validate all files before processing
    for f in files:
        fname = f.filename.lower()
        if not any(fname.endswith(ext) for ext in RECEIPT_EXTENSIONS):
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {f.filename}. Please upload PDF or image files (PNG, JPG, TIFF)."
            )

    upload_paths = []
    try:
        # Save all files temporarily
        for f in files:
            contents = await f.read()
            if len(contents) > 20 * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"File too large: {f.filename}. Maximum size is 20MB per file.")

            safe_filename = Path(f.filename).name
            job_id = str(uuid.uuid4())[:8]
            upload_path = UPLOAD_DIR / f"{job_id}_{safe_filename}"

            with open(upload_path, "wb") as fout:
                fout.write(contents)
            upload_paths.append(str(upload_path))

        # Parse all receipts (AI-only). Off-load to a thread so this
        # blocking call doesn't pin the event loop — see /api/parse comment.
        bulk_result = await asyncio.to_thread(parse_receipts_bulk, upload_paths)
        bulk_usage = bulk_result.get("ai_usage") or {}

        if not bulk_result["combined_items"]:
            if bulk_usage.get("input_tokens") or bulk_usage.get("output_tokens"):
                record_ai_spend(
                    user["id"], "receipt", bulk_usage.get("model", ""),
                    int(bulk_usage.get("input_tokens", 0)),
                    int(bulk_usage.get("output_tokens", 0)),
                    success=False,
                )
            raise HTTPException(
                status_code=422,
                detail="No items found on any of the receipts. The formats may not be supported, or the images may be unclear."
            )

        # Generate combined XLSX
        job_id = str(uuid.uuid4())[:8]
        output_filename = f"receipts_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        export_bulk_receipts_to_xlsx(bulk_result, str(output_path))
        track_output_file(output_filename)

        # Increment monthly receipt count for all files in batch
        increment_monthly_receipts(user["id"], len(upload_paths))

        # Bill the exact aggregate AI cost across the whole batch.
        if bulk_usage.get("input_tokens") or bulk_usage.get("output_tokens"):
            record_ai_spend(
                user["id"], "receipt", bulk_usage.get("model", ""),
                int(bulk_usage.get("input_tokens", 0)),
                int(bulk_usage.get("output_tokens", 0)),
                success=True,
            )

        # Persist each receipt's items so the dashboard shows cumulative
        # totals across uploads. Cleared only by "Clear & Upload New".
        try:
            for receipt in bulk_result["receipts"]:
                save_extracted_data(
                    user["id"], "receipt", receipt.get("filename", ""),
                    receipt.get("items", []),
                    source_size_bytes=receipt.get("source_size_bytes", 0),
                )
        except Exception:
            logger.exception("Failed to persist bulk receipt data for user %s", user["id"])

        return JSONResponse({
            "receipts": bulk_result["receipts"],
            "combined_items": bulk_result["combined_items"],
            "grand_total": bulk_result["grand_total"],
            "receipt_count": bulk_result["receipt_count"],
            "total_items": bulk_result["total_items"],
            "download_url": f"/downloads/{output_filename}",
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Bulk receipt parsing error: %s", e)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")
    finally:
        for path in upload_paths:
            p = Path(path)
            if p.exists():
                p.unlink()


@app.post("/api/parse-statements-bulk")
@limiter.limit("5/minute")
async def parse_statements_bulk_endpoint(request: Request, files: list[UploadFile] = File(...)):
    """Parse multiple bank statement files in a single batch."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    tier = get_user_tier(user)
    limits = TIER_LIMITS[tier]

    # Free tier cannot use bulk upload
    if limits["bulk_max_files"] == 0:
        raise HTTPException(status_code=403, detail="Bulk upload requires a paid subscription (Starter or above).")

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    if len(files) > limits["bulk_max_files"]:
        raise HTTPException(status_code=400, detail=f"Maximum {limits['bulk_max_files']} statements per batch on your plan.")

    # Check monthly statement limit for the batch
    monthly_limit = limits["monthly_statements"]
    if monthly_limit is not None:
        statements_used = get_monthly_statements(user["id"])
        if statements_used + len(files) > monthly_limit:
            remaining = max(0, monthly_limit - statements_used)
            raise HTTPException(status_code=403, detail=f"Monthly statement limit reached. You have {remaining} statements remaining this month.")

    # Pre-flight spend check: pessimistic ~3 pages/file for statements.
    allowed, _tier, reason, _cost = check_can_use(user, "statement", num_pages=max(len(files) * 3, 1))
    if not allowed:
        code = QUOTA_REASON_MESSAGES.get(reason, "FREE_LIMIT_REACHED")
        status = 503 if reason == "global_daily_cap" else 403
        raise HTTPException(status_code=status, detail=code)

    STATEMENT_EXTENSIONS = [".pdf", ".csv", ".tsv", ".txt"]
    for f in files:
        fname = f.filename.lower()
        if not any(fname.endswith(ext) for ext in STATEMENT_EXTENSIONS):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {f.filename}. Please upload PDF or CSV files.")

    upload_paths = []
    try:
        for f in files:
            contents = await f.read()
            if len(contents) > 20 * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"File too large: {f.filename}. Maximum 20MB per file.")
            safe_filename = Path(f.filename).name
            job_id = str(uuid.uuid4())[:8]
            upload_path = UPLOAD_DIR / f"{job_id}_{safe_filename}"
            with open(upload_path, "wb") as fout:
                fout.write(contents)
            upload_paths.append(str(upload_path))

        # Off-load to a thread so the event loop stays responsive for other
        # users while a multi-PDF parse is in flight — see /api/parse comment.
        bulk_result = await asyncio.to_thread(parse_statements_bulk, upload_paths)
        bulk_usage = bulk_result.get("ai_usage") or {}

        if not bulk_result["all_transactions"]:
            if bulk_usage.get("input_tokens") or bulk_usage.get("output_tokens"):
                record_ai_spend(
                    user["id"], "statement", bulk_usage.get("model", ""),
                    int(bulk_usage.get("input_tokens", 0)),
                    int(bulk_usage.get("output_tokens", 0)),
                    success=False,
                )
            raise HTTPException(status_code=422, detail="No transactions found in any of the uploaded statements.")

        job_id = str(uuid.uuid4())[:8]
        output_filename = f"statements_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        export_bulk_statements_to_xlsx(bulk_result, str(output_path))
        track_output_file(output_filename)

        # Increment monthly statement count for all files in batch
        increment_monthly_statements(user["id"], len(upload_paths))

        # Bill the exact aggregate AI cost across the whole batch.
        if bulk_usage.get("input_tokens") or bulk_usage.get("output_tokens"):
            record_ai_spend(
                user["id"], "statement", bulk_usage.get("model", ""),
                int(bulk_usage.get("input_tokens", 0)),
                int(bulk_usage.get("output_tokens", 0)),
                success=True,
            )

        # Persist each statement's transactions so the dashboard shows cumulative
        # totals across uploads. Cleared only by "Clear & Upload New".
        try:
            for stmt in bulk_result["statements"]:
                save_extracted_data(
                    user["id"], "statement", stmt.get("filename", ""),
                    stmt.get("transactions", []),
                    source_size_bytes=stmt.get("source_size_bytes", 0),
                )
        except Exception:
            logger.exception("Failed to persist bulk statement data for user %s", user["id"])

        return JSONResponse({
            "statements": bulk_result["statements"],
            "all_transactions": bulk_result["all_transactions"],
            "summary": bulk_result["summary"],
            "statement_count": bulk_result["statement_count"],
            "download_url": f"/downloads/{output_filename}",
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Bulk statement parsing error: %s", e)
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")
    finally:
        for path in upload_paths:
            p = Path(path)
            if p.exists():
                p.unlink()


# ==========================================================================
# AI Chat API
# ==========================================================================

# ---- helpers ----

_GBP_STORES = {"tesco", "sainsbury", "waitrose", "marks", "spencer", "m&s",
                "boots", "asda", "morrisons", "aldi", "lidl", "co-op", "coop",
                "iceland", "poundland", "wilko", "superdrug", "homebase",
                "b&q", "screwfix", "argos", "john lewis", "next", "primark",
                "debenhams", "house of fraser", "currys", "pc world",
                "greggs", "pret", "costa", "nero", "wethers", "spoons",
                "nandos", "itsu", "wasabi", "wagamama"}

def _guess_currency(receipt_rows: list, statement_rows: list) -> str:
    """Heuristic: if store names look British, return GBP."""
    store_field = None
    for row in receipt_rows:
        store_field = row.get("store", row.get("store_name", "")).lower()
        if store_field:
            for gbp_store in _GBP_STORES:
                if gbp_store in store_field:
                    return "GBP"
    return ""  # Unknown — let the frontend leave amounts unadorned


# 3-letter ISO currency code → display symbol.
# Used both for the formatted chat context and for the system prompt so the
# AI is told explicitly which currency it's looking at. Symbols mirror what
# the user sees on their original bank statement.
_CURRENCY_SYMBOLS = {
    "GBP": "£",  # £
    "USD": "$",
    "EUR": "€",  # €
    "JPY": "¥",  # ¥
    "INR": "₹",  # ₹
    "CAD": "CA$",
    "AUD": "A$",
    "NZD": "NZ$",
    "CHF": "CHF ",
    "ZAR": "R",
    "CNY": "¥",  # ¥ (RMB)
    "HKD": "HK$",
    "SGD": "S$",
}


def _currency_symbol(code: str | None) -> str:
    """Pick a display symbol for a 3-letter ISO currency code.

    Falls back to GBP for blank/missing codes (most of our users are UK).
    Unrecognised codes (e.g. ``"SEK"``) fall through to ``"SEK "`` so the AI
    still sees the currency, just labelled rather than symbolised.
    """
    if not code:
        return "£"  # default GBP
    code = code.strip().upper()
    return _CURRENCY_SYMBOLS.get(code, f"{code} ")


def _statement_currency(context_data: dict) -> str:
    """Pull the parsed currency code out of statement metadata.

    The AI parsers set ``metadata.currency`` per HMRC-recognisable
    statement; we trust their detection. Defaults to GBP when missing so
    chat keeps working on older payloads that pre-date currency detection.
    """
    meta = context_data.get("metadata") or {}
    return (meta.get("currency") or "GBP").strip().upper() or "GBP"


def _format_chat_context(context_type: str, context_data: dict) -> tuple[str, str]:
    """Format parsed results into readable text for the chat system prompt.

    Returns ``(formatted_text, currency_code)`` — the caller uses the currency
    code in the system prompt so the AI is told which currency the user is
    asking about. Previously this function hardcoded ``$`` on every amount,
    which made Claude confidently mis-label GBP statements as USD even when
    we told it "use GBP" in the system prompt: the *data* it was reading
    contradicted the instructions. Now we use the statement's own detected
    currency symbol.

    Sonnet 4 has 200K context — we can send ALL transactions for accurate
    answers. Truncates at 150K chars (~40K tokens) as a safety limit.
    """
    lines = []
    max_chars = 150000

    currency = _statement_currency(context_data)
    sym = _currency_symbol(currency)

    def _fmt(amount) -> str:
        try:
            return f"{sym}{float(amount or 0):.2f}"
        except (TypeError, ValueError):
            return f"{sym}0.00"

    if context_type == "statement":
        summary = context_data.get("summary", {})
        lines.append(f"=== Bank Statement Summary ({currency}) ===")
        lines.append(f"Total transactions: {summary.get('total_transactions', 'N/A')}")
        lines.append(f"Total credits (money in): {_fmt(summary.get('total_credits'))}")
        lines.append(f"Total debits (money out): {_fmt(summary.get('total_debits'))}")
        lines.append(f"Net: {_fmt(summary.get('net'))}")
        lines.append("")
        lines.append("=== Transactions ===")
        for tx in context_data.get("transactions", []):
            line = (
                f"{tx.get('date', 'N/A')} | {tx.get('description', 'N/A')} | "
                f"{_fmt(tx.get('amount'))} | {tx.get('type', 'N/A')}"
            )
            lines.append(line)

    elif context_type == "receipt":
        meta = context_data.get("metadata", {})
        totals = context_data.get("totals", {})
        lines.append(f"=== Receipt from {meta.get('store_name', 'Unknown Store')} ({currency}) ===")
        if meta.get("date"):
            lines.append(f"Date: {meta['date']}")
        lines.append(f"Total: {_fmt(totals.get('total'))}")
        if totals.get("tax") is not None:
            lines.append(f"Tax: {_fmt(totals.get('tax'))}")
        lines.append("")
        lines.append("=== Items ===")
        for item in context_data.get("items", []):
            line = (
                f"{item.get('description', 'N/A')} | Qty: {item.get('quantity', 1)} | "
                f"Unit: {_fmt(item.get('unit_price'))} | Total: {_fmt(item.get('total_price'))}"
            )
            lines.append(line)

    elif context_type == "bulk_receipt":
        lines.append(f"=== Bulk Receipt Summary ({currency}) ===")
        lines.append(f"Receipts processed: {context_data.get('receipt_count', 0)}")
        lines.append(f"Total items: {context_data.get('total_items', 0)}")
        lines.append(f"Grand total: {_fmt(context_data.get('grand_total'))}")
        lines.append("")
        lines.append("=== All Items ===")
        for item in context_data.get("combined_items", []):
            line = (
                f"{item.get('store', 'N/A')} | {item.get('description', 'N/A')} | "
                f"Qty: {item.get('quantity', 1)} | {_fmt(item.get('total_price'))}"
            )
            lines.append(line)

    elif context_type == "bulk_statement":
        summary = context_data.get("summary", {})
        lines.append(f"=== Combined Statements Summary ({currency}) ===")
        lines.append(f"Statements: {context_data.get('statement_count', 0)}")
        lines.append(f"Total transactions: {summary.get('total_transactions', 0)}")
        lines.append(f"Total credits: {_fmt(summary.get('total_credits'))}")
        lines.append(f"Total debits: {_fmt(summary.get('total_debits'))}")
        lines.append(f"Net: {_fmt(summary.get('net'))}")
        lines.append("")
        lines.append("=== All Transactions ===")
        for tx in context_data.get("all_transactions", []):
            source = tx.get("source", "")
            line = (
                f"{source} | {tx.get('date', 'N/A')} | {tx.get('description', 'N/A')} | "
                f"{_fmt(tx.get('amount'))} | {tx.get('type', 'N/A')}"
            )
            lines.append(line)

    else:
        lines.append("No recognized data format.")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n... (data truncated for brevity)"
    return text, currency


@app.post("/api/chat")
@limiter.limit("20/minute")
async def chat_endpoint(request: Request):
    """AI chat about uploaded financial data."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    tier = get_user_tier(user)
    limits = TIER_LIMITS[tier]

    # Chat access: only business (50/day) and enterprise (unlimited)
    if limits["chat_per_day"] == 0:
        raise HTTPException(
            status_code=403,
            detail="AI Chat is available on Business (\u00a359.99/mo) and Enterprise (\u00a3149/mo) plans."
        )

    # Business tier: enforce daily limit
    if limits["chat_per_day"] is not None:
        chat_used = get_chat_usage(user["id"])
        if chat_used >= limits["chat_per_day"]:
            raise HTTPException(
                status_code=403,
                detail=f"Daily chat limit reached ({limits['chat_per_day']}/day). Upgrade to Enterprise for unlimited."
            )

    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=501, detail="AI chat is not available. ANTHROPIC_API_KEY is not configured.")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    message = body.get("message", "").strip()
    context_type = body.get("context_type", "")
    context_data = body.get("context_data", {})

    if not message:
        raise HTTPException(status_code=400, detail="Message is required.")
    if len(message) > 1000:
        raise HTTPException(status_code=400, detail="Message too long. Maximum 1000 characters.")
    if context_type not in ("statement", "receipt", "bulk_receipt", "bulk_statement"):
        raise HTTPException(status_code=400, detail="Invalid context_type. Must be one of: statement, receipt, bulk_receipt, bulk_statement.")

    formatted_context, currency_code = _format_chat_context(context_type, context_data)
    currency_symbol = _currency_symbol(currency_code)

    # Explicitly tell the AI the currency of the data \u2014 previously this said
    # "use GBP" while the data was rendered with $ signs, so Claude reasonably
    # ignored the instruction and labelled UK statements as USD. The symbol
    # we tell the model here MUST match the symbol used inside the data block
    # below (both come from the same `_statement_currency()` call).
    system_prompt = (
        "You are a helpful financial assistant for BankScan AI. The user has uploaded "
        "bank statements and/or store receipts. Answer their questions based ONLY on "
        "the data provided below.\n"
        f"\nCurrency: ALL amounts in the data below are in {currency_code} "
        f"({currency_symbol}). When you quote amounts back, ALWAYS use the {currency_symbol} "
        f"symbol \u2014 never reinterpret the data as USD or any other currency.\n"
        "\nBe concise, specific, and show your working when calculating totals. "
        "If the data doesn't contain the answer, say so clearly.\n\n"
        f"Here is the uploaded financial data:\n{formatted_context}"
    )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": message}],
        )

        ai_text = response.content[0].text
        tokens_used = (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)

        # Increment daily chat usage counter
        increment_chat_usage(user["id"])

        return JSONResponse({
            "response": ai_text,
            "tokens_used": tokens_used,
        })

    except ImportError:
        raise HTTPException(status_code=501, detail="Anthropic SDK is not installed.")
    except Exception as e:
        logger.exception("Chat API error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get AI response. Please try again.")


# ==========================================================================
# Stripe Billing Routes
# ==========================================================================

@app.post("/api/billing/start-trial-checkout")
@limiter.limit("3/minute")
async def start_trial_checkout(request: Request):
    """Create a Stripe Checkout session in subscription mode with a 7-day trial.

    Requires the user to be logged in and email-verified. Refuses if the user
    already has a subscription (any status — they should use the customer
    portal to manage it). Grandfathered users don't need this; the gate page
    routes them away before they get here, but we re-check server-side.

    Returns ``{"checkout_url": "https://checkout.stripe.com/..."}``; the
    client redirects there. Stripe is the source of truth for state; we
    don't optimistically mark trialing until the webhook fires.
    """
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if not is_email_verified(user["id"]):
        raise HTTPException(status_code=403, detail="Please verify your email first.")

    # Note: grandfathered users used to be 409'd here. They're now allowed
    # through so they can enter a card and start the new 7-day countdown.

    if has_active_subscription(user):
        raise HTTPException(status_code=409, detail="You already have an active subscription.")

    from services.billing import create_trial_checkout_session
    origin = get_safe_origin(request)
    try:
        url = create_trial_checkout_session(
            user=user,
            success_url=f"{origin}/?trial=started",
            cancel_url=f"{origin}/start-trial?canceled=1",
        )
    except ValueError as e:
        msg = str(e)
        # The service raises ValueError both for misconfiguration AND for
        # "customer already has an active subscription" (Stripe-side guard).
        # The latter is a 409, not a 500.
        if "active subscription" in msg.lower():
            raise HTTPException(status_code=409, detail="You already have an active subscription.")
        logger.exception("Trial checkout misconfigured")
        raise HTTPException(status_code=500, detail=f"Billing misconfigured: {e}")
    except Exception:
        logger.exception("Trial checkout creation failed for user %s", user["id"])
        raise HTTPException(status_code=502, detail="Could not start trial. Please try again.")

    return JSONResponse({"checkout_url": url})


@app.post("/api/create-checkout")
@limiter.limit("5/minute")
async def create_checkout_session(request: Request):
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    plan = body.get("plan", "starter")
    email = user["email"]

    plan_price_map = {
        "starter": STRIPE_STARTER_PRICE_ID,
        "pro": STRIPE_PRO_PRICE_ID,
        "business": STRIPE_BUSINESS_PRICE_ID,
        "enterprise": STRIPE_ENTERPRISE_PRICE_ID,
    }
    price_id = plan_price_map.get(plan, "")
    if not price_id:
        raise HTTPException(status_code=500, detail="Stripe price not configured for this plan.")

    try:
        existing = stripe.Customer.search(query=f'email:"{email}"')
        if existing.data:
            customer_id = existing.data[0].id
        else:
            customer = stripe.Customer.create(email=email, metadata={"source": "bankparse"})
            customer_id = customer.id

        # Link stripe customer to user record
        update_user(user["id"], stripe_customer_id=customer_id)

        origin = get_safe_origin(request)
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{origin}/?session_id={{CHECKOUT_SESSION_ID}}&status=success",
            cancel_url=f"{origin}/?status=cancelled",
            metadata={"user_id": str(user["id"])},
        )

        response = JSONResponse({"checkout_url": session.url, "session_id": session.id})
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Checkout error")
        raise HTTPException(status_code=500, detail="Checkout failed. Please try again.")


@app.post("/api/credits/checkout")
@limiter.limit("5/minute")
async def create_credit_pack_checkout(request: Request):
    """Create a Stripe one-time-payment checkout for an AI credit pack.

    Paid users who've exhausted their monthly AI budget can pre-purchase a
    credit pack; once paid, ``checkout.session.completed`` (mode=payment)
    tops up ``ai_credit_balance_gbp`` by the pack face value.
    """
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    pack_id = body.get("pack", "small")

    import ai_pricing
    pack = ai_pricing.CREDIT_PACKS.get(pack_id)
    amount_pence = ai_pricing.credit_pack_stripe_amount(pack_id)
    if not pack or not amount_pence:
        raise HTTPException(status_code=400, detail="Unknown credit pack.")

    email = user["email"]

    try:
        existing = stripe.Customer.search(query=f'email:"{email}"')
        if existing.data:
            customer_id = existing.data[0].id
        else:
            customer = stripe.Customer.create(email=email, metadata={"source": "bankparse"})
            customer_id = customer.id

        update_user(user["id"], stripe_customer_id=customer_id)

        origin = get_safe_origin(request)
        session = stripe.checkout.Session.create(
            mode="payment",
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "product_data": {
                        "name": f"BankScan AI — {pack['label']}",
                        "description": "Pre-purchased AI parsing credit pack. Non-refundable. Each parse deducts the exact Anthropic token cost.",
                    },
                    "unit_amount": amount_pence,
                },
                "quantity": 1,
            }],
            success_url=f"{origin}/credits?status=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/credits?status=cancelled",
            metadata={
                "user_id": str(user["id"]),
                "credit_pack": pack_id,
                "credit_amount_gbp": str(pack["amount_gbp"]),
                "purchase_type": "ai_credit_pack",
            },
        )

        return JSONResponse({"checkout_url": session.url, "session_id": session.id})

    except HTTPException:
        raise
    except Exception:
        logger.exception("Credit pack checkout error")
        raise HTTPException(status_code=500, detail="Credit pack checkout failed. Please try again.")


@app.get("/api/verify-session")
async def verify_checkout_session(request: Request, session_id: str):
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    try:
        checkout = stripe.checkout.Session.retrieve(session_id)

        if checkout.payment_status == "paid" and checkout.customer:
            # Update logged-in user's stripe_customer_id
            user = get_current_user(request)
            if user:
                update_user(user["id"], stripe_customer_id=checkout.customer)

            customer = stripe.Customer.retrieve(checkout.customer)

            response = JSONResponse({
                "status": "active",
                "email": customer.email,
            })
            return response

        return JSONResponse({"status": "pending"})

    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook dispatcher.

    Idempotent — every event is deduped by ``event.id`` via the
    ``processed_webhooks`` table so retries don't double-apply state changes.

    Handled events:
      - ``checkout.session.completed`` (mode=subscription) → trial start, set
        trial_end_at + stripe_subscription_id. Delegated to
        ``services.billing.handle_checkout_completed``.
      - ``checkout.session.completed`` (mode=payment, ai_credit_pack) → credit
        top-up (legacy path, retained inline).
      - ``customer.subscription.updated`` / ``customer.subscription.deleted`` →
        mirror status + trial_end_at from Stripe truth.
      - ``invoice.payment_failed`` → mark past_due ahead of the subsequent
        subscription.updated.
    """
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        return JSONResponse({"status": "ignored"})

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    event_id = event.get("id", "")
    event_type = event.get("type", "")
    event_data = event.get("data", {}).get("object", {})

    from services.billing import (
        was_processed,
        mark_processed,
        handle_checkout_completed,
        handle_subscription_lifecycle,
        handle_payment_failed,
    )

    # Stripe retries failed webhooks for up to 3 days; dedup at the edge.
    if was_processed(event_id):
        return JSONResponse({"status": "ok", "duplicate": True})

    try:
        if event_type == "checkout.session.completed":
            mode = event_data.get("mode", "")
            metadata = event_data.get("metadata") or {}
            customer_id = event_data.get("customer")

            if mode == "payment" and metadata.get("purchase_type") == "ai_credit_pack":
                # One-time credit pack purchase — top up ai_credit_balance_gbp
                if event_data.get("payment_status") == "paid":
                    from database import add_credit_balance
                    try:
                        amount_gbp = float(metadata.get("credit_amount_gbp", "0"))
                    except (TypeError, ValueError):
                        amount_gbp = 0.0
                    user_id_str = metadata.get("user_id", "")
                    target_user = None
                    if user_id_str:
                        try:
                            target_user = get_user_by_id(int(user_id_str))
                        except (TypeError, ValueError):
                            target_user = None
                    if not target_user and customer_id:
                        target_user = get_user_by_stripe_customer(customer_id)
                    if target_user and amount_gbp > 0:
                        add_credit_balance(target_user["id"], amount_gbp)
                        logger.info(
                            "Credited user %s with £%.2f AI credit pack (%s)",
                            target_user["id"], amount_gbp, metadata.get("credit_pack", "?"),
                        )
                    else:
                        logger.warning(
                            "Credit pack webhook: could not resolve user (customer=%s, metadata=%s)",
                            customer_id, metadata,
                        )
            elif mode == "subscription":
                # Card-on-file trial OR direct paid subscription. Delegate to
                # billing service which fetches the subscription and writes
                # the canonical state (subscription_id, trial_end_at, status).
                handle_checkout_completed(event_data)
            else:
                logger.info("checkout.session.completed: unhandled mode=%s", mode)

        elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
            handle_subscription_lifecycle(event_data)

        elif event_type == "invoice.payment_failed":
            handle_payment_failed(event_data)

    except Exception:
        # Don't mark processed on failure — Stripe will retry. Logging here
        # so we have a record of which event_id failed.
        logger.exception("Stripe webhook handler failed for event %s (%s)", event_id, event_type)
        raise HTTPException(status_code=500, detail="Webhook handler failed.")

    mark_processed(event_id, event_type)
    return JSONResponse({"status": "ok"})


@app.post("/api/manage-billing")
async def manage_billing(request: Request):
    """Create a Stripe Billing Portal session for subscription management."""
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    stripe_customer_id = user.get("stripe_customer_id")
    if not stripe_customer_id:
        raise HTTPException(status_code=400, detail="No subscription found. You need to subscribe first.")

    try:
        origin = get_safe_origin(request)
        portal_session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{origin}/",
        )
        return JSONResponse({"portal_url": portal_session.url})
    except Exception as e:
        logger.exception("Billing portal error: %s", e)
        raise HTTPException(status_code=500, detail="Billing request failed. Please try again.")


@app.post("/api/cancel-subscription")
@limiter.limit("5/minute")
async def cancel_subscription(request: Request):
    """One-click cancel — schedules the user's Stripe subscription to end
    at the current period's close. They keep access until then; no refunds.
    Idempotent: re-calling on an already-cancelling sub is a no-op.

    Returns ``{"status": "ok", "cancel_at": <unix-ts>}`` on success."""
    # Auth + has-something-to-cancel check FIRST. A 501 about Stripe config
    # before these would (a) leak information to anonymous callers about
    # which routes exist and (b) misorder the tests (an anonymous user
    # should see 401, not "Stripe not configured"; a free-tier user
    # should see 400, not 501). The Stripe import-availability check
    # stays — but the secret-key check is removed: a user can only hold
    # `stripe_subscription_id` if the key was valid at the time of
    # checkout, and at this point Stripe.modify() will raise its own
    # AuthenticationError if the key has since gone stale, which we
    # catch in the except block below as a 500.
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    sub_id = user.get("stripe_subscription_id")
    if not sub_id:
        raise HTTPException(
            status_code=400,
            detail="No active subscription to cancel.",
        )

    if not STRIPE_AVAILABLE:
        raise HTTPException(status_code=501, detail="Stripe library not installed.")

    try:
        sub = stripe.Subscription.modify(
            sub_id,
            cancel_at_period_end=True,
        )
        cancel_at = getattr(sub, "cancel_at", None) or getattr(sub, "current_period_end", None)
        return JSONResponse({
            "status": "ok",
            "cancel_at": cancel_at,
            "message": "Subscription cancelled. You keep access until your current period ends.",
        })
    except Exception as e:
        logger.exception("Cancel subscription error for user %s: %s", user.get("id"), e)
        raise HTTPException(
            status_code=500,
            detail="Cancellation failed. Please use Manage Subscription as a backup, or contact support.",
        )


# ==========================================================================
# Password reset — email-token flow
# ==========================================================================

@app.post("/api/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(request: Request):
    """Initiate a password reset.

    Always returns 200 (no email enumeration). Generates a single-use
    token, stores it server-side, and emails the reset link to the user
    if the email exists. If it doesn't, we silently no-op so an attacker
    can't probe for valid accounts.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required.")

    from database import get_user_by_email, create_password_reset_token
    from otp import send_password_reset_email

    user = get_user_by_email(email)
    if user:
        token = create_password_reset_token(user["id"])
        origin = get_safe_origin(request)
        reset_link = f"{origin}/reset-password?token={token}"
        try:
            send_password_reset_email(email, reset_link)
        except Exception:
            logger.exception("Failed to send password reset email to %s", email)

    # Always return ok, regardless of whether the email existed.
    return JSONResponse({
        "status": "ok",
        "message": "If an account exists for that email, a reset link has been sent.",
    })


@app.post("/api/reset-password")
@limiter.limit("5/minute")
async def reset_password(request: Request):
    """Complete a password reset.

    Body: ``{"token": "...", "password": "newpass1234"}``.
    Verifies the token, updates password_hash, marks the token used.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    token = (body.get("token") or "").strip()
    new_password = body.get("password") or ""

    if not token:
        raise HTTPException(status_code=400, detail="Reset token is required.")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    from database import consume_password_reset_token, update_user

    user_id = consume_password_reset_token(token)
    if user_id is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")

    update_user(user_id, password_hash=hash_password(new_password))
    return JSONResponse({"status": "ok", "message": "Password updated. You can now sign in."})


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html")


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    return templates.TemplateResponse(request, "reset_password.html")


# ==========================================================================
# QuickBooks Online (Intuit) integration
# ==========================================================================

import quickbooks as qbo
from core import INTUIT_AVAILABLE
from database import get_qbo_connection


@app.get("/api/qbo/status")
async def qbo_status(request: Request):
    """Return connection status for the current user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not INTUIT_AVAILABLE:
        return JSONResponse({"available": False, "connected": False})
    conn = get_qbo_connection(user["id"])
    if not conn:
        return JSONResponse({"available": True, "connected": False})
    return JSONResponse({
        "available": True,
        "connected": True,
        "realm_id": conn["realm_id"],
        "company_name": conn.get("company_name"),
        "environment": conn["environment"],
        "connected_at": conn["connected_at"],
    })


@app.get("/api/qbo/connect")
async def qbo_connect(request: Request):
    """Redirect the user to Intuit's OAuth consent page."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login?next=/api/qbo/connect", status_code=302)
    if not INTUIT_AVAILABLE:
        raise HTTPException(status_code=501, detail="QuickBooks integration is not configured on this server.")
    url = qbo.build_authorize_url(user["id"])
    return RedirectResponse(url=url, status_code=302)


@app.get("/api/qbo/callback")
async def qbo_callback(request: Request):
    """Intuit redirects here with `?code=...&state=...&realmId=...`."""
    if not INTUIT_AVAILABLE:
        raise HTTPException(status_code=501, detail="QuickBooks integration is not configured on this server.")

    params = dict(request.query_params)
    error = params.get("error")
    if error:
        msg = params.get("error_description", error)
        return RedirectResponse(url=f"/?qbo=error&msg={msg}", status_code=302)

    code = params.get("code")
    state = params.get("state")
    realm_id = params.get("realmId")
    if not code or not state or not realm_id:
        raise HTTPException(status_code=400, detail="Missing code, state, or realmId.")

    user_id = qbo.verify_state(state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired state token. Please retry the connection.")

    # Make sure the logged-in user matches the state token (defence-in-depth).
    user = get_current_user(request)
    if not user or user["id"] != user_id:
        return RedirectResponse(url="/login?next=/api/qbo/connect", status_code=302)

    try:
        token_response = qbo.exchange_code_for_tokens(code)
    except Exception:
        logger.exception("QBO token exchange failed for user %s", user_id)
        return RedirectResponse(url="/?qbo=error&msg=token_exchange_failed", status_code=302)

    qbo.store_initial_connection(user_id, token_response, realm_id)
    # Best-effort: pull and cache the company name.
    try:
        qbo.get_company_info(user_id)
    except Exception:
        pass

    return RedirectResponse(url="/?qbo=connected", status_code=302)


@app.post("/api/qbo/disconnect")
async def qbo_disconnect(request: Request):
    """Revoke the QBO connection for the current user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    qbo.disconnect(user["id"])
    return JSONResponse({"status": "disconnected"})


@app.get("/api/qbo/accounts")
async def qbo_accounts(request: Request):
    """List the user's QBO accounts so they can pick where to push transactions."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    conn = get_qbo_connection(user["id"])
    if not conn:
        raise HTTPException(status_code=400, detail="QuickBooks not connected.")
    try:
        bank = qbo.list_accounts(user["id"], "Bank")
        expense = qbo.list_accounts(user["id"], "Expense")
        income = qbo.list_accounts(user["id"], "Income")
        cards = qbo.list_accounts(user["id"], "Credit Card")
    except Exception:
        logger.exception("QBO list accounts failed")
        raise HTTPException(status_code=502, detail="Could not load accounts from QuickBooks.")
    return JSONResponse({
        "bank_accounts": [{"id": a["Id"], "name": a["Name"], "type": a.get("AccountType")} for a in (bank + cards)],
        "expense_accounts": [{"id": a["Id"], "name": a["Name"]} for a in expense],
        "income_accounts": [{"id": a["Id"], "name": a["Name"]} for a in income],
    })


@app.post("/api/qbo/push")
@limiter.limit("5/minute")
async def qbo_push(request: Request):
    """Push parsed transactions into the user's QBO company."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if not get_qbo_connection(user["id"]):
        raise HTTPException(status_code=400, detail="QuickBooks not connected.")

    body = await request.json()
    transactions = body.get("transactions") or []
    bank_account_id = body.get("bank_account_id")
    expense_account_id = body.get("expense_account_id")
    income_account_id = body.get("income_account_id")

    if not transactions or not isinstance(transactions, list):
        raise HTTPException(status_code=400, detail="No transactions to push.")
    if not bank_account_id or not expense_account_id or not income_account_id:
        raise HTTPException(
            status_code=400,
            detail="bank_account_id, expense_account_id, and income_account_id are all required.",
        )
    if len(transactions) > 500:
        raise HTTPException(status_code=400, detail="Cannot push more than 500 transactions in one request.")

    try:
        summary = qbo.push_transactions(
            user_id=user["id"],
            transactions=transactions,
            bank_account_id=str(bank_account_id),
            expense_account_id=str(expense_account_id),
            income_account_id=str(income_account_id),
        )
    except Exception:
        logger.exception("QBO push failed for user %s", user["id"])
        raise HTTPException(status_code=502, detail="Push to QuickBooks failed. Please try again.")

    return JSONResponse(summary)


@app.get("/api/admin/subscribers")
async def admin_subscribers(request: Request):
    """Admin-only endpoint to check subscriber count and details."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    email = (user.get("email") or "").lower()
    if email not in UNLIMITED_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required.")

    subscribers = _fetchall_dicts(
        "SELECT id, email, subscription_status, stripe_customer_id, created_at "
        "FROM users WHERE subscription_status IN ('active', 'trialing')"
    )
    total_users = _fetchall_dicts("SELECT COUNT(*) as count FROM users")

    return JSONResponse({
        "total_users": total_users[0]["count"] if total_users else 0,
        "active_subscribers": len(subscribers),
        "subscribers": subscribers,
    })


@app.get("/api/admin/users")
async def admin_users(request: Request):
    """Admin-only endpoint to list all registered users."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    email = (user.get("email") or "").lower()
    if email not in UNLIMITED_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required.")

    users = _fetchall_dicts(
        "SELECT id, email, subscription_status, stripe_customer_id, created_at "
        "FROM users ORDER BY created_at DESC"
    )

    return JSONResponse({
        "total_users": len(users),
        "users": users,
    })


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(request: Request, user_id: int):
    """Admin-only endpoint to delete a user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    email = (user.get("email") or "").lower()
    if email not in UNLIMITED_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required.")

    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account.")

    delete_user(user_id)
    return JSONResponse({"ok": True})


@app.get("/api/admin/ai-spend")
async def admin_ai_spend(request: Request):
    """Admin-only snapshot of AI spend against the global + tier budgets.

    Returns: today's global spend vs the daily ceiling, the top 20 spenders
    this calendar month, and the most recent 50 usage rows for audit.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    email = (user.get("email") or "").lower()
    if email not in UNLIMITED_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required.")

    import ai_pricing
    from database import get_global_daily_ai_spend, get_recent_ai_usage

    global_today = get_global_daily_ai_spend()
    top_spenders = _fetchall_dicts(
        "SELECT id, email, subscription_status, ai_spend_this_month, "
        "ai_credit_balance_gbp, usage_month "
        "FROM users WHERE ai_spend_this_month > 0 "
        "ORDER BY ai_spend_this_month DESC LIMIT 20"
    )
    recent = get_recent_ai_usage(limit=50)

    return JSONResponse({
        "global_daily_budget_gbp": ai_pricing.AI_DAILY_BUDGET_GBP,
        "global_daily_spend_gbp": round(global_today, 4),
        "global_daily_remaining_gbp": round(
            max(0.0, ai_pricing.AI_DAILY_BUDGET_GBP - global_today), 4
        ),
        "user_daily_cap_gbp": ai_pricing.AI_USER_DAILY_CAP_GBP,
        "tier_monthly_budgets_gbp": ai_pricing.TIER_MONTHLY_AI_BUDGET_GBP,
        "top_spenders_this_month": top_spenders,
        "recent_calls": recent,
    })


@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "plans": {
            "starter": {"price": "$9.99/mo", "name": "BankScan AI Starter", "statements": 120, "receipts": 500, "clients": "5-10"},
            "pro": {"price": "$24.99/mo", "name": "BankScan AI Pro", "statements": 300, "receipts": 1500, "clients": "11-25"},
            "business": {"price": "$59.99/mo", "name": "BankScan AI Business", "statements": 840, "receipts": 5000, "clients": "26-70"},
            "enterprise": {"price": "$149/mo", "name": "BankScan AI Enterprise", "statements": "Unlimited", "receipts": "Unlimited", "clients": "71-1,000"},
        },
    })


# ==========================================================================
# Blog Routes
# ==========================================================================

BLOG_POSTS = {
    "convert-credit-card-statement-to-excel": {
        "title": "Convert Credit Card Statement to Excel — Complete UK Guide (2026)",
        "description": "Struggling to convert business credit card or expense card statements to Excel? Our complete UK guide shows bookkeepers how to handle Amex, Barclaycard, Capital On Tap, and more. No manual data entry.",
        "date": "2026-06-16",
        "author": "BankScan AI Team",
        "template": "blog/convert-credit-card-statement-to-excel.html",
        "keywords": [
            "convert credit card statement to Excel",
            "credit card statement Excel conversion UK",
            "business credit card statement converter",
            "Amex statement to Excel",
            "Barclaycard statement to Excel",
            "Capital On Tap statement Excel",
            "expense card statement data entry",
            "credit card PDF to CSV",
            "business credit card reconciliation",
            "how to convert credit card statements for bookkeeping UK"
        ],
        "related": ["batch-bank-statement-processing", "best-bank-statement-converters-2026", "cost-of-manual-data-entry-bookkeeping", "bank-statement-conversion-small-business-owners", "tax-season-bank-statement-conversion"],
    },
    "convert-first-direct-statement-to-excel": {
        "title": "Convert First Direct Bank Statement to Excel — Complete UK Guide (2026)",
        "description": "First Direct's minimalist PDF statements omit metadata that generic converters expect, and abbreviated telephone banking codes leave rows unreadable. Step-by-step guide for UK bookkeepers to convert First Direct statements to Excel, CSV, or Google Sheets cleanly.",
        "date": "2026-06-14",
        "author": "BankScan AI Team",
        "template": "blog/convert-first-direct-statement-to-excel.html",
        "keywords": ["convert First Direct bank statement to Excel", "First Direct statement Excel conversion", "First Direct PDF to Excel", "First Direct bank statement converter UK", "First Direct online banking export to Excel", "First Direct DD MMM YYYY date format", "First Direct statement to Xero", "First Direct to QuickBooks", "First Direct transaction type codes Excel", "First Direct PDF to CSV", "download First Direct statement Excel"],
        "related": ["convert-hsbc-statement-to-excel", "uk-bank-statement-formats-guide", "choosing-bank-statement-converter-buyers-guide", "import-bank-statements-into-xero-guide", "import-bank-statements-into-quickbooks-guide"],
    },
    "bank-statement-conversion-mortgage-applications": {
        "title": "Bank Statement Conversion for Mortgage Applications — The Complete UK Guide (2026)",
        "description": "Your mortgage application is on hold because your bank statements are the wrong format. PDFs that won't upload, app screenshots the lender rejected, and 3 months of transactions needed in Excel by Friday. Complete guide covering lender requirements, self-employed proof of income, multi-bank statement consolidation, and step-by-step conversion for UK mortgage applicants, brokers, and accountants.",
        "date": "2026-06-13",
        "author": "BankScan AI Team",
        "template": "blog/bank-statement-conversion-mortgage-applications.html",
        "keywords": ["bank statement conversion mortgage application UK", "convert bank statements for mortgage lender", "mortgage application bank statement format", "bank statement PDF to Excel mortgage", "mortgage broker bank statement processing", "bank statements for mortgage affordability check", "self-employed mortgage bank statement proof of income", "mortgage underwriter bank statement review", "bank statement to Excel for mortgage broker", "3 months bank statements mortgage application", "UK mortgage bank statement conversion guide", "convert scanned bank statements for mortgage", "multi-bank statement consolidation mortgage", "Halifax mortgage bank statement requirements"],
        "related": ["convert-scanned-bank-statements-to-excel", "uk-bank-statement-formats-guide", "choosing-bank-statement-converter-buyers-guide", "bank-statement-conversion-freelancers-self-employed", "bank-statement-conversion-small-business-owners", "bank-statement-data-security-gdpr-compliance"],
    },
    "open-banking-vs-bank-statement-conversion": {
        "title": "Open Banking vs Bank Statement Conversion — Which Should UK Bookkeepers Actually Use? (2026)",
        "description": "Open Banking feeds promised to eliminate manual bank statement processing for UK bookkeepers. In reality, connection failures, 90-day reauthorisation, and clients who still send PDFs mean most practices need both approaches. Honest comparison guide covering when to use each and the MTD compliance angle.",
        "date": "2026-06-12",
        "author": "BankScan AI Team",
        "template": "blog/open-banking-vs-bank-statement-conversion.html",
        "keywords": ["open banking vs bank statement conversion UK", "open banking feeds vs PDF statement", "bank feeds vs manual statement upload", "open banking bookkeeping UK", "should I use open banking or PDF conversion", "bank statement processing for accountants", "MTD bank feeds comparison", "UK bookkeeper open banking guide", "bank statement PDF to CSV vs bank feed", "hybrid bookkeeping workflow UK", "open banking limitations for accountants", "bank statement conversion backup for broken feeds"],
        "related": ["mtd-bank-statement-compliance-guide", "choosing-bank-statement-converter-buyers-guide", "batch-bank-statement-processing", "multi-currency-bank-statement-handling", "bank-statement-automation-accounting-firms"],
    },
    "outsourcing-vs-automating-bank-statement-data-entry": {
        "title": "Outsourcing vs Automating Bank Statement Data Entry — The Complete UK Bookkeeper's Guide (2026)",
        "description": "Should you hire a virtual assistant, outsource to a data entry service, or use AI automation? Compare costs, accuracy, turnaround time, and GDPR risks for UK bookkeeping firms processing 20–200+ client statements per month.",
        "date": "2026-06-11",
        "author": "BankScan AI Team",
        "template": "blog/outsourcing-vs-automating-bank-statement-data-entry.html",
        "keywords": ["outsourcing bank statement data entry UK", "automate bank statement processing vs VA", "bank statement data entry service cost comparison", "virtual assistant bookkeeping data entry UK", "AI bank statement automation vs outsourcing", "bookkeeping data entry outsourcing pros cons", "cost per bank statement data entry UK", "GDPR compliant bank statement processing UK", "bulk bank statement data entry for accountants", "manual vs automated bank statement processing"],
        "related": ["cost-of-manual-data-entry-bookkeeping", "bank-statement-automation-sole-practitioners", "bank-statement-automation-accounting-firms", "bank-statement-data-security-gdpr-compliance", "batch-bank-statement-processing"],
    },
    "self-assessment-tax-return-bank-statement-prep": {
        "title": "Self-Assessment Tax Return Bank Statement Prep — Complete UK Guide (2026)",
        "description": "January 31st Self Assessment deadline looming and you're staring at 12 months of unsorted bank statements? Complete step-by-step guide to preparing bank statements for your SA100 tax return — from categorising transactions to calculating allowable expenses. Stop typing at 10pm.",
        "date": "2026-06-10",
        "author": "BankScan AI Team",
        "template": "blog/self-assessment-tax-return-bank-statement-prep.html",
        "keywords": ["Self Assessment bank statement preparation", "SA100 bank statement guide", "tax return bank statement Excel", "HMRC Self Assessment allowable expenses from bank statements", "sole trader bank statement tax return", "self-employed bank statement tax prep UK", "January 31 deadline bank statements", "convert bank statements for tax return", "Self Assessment expense categorisation", "personal business split bank account tax return", "SA103 bank statement preparation", "HMRC simplified expenses bank statements"]
    },
    "mtd-bank-statement-compliance-guide": {
        "slug": "mtd-bank-statement-compliance-guide",
        "title": "Making Tax Digital & Bank Statements — The Complete UK Compliance Guide for Bookkeepers (2026)",
        "description": "MTD for Income Tax is rolling out from April 2026. Complete guide for UK bookkeepers: digital record-keeping requirements for bank statements, quarterly submissions, compatible software, and how to handle paper bank statements under MTD rules.",
        "date": "2026-06-09",
        "author": "BankScan AI Team",
        "template": "blog/mtd-bank-statement-compliance-guide.html",
        "keywords": ["Making Tax Digital bank statements", "MTD digital records bank statements", "MTD for Income Tax bookkeeping requirements 2026", "Making Tax Digital compatible software bank statements", "MTD quarterly submission bank statement records", "digital record keeping HMRC bank statements", "MTD bank statement PDF to digital format", "Making Tax Digital self-employed bank statements", "HMRC digital links bank statement spreadsheet", "MTD ITSA bank statement compliance"]
    },
    "ai-bank-statement-extraction-vs-ocr": {
        "slug": "ai-bank-statement-extraction-vs-ocr",
        "title": "AI Bank Statement Extraction vs Traditional OCR — Which Is Better for UK Accountants in 2026?",
        "description": "Compare AI-powered bank statement extraction with traditional OCR. Why UK accountants are switching: accuracy, data validation, 16+ bank formats, real-world comparison.",
        "date": "2026-06-08",
        "author": "BankScan AI Team",
        "template": "ai-bank-statement-extraction-vs-ocr.html",
        "keywords": ["ai bank statement extraction", "ocr vs ai bank statements", "automated bank statement data entry", "bank statement PDF to excel AI", "AI data extraction for accountants", "traditional OCR bank statements problems", "intelligent document processing bank statements", "AI bookkeeping automation UK"]
    },
    "bank-statement-automation-month-end-close": {
        "title": "Month-End Close Automation for Bookkeepers — The Complete UK Guide (2026)",
        "description": "Still processing client bank statements at 10pm on the last day of the month? Complete month-end close automation guide for UK bookkeepers — practical workflows, real bank format quirks, and tools that actually save hours.",
        "date": "2026-06-07",
        "author": "BankScan AI Team",
        "template": "blog/bank-statement-automation-month-end-close.html",
        "keywords": ["month-end close automation", "automate month-end bank statement processing UK", "bookkeeper month-end workflow", "bank statement data entry automation", "reduce month-end close time accounting"],
        "related": ["batch-bank-statement-processing", "bank-reconciliation-automation-uk-guide", "bank-statement-automation-accounting-firms", "bank-statement-automation-sole-practitioners", "cost-of-manual-data-entry-bookkeeping"],
    },
    "ai-transforming-bookkeeping-accounting-firms": {
        "title": "How AI Is Transforming Bookkeeping for UK Accounting Firms in 2026",
        "description": "Discover how AI-powered bookkeeping automation is helping UK accounting firms save hours on bank statement processing, reconciliation, and data entry.",
        "date": "2026-03-25",
        "author": "BankScan AI Team",
        "template": "blog/ai-transforming-bookkeeping-accounting-firms.html",
        "keywords": "AI bookkeeping, accounting automation UK, automate bank statement processing, AI accounting firms, bookkeeping automation",
    },
    "convert-bank-statement-pdf-to-excel": {
        "title": "How to Convert a Bank Statement PDF to Excel (5 Methods Compared)",
        "description": "Compare 5 methods to convert bank statement PDFs to Excel spreadsheets. From manual copy-paste to AI-powered tools like BankScan AI for UK accountants.",
        "date": "2026-03-25",
        "author": "BankScan AI Team",
        "template": "blog/convert-bank-statement-pdf-to-excel.html",
        "keywords": "convert bank statement to excel, bank statement PDF to Excel, bank statement to spreadsheet, UK bank statement converter",
    },
    "convert-hsbc-statement-to-excel": {
        "title": "Convert HSBC Bank Statement to Excel — Complete UK Guide (2026)",
        "description": "HSBC's multi-line transaction descriptions and grouped date headers break standard Excel imports every time. Complete guide for UK bookkeepers to convert HSBC personal, business, and Kinetic statements cleanly — without spending hours manually retyping rows.",
        "date": "2026-06-06",
        "author": "BankScan AI Team",
        "template": "blog/convert-hsbc-statement-to-excel-guide.html",
        "keywords": "convert HSBC bank statement to Excel, HSBC statement Excel conversion, HSBC PDF to Excel, HSBC bank statement converter UK, HSBC online banking export to Excel",
    },
    "convert-barclays-statement-to-excel": {
        "title": "How to Convert Barclays Bank Statements to Excel (2026 Guide)",
        "description": "Barclays bank statement PDFs contain invisible formatting characters that break Excel imports. Step-by-step guide for UK accountants to convert Barclays statements cleanly.",
        "date": "2026-05-03",
        "author": "BankScan AI Team",
        "template": "blog/convert-barclays-statement-to-excel.html",
        "keywords": "Barclays statement to Excel, convert Barclays bank statement, Barclays PDF to Excel, Barclays PDF to CSV, Barclays statement converter, Barclays to Xero, Barclays to QuickBooks",
    },
    "convert-monzo-statement-to-excel": {
        "title": "How to Convert Monzo Bank Statements to Excel (2026 Guide)",
        "description": "Monzo exports 17-column CSVs but bookkeeping software only needs 5. Learn how to clean Monzo statements for Xero, QuickBooks, and Excel imports.",
        "date": "2026-05-03",
        "author": "BankScan AI Team",
        "template": "blog/convert-monzo-statement-to-excel.html",
        "keywords": "Monzo statement to Excel, Monzo CSV to Xero, convert Monzo statement, Monzo PDF to Excel, Monzo 17 columns fix, Monzo bank statement converter",
    },
    "convert-natwest-santander-statement-to-excel": {
        "title": "How to Convert NatWest & Santander Bank Statements to Excel (2026 Guide)",
        "description": "NatWest and Santander statements have unique formatting quirks that trip up standard converters. Complete guide for UK accountants to convert both banks cleanly.",
        "date": "2026-05-03",
        "author": "BankScan AI Team",
        "template": "blog/convert-natwest-santander-statement-to-excel.html",
        "keywords": "NatWest statement to Excel, Santander statement to Excel, convert NatWest bank statement, Santander PDF to Excel, NatWest to Xero, Santander to QuickBooks",
    },
    "best-bank-statement-converters-2026": {
        "title": "Best Bank Statement Converters 2026: Honest Comparison for UK Accountants",
        "description": "Compare the 6 best bank statement converters in 2026. Honest reviews covering pricing, UK bank support, and features. Written for UK accountants by BankScan AI.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/best-bank-statement-converters-2026.html",
        "keywords": "best bank statement converter 2026, bank statement converter comparison, best PDF to Excel converter for accountants, cheapest bank statement converter, DocuClipper alternative, Statement Desk alternative, UK bank statement converter",
    },
    "cost-of-manual-data-entry-bookkeeping": {
        "title": "The True Cost of Manual Data Entry for UK Bookkeepers (2026)",
        "description": "How much does manual bank statement and receipt data entry actually cost your bookkeeping practice? We break down the numbers with real UK bookkeeper rates.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/cost-of-manual-data-entry-bookkeeping.html",
        "keywords": "cost of manual data entry accounting, cost of data entry bookkeeping, automate data entry bookkeeping, bookkeeping automation ROI, manual data entry cost UK, save time bookkeeping automation",
    },
    "how-to-reconcile-bank-statements-faster": {
        "title": "How to Reconcile Bank Statements Faster: A Guide for UK Bookkeepers",
        "description": "Bank reconciliation doesn't have to take hours. Learn practical techniques to speed up reconciliation — from AI bank statement conversion to smart matching strategies.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/how-to-reconcile-bank-statements-faster.html",
        "keywords": "reconcile bank statements faster, bank reconciliation tips, speed up bank reconciliation, bookkeeping reconciliation faster, automate bank reconciliation",
    },
    "uk-bank-statement-formats-guide": {
        "title": "UK Bank Statement Formats Compared: A Guide for Accountants",
        "description": "Every UK bank formats statements differently. Compare HSBC, Barclays, Lloyds, NatWest, Monzo, and Starling statement formats in one guide.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/uk-bank-statement-formats-guide.html",
        "keywords": "UK bank statement formats, HSBC statement format, Barclays statement format, Lloyds statement format, NatWest statement format, Monzo statement format, UK bank statement comparison",
    },
    "convert-lloyds-statement-to-excel": {
        "title": "How to Convert Lloyds Bank Statements to Excel (2026 Guide)",
        "description": "Lloyds bank statements use Payment/Receipt columns with transaction type codes. Step-by-step guide for UK accountants to convert Lloyds statements cleanly.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/convert-lloyds-statement-to-excel.html",
        "keywords": "Lloyds statement to Excel, convert Lloyds bank statement, Lloyds PDF to Excel, Lloyds statement converter",
    },
    "convert-starling-statement-to-excel": {
        "title": "How to Convert Starling Bank Statements to Excel (2026 Guide)",
        "description": "Starling Bank statements include Spaces transfers and spending insights. Guide for UK accountants to convert Starling statements to Excel.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/convert-starling-statement-to-excel.html",
        "keywords": "Starling statement to Excel, convert Starling bank statement, Starling PDF to Excel, Starling statement converter",
    },
    "convert-revolut-statement-to-excel": {
        "title": "How to Convert Revolut Bank Statements to Excel (2026 Guide)",
        "description": "Revolut statements include multi-currency, crypto, and Vault transfers. Guide for UK accountants to convert Revolut statements to Excel.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/convert-revolut-statement-to-excel.html",
        "keywords": "Revolut statement to Excel, convert Revolut bank statement, Revolut PDF to Excel, Revolut statement converter",
    },
    "convert-tide-statement-to-excel": {
        "title": "How to Convert Tide Bank Statements to Excel (2026 Guide)",
        "description": "Tide business bank statements include transaction tags and invoicing references. Guide for UK accountants to convert Tide statements to Excel.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/convert-tide-statement-to-excel.html",
        "keywords": "Tide statement to Excel, convert Tide bank statement, Tide PDF to Excel, Tide statement converter",
    },
    "convert-nationwide-statement-to-excel": {
        "title": "How to Convert Nationwide Bank Statements to Excel (2026 Guide)",
        "description": "Nationwide is the UK's largest building society. Guide for accountants to convert Nationwide statements to Excel.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/convert-nationwide-statement-to-excel.html",
        "keywords": "Nationwide statement to Excel, convert Nationwide bank statement, Nationwide PDF to Excel, Nationwide statement converter",
    },
    "convert-halifax-statement-to-excel": {
        "title": "How to Convert Halifax Bank Statements to Excel (2026 Guide)",
        "description": "Halifax statements share Lloyds Banking Group DNA with distinct layouts. Guide for accountants to convert Halifax statements to Excel.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/convert-halifax-statement-to-excel.html",
        "keywords": "Halifax statement to Excel, convert Halifax bank statement, Halifax PDF to Excel, Halifax statement converter",
    },
    "convert-santander-statement-to-excel": {
        "title": "How to Convert Santander Bank Statements to Excel (2026 Guide)",
        "description": "Santander UK statements use two-column debit/credit layout with 1|2|3 cashback entries. Guide for accountants to convert Santander statements to Excel.",
        "date": "2026-05-08",
        "author": "BankScan AI Team",
        "template": "blog/convert-santander-statement-to-excel.html",
        "keywords": "Santander statement to Excel, convert Santander bank statement, Santander PDF to Excel, Santander 123 account statement",
    },
    "batch-bank-statement-processing": {
        "title": "Batch Bank Statement Processing for Accounting Firms — Complete Guide",
        "description": "UK accounting firms lose 10-20+ hours a month manually converting bank statements. Guide to batch processing, what to look for, and how to cut processing time by 90%.",
        "date": "2026-05-09",
        "author": "BankScan AI Team",
        "template": "blog/batch-bank-statement-processing.html",
        "keywords": "batch convert bank statements for accounting firms, bulk bank statement processing UK, automate bank statement data entry for bookkeepers, convert multiple bank statements to excel at once",
    },
    "convert-tsb-statement-to-excel": {
        "title": "How to Convert TSB Bank Statements to Excel (2026 Guide)",
        "description": "TSB statements use DD MMM YYYY dates and truncate transaction descriptions at 18 characters. Complete guide for UK accountants to convert TSB statements to Excel cleanly.",
        "date": "2026-05-10",
        "author": "BankScan AI Team",
        "template": "blog/convert-tsb-statement-to-excel.html",
        "keywords": "TSB statement to Excel, convert TSB bank statement, TSB PDF to Excel, TSB PDF to CSV, TSB statement converter, TSB to Xero, TSB to QuickBooks",
    },
    "convert-metro-bank-statement-to-excel": {
        "title": "How to Convert Metro Bank Statements to Excel (2026 Guide)",
        "description": "Metro Bank statements use dual Money In/Money Out columns and the app doesn't support downloads. Complete guide for UK accountants to convert Metro Bank statements to Excel cleanly.",
        "date": "2026-05-11",
        "author": "BankScan AI Team",
        "template": "blog/convert-metro-bank-statement-to-excel.html",
        "keywords": "Metro Bank statement to Excel, convert Metro Bank statement, Metro Bank PDF to Excel, Metro Bank PDF to CSV, Metro Bank statement converter, Metro Bank to Xero, Metro Bank to QuickBooks",
    },
    "convert-chase-uk-statement-to-excel": {
        "title": "How to Convert Chase UK Bank Statements to Excel (2026 Guide)",
        "description": "Chase UK statements include reward cashback entries and categorised spending that break generic converters. Step-by-step guide for UK accountants to convert Chase bank statements to Excel cleanly.",
        "date": "2026-05-12",
        "author": "BankScan AI Team",
        "template": "blog/convert-chase-uk-statement-to-excel.html",
        "keywords": "Chase UK statement to Excel, convert Chase bank statement, Chase UK PDF to Excel, Chase UK PDF to CSV, Chase bank statement converter, Chase UK to Xero, Chase UK to QuickBooks, Chase reward cashback statement",
    },
    "convert-virgin-money-statement-to-excel": {
        "title": "How to Convert Virgin Money Bank Statements to Excel (2026 Guide)",
        "description": "Virgin Money statements inherit formatting quirks from legacy Clydesdale and Yorkshire Bank systems. Step-by-step guide for UK accountants to convert Virgin Money bank statements to Excel cleanly — including legacy-format accounts.",
        "date": "2026-05-13",
        "author": "BankScan AI Team",
        "template": "blog/convert-virgin-money-statement-to-excel.html",
        "keywords": "Virgin Money statement to Excel, convert Virgin Money bank statement, Virgin Money PDF to Excel, Virgin Money PDF to CSV, Virgin Money statement converter, Virgin Money to Xero, Virgin Money to QuickBooks, Clydesdale Bank statement to Excel, Yorkshire Bank statement to Excel",
    },
    "convert-cooperative-bank-statement-to-excel": {
        "title": "How to Convert Co-operative Bank Statements to Excel (2026 Guide)",
        "description": "Co-operative Bank statements use a traditional Debit/Credit/Balance three-column layout that breaks most generic PDF converters. Step-by-step guide for UK accountants to convert Co-operative Bank statements to Excel, CSV, or Google Sheets cleanly.",
        "date": "2026-05-15",
        "author": "BankScan AI Team",
        "template": "blog/convert-cooperative-bank-statement-to-excel.html",
        "keywords": "Co-operative Bank statement to Excel, convert Co-operative Bank statement, Co-op Bank PDF to Excel, Co-operative Bank PDF to CSV, Co-operative Bank statement converter, Co-operative Bank to Xero, Co-operative Bank to QuickBooks",
    },
    "import-bank-statements-into-quickbooks-guide": {
        "title": "How to Import Bank Statements into QuickBooks — Complete UK Guide (2026)",
        "description": "Step-by-step guide to importing UK bank statements into QuickBooks Online and Desktop. Covers bank feeds vs manual upload, supported file formats, common QuickBooks import errors with bank-specific fixes for Barclays, HSBC, Monzo, NatWest, and Lloyds, plus how BankScan AI pre-cleans statements for QuickBooks-ready CSV import.",
        "date": "2026-05-17",
        "author": "BankScan AI Team",
        "template": "blog/import-bank-statements-into-quickbooks-guide.html",
        "keywords": "import bank statements into QuickBooks, QuickBooks bank statement import guide UK, how to upload bank statements to QuickBooks Online, QuickBooks CSV upload format, QuickBooks bank feed not working UK, convert bank statement for QuickBooks, QuickBooks PDF bank statement import",
    },
    "import-bank-statements-into-xero-guide": {
        "title": "How to Import Bank Statements into Xero — Complete UK Guide (2026)",
        "description": "Step-by-step guide to importing any UK bank statement into Xero. Covers bank feeds vs manual import, CSV formatting requirements, common Xero import errors and fixes, bank-specific quirks, and how to pre-clean statements for Xero-compatible import.",
        "date": "2026-05-14",
        "author": "BankScan AI Team",
        "template": "blog/import-bank-statements-into-xero-guide.html",
        "keywords": "import bank statements into Xero, Xero bank statement import guide, how to upload bank statements to Xero, Xero bank feed not working, Xero CSV upload format, Xero PDF bank statement import, import bank statement to Xero without bank feed, Xero bank reconciliation import",
    },
    "bank-statement-automation-sole-practitioners": {
        "title": "Bank Statement Automation for Sole Practitioners — The Complete UK Guide (2026)",
        "description": "As a sole practitioner, every hour spent typing bank statements is an hour you can't bill. Practical guide covering unique solo-practice challenges, time-cost comparisons, automation options ranked by solo-friendliness, and a start-today workflow for self-employed bookkeepers and accountants.",
        "date": "2026-05-16",
        "author": "BankScan AI Team",
        "template": "blog/bank-statement-automation-sole-practitioners.html",
        "keywords": "bank statement automation sole practitioner UK, automate bank statements for self-employed bookkeeper, sole trader bookkeeping automation, bank statement converter for sole practitioners, save time bookkeeping sole practice, PDF bank statement automation small practice",
    },
    "convert-scanned-bank-statements-to-excel": {
        "title": "How to Convert Scanned Paper Bank Statements to Excel (2026 Guide)",
        "description": "Scanned bank statements are image-based PDFs with no text layer — standard converters can't read them. Complete OCR guide for UK accountants and bookkeepers: tool comparison, step-by-step workflow, and common OCR errors to watch for.",
        "date": "2026-05-18",
        "author": "BankScan AI Team",
        "template": "blog/convert-scanned-bank-statements-to-excel.html",
        "keywords": "convert scanned bank statement to Excel, OCR bank statement to Excel, scanned PDF bank statement converter, paper bank statement to spreadsheet, convert image bank statement to Excel, OCR for bank statements UK, bank statement scanning to Excel",
    },
    "multi-currency-bank-statement-handling": {
        "title": "Multi-Currency Bank Statement Handling for UK Bookkeepers — Complete Guide (2026)",
        "description": "Struggling with Revolut, Wise, or international business account statements in EUR, USD, and GBP? Complete guide to multi-currency statement handling for UK bookkeepers — exchange rates, reconciliation, VAT, and automation.",
        "date": "2026-05-19",
        "author": "BankScan AI Team",
        "template": "blog/multi-currency-bank-statement-handling.html",
        "keywords": "multi-currency bank statement handling UK, foreign currency bank statement to Excel, Revolut multi-currency statement conversion, Wise borderless account statement Excel, GBP equivalent bank statement calculation, convert foreign currency bank statement for Xero, multi-currency bookkeeping reconciliation UK",
    },
    "ecommerce-bank-statement-conversion": {
        "title": "Bank Statement Conversion for E-Commerce Sellers — Complete UK Guide (2026)",
        "description": "Amazon, Shopify, and eBay sellers deal with complex bank statements packed with payment processor fees, marketplace settlements, refunds, chargebacks, and multi-currency. Complete guide to converting e-commerce bank statements to Excel for UK bookkeepers and sellers.",
        "date": "2026-05-20",
        "author": "BankScan AI Team",
        "template": "blog/ecommerce-bank-statement-conversion.html",
        "keywords": "ecommerce bank statement conversion UK, Amazon seller bank statement to Excel, Shopify statement converter, eBay payout statement Excel, marketplace seller bookkeeping, PayPal statement to Xero, Stripe statement conversion UK, ecommerce accounting automation",
    },
    "tax-season-bank-statement-conversion": {
        "title": "Bank Statement Conversion for Tax Season — Complete UK Guide (2026)",
        "description": "Dreading the January 31 Self Assessment deadline? Complete guide to handling bank statement conversion during tax season. Covers SA deadlines, VAT quarter-ends, year-end client onboarding, and how to avoid last-minute panic when clients deliver 18 months of statements on January 28th.",
        "date": "2026-05-21",
        "author": "BankScan AI Team",
        "template": "blog/tax-season-bank-statement-conversion.html",
        "keywords": "tax season bank statement conversion UK, Self Assessment bank statement preparation, year-end bank statement processing, January tax deadline bank statements, VAT quarter-end statement conversion, convert bank statements for tax return, bookkeeper tax season automation UK",
    },
    "bank-statement-data-security-gdpr-compliance": {
        "title": "Bank Statement Data Security & GDPR Compliance — A Guide for UK Bookkeepers (2026)",
        "description": "Are your clients' bank statements sitting unprotected in email inboxes, free cloud tools, or unencrypted laptops? Complete GDPR compliance guide for UK bookkeepers — ICO requirements, data residency, secure file transfer, breach response, and practical steps to protect your practice from fines.",
        "date": "2026-05-22",
        "author": "BankScan AI Team",
        "template": "blog/bank-statement-data-security-gdpr.html",
        "keywords": "bank statement data security UK, GDPR bank statement processing, secure bank statement conversion, accountant data protection compliance, ICO financial data guidance, cloud tool GDPR bookkeepers, client bank statement security, UK data residency bank statements",
    },
    "bank-statement-conversion-property-investors": {
        "title": "Bank Statement Conversion for Property Investors & Landlords — Complete UK Guide (2026)",
        "description": "Managing bank statements across a buy-to-let portfolio? Complete guide to converting bank and mortgage statements for property investors and landlords — from multi-property reconciliation to SA105 tax return preparation.",
        "date": "2026-05-23",
        "author": "BankScan AI Team",
        "template": "blog/bank-statement-conversion-property-investors.html",
        "keywords": "bank statement conversion property investors UK, landlord bank statement to Excel, buy-to-let statement processing, property portfolio bank statement management, SA105 bank statement preparation, rental income bank statement reconciliation, multiple property bank statement conversion, HMO landlord bookkeeping automation",
    },
    "bank-statement-conversion-freelancers-self-employed": {
        "title": "Bank Statement Conversion for Freelancers & Self-Employed — Complete UK Guide (2026)",
        "description": "Freelancers and sole traders face unique bank statement challenges — irregular income, mixed personal/business accounts, and Self Assessment deadlines. Complete guide to converting bank statements for self-employed UK bookkeeping.",
        "date": "2026-05-26",
        "author": "BankScan AI Team",
        "template": "blog/bank-statement-conversion-freelancers.html",
        "keywords": "bank statement conversion freelancers UK, self-employed bank statement to Excel, sole trader bank statement processing, freelancer Self Assessment bank statements, HMRC record keeping sole trader, convert bank statements for tax return freelancer, freelance bookkeeping automation UK, self-employed expense tracking bank statements",
    },
    "bank-statement-conversion-small-business-owners": {
        "title": "Bank Statement Conversion for Small Business Owners — Complete UK Guide (2026)",
        "description": "Running a small Ltd company and drowning in bank statements? Complete guide to converting business bank statements for bookkeeping, VAT returns, MTD compliance, and corporation tax — built for directors who do their own books after hours.",
        "date": "2026-05-27",
        "author": "BankScan AI Team",
        "template": "blog/bank-statement-conversion-small-business.html",
        "keywords": "bank statement conversion small business UK, small business bank statement to Excel, Ltd company bank statement processing, MTD for VAT bank statements, director account bank statement conversion, small business bookkeeping automation, convert business bank statements for corporation tax, multi-account bank statement management UK",
    },
    "choosing-bank-statement-converter-buyers-guide": {
        "title": "How to Choose the Right Bank Statement Converter — A Buyer's Guide for UK Accountants (2026)",
        "description": "Choosing the wrong bank statement converter costs your practice time, accuracy, and client trust. Complete buyer's guide with evaluation framework, vendor questions, red flags, and a practical checklist for UK accountants.",
        "date": "2026-05-28",
        "author": "BankScan AI Team",
        "template": "blog/choosing-bank-statement-converter-buyers-guide.html",
        "keywords": "bank statement converter buyer's guide UK, how to choose bank statement converter, best bank statement conversion tool for accountants, bank statement converter evaluation criteria, bank statement converter questions to ask, free vs paid bank statement converter, bank statement converter comparison checklist UK",
    },
    "bank-reconciliation-automation-uk-guide": {
        "title": "Bank Reconciliation Automation — A Complete Guide for UK Accountants (2026)",
        "description": "Still manually matching bank transactions at 10pm? Complete guide to bank reconciliation automation for UK accountants and bookkeepers — practical tools, workflows, and the real ROI of automating your bank rec process.",
        "date": "2026-05-29",
        "author": "BankScan AI Team",
        "template": "blog/bank-reconciliation-automation-uk-guide.html",
        "keywords": "bank reconciliation automation UK, automate bank reconciliation, bank rec software for accountants, reconciliation automation tools UK, auto-match bank transactions, bank statement reconciliation software, bookkeeping reconciliation automation, reduce reconciliation time accounting",
    },
    "quickbooks-bank-statement-import-guide": {
        "title": "QuickBooks Bank Statement Import Guide for UK Accountants (2026)",
        "description": "Your client's bank statement imports into QuickBooks as garbled data at 10pm. Here's exactly why QuickBooks import fails, which UK banks cause the worst problems, and what actually works.",
        "date": "2026-05-30",
        "author": "BankScan AI Team",
        "template": "blog/quickbooks-bank-statement-import-guide.html",
        "keywords": "QuickBooks bank statement import UK, QuickBooks import guide accountants, QuickBooks CSV import not working, QuickBooks bank feed import errors, import bank statements into QuickBooks Online, QuickBooks PDF statement import fails, QuickBooks wrong date format, QuickBooks transaction mismatch, convert bank statement for QuickBooks UK",
    },
    "xero-bank-statement-import-guide": {
        "title": "Xero Bank Statement Import Guide for UK Accountants (2026)",
        "description": "Your client's bank statement just imported into Xero as garbled data at 10pm. Here's exactly why Xero import fails, which UK banks cause the worst problems, and what actually works in 2026.",
        "date": "2026-05-31",
        "author": "BankScan AI Team",
        "template": "blog/xero-bank-statement-import-guide.html",
        "keywords": "Xero bank statement import UK, Xero CSV import not working, import bank statements into Xero, Xero bank feed not working UK, Xero PDF statement import, Xero bank reconciliation import problems, multi-currency statement import Xero, convert bank statement for Xero UK",
    },
    "sage-bank-statement-import-guide": {
        "title": "Sage Bank Statement Import Guide for UK Accountants (2026)",
        "description": "Sage bank feeds failing with UK statements? Complete guide to CSV imports, date format fixes, VAT handling, and PDF conversion for Sage 50 and Sage Business Cloud.",
        "date": "2026-06-01",
        "author": "BankScan AI Team",
        "template": "blog/sage-bank-statement-import-guide.html",
        "keywords": "Sage bank statement import UK, Sage 50 CSV import guide, Sage Business Cloud bank feed not working, import PDF bank statement into Sage, Sage bank statement import errors, convert bank statement for Sage UK, Sage import date format wrong, Sage accounting bank statement setup UK",
    },
    "bank-statement-automation-accounting-firms": {
        "title": "Bank Statement Automation for Accounting Firms — How to Scale Across 30–200+ Clients",
        "description": "Bank statement data entry doesn't scale past 30 clients. See the real maths at 50, 100, and 200 clients, and how automating statement processing transforms practice margins.",
        "date": "2026-06-02",
        "author": "BankScan AI Team",
        "template": "blog/bank-statement-automation-accounting-firms.html",
        "keywords": "bank statement automation for accounting firms UK, automate bank statement data entry accounting practice, bank statement processing for multiple clients, accounting firm bank statement conversion scaling, practice management bank statement automation, reduce bank statement processing cost accounting firm, automated bank statement processing ROI for accountants, BankScan AI 22-bank UK parser",
    },
    "vat-bank-statement-reconciliation-guide": {
        "title": "VAT Bank Statement Reconciliation Guide for UK Businesses (2026)",
        "description": "Complete guide to VAT bank statement reconciliation for UK businesses. Covers common VAT reconciliation errors, HMRC MTD requirements, step-by-step process, practice scenarios, and how to avoid penalties.",
        "date": "2026-06-03",
        "author": "BankScan AI Team",
        "template": "blog/vat-bank-statement-reconciliation-guide.html",
        "keywords": "VAT bank statement reconciliation, VAT return bank statement preparation, Making Tax Digital bank data, HMRC VAT reconciliation guide, bank statement VAT categories, MTD for VAT bank statements, UK VAT reconciliation mistakes, automated VAT bank statement reconciliation",
    },
    "paper-bank-statement-digitisation-guide-uk-bookkeepers": {
        "title": "Paper Bank Statement Digitisation Guide for UK Bookkeepers (2026)",
        "description": "Still typing paper bank statements by hand at 10pm? Complete guide to digitising paper statements for UK bookkeepers — scanning vs photographing, OCR pitfalls, batch workflows, and how BankScan AI eliminates manual data entry entirely.",
        "date": "2026-06-04",
        "author": "BankScan AI Team",
        "template": "blog/paper-bank-statement-digitisation-guide-uk-bookkeepers.html",
        "keywords": "paper bank statement digitisation UK, scan bank statements to Excel, photograph bank statement OCR, paper statement to spreadsheet bookkeepers, digitise paper bank statements UK, convert paper bank statements for Xero, paper bank statement OCR guide, scan paper statements for QuickBooks",
    },
    "import-bank-statements-into-freeagent-guide": {
        "title": "How to Import Bank Statements into FreeAgent — Complete UK Guide (2026)",
        "description": "FreeAgent bank feed failed at 10pm and now you're manually formatting a CSV? Complete guide to importing any UK bank statement into FreeAgent — CSV formatting rules, common date errors, and bank-specific fixes for NatWest, Monzo, HSBC, Barclays, and Revolut.",
        "date": "2026-06-05",
        "author": "BankScan AI Team",
        "template": "blog/import-bank-statements-into-freeagent-guide.html",
        "keywords": "FreeAgent bank statement import UK, import bank statements into FreeAgent, FreeAgent CSV upload format, FreeAgent bank feed not working, convert bank statement for FreeAgent, FreeAgent PDF statement import, FreeAgent import date format wrong, FreeAgent bank statement converter UK",
    },
    "handling-duplicate-bank-statements-guide": {
        "title": "Handling Duplicate Bank Statements — UK Bookkeeper's Guide (2026)",
        "description": "Client sent the same bank statement twice and you've just spent 20 minutes reconciling it all over again? Complete UK bookkeeper's guide to detecting, preventing, and handling duplicate bank statements before they wreck your month-end close.",
        "date": "2026-06-16",
        "author": "BankScan AI Team",
        "template": "blog/handling-duplicate-bank-statements-guide.html",
        "keywords": [
            "duplicate bank statements bookkeeping UK",
            "client sent same statement twice",
            "detect duplicate bank statements",
            "prevent duplicate bank statement processing",
            "bank statement reconciliation duplicates",
            "duplicate PDF bank statements bookkeeper",
            "avoid processing same statement twice",
            "bank statement deduplication UK",
            "duplicate bank statement detection workflow",
            "stop processing duplicate client statements"
        ],
        "related": ["bank-reconciliation-automation-uk-guide", "batch-bank-statement-processing", "bank-statement-automation-month-end-close", "how-to-reconcile-bank-statements-faster", "cost-of-manual-data-entry-bookkeeping"],
    },
}


@app.get("/blog", response_class=HTMLResponse)
async def blog_index(request: Request):
    """Blog listing page."""
    posts = [
        {"slug": slug, **data}
        for slug, data in sorted(BLOG_POSTS.items(), key=lambda x: x[1]["date"], reverse=True)
    ]
    response = templates.TemplateResponse(request, "blog/index.html", {"posts": posts})
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=3600"
    return response


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(request: Request, slug: str):
    """Individual blog post page."""
    if slug not in BLOG_POSTS:
        raise HTTPException(status_code=404, detail="Blog post not found")
    post = BLOG_POSTS[slug]
    response = templates.TemplateResponse(request, post["template"], {"post": post, "slug": slug})
    response.headers["Cache-Control"] = "public, max-age=86400, s-maxage=86400"
    return response


def _generate_blog_image(slug: str, image_type: str) -> bytes:
    """Generate raster PNG blog images using Pillow for Google Image Search indexing."""
    from PIL import Image, ImageDraw, ImageFont
    import io

    post = BLOG_POSTS[slug]

    # Try to load a TTF font, fall back to default
    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        font_xs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_num = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_step = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except (OSError, IOError):
        font_lg = ImageFont.load_default()
        font_md = font_sm = font_xs = font_label = font_num = font_step = font_lg

    PRIMARY = (27, 79, 114)
    PRIMARY_DARK = (21, 67, 96)
    ACCENT = (39, 174, 96)
    WHITE = (255, 255, 255)
    LIGHT_BG = (240, 244, 248)
    BORDER = (213, 219, 222)
    TEXT = (44, 62, 80)
    TEXT_LIGHT = (93, 109, 126)
    RED = (231, 76, 60)

    if image_type == "og":
        # --- OG Image: 1200x630 branded card ---
        img = Image.new("RGB", (1200, 630), PRIMARY)
        draw = ImageDraw.Draw(img)
        # Gradient effect via overlapping rectangles
        for i in range(630):
            r = int(PRIMARY[0] + (PRIMARY_DARK[0] - PRIMARY[0]) * i / 630)
            g = int(PRIMARY[1] + (PRIMARY_DARK[1] - PRIMARY[1]) * i / 630)
            b = int(PRIMARY[2] + (PRIMARY_DARK[2] - PRIMARY[2]) * i / 630)
            draw.line([(0, i), (1200, i)], fill=(r, g, b))
        # Border frame
        draw.rounded_rectangle([40, 40, 1160, 590], radius=16, outline=(255, 255, 255, 25), width=2)
        # Title text - word wrap
        title = post["title"]
        words = title.split()
        lines, current = [], ""
        for w in words:
            test = f"{current} {w}".strip()
            bbox = draw.textbbox((0, 0), test, font=font_lg)
            if bbox[2] - bbox[0] > 1040:
                lines.append(current)
                current = w
            else:
                current = test
        if current:
            lines.append(current)
        y = 200
        for line in lines:
            draw.text((60, y), line, fill=WHITE, font=font_lg)
            y += 56
        # Meta line
        draw.text((60, y + 20), f"{post['date']}  |  {post['author']}", fill=(255, 255, 255, 150), font=font_sm)
        # Brand
        draw.text((60, 550), "BankScan", fill=WHITE, font=font_md)
        bbox = draw.textbbox((60, 550), "BankScan", font=font_md)
        draw.text((bbox[2], 550), "AI", fill=ACCENT, font=font_md)
        draw.text((1140, 558), "bankscanai.com", fill=(180, 200, 220), font=font_xs, anchor="ra")

    elif image_type == "hero":
        # --- Hero: 760x280 PDF -> AI -> Excel workflow ---
        img = Image.new("RGB", (760, 280), LIGHT_BG)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, 759, 279], radius=12, outline=BORDER, width=1)

        # PDF document
        draw.rounded_rectangle([50, 45, 180, 205], radius=8, fill=WHITE, outline=BORDER)
        draw.rounded_rectangle([65, 65, 120, 82], radius=3, fill=RED)
        draw.text((75, 66), "PDF", fill=WHITE, font=font_xs)
        for i, y in enumerate(range(92, 185, 14)):
            w = [90, 75, 85, 60, 90, 70][i % 6]
            draw.rounded_rectangle([65, y, 65 + w, y + 6], radius=2, fill=BORDER)
        draw.text((115, 215), "Bank Statement", fill=TEXT_LIGHT, font=font_xs, anchor="mt")

        # Arrow 1
        draw.line([(200, 125), (270, 125)], fill=PRIMARY, width=2)
        draw.polygon([(270, 118), (285, 125), (270, 132)], fill=PRIMARY)

        # AI brain circle
        draw.ellipse([325, 65, 435, 175], fill=(27, 79, 114, 25), outline=PRIMARY, width=2)
        draw.ellipse([345, 85, 415, 155], fill=PRIMARY)
        draw.text((380, 110), "AI", fill=WHITE, font=font_step, anchor="mt")
        draw.text((380, 190), "BankScan AI", fill=TEXT_LIGHT, font=font_xs, anchor="mt")
        draw.text((380, 208), "Extracts  |  Categorises  |  Validates", fill=TEXT_LIGHT, font=ImageFont.load_default(), anchor="mt")

        # Arrow 2
        draw.line([(455, 125), (525, 125)], fill=ACCENT, width=2)
        draw.polygon([(525, 118), (540, 125), (525, 132)], fill=ACCENT)

        # Excel document
        draw.rounded_rectangle([565, 45, 700, 205], radius=8, fill=WHITE, outline=BORDER)
        draw.rounded_rectangle([580, 65, 635, 82], radius=3, fill=ACCENT)
        draw.text((590, 66), "XLSX", fill=WHITE, font=font_xs)
        # Table grid
        for y in [92, 115, 138, 161]:
            draw.line([(580, y), (685, y)], fill=BORDER, width=1)
        for x in [610, 645]:
            draw.line([(x, 92), (x, 175)], fill=BORDER, width=1)
        # Checkmarks
        for cx in [595, 627, 662]:
            for cy in [100, 123, 146]:
                draw.text((cx, cy), "\u2713", fill=ACCENT, font=font_xs)
        draw.text((632, 215), "Clean Spreadsheet", fill=TEXT_LIGHT, font=font_xs, anchor="mt")

        # Bottom badge
        draw.rounded_rectangle([300, 240, 460, 268], radius=14, fill=(39, 174, 96, 30), outline=ACCENT)
        draw.text((380, 248), "Under 30 seconds", fill=ACCENT, font=font_label, anchor="mt")

    elif image_type == "infographic":
        # --- Infographic: 700x200 four-step process ---
        img = Image.new("RGB", (700, 200), LIGHT_BG)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, 699, 199], radius=10, outline=BORDER, width=1)

        steps = [
            ("1", "Upload PDF", "Any UK bank", "statement", PRIMARY),
            ("2", "AI Extracts", "Dates, amounts,", "descriptions", (46, 134, 193)),
            ("3", "Review", "Check flagged", "items only", ACCENT),
            ("4", "Export", "Xero, Sage,", "FreeAgent", ACCENT),
        ]
        for i, (num, title, line1, line2, color) in enumerate(steps):
            x_start = 18 + i * 170
            # Card
            draw.rounded_rectangle([x_start, 20, x_start + 148, 135], radius=8, fill=WHITE, outline=BORDER)
            # Number circle
            cx = x_start + 74
            draw.ellipse([cx - 18, 30, cx + 18, 66], fill=(*color, 25), outline=color, width=1)
            draw.text((cx, 42), num, fill=color, font=font_label, anchor="mt")
            # Step text
            draw.text((cx, 78), title, fill=TEXT, font=font_label, anchor="mt")
            draw.text((cx, 98), line1, fill=TEXT_LIGHT, font=ImageFont.load_default(), anchor="mt")
            draw.text((cx, 112), line2, fill=TEXT_LIGHT, font=ImageFont.load_default(), anchor="mt")
            # Arrow between steps
            if i < 3:
                ax = x_start + 156
                draw.text((ax, 70), "\u2192", fill=color, font=font_md)

        # Bottom label
        draw.text((350, 162), "Entire workflow completes in under 60 seconds", fill=PRIMARY, font=font_label, anchor="mt")
    else:
        raise ValueError(f"Unknown image type: {image_type}")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()


@app.get("/blog/{slug}/og-image")
async def blog_og_image(slug: str):
    """Generate a PNG Open Graph image for social sharing and Google Image Search."""
    if slug not in BLOG_POSTS:
        raise HTTPException(status_code=404, detail="Blog post not found")
    from starlette.responses import Response
    png_bytes = _generate_blog_image(slug, "og")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, s-maxage=604800"},
    )


@app.get("/blog/{slug}/hero-image")
async def blog_hero_image(slug: str):
    """Generate a PNG hero image for the blog post (PDF -> AI -> Excel workflow)."""
    if slug not in BLOG_POSTS:
        raise HTTPException(status_code=404, detail="Blog post not found")
    from starlette.responses import Response
    png_bytes = _generate_blog_image(slug, "hero")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, s-maxage=604800"},
    )


@app.get("/blog/{slug}/infographic")
async def blog_infographic(slug: str):
    """Generate a PNG infographic image (4-step AI bookkeeping workflow)."""
    if slug not in BLOG_POSTS:
        raise HTTPException(status_code=404, detail="Blog post not found")
    from starlette.responses import Response
    png_bytes = _generate_blog_image(slug, "infographic")
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, s-maxage=604800"},
    )


# ==========================================================================
# Programmatic SEO — /tools routes
# ==========================================================================

@app.get("/tools", response_class=HTMLResponse)
async def tools_index(request: Request):
    """Tools directory page — lists all programmatic SEO pages by category."""
    bank_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "bank"]
    profession_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "profession"]
    receipt_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "receipt"]
    format_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "bank_format"]
    software_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "software"]
    use_case_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "use_case"]
    combo_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "bank_profession"]
    bank_software_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "bank_software"]
    bank_usecase_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "bank_usecase"]
    sw_prof_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "software_profession"]
    prof_uc_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "profession_usecase"]
    sw_uc_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "software_usecase"]
    bank_prof_fmt_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "bank_profession_format"]
    prof_fmt_pages = [(s, p) for s, p in sorted(SEO_PAGES.items()) if p["type"] == "profession_format"]
    response = templates.TemplateResponse(request, "tools/index.html", {
        "bank_pages": bank_pages,
        "profession_pages": profession_pages,
        "receipt_pages": receipt_pages,
        "format_pages": format_pages,
        "software_pages": software_pages,
        "use_case_pages": use_case_pages,
        "combo_pages": combo_pages,
        "bank_software_pages": bank_software_pages,
        "bank_usecase_pages": bank_usecase_pages,
        "sw_prof_pages": sw_prof_pages,
        "prof_uc_pages": prof_uc_pages,
        "sw_uc_pages": sw_uc_pages,
        "bank_prof_fmt_pages": bank_prof_fmt_pages,
        "prof_fmt_pages": prof_fmt_pages,
    })
    response.headers["Cache-Control"] = "public, max-age=86400, s-maxage=86400"
    return response


@app.get("/tools/{slug}", response_class=HTMLResponse)
async def tools_seo_page(request: Request, slug: str):
    """Individual programmatic SEO page."""
    if slug not in SEO_PAGES:
        raise HTTPException(status_code=404, detail="Tool page not found")
    page = SEO_PAGES[slug]

    # Build related pages: same type, max 3, excluding self
    related_pages = [
        {"slug": s, **p}
        for s, p in SEO_PAGES.items()
        if p["type"] == page["type"] and s != slug
    ][:3]

    response = templates.TemplateResponse(request, "tools/seo_page.html", {
        "page": page,
        "slug": slug,
        "related_pages": related_pages,
    })
    response.headers["Cache-Control"] = "public, max-age=86400, s-maxage=86400"
    return response


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return """User-agent: *
Allow: /landing
Allow: /login
Allow: /compare
Allow: /solutions
Allow: /blog
Allow: /tools
Disallow: /api/
Disallow: /downloads/
Sitemap: https://bankscanai.com/sitemap.xml"""


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap():
    urls = [
        '<url><loc>https://bankscanai.com/landing</loc><priority>1.0</priority><changefreq>weekly</changefreq></url>',
        '<url><loc>https://bankscanai.com/login</loc><priority>0.5</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/compare</loc><priority>0.8</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/compare/docuclipper</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/compare/statement-desk</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/import-bank-statement-without-bank-feed</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/quickbooks-desktop-eol</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/xero-pdf-import</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/import-bank-statements-into-quickbooks-online</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/import-bank-statements-into-sage</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/import-bank-statements-into-freeagent</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/bank-statement-conversion-for-year-end</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/receipt-scanner-for-accountants</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/compare/lido</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/compare/capyparse</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/convert-bank-statements-for-mortgage-application</loc><priority>0.6</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/bank-statement-conversion-for-audit</loc><priority>0.6</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/receipt-to-excel-guide</loc><priority>0.6</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/bank-statement-conversion-bookkeepers</loc><priority>0.6</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/solutions/convert-multiple-bank-statements-bulk</loc><priority>0.6</priority><changefreq>monthly</changefreq></url>',
        '<url><loc>https://bankscanai.com/blog</loc><priority>0.8</priority><changefreq>weekly</changefreq></url>',
    ]
    for slug, post in BLOG_POSTS.items():
        urls.append(
            f'<url><loc>https://bankscanai.com/blog/{slug}</loc><lastmod>{post["date"]}</lastmod><priority>0.7</priority><changefreq>monthly</changefreq>'
            f'<image:image><image:loc>https://bankscanai.com/blog/{slug}/hero-image</image:loc><image:title>{post["title"]}</image:title></image:image>'
            f'<image:image><image:loc>https://bankscanai.com/blog/{slug}/og-image</image:loc><image:title>{post["title"]} - Social Preview</image:title></image:image>'
            f'</url>'
        )
    # Programmatic SEO tool pages
    urls.append('<url><loc>https://bankscanai.com/tools</loc><priority>0.8</priority><changefreq>weekly</changefreq></url>')
    for slug in SEO_PAGES:
        urls.append(
            f'<url><loc>https://bankscanai.com/tools/{slug}</loc><priority>0.6</priority><changefreq>monthly</changefreq></url>'
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  {"".join(urls)}
</urlset>"""


@app.post("/api/web-vitals")
async def web_vitals(request: Request):
    """Collect Core Web Vitals metrics from real users (LCP, CLS, INP, FCP, TTFB)."""
    try:
        body = await request.body()
        data = json.loads(body)
        metric = data.get("name", "unknown")
        value = data.get("value", 0)
        rating = data.get("rating", "unknown")
        page = data.get("page", "/")
        logging.info(f"[WebVitals] {metric}={value} rating={rating} page={page}")
    except Exception:
        pass
    return JSONResponse({"ok": True}, status_code=204)


INDEXNOW_KEY = "bankscanai2026seokey"


@app.get(f"/{INDEXNOW_KEY}.txt", response_class=PlainTextResponse)
async def indexnow_key_file():
    """Serve the IndexNow API key verification file."""
    return INDEXNOW_KEY


@app.post("/api/indexnow")
async def submit_indexnow(request: Request):
    """Submit URLs to search engines via IndexNow (Bing, Yandex, DuckDuckGo, Naver).
    POST body: {"urls": ["/blog/ai-transforming-bookkeeping-accounting-firms"]}
    """
    import urllib.request
    try:
        body = await request.json()
        paths = body.get("urls", [])
    except Exception:
        paths = [f"/blog/{slug}" for slug in BLOG_POSTS] + [f"/tools/{slug}" for slug in SEO_PAGES]

    full_urls = [
        f"https://bankscanai.com{p}" if p.startswith("/") else p
        for p in paths
    ]

    payload = json.dumps({
        "host": "bankscanai.com",
        "key": INDEXNOW_KEY,
        "keyLocation": f"https://bankscanai.com/{INDEXNOW_KEY}.txt",
        "urlList": full_urls,
    }).encode()

    results = {}
    for engine, endpoint in [
        ("bing", "https://www.bing.com/indexnow"),
        ("yandex", "https://yandex.com/indexnow"),
    ]:
        try:
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                results[engine] = {"status": resp.status, "ok": resp.status in (200, 202)}
        except urllib.error.HTTPError as e:
            results[engine] = {"status": e.code, "ok": e.code in (200, 202)}
        except Exception as e:
            results[engine] = {"status": 0, "ok": False, "error": str(e)}

    results["google"] = {
        "note": "Google does not support IndexNow. Submit via Google Search Console > URL Inspection > Request Indexing.",
        "search_console_url": "https://search.google.com/search-console",
    }
    results["urls_submitted"] = full_urls
    return results


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "2.3.0",
        "stripe_configured": bool(STRIPE_SECRET_KEY),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
