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
)
from otp import generate_otp, send_otp_email

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
    SUBSCRIPTION_CACHE_TTL,
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
    allow_headers=["*"],
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
        return RedirectResponse(url="/login", status_code=302)
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

    pw_hash = hash_password(password)
    try:
        user_id = create_user(email, pw_hash)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

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
        return JSONResponse({
            "has_subscription": False,
            "tier": "free",
            "tier_limits": TIER_LIMITS["free"],
            "monthly_scans_used": 0,
            "monthly_scans_limit": TIER_LIMITS["free"]["monthly_scans"],
            "chat_used_today": 0,
            "chat_limit": 0,
            "email": None,
            "stripe_configured": bool(STRIPE_PUBLISHABLE_KEY),
        })

    tier = get_user_tier(user)
    is_subscriber = tier != "free"
    limits = TIER_LIMITS[tier]
    scans_used = get_monthly_scans(user["id"])
    chat_used = get_chat_usage(user["id"]) if limits["chat_per_day"] else 0

    return JSONResponse({
        "has_subscription": is_subscriber,
        "tier": tier,
        "tier_limits": limits,
        "monthly_scans_used": scans_used,
        "monthly_scans_limit": limits["monthly_scans"],
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

        # Increment monthly scan count for all tiers
        increment_monthly_scans(user["id"], 1)

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

        # Increment monthly scan count for all tiers
        increment_monthly_scans(user["id"], 1)

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
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.exception("Receipt parsing error: %s", e)
        raise HTTPException(status_code=500, detail=f"Receipt parsing error: {str(e)}")
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

    # Check monthly scan limit for the batch
    monthly_limit = limits["monthly_scans"]
    if monthly_limit is not None:
        scans_used = get_monthly_scans(user["id"])
        if scans_used + len(files) > monthly_limit:
            remaining = max(0, monthly_limit - scans_used)
            raise HTTPException(status_code=403, detail=f"Monthly scan limit reached. You have {remaining} scans remaining this month.")

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

        # Increment monthly scan count for all files in batch
        increment_monthly_scans(user["id"], len(upload_paths))

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
        raise HTTPException(status_code=500, detail=f"Bulk receipt parsing error: {str(e)}")
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

    # Check monthly scan limit for the batch
    monthly_limit = limits["monthly_scans"]
    if monthly_limit is not None:
        scans_used = get_monthly_scans(user["id"])
        if scans_used + len(files) > monthly_limit:
            remaining = max(0, monthly_limit - scans_used)
            raise HTTPException(status_code=403, detail=f"Monthly scan limit reached. You have {remaining} scans remaining this month.")

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

        # Increment monthly scan count for all files in batch
        increment_monthly_scans(user["id"], len(upload_paths))

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
        raise HTTPException(status_code=500, detail=f"Bulk statement parsing error: {str(e)}")
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
            detail="AI Chat is available on Business (\u00a379.99/mo) and Enterprise (\u00a3199/mo) plans."
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

        origin = request.headers.get("origin", "http://localhost:8000")
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
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")


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


@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "plans": {
            "starter": {"price": "\u00a39.99/mo", "name": "BankScan AI Starter", "monthly_scans": 100},
            "pro": {"price": "\u00a329.99/mo", "name": "BankScan AI Pro", "monthly_scans": 500},
            "business": {"price": "\u00a379.99/mo", "name": "BankScan AI Business", "monthly_scans": 2000},
            "enterprise": {"price": "\u00a3199/mo", "name": "BankScan AI Enterprise", "monthly_scans": "Unlimited"},
        },
    })


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
