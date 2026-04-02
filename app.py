"""
BankParse — FastAPI Application
Bank statement and receipt to spreadsheet converter with Stripe billing.
Identity: email/password auth with cookie-based sessions.
Storage: SQLite database. Email verification via OTP for subscription restore.
"""

import os
import uuid
import asyncio
import logging
import sqlite3
import time
import json
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from parsers.pdf_parser import parse_pdf
from parsers.csv_parser import parse_csv
from parsers.xlsx_exporter import export_to_xlsx, export_receipt_to_xlsx, export_bulk_receipts_to_xlsx, export_bulk_statements_to_xlsx
from parsers.receipt_parser import parse_receipt
from parsers.ai_parser import parse_receipt_ai, parse_receipts_bulk, parse_statement_ai, parse_statements_bulk
from database import (
    get_usage, save_usage, increment_usage,
    store_otp, verify_otp, cleanup_expired_otps,
    track_output_file, get_stale_output_files, remove_output_file_record,
    create_user, get_user_by_email, update_user, increment_user_usage,
    get_user_by_stripe_customer,
    get_chat_usage, increment_chat_usage,
    get_monthly_scans, increment_monthly_scans,
    get_monthly_statements, increment_monthly_statements,
    get_monthly_receipts, increment_monthly_receipts,
    _fetchall_dicts,
)
from otp import generate_otp, send_otp_email
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

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc):
    return PlainTextResponse("Rate limit exceeded. Please try again later.", status_code=429)


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/downloads", StaticFiles(directory=str(OUTPUT_DIR)), name="downloads")


# ==========================================================================
# Page Routes
# ==========================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Serve the login/register page."""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/landing", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


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

    response = JSONResponse({"status": "ok", "email": email})
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

    allowed, tier = check_can_use(user, "statement")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    limits = TIER_LIMITS[tier]

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in [".pdf", ".csv", ".tsv", ".txt"]):
        raise HTTPException(status_code=400, detail="Unsupported file type. Please upload a PDF or CSV file.")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    safe_filename = Path(file.filename).name  # Strip path components
    job_id = str(uuid.uuid4())[:8]
    upload_path = UPLOAD_DIR / f"{job_id}_{safe_filename}"

    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        if filename.endswith(".pdf"):
            result = parse_pdf(str(upload_path))
        else:
            result = parse_csv(str(upload_path))

        # AI fallback: only for Pro/Business tiers (ai_parsing == True)
        if not result["transactions"] and limits["ai_parsing"] and ANTHROPIC_API_KEY and filename.endswith(".pdf"):
            logger.info("Traditional parser found 0 transactions, trying AI parser")
            ai_result = parse_statement_ai(str(upload_path))
            if ai_result and ai_result.get("transactions"):
                result = ai_result

        if not result["transactions"]:
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

    allowed, tier = check_can_use(user, "receipt")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    limits = TIER_LIMITS[tier]

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in RECEIPT_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload a PDF or image (PNG, JPG, TIFF) of your receipt."
        )

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    safe_filename = Path(file.filename).name  # Strip path components
    job_id = str(uuid.uuid4())[:8]
    upload_path = UPLOAD_DIR / f"{job_id}_{safe_filename}"

    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        # AI-powered parsing only for Pro/Business tiers
        result = None
        if limits["ai_parsing"] and ANTHROPIC_API_KEY:
            result = parse_receipt_ai(str(upload_path))
        if result is None or not result.get("items"):
            result = parse_receipt(str(upload_path))

        if not result["items"]:
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

        response = JSONResponse({
            "items": result["items"],
            "totals": result["totals"],
            "metadata": result["metadata"],
            "download_url": f"/downloads/{output_filename}",
        })
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

        # Parse all receipts
        bulk_result = parse_receipts_bulk(upload_paths)

        if not bulk_result["combined_items"]:
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

        bulk_result = parse_statements_bulk(upload_paths)

        if not bulk_result["all_transactions"]:
            raise HTTPException(status_code=422, detail="No transactions found in any of the uploaded statements.")

        job_id = str(uuid.uuid4())[:8]
        output_filename = f"statements_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        export_bulk_statements_to_xlsx(bulk_result, str(output_path))
        track_output_file(output_filename)

        # Increment monthly statement count for all files in batch
        increment_monthly_statements(user["id"], len(upload_paths))

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

def _format_chat_context(context_type: str, context_data: dict) -> str:
    """Format parsed results into readable text for the chat system prompt.
    Sonnet 4 has 200K context — we can send ALL transactions for accurate answers.
    Truncates at 150K chars (~40K tokens) as a safety limit."""
    lines = []
    max_chars = 150000

    if context_type == "statement":
        summary = context_data.get("summary", {})
        lines.append(f"=== Bank Statement Summary ===")
        lines.append(f"Total transactions: {summary.get('total_transactions', 'N/A')}")
        lines.append(f"Total credits (money in): £{summary.get('total_credits', 0):.2f}")
        lines.append(f"Total debits (money out): £{summary.get('total_debits', 0):.2f}")
        lines.append(f"Net: £{summary.get('net', 0):.2f}")
        lines.append("")
        lines.append("=== Transactions ===")
        for tx in context_data.get("transactions", []):
            line = f"{tx.get('date', 'N/A')} | {tx.get('description', 'N/A')} | £{tx.get('amount', 0):.2f} | {tx.get('type', 'N/A')}"
            lines.append(line)

    elif context_type == "receipt":
        meta = context_data.get("metadata", {})
        totals = context_data.get("totals", {})
        lines.append(f"=== Receipt from {meta.get('store_name', 'Unknown Store')} ===")
        if meta.get("date"):
            lines.append(f"Date: {meta['date']}")
        lines.append(f"Total: £{totals.get('total', 0):.2f}")
        if totals.get("tax") is not None:
            lines.append(f"Tax: £{totals['tax']:.2f}")
        lines.append("")
        lines.append("=== Items ===")
        for item in context_data.get("items", []):
            line = f"{item.get('description', 'N/A')} | Qty: {item.get('quantity', 1)} | Unit: £{item.get('unit_price', 0):.2f} | Total: £{item.get('total_price', 0):.2f}"
            lines.append(line)

    elif context_type == "bulk_receipt":
        lines.append(f"=== Bulk Receipt Summary ===")
        lines.append(f"Receipts processed: {context_data.get('receipt_count', 0)}")
        lines.append(f"Total items: {context_data.get('total_items', 0)}")
        lines.append(f"Grand total: £{context_data.get('grand_total', 0):.2f}")
        lines.append("")
        lines.append("=== All Items ===")
        for item in context_data.get("combined_items", []):
            line = f"{item.get('store', 'N/A')} | {item.get('description', 'N/A')} | Qty: {item.get('quantity', 1)} | £{item.get('total_price', 0):.2f}"
            lines.append(line)

    elif context_type == "bulk_statement":
        summary = context_data.get("summary", {})
        lines.append(f"=== Combined Statements Summary ===")
        lines.append(f"Statements: {context_data.get('statement_count', 0)}")
        lines.append(f"Total transactions: {summary.get('total_transactions', 0)}")
        lines.append(f"Total credits: £{summary.get('total_credits', 0):.2f}")
        lines.append(f"Total debits: £{summary.get('total_debits', 0):.2f}")
        lines.append(f"Net: £{summary.get('net', 0):.2f}")
        lines.append("")
        lines.append("=== All Transactions ===")
        for tx in context_data.get("all_transactions", []):
            source = tx.get("source", "")
            line = f"{source} | {tx.get('date', 'N/A')} | {tx.get('description', 'N/A')} | £{tx.get('amount', 0):.2f} | {tx.get('type', 'N/A')}"
            lines.append(line)

    else:
        lines.append("No recognized data format.")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n... (data truncated for brevity)"
    return text


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

    formatted_context = _format_chat_context(context_type, context_data)

    system_prompt = (
        "You are a helpful financial assistant for BankScan AI. The user has uploaded "
        "bank statements and/or store receipts. Answer their questions based ONLY on "
        "the data provided below. Be concise, specific, and use GBP (\u00a3) currency "
        "formatting. If asked to calculate totals, show your working. If the data "
        "doesn't contain the answer, say so clearly.\n\n"
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
        raise HTTPException(status_code=500, detail="Billing request failed. Please try again.")


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


@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "plans": {
            "starter": {"price": "\u00a37.99/mo", "name": "BankScan AI Starter", "statements": 120, "receipts": 500, "clients": "5-10"},
            "pro": {"price": "\u00a324.99/mo", "name": "BankScan AI Pro", "statements": 300, "receipts": 1500, "clients": "11-25"},
            "business": {"price": "\u00a359.99/mo", "name": "BankScan AI Business", "statements": 840, "receipts": 5000, "clients": "26-70"},
            "enterprise": {"price": "\u00a3149/mo", "name": "BankScan AI Enterprise", "statements": "Unlimited", "receipts": "Unlimited", "clients": "71-1,000"},
        },
    })


# ==========================================================================
# Blog Routes
# ==========================================================================

BLOG_POSTS = {
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
}


@app.get("/blog", response_class=HTMLResponse)
async def blog_index(request: Request):
    """Blog listing page."""
    posts = [
        {"slug": slug, **data}
        for slug, data in sorted(BLOG_POSTS.items(), key=lambda x: x[1]["date"], reverse=True)
    ]
    response = templates.TemplateResponse("blog/index.html", {"request": request, "posts": posts})
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=3600"
    return response


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(request: Request, slug: str):
    """Individual blog post page."""
    if slug not in BLOG_POSTS:
        raise HTTPException(status_code=404, detail="Blog post not found")
    post = BLOG_POSTS[slug]
    response = templates.TemplateResponse(post["template"], {"request": request, "post": post, "slug": slug})
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
    response = templates.TemplateResponse("tools/index.html", {
        "request": request,
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

    response = templates.TemplateResponse("tools/seo_page.html", {
        "request": request,
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
