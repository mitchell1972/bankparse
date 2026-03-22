"""
BankParse — Vercel Serverless Entry Point
Adapts the FastAPI app for Vercel's serverless Python runtime with Stripe billing.
Identity: cookie-based, tied to Stripe customer email.
Storage: SQLite database. Email verification via OTP for subscription restore.
"""

import os
import sys
import uuid
import secrets
import base64
import logging
import tempfile
from pathlib import Path

# Add parent directory to path so parsers can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from parsers.pdf_parser import parse_pdf
from parsers.csv_parser import parse_csv
from parsers.xlsx_exporter import export_to_xlsx, export_receipt_to_xlsx
from parsers.receipt_parser import parse_receipt
from database import get_usage, save_usage, increment_usage, store_otp, verify_otp
from otp import generate_otp, send_otp_email

logger = logging.getLogger("bankparse")

# Optional Stripe import
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False

# Use /tmp on Vercel for file operations
TMP_DIR = Path(tempfile.gettempdir()) / "bankparse"
TMP_DIR.mkdir(exist_ok=True)

# Read templates once at cold start
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "index.html"
TEMPLATE_HTML = TEMPLATE_PATH.read_text()
LANDING_PATH = Path(__file__).parent.parent / "templates" / "landing.html"
LANDING_HTML = LANDING_PATH.read_text() if LANDING_PATH.exists() else TEMPLATE_HTML

# Stripe config
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "")
STRIPE_BUSINESS_PRICE_ID = os.environ.get("STRIPE_BUSINESS_PRICE_ID", "")

if STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

FREE_STATEMENT_LIMIT = 1
FREE_RECEIPT_LIMIT = 1

# Cookie config
COOKIE_NAME = "bp_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year

IS_PRODUCTION = os.environ.get("ENVIRONMENT", "production") == "production"

app = FastAPI(title="BankParse", version="2.2.0")


# ==========================================================================
# Identity — cookie-based sessions
# ==========================================================================

def get_session_id(request: Request) -> str:
    return request.cookies.get(COOKIE_NAME, "")


def ensure_session(request: Request) -> str:
    sid = get_session_id(request)
    if sid:
        return sid
    return f"bp_{secrets.token_urlsafe(24)}"


def verify_subscription(stripe_customer_id: str) -> bool:
    if not stripe_customer_id or not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        return False
    try:
        subs = stripe.Subscription.list(customer=stripe_customer_id, status="active", limit=1)
        return len(subs.data) > 0
    except Exception:
        return False


def check_can_use(session_id: str, mode: str) -> tuple:
    usage = get_usage(session_id)

    if usage.get("stripe_customer_id"):
        if verify_subscription(usage["stripe_customer_id"]):
            return True, usage, True

    if mode == "statement" and usage["statements"] >= FREE_STATEMENT_LIMIT:
        return False, usage, False
    if mode == "receipt" and usage["receipts"] >= FREE_RECEIPT_LIMIT:
        return False, usage, False

    return True, usage, False


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
# Page Routes
# ==========================================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(TEMPLATE_HTML)


@app.get("/landing", response_class=HTMLResponse)
async def landing():
    return HTMLResponse(LANDING_HTML)


# ==========================================================================
# Usage / Auth API
# ==========================================================================

@app.get("/api/usage")
async def get_usage_status(request: Request):
    session_id = get_session_id(request)
    usage = get_usage(session_id)

    is_subscriber = False
    email = usage.get("email")
    if usage.get("stripe_customer_id"):
        is_subscriber = verify_subscription(usage["stripe_customer_id"])

    response = JSONResponse({
        "statements_used": usage["statements"],
        "receipts_used": usage["receipts"],
        "statements_limit": FREE_STATEMENT_LIMIT,
        "receipts_limit": FREE_RECEIPT_LIMIT,
        "has_subscription": is_subscriber,
        "email": email,
        "stripe_configured": bool(STRIPE_PUBLISHABLE_KEY),
    })

    if not session_id:
        session_id = ensure_session(request)
        set_session_cookie(response, session_id)

    return response


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
    session_id = ensure_session(request)
    allowed, usage, is_sub = check_can_use(session_id, "statement")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in [".pdf", ".csv", ".tsv", ".txt"]):
        raise HTTPException(status_code=400, detail="Unsupported file type. Please upload a PDF or CSV file.")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 20MB.")

    job_id = str(uuid.uuid4())[:8]
    upload_path = TMP_DIR / f"{job_id}_{file.filename}"
    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        if filename.endswith(".pdf"):
            result = parse_pdf(str(upload_path))
        else:
            result = parse_csv(str(upload_path))
        if not result["transactions"]:
            raise HTTPException(status_code=422, detail="No transactions found.")

        output_path = TMP_DIR / f"bankparse_{job_id}.xlsx"
        export_to_xlsx(result, str(output_path))
        xlsx_b64 = base64.b64encode(output_path.read_bytes()).decode("utf-8")

        if not is_sub:
            increment_usage(session_id, "statement")

        response = JSONResponse({
            "transactions": result["transactions"],
            "summary": result["summary"],
            "metadata": result["metadata"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"bankparse_{job_id}.xlsx",
        })
        set_session_cookie(response, session_id)
        return response
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        for f in [upload_path, TMP_DIR / f"bankparse_{job_id}.xlsx"]:
            if f.exists():
                f.unlink()


IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"]
RECEIPT_EXTENSIONS = [".pdf"] + IMAGE_EXTENSIONS


@app.post("/api/parse-receipt")
async def parse_receipt_endpoint(request: Request, file: UploadFile = File(...)):
    session_id = ensure_session(request)
    allowed, usage, is_sub = check_can_use(session_id, "receipt")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in RECEIPT_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 20MB.")

    job_id = str(uuid.uuid4())[:8]
    upload_path = TMP_DIR / f"{job_id}_{file.filename}"
    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        result = parse_receipt(str(upload_path))
        if not result["items"]:
            raise HTTPException(status_code=422, detail="No items found.")

        output_path = TMP_DIR / f"receipt_{job_id}.xlsx"
        export_receipt_to_xlsx(result, str(output_path))
        xlsx_b64 = base64.b64encode(output_path.read_bytes()).decode("utf-8")

        if not is_sub:
            increment_usage(session_id, "receipt")

        response = JSONResponse({
            "items": result["items"],
            "totals": result["totals"],
            "metadata": result["metadata"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"receipt_{job_id}.xlsx",
        })
        set_session_cookie(response, session_id)
        return response
    except HTTPException:
        raise
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred.")
    finally:
        for f in [upload_path, TMP_DIR / f"receipt_{job_id}.xlsx"]:
            if f.exists():
                f.unlink()


# ==========================================================================
# Stripe Billing Routes
# ==========================================================================

@app.post("/api/create-checkout")
async def create_checkout_session(request: Request):
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    body = await request.json()
    plan = body.get("plan", "pro")
    email = body.get("email", "").strip().lower()

    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email is required to create a subscription.")

    price_id = STRIPE_PRO_PRICE_ID if plan == "pro" else STRIPE_BUSINESS_PRICE_ID
    if not price_id:
        raise HTTPException(status_code=500, detail="Stripe price not configured for this plan.")

    session_id = ensure_session(request)

    try:
        existing = stripe.Customer.search(query=f'email:"{email}"')
        if existing.data:
            customer_id = existing.data[0].id
        else:
            customer = stripe.Customer.create(email=email, metadata={"source": "bankparse"})
            customer_id = customer.id

        usage = get_usage(session_id)
        usage["stripe_customer_id"] = customer_id
        usage["email"] = email
        save_usage(session_id, usage)

        origin = request.headers.get("origin", "https://bankparse-pi.vercel.app")
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{origin}/?session_id={{CHECKOUT_SESSION_ID}}&status=success",
            cancel_url=f"{origin}/?status=cancelled",
            metadata={"session_id": session_id},
        )

        response = JSONResponse({"checkout_url": session.url, "session_id": session.id})
        set_session_cookie(response, session_id)
        return response

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="An internal error occurred.")


@app.get("/api/verify-session")
async def verify_checkout_session(request: Request, session_id: str):
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    try:
        checkout = stripe.checkout.Session.retrieve(session_id)

        if checkout.payment_status == "paid" and checkout.customer:
            browser_session = checkout.metadata.get("session_id") or ensure_session(request)
            usage = get_usage(browser_session)
            usage["stripe_customer_id"] = checkout.customer
            customer = stripe.Customer.retrieve(checkout.customer)
            usage["email"] = customer.email
            save_usage(browser_session, usage)

            response = JSONResponse({
                "status": "active",
                "email": customer.email,
            })
            set_session_cookie(response, browser_session)
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

    return JSONResponse({"status": "ok"})


@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "free_statement_limit": FREE_STATEMENT_LIMIT,
        "free_receipt_limit": FREE_RECEIPT_LIMIT,
        "plans": {
            "pro": {"price": "\u00a39.99/mo", "name": "BankParse Pro"},
            "business": {"price": "\u00a319.99/mo", "name": "BankParse Business"},
        },
    })


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.2.0", "runtime": "vercel", "stripe_configured": bool(STRIPE_SECRET_KEY)}
