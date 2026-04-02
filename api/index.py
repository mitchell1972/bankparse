"""
BankParse — Vercel Serverless Entry Point
Adapts the FastAPI app for Vercel's serverless Python runtime with Stripe billing.
Identity: email/password auth with cookie-based sessions.
Storage: SQLite database. Email verification via OTP for subscription restore.
"""

import os
import sys
import uuid
import base64
import logging
import sqlite3
import tempfile
import time
from pathlib import Path

# Add parent directory to path so parsers and core can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from typing import List

from parsers.pdf_parser import parse_pdf
from parsers.csv_parser import parse_csv
from parsers.xlsx_exporter import export_to_xlsx, export_receipt_to_xlsx
from parsers.receipt_parser import parse_receipt

# AI-powered parsers (optional — require anthropic SDK)
try:
    from parsers.ai_parser import parse_receipt_ai, parse_receipts_bulk, parse_statement_ai, parse_statements_bulk
    from parsers.xlsx_exporter import export_bulk_receipts_to_xlsx, export_bulk_statements_to_xlsx
    AI_PARSERS_AVAILABLE = True
except ImportError:
    AI_PARSERS_AVAILABLE = False
from database import (
    get_usage, save_usage, increment_usage, store_otp, verify_otp,
    create_user, get_user_by_email, update_user, increment_user_usage,
    get_user_by_stripe_customer,
    get_chat_usage, increment_chat_usage,
    get_monthly_scans, increment_monthly_scans,
    get_monthly_statements, increment_monthly_statements,
    get_monthly_receipts, increment_monthly_receipts,
    _fetchall_dicts,
)
from otp import generate_otp, send_otp_email
from ratelimit import check_rate_limit

from core import (
    # Constants
    SECRET_KEY, IS_PRODUCTION,
    STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET,
    STRIPE_STARTER_PRICE_ID, STRIPE_PRO_PRICE_ID, STRIPE_BUSINESS_PRICE_ID, STRIPE_ENTERPRISE_PRICE_ID,
    STRIPE_AVAILABLE,
    FREE_STATEMENT_LIMIT, FREE_RECEIPT_LIMIT,
    TIER_LIMITS,
    AUTH_COOKIE, AUTH_COOKIE_MAX_AGE,
    COOKIE_NAME, COOKIE_MAX_AGE,
    SUBSCRIPTION_CACHE_TTL, UNLIMITED_EMAILS,
    IMAGE_EXTENSIONS, RECEIPT_EXTENSIONS,
    # Auth helpers
    hash_password, verify_password,
    make_auth_token, verify_auth_token,
    get_current_user, set_auth_cookie, clear_auth_cookie,
    # Session helpers
    get_session_id, ensure_session, set_session_cookie,
    # Subscription helpers
    verify_subscription, check_can_use, get_user_tier,
)

# Re-import stripe if available (needed for direct Stripe API calls in routes)
try:
    import stripe
except ImportError:
    pass

# AI chat support (optional — requires anthropic SDK)
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

logger = logging.getLogger("bankparse")

# Use /tmp on Vercel for file operations
TMP_DIR = Path(tempfile.gettempdir()) / "bankparse"
TMP_DIR.mkdir(exist_ok=True)

# Read templates once at cold start
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "index.html"
TEMPLATE_HTML = TEMPLATE_PATH.read_text()
LANDING_PATH = Path(__file__).parent.parent / "templates" / "landing.html"
LANDING_HTML = LANDING_PATH.read_text() if LANDING_PATH.exists() else TEMPLATE_HTML
LOGIN_PATH = Path(__file__).parent.parent / "templates" / "login.html"
LOGIN_HTML = LOGIN_PATH.read_text() if LOGIN_PATH.exists() else ""

# Jinja2 for tools templates
from jinja2 import Environment, FileSystemLoader
TOOLS_TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "tools"
_jinja_env = Environment(loader=FileSystemLoader(str(TOOLS_TEMPLATE_DIR)))

# SEO pages (pre-built at import time)
from seo_pages import SEO_PAGES

# Blog templates
BLOG_DIR = Path(__file__).parent.parent / "templates" / "blog"
BLOG_INDEX_HTML = (BLOG_DIR / "index.html").read_text() if (BLOG_DIR / "index.html").exists() else ""
BLOG_TEMPLATES = {}
if BLOG_DIR.exists():
    for f in BLOG_DIR.glob("*.html"):
        if f.name != "index.html":
            BLOG_TEMPLATES[f.stem] = f.read_text()

# Vercel defaults ENVIRONMENT to "production"; validate SECRET_KEY accordingly
if IS_PRODUCTION and SECRET_KEY == "bankparse-dev-secret-change-me":
    raise RuntimeError("FATAL: SECRET_KEY must be set to a secure random value in production. Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")

app = FastAPI(title="BankScan AI", version="2.3.0")

from csrf import CSRFMiddleware
app.add_middleware(CSRFMiddleware)

ALLOWED_ORIGINS_REDIRECT = os.environ.get("ALLOWED_REDIRECT_ORIGINS", "https://bankscanai.com,http://localhost:8000").split(",")

def get_safe_origin(request: Request) -> str:
    origin = request.headers.get("origin", "")
    for allowed in ALLOWED_ORIGINS_REDIRECT:
        if origin == allowed.strip():
            return origin
    return ALLOWED_ORIGINS_REDIRECT[0].strip()


# ==========================================================================
# Page Routes
# ==========================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the login/register page."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(LOGIN_HTML)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/landing", status_code=302)
    return HTMLResponse(TEMPLATE_HTML)


@app.get("/landing", response_class=HTMLResponse)
async def landing():
    return HTMLResponse(LANDING_HTML)


# ==========================================================================
# Auth API — register, login, logout
# ==========================================================================

@app.post("/api/register")
async def register(request: Request):
    """Create a new user account."""
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"{client_ip}:/api/register", limit=5, window_seconds=60):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")

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

    response = JSONResponse({"status": "ok", "email": email})
    set_auth_cookie(response, user_id)
    return response


@app.post("/api/login")
async def login(request: Request):
    """Sign in with email and password."""
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"{client_ip}:/api/login", limit=10, window_seconds=60):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")

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

    response = JSONResponse({"status": "ok", "email": user["email"]})
    set_auth_cookie(response, user["id"])
    return response


@app.post("/api/logout")
async def logout(request: Request):
    """Clear the auth cookie."""
    response = JSONResponse({"status": "ok"})
    clear_auth_cookie(response)
    return response


# ==========================================================================
# Usage / Auth API
# ==========================================================================

@app.get("/api/usage")
async def get_usage_status(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({
            "has_subscription": False,
            "tier": "free",
            "tier_limits": TIER_LIMITS["free"],
            "statements_used": 0,
            "statements_limit": TIER_LIMITS["free"]["monthly_statements"],
            "receipts_used": 0,
            "receipts_limit": TIER_LIMITS["free"]["monthly_receipts"],
            "chat_used_today": 0,
            "chat_limit": 0,
            "email": None,
            "stripe_configured": bool(STRIPE_PUBLISHABLE_KEY),
        })

    tier = get_user_tier(user)
    is_subscriber = tier != "free"
    limits = TIER_LIMITS[tier]
    statements_used = get_monthly_statements(user["id"])
    receipts_used = get_monthly_receipts(user["id"])
    chat_used = get_chat_usage(user["id"]) if limits["chat_per_day"] else 0

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
    })


@app.post("/api/restore/request")
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
            raise HTTPException(status_code=404, detail="No subscription found for this email.")

        active_customer = None
        for customer in customers.data:
            subs = stripe.Subscription.list(customer=customer.id, status="active", limit=1)
            if subs.data:
                active_customer = customer
                break

        if not active_customer:
            raise HTTPException(status_code=404, detail="No active subscription found.")

        session_id = ensure_session(request)
        code = generate_otp()
        store_otp(email, code, session_id)

        if not send_otp_email(email, code):
            raise HTTPException(status_code=500, detail="Failed to send verification email.")

        response = JSONResponse({
            "status": "otp_sent",
            "message": "A verification code has been sent to your email.",
        })
        set_session_cookie(response, session_id)
        return response

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred.")


@app.post("/api/restore/verify")
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
        raise HTTPException(status_code=500, detail="An internal error occurred.")


# ==========================================================================
# Parse API (with usage gating)
# ==========================================================================

@app.post("/api/parse")
async def parse_statement(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    allowed, tier = check_can_use(user, "statement")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    limits = TIER_LIMITS[tier]

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in [".pdf", ".csv", ".tsv", ".txt"]):
        raise HTTPException(status_code=400, detail="Unsupported file type. Please upload a PDF or CSV file.")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 20MB.")

    safe_filename = Path(file.filename).name  # Strip path components
    job_id = str(uuid.uuid4())[:8]
    upload_path = TMP_DIR / f"{job_id}_{safe_filename}"
    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        if filename.endswith(".pdf"):
            result = parse_pdf(str(upload_path))
        else:
            result = parse_csv(str(upload_path))

        # AI fallback: only for Pro/Business tiers (ai_parsing == True)
        if not result["transactions"] and limits["ai_parsing"] and AI_PARSERS_AVAILABLE and ANTHROPIC_API_KEY:
            try:
                logger.info("No transactions from standard parser, trying AI fallback")
                result = parse_statement_ai(str(upload_path))
            except Exception as ai_err:
                logger.warning("AI statement parsing fallback failed: %s", ai_err)

        if not result["transactions"]:
            raise HTTPException(status_code=422, detail="No transactions found.")

        output_path = TMP_DIR / f"bankparse_{job_id}.xlsx"
        export_to_xlsx(result, str(output_path))
        xlsx_b64 = base64.b64encode(output_path.read_bytes()).decode("utf-8")

        # Increment monthly statement count for all tiers
        increment_monthly_statements(user["id"], 1)

        response = JSONResponse({
            "transactions": result["transactions"],
            "summary": result["summary"],
            "metadata": result["metadata"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"bankparse_{job_id}.xlsx",
        })
        return response
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        for f in [upload_path, TMP_DIR / f"bankparse_{job_id}.xlsx"]:
            if f.exists():
                f.unlink()


@app.post("/api/parse-receipt")
async def parse_receipt_endpoint(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    allowed, tier = check_can_use(user, "receipt")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    limits = TIER_LIMITS[tier]

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in RECEIPT_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 20MB.")

    safe_filename = Path(file.filename).name  # Strip path components
    job_id = str(uuid.uuid4())[:8]
    upload_path = TMP_DIR / f"{job_id}_{safe_filename}"
    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        # AI-powered parsing only for Pro/Business tiers
        result = None
        if limits["ai_parsing"] and AI_PARSERS_AVAILABLE and ANTHROPIC_API_KEY:
            try:
                result = parse_receipt_ai(str(upload_path))
            except Exception as ai_err:
                logger.warning("AI receipt parsing failed, falling back to standard: %s", ai_err)
                result = None

        # Fall back to standard parser
        if result is None or not result.get("items"):
            result = parse_receipt(str(upload_path))

        if not result["items"]:
            raise HTTPException(status_code=422, detail="No items found.")

        output_path = TMP_DIR / f"receipt_{job_id}.xlsx"
        export_receipt_to_xlsx(result, str(output_path))
        xlsx_b64 = base64.b64encode(output_path.read_bytes()).decode("utf-8")

        # Increment monthly receipt count for all tiers
        increment_monthly_receipts(user["id"], 1)

        response = JSONResponse({
            "items": result["items"],
            "totals": result["totals"],
            "metadata": result["metadata"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"receipt_{job_id}.xlsx",
        })
        return response
    except HTTPException:
        raise
    except ImportError as e:
        logger.exception("Missing dependency for receipt parsing: %s", e)
        raise HTTPException(status_code=501, detail="A required parsing dependency is not available.")
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        for f in [upload_path, TMP_DIR / f"receipt_{job_id}.xlsx"]:
            if f.exists():
                f.unlink()


@app.post("/api/parse-receipts-bulk")
async def parse_receipts_bulk_endpoint(request: Request, files: List[UploadFile] = File(...)):
    """Parse multiple receipts in a single batch and return a combined XLSX."""
    # Authentication
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    # Tier-based access control
    tier = get_user_tier(user)
    limits = TIER_LIMITS[tier]

    # Free tier cannot use bulk upload
    if limits["bulk_max_files"] == 0:
        raise HTTPException(status_code=403, detail="Bulk upload requires a paid subscription (Starter or above).")

    # AI parsers required for bulk
    if not AI_PARSERS_AVAILABLE or not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=501,
            detail="AI-powered parsing is not available. Please configure ANTHROPIC_API_KEY.",
        )

    # Rate limiting (database-backed, works across serverless instances)
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"{client_ip}:/api/parse-receipts-bulk", limit=5, window_seconds=60):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")

    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    if len(files) > limits["bulk_max_files"]:
        raise HTTPException(status_code=400, detail=f"Maximum {limits['bulk_max_files']} receipts per batch on your plan.")

    # Check monthly receipt limit for the batch
    monthly_limit = limits["monthly_receipts"]
    if monthly_limit is not None:
        receipts_used = get_monthly_receipts(user["id"])
        if receipts_used + len(files) > monthly_limit:
            remaining = max(0, monthly_limit - receipts_used)
            raise HTTPException(status_code=403, detail=f"Monthly receipt limit reached. You have {remaining} receipts remaining this month.")

    # Validate and save all files to /tmp
    upload_paths = []
    job_id = str(uuid.uuid4())[:8]

    try:
        for i, upload_file in enumerate(files):
            fname = upload_file.filename.lower()
            if not any(fname.endswith(ext) for ext in RECEIPT_EXTENSIONS):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type for '{upload_file.filename}'. Accepted: PDF, PNG, JPG, TIFF.",
                )

            contents = await upload_file.read()
            if len(contents) > 20 * 1024 * 1024:
                raise HTTPException(
                    status_code=400,
                    detail=f"File '{upload_file.filename}' is too large. Max 20MB per file.",
                )

            safe_name = Path(upload_file.filename).name
            upload_path = TMP_DIR / f"{job_id}_{i}_{safe_name}"
            with open(upload_path, "wb") as f:
                f.write(contents)
            upload_paths.append(str(upload_path))

        # Parse all receipts via AI
        results = parse_receipts_bulk(upload_paths)

        if not results:
            raise HTTPException(status_code=422, detail="No items found in any of the uploaded receipts.")

        # Generate combined XLSX
        output_path = TMP_DIR / f"receipts_bulk_{job_id}.xlsx"
        export_bulk_receipts_to_xlsx(results, str(output_path))
        xlsx_b64 = base64.b64encode(output_path.read_bytes()).decode("utf-8")

        # Increment monthly receipt count for all files in batch
        increment_monthly_receipts(user["id"], len(upload_paths))

        return JSONResponse({
            "receipts": results,
            "count": len(results),
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"receipts_bulk_{job_id}.xlsx",
        })

    except HTTPException:
        raise
    except ImportError as e:
        logger.exception("Missing dependency for bulk receipt parsing: %s", e)
        raise HTTPException(status_code=501, detail="A required parsing dependency is not available.")
    except Exception as e:
        logger.exception("Bulk receipt parsing error: %s", e)
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        # Clean up all temp files
        for p in upload_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
        output_file = TMP_DIR / f"receipts_bulk_{job_id}.xlsx"
        if output_file.exists():
            try:
                output_file.unlink()
            except OSError:
                pass


# ==========================================================================
# Bulk Statement Parsing
# ==========================================================================

@app.post("/api/parse-statements-bulk")
async def parse_statements_bulk_endpoint(request: Request, files: List[UploadFile] = File(...)):
    """Parse multiple bank statement files in a single batch."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    tier = get_user_tier(user)
    limits = TIER_LIMITS[tier]

    # Free tier cannot use bulk upload
    if limits["bulk_max_files"] == 0:
        raise HTTPException(status_code=403, detail="Bulk upload requires a paid subscription (Starter or above).")

    # AI parsers required for bulk
    if not AI_PARSERS_AVAILABLE or not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=501,
            detail="AI-powered parsing is not available. Please configure ANTHROPIC_API_KEY.",
        )

    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"{client_ip}:/api/parse-statements-bulk", limit=5, window_seconds=60):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")

    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    if len(files) > limits["bulk_max_files"]:
        raise HTTPException(status_code=400, detail=f"Maximum {limits['bulk_max_files']} statements per batch on your plan.")

    # Check monthly statement limit for the batch
    monthly_limit = limits["monthly_statements"]
    if monthly_limit is not None:
        statements_used = get_monthly_statements(user["id"])
        if statements_used + len(files) > monthly_limit:
            remaining = max(0, monthly_limit - statements_used)
            raise HTTPException(status_code=403, detail=f"Monthly statement limit reached. You have {remaining} statements remaining this month.")

    STATEMENT_EXTENSIONS = [".pdf", ".csv", ".tsv", ".txt"]
    for f in files:
        fname = f.filename.lower()
        if not any(fname.endswith(ext) for ext in STATEMENT_EXTENSIONS):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {f.filename}. Please upload PDF or CSV files.")

    upload_paths = []
    job_id = str(uuid.uuid4())[:8]

    try:
        for i, upload_file in enumerate(files):
            contents = await upload_file.read()
            if len(contents) > 20 * 1024 * 1024:
                raise HTTPException(status_code=400, detail=f"File '{upload_file.filename}' is too large. Max 20MB per file.")

            safe_name = Path(upload_file.filename).name
            upload_path = TMP_DIR / f"{job_id}_{i}_{safe_name}"
            with open(upload_path, "wb") as f:
                f.write(contents)
            upload_paths.append(str(upload_path))

        bulk_result = parse_statements_bulk(upload_paths)

        if not bulk_result["all_transactions"]:
            raise HTTPException(status_code=422, detail="No transactions found in any of the uploaded statements.")

        output_path = TMP_DIR / f"statements_bulk_{job_id}.xlsx"
        export_bulk_statements_to_xlsx(bulk_result, str(output_path))
        xlsx_b64 = base64.b64encode(output_path.read_bytes()).decode("utf-8")

        # Increment monthly statement count for all files in batch
        increment_monthly_statements(user["id"], len(upload_paths))

        return JSONResponse({
            "statements": bulk_result["statements"],
            "all_transactions": bulk_result["all_transactions"],
            "summary": bulk_result["summary"],
            "statement_count": bulk_result["statement_count"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"statements_bulk_{job_id}.xlsx",
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Bulk statement parsing error: %s", e)
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        for p in upload_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
        output_file = TMP_DIR / f"statements_bulk_{job_id}.xlsx"
        if output_file.exists():
            try:
                output_file.unlink()
            except OSError:
                pass


# ==========================================================================
# Stripe Billing Routes
# ==========================================================================

@app.post("/api/create-checkout")
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
            line_items=[{"price": price_id.strip(), "quantity": 1}],
            success_url=f"{origin}/?session_id={{CHECKOUT_SESSION_ID}}&status=success",
            cancel_url=f"{origin}/?status=cancelled",
            metadata={"user_id": str(user["id"])},
        )

        response = JSONResponse({"checkout_url": session.url, "session_id": session.id})
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Checkout error: %s", e)
        raise HTTPException(status_code=500, detail="An error occurred while creating the checkout session. Please try again.")


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
        raise HTTPException(status_code=500, detail="An internal error occurred.")


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        return JSONResponse({"status": "ignored"})

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Webhook signature verification failed.")

    event_type = event.get("type", "")
    event_data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        # Link subscription to user
        customer_id = event_data.get("customer")
        user_email = None
        if customer_id:
            try:
                customer = stripe.Customer.retrieve(customer_id)
                user_email = customer.email
            except Exception:
                pass
        if user_email:
            user = get_user_by_email(user_email)
            if user:
                update_user(user["id"], stripe_customer_id=customer_id, subscription_status="active", subscription_checked_at=time.time())

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub_status = event_data.get("status", "")
        customer_id = event_data.get("customer")
        if customer_id:
            # Find user by stripe_customer_id
            user = get_user_by_stripe_customer(customer_id)
            if user:
                is_active = sub_status in ("active", "trialing")
                update_user(user["id"], subscription_status="active" if is_active else "cancelled", subscription_checked_at=time.time())

    elif event_type == "invoice.payment_failed":
        customer_id = event_data.get("customer")
        if customer_id:
            user = get_user_by_stripe_customer(customer_id)
            if user:
                update_user(user["id"], subscription_status="past_due", subscription_checked_at=time.time())

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
        raise HTTPException(status_code=500, detail="An error occurred while accessing the billing portal. Please try again.")


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
# AI Chat API
# ==========================================================================

CHAT_SYSTEM_PROMPT = (
    "You are a helpful financial assistant for BankScan AI. The user has uploaded "
    "bank statements and/or store receipts. Answer their questions based ONLY on "
    "the data provided below. Be concise, specific, and use GBP (\u00a3) currency "
    "formatting. If asked to calculate totals, show your working. If the data "
    "doesn't contain the answer, say so clearly."
)


def _format_context_data(context_type: str, context_data: dict) -> str:
    """Format context_data into a text summary for the AI prompt.

    Sonnet 4 has 200K context — send ALL data for accurate answers.
    """
    MAX_ITEMS = 5000  # Sonnet 4 can handle thousands of transactions

    parts: list[str] = []

    if context_type == "statement":
        # Single statement
        if meta := context_data.get("metadata"):
            parts.append(f"Statement: {meta.get('bank', 'Unknown bank')}")
            if meta.get("period"):
                parts.append(f"Period: {meta['period']}")

        if summary := context_data.get("summary"):
            parts.append(f"Total income: \u00a3{summary.get('total_income', 0):.2f}")
            parts.append(f"Total expenses: \u00a3{summary.get('total_expenses', 0):.2f}")
            parts.append(f"Net: \u00a3{summary.get('net', 0):.2f}")
            parts.append(f"Transaction count: {summary.get('transaction_count', 0)}")

        txns = context_data.get("transactions", [])
        if txns:
            truncated = txns[:MAX_ITEMS]
            parts.append(f"\nTransactions ({len(truncated)} of {len(txns)}):")
            for t in truncated:
                date = t.get("date", "")
                desc = t.get("description", "")[:60]
                amount = t.get("amount", 0)
                parts.append(f"  {date}  {desc}  \u00a3{amount}")

    elif context_type == "receipt":
        # Single receipt
        if meta := context_data.get("metadata"):
            parts.append(f"Store: {meta.get('store_name', 'Unknown')}")
            if meta.get("date"):
                parts.append(f"Date: {meta['date']}")

        if totals := context_data.get("totals"):
            parts.append(f"Subtotal: \u00a3{totals.get('subtotal', 0):.2f}")
            parts.append(f"Tax: \u00a3{totals.get('tax', 0):.2f}")
            parts.append(f"Total: \u00a3{totals.get('total', 0):.2f}")

        items = context_data.get("items", [])
        if items:
            truncated = items[:MAX_ITEMS]
            parts.append(f"\nItems ({len(truncated)} of {len(items)}):")
            for it in truncated:
                name = it.get("description", it.get("name", ""))[:50]
                qty = it.get("quantity", 1)
                price = it.get("price", 0)
                parts.append(f"  {name} x{qty}  \u00a3{price}")

    elif context_type == "bulk_receipt":
        receipts = context_data.get("receipts", [])
        parts.append(f"Batch of {len(receipts)} receipts")
        if grand := context_data.get("grand_total"):
            parts.append(f"Grand total: \u00a3{grand:.2f}")

        item_count = 0
        for r in receipts:
            store = r.get("metadata", {}).get("store_name", "Unknown")
            total = r.get("totals", {}).get("total", 0)
            parts.append(f"\n--- {store} (total: \u00a3{total}) ---")
            for it in r.get("items", []):
                if item_count >= MAX_ITEMS:
                    parts.append("  ... (truncated)")
                    break
                name = it.get("description", it.get("name", ""))[:50]
                price = it.get("price", 0)
                parts.append(f"  {name}  \u00a3{price}")
                item_count += 1
            if item_count >= MAX_ITEMS:
                break

    elif context_type == "bulk_statement":
        statements = context_data.get("statements", [])
        parts.append(f"Batch of {len(statements)} statements")
        if summary := context_data.get("summary"):
            parts.append(f"Total income: \u00a3{summary.get('total_income', 0):.2f}")
            parts.append(f"Total expenses: \u00a3{summary.get('total_expenses', 0):.2f}")

        txn_count = 0
        for s in statements:
            bank = s.get("metadata", {}).get("bank", "Unknown")
            parts.append(f"\n--- {bank} ---")
            for t in s.get("transactions", []):
                if txn_count >= MAX_ITEMS:
                    parts.append("  ... (truncated)")
                    break
                date = t.get("date", "")
                desc = t.get("description", "")[:60]
                amount = t.get("amount", 0)
                parts.append(f"  {date}  {desc}  \u00a3{amount}")
                txn_count += 1
            if txn_count >= MAX_ITEMS:
                break

    else:
        parts.append(f"Context type: {context_type}")
        # Best-effort: dump a truncated repr
        raw = str(context_data)[:2000]
        parts.append(raw)

    return "\n".join(parts)


@app.post("/api/chat")
async def chat(request: Request):
    """AI chat endpoint — answer questions about uploaded financial data."""
    # Authentication
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    # Tier-based access control
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

    # Check anthropic availability
    if not ANTHROPIC_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="AI chat is not available. The anthropic SDK is not installed.",
        )
    if not ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=501,
            detail="AI chat is not configured. ANTHROPIC_API_KEY is not set.",
        )

    # Rate limiting (20/minute)
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(f"{client_ip}:/api/chat", limit=20, window_seconds=60):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")

    # Parse request body
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    message = body.get("message", "")
    context_type = body.get("context_type", "")
    context_data = body.get("context_data", {})

    # Validate message
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="Message is required.")
    if len(message) > 1000:
        raise HTTPException(status_code=400, detail="Message too long. Maximum 1000 characters.")

    # Validate context_type
    valid_types = {"statement", "receipt", "bulk_receipt", "bulk_statement"}
    if context_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid context_type. Must be one of: {', '.join(sorted(valid_types))}",
        )

    # Build prompt
    context_text = _format_context_data(context_type, context_data)

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=CHAT_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Here is the financial data:\n\n{context_text}\n\nQuestion: {message}",
                }
            ],
        )

        reply_text = response.content[0].text
        tokens_used = (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)

        # Increment daily chat usage counter
        increment_chat_usage(user["id"])

        return JSONResponse({
            "response": reply_text,
            "tokens_used": tokens_used,
        })

    except anthropic.AuthenticationError:
        raise HTTPException(status_code=501, detail="AI chat configuration error. Invalid API key.")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="AI service rate limit reached. Please try again shortly.")
    except Exception as e:
        logger.exception("Chat API error: %s", e)
        raise HTTPException(status_code=500, detail="An error occurred while processing your question. Please try again.")


BLOG_POSTS = {
    "ai-transforming-bookkeeping-accounting-firms": {
        "title": "How AI Is Transforming Bookkeeping for UK Accounting Firms in 2026",
        "date": "2026-03-25",
        "author": "BankScan AI Team",
    },
    "convert-bank-statement-pdf-to-excel": {
        "title": "How to Convert a Bank Statement PDF to Excel (5 Methods Compared)",
        "date": "2026-03-25",
        "author": "BankScan AI Team",
    },
    "convert-hsbc-statement-to-excel-guide": {
        "title": "How to Convert HSBC Bank Statements to Excel (2026 Guide)",
        "date": "2026-03-31",
        "author": "BankScan AI Team",
    },
    "import-bank-statements-into-xero": {
        "title": "How to Import Bank Statements into Xero (Step-by-Step for UK Accountants)",
        "date": "2026-03-31",
        "author": "BankScan AI Team",
    },
    "bank-statement-conversion-guide-accountants": {
        "title": "The UK Accountant's Complete Guide to Bank Statement Conversion (2026)",
        "date": "2026-03-31",
        "author": "BankScan AI Team",
    },
}


@app.get("/blog", response_class=HTMLResponse)
async def blog_index():
    from jinja2 import Environment, BaseLoader
    env = Environment(loader=BaseLoader())
    tmpl = env.from_string(BLOG_INDEX_HTML)
    posts = [
        {"slug": slug, "title": post["title"], "date": post["date"], "author": post["author"],
         "description": post["title"]}
        for slug, post in sorted(BLOG_POSTS.items(), key=lambda x: x[1]["date"], reverse=True)
    ]
    html = tmpl.render(posts=posts)
    resp = HTMLResponse(html)
    resp.headers["Cache-Control"] = "public, max-age=3600, s-maxage=3600"
    return resp


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(slug: str):
    if slug not in BLOG_TEMPLATES:
        raise HTTPException(status_code=404, detail="Blog post not found")
    resp = HTMLResponse(BLOG_TEMPLATES[slug])
    resp.headers["Cache-Control"] = "public, max-age=86400, s-maxage=86400"
    return resp


# ==========================================================================
# Tools / Programmatic SEO Routes
# ==========================================================================

@app.get("/tools", response_class=HTMLResponse)
async def tools_index():
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
    tmpl = _jinja_env.get_template("index.html")
    html = tmpl.render(
        bank_pages=bank_pages, profession_pages=profession_pages,
        receipt_pages=receipt_pages, format_pages=format_pages,
        software_pages=software_pages, use_case_pages=use_case_pages,
        combo_pages=combo_pages, bank_software_pages=bank_software_pages,
        bank_usecase_pages=bank_usecase_pages, sw_prof_pages=sw_prof_pages,
        prof_uc_pages=prof_uc_pages, sw_uc_pages=sw_uc_pages,
        bank_prof_fmt_pages=bank_prof_fmt_pages, prof_fmt_pages=prof_fmt_pages,
    )
    resp = HTMLResponse(html)
    resp.headers["Cache-Control"] = "public, max-age=86400, s-maxage=86400"
    return resp


@app.get("/tools/{slug}", response_class=HTMLResponse)
async def tools_seo_page(slug: str):
    """Individual programmatic SEO page."""
    if slug not in SEO_PAGES:
        raise HTTPException(status_code=404, detail="Tool page not found")
    page = SEO_PAGES[slug]
    related_pages = [
        {"slug": s, **p}
        for s, p in SEO_PAGES.items()
        if p["type"] == page["type"] and s != slug
    ][:3]
    tmpl = _jinja_env.get_template("seo_page.html")
    html = tmpl.render(page=page, slug=slug, related_pages=related_pages)
    resp = HTMLResponse(html)
    resp.headers["Cache-Control"] = "public, max-age=86400, s-maxage=86400"
    return resp


def _generate_blog_image(slug: str, image_type: str) -> bytes:
    """Generate raster PNG blog images using Pillow for Google Image Search indexing."""
    from PIL import Image, ImageDraw, ImageFont
    import io

    post = BLOG_POSTS[slug]

    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        font_xs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_step = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
    except (OSError, IOError):
        font_lg = ImageFont.load_default()
        font_md = font_sm = font_xs = font_label = font_step = font_lg

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
        img = Image.new("RGB", (1200, 630), PRIMARY)
        draw = ImageDraw.Draw(img)
        for i in range(630):
            r = int(PRIMARY[0] + (PRIMARY_DARK[0] - PRIMARY[0]) * i / 630)
            g = int(PRIMARY[1] + (PRIMARY_DARK[1] - PRIMARY[1]) * i / 630)
            b = int(PRIMARY[2] + (PRIMARY_DARK[2] - PRIMARY[2]) * i / 630)
            draw.line([(0, i), (1200, i)], fill=(r, g, b))
        draw.rounded_rectangle([40, 40, 1160, 590], radius=16, outline=(255, 255, 255, 25), width=2)
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
        draw.text((60, y + 20), f"{post['date']}  |  {post.get('author', 'BankScan AI Team')}", fill=(180, 200, 220), font=font_sm)
        draw.text((60, 550), "BankScan", fill=WHITE, font=font_md)
        bbox = draw.textbbox((60, 550), "BankScan", font=font_md)
        draw.text((bbox[2], 550), "AI", fill=ACCENT, font=font_md)
        draw.text((1140, 558), "bankscanai.com", fill=(180, 200, 220), font=font_xs, anchor="ra")

    elif image_type == "hero":
        img = Image.new("RGB", (760, 280), LIGHT_BG)
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([0, 0, 759, 279], radius=12, outline=BORDER, width=1)
        draw.rounded_rectangle([50, 45, 180, 205], radius=8, fill=WHITE, outline=BORDER)
        draw.rounded_rectangle([65, 65, 120, 82], radius=3, fill=RED)
        draw.text((75, 66), "PDF", fill=WHITE, font=font_xs)
        for i, y in enumerate(range(92, 185, 14)):
            w = [90, 75, 85, 60, 90, 70][i % 6]
            draw.rounded_rectangle([65, y, 65 + w, y + 6], radius=2, fill=BORDER)
        draw.text((115, 215), "Bank Statement", fill=TEXT_LIGHT, font=font_xs, anchor="mt")
        draw.line([(200, 125), (270, 125)], fill=PRIMARY, width=2)
        draw.polygon([(270, 118), (285, 125), (270, 132)], fill=PRIMARY)
        draw.ellipse([325, 65, 435, 175], fill=(27, 79, 114, 25), outline=PRIMARY, width=2)
        draw.ellipse([345, 85, 415, 155], fill=PRIMARY)
        draw.text((380, 110), "AI", fill=WHITE, font=font_step, anchor="mt")
        draw.text((380, 190), "BankScan AI", fill=TEXT_LIGHT, font=font_xs, anchor="mt")
        draw.line([(455, 125), (525, 125)], fill=ACCENT, width=2)
        draw.polygon([(525, 118), (540, 125), (525, 132)], fill=ACCENT)
        draw.rounded_rectangle([565, 45, 700, 205], radius=8, fill=WHITE, outline=BORDER)
        draw.rounded_rectangle([580, 65, 635, 82], radius=3, fill=ACCENT)
        draw.text((590, 66), "XLSX", fill=WHITE, font=font_xs)
        for y in [92, 115, 138, 161]:
            draw.line([(580, y), (685, y)], fill=BORDER, width=1)
        for x in [610, 645]:
            draw.line([(x, 92), (x, 175)], fill=BORDER, width=1)
        for cx in [595, 627, 662]:
            for cy in [100, 123, 146]:
                draw.text((cx, cy), "\u2713", fill=ACCENT, font=font_xs)
        draw.text((632, 215), "Clean Spreadsheet", fill=TEXT_LIGHT, font=font_xs, anchor="mt")
        draw.rounded_rectangle([300, 240, 460, 268], radius=14, fill=(39, 174, 96, 30), outline=ACCENT)
        draw.text((380, 248), "Under 30 seconds", fill=ACCENT, font=font_label, anchor="mt")

    elif image_type == "infographic":
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
            draw.rounded_rectangle([x_start, 20, x_start + 148, 135], radius=8, fill=WHITE, outline=BORDER)
            cx = x_start + 74
            draw.ellipse([cx - 18, 30, cx + 18, 66], fill=(*color, 25), outline=color, width=1)
            draw.text((cx, 42), num, fill=color, font=font_label, anchor="mt")
            draw.text((cx, 78), title, fill=TEXT, font=font_label, anchor="mt")
            draw.text((cx, 98), line1, fill=TEXT_LIGHT, font=ImageFont.load_default(), anchor="mt")
            draw.text((cx, 112), line2, fill=TEXT_LIGHT, font=ImageFont.load_default(), anchor="mt")
            if i < 3:
                ax = x_start + 156
                draw.text((ax, 70), "\u2192", fill=color, font=font_md)
        draw.text((350, 162), "Entire workflow completes in under 60 seconds", fill=PRIMARY, font=font_label, anchor="mt")
    else:
        raise ValueError(f"Unknown image type: {image_type}")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.getvalue()


@app.get("/blog/{slug}/og-image")
async def blog_og_image(slug: str):
    if slug not in BLOG_POSTS:
        raise HTTPException(status_code=404, detail="Blog post not found")
    from starlette.responses import Response
    return Response(
        content=_generate_blog_image(slug, "og"),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, s-maxage=604800"},
    )


@app.get("/blog/{slug}/hero-image")
async def blog_hero_image(slug: str):
    if slug not in BLOG_POSTS:
        raise HTTPException(status_code=404, detail="Blog post not found")
    from starlette.responses import Response
    return Response(
        content=_generate_blog_image(slug, "hero"),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, s-maxage=604800"},
    )


@app.get("/blog/{slug}/infographic")
async def blog_infographic(slug: str):
    if slug not in BLOG_POSTS:
        raise HTTPException(status_code=404, detail="Blog post not found")
    from starlette.responses import Response
    return Response(
        content=_generate_blog_image(slug, "infographic"),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, s-maxage=604800"},
    )


INDEXNOW_KEY = "bankscanai2026seokey"


@app.get(f"/{INDEXNOW_KEY}.txt", response_class=PlainTextResponse)
async def indexnow_key_file():
    return INDEXNOW_KEY


@app.post("/api/indexnow")
async def submit_indexnow(request: Request):
    """Submit URLs to search engines via IndexNow (Bing, Yandex)."""
    import json as _json
    import urllib.request
    try:
        body = await request.json()
        paths = body.get("urls", [])
    except Exception:
        paths = [f"/blog/{slug}" for slug in BLOG_POSTS]

    full_urls = [f"https://bankscanai.com{p}" if p.startswith("/") else p for p in paths]
    payload = _json.dumps({
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
            req = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                results[engine] = {"status": resp.status, "ok": resp.status in (200, 202)}
        except urllib.error.HTTPError as e:
            results[engine] = {"status": e.code, "ok": e.code in (200, 202)}
        except Exception as e:
            results[engine] = {"status": 0, "ok": False, "error": str(e)}
    results["google"] = {"note": "Google does not support IndexNow. Submit via Google Search Console > URL Inspection > Request Indexing."}
    results["urls_submitted"] = full_urls
    return results


@app.post("/api/web-vitals")
async def web_vitals(request: Request):
    """Collect Core Web Vitals metrics from real users."""
    import json as _json
    try:
        body = await request.body()
        data = _json.loads(body)
        logger.info(f"[WebVitals] {data.get('name','?')}={data.get('value',0)} rating={data.get('rating','?')} page={data.get('page','/')}")
    except Exception:
        pass
    return JSONResponse({"ok": True}, status_code=204)


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return """User-agent: *
Allow: /landing
Allow: /login
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
        '<url><loc>https://bankscanai.com/blog</loc><priority>0.8</priority><changefreq>weekly</changefreq></url>',
    ]
    for slug, post in BLOG_POSTS.items():
        urls.append(
            f'<url><loc>https://bankscanai.com/blog/{slug}</loc><lastmod>{post["date"]}</lastmod><priority>0.7</priority><changefreq>monthly</changefreq>'
            f'<image:image><image:loc>https://bankscanai.com/blog/{slug}/hero-image</image:loc><image:title>{post["title"]}</image:title></image:image>'
            f'<image:image><image:loc>https://bankscanai.com/blog/{slug}/og-image</image:loc><image:title>{post["title"]} - Social Preview</image:title></image:image>'
            f'</url>'
        )
    # Tools pages
    urls.append('<url><loc>https://bankscanai.com/tools</loc><priority>0.8</priority><changefreq>weekly</changefreq></url>')
    for slug in SEO_PAGES:
        urls.append(f'<url><loc>https://bankscanai.com/tools/{slug}</loc><priority>0.6</priority><changefreq>monthly</changefreq></url>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  {"".join(urls)}
</urlset>"""


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.3.0", "runtime": "vercel", "stripe_configured": bool(STRIPE_SECRET_KEY)}
