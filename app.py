"""
BankParse — FastAPI Application
Bank statement and receipt to spreadsheet converter with Stripe billing.
Identity: cookie-based, tied to Stripe customer email. No login needed.
"""

import os
import uuid
import hashlib
import json
import secrets
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Cookie
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from parsers.pdf_parser import parse_pdf
from parsers.csv_parser import parse_csv
from parsers.xlsx_exporter import export_to_xlsx, export_receipt_to_xlsx
from parsers.receipt_parser import parse_receipt

# Optional Stripe import
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False

# --- Configuration ---
BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
USAGE_DIR = BASE_DIR / "usage"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
USAGE_DIR.mkdir(exist_ok=True)

# Stripe keys
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID", "")
STRIPE_BUSINESS_PRICE_ID = os.environ.get("STRIPE_BUSINESS_PRICE_ID", "")

if STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Free tier limits
FREE_STATEMENT_LIMIT = 1
FREE_RECEIPT_LIMIT = 1

# Cookie config
COOKIE_NAME = "bp_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year

# App
app = FastAPI(title="BankParse", version="2.1.0")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/downloads", StaticFiles(directory=str(OUTPUT_DIR)), name="downloads")


# ==========================================================================
# Identity & Usage — cookie-based, tied to Stripe customer
# ==========================================================================

def get_session_id(request: Request) -> str:
    """Get or generate a session ID from cookie. Returns the session ID."""
    return request.cookies.get(COOKIE_NAME, "")


def ensure_session(request: Request) -> str:
    """Return existing session ID or generate a new one."""
    sid = get_session_id(request)
    if sid:
        return sid
    return f"bp_{secrets.token_urlsafe(24)}"


def get_usage(session_id: str) -> dict:
    """Get usage data for a session."""
    if not session_id:
        return {"statements": 0, "receipts": 0, "stripe_customer_id": None, "email": None}
    safe_id = hashlib.sha256(session_id.encode()).hexdigest()[:20]
    usage_file = USAGE_DIR / f"{safe_id}.json"
    if usage_file.exists():
        return json.loads(usage_file.read_text())
    return {"statements": 0, "receipts": 0, "stripe_customer_id": None, "email": None}


def save_usage(session_id: str, usage: dict):
    """Save usage data for a session."""
    safe_id = hashlib.sha256(session_id.encode()).hexdigest()[:20]
    usage_file = USAGE_DIR / f"{safe_id}.json"
    usage_file.write_text(json.dumps(usage))


def verify_subscription(stripe_customer_id: str) -> bool:
    """Check if a Stripe customer has an active subscription."""
    if not stripe_customer_id or not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        return False
    try:
        subs = stripe.Subscription.list(customer=stripe_customer_id, status="active", limit=1)
        return len(subs.data) > 0
    except Exception:
        return False


def check_can_use(session_id: str, mode: str) -> tuple:
    """Check if user can use the service. Returns (allowed, usage, is_subscriber)."""
    usage = get_usage(session_id)

    # Check Stripe subscription
    if usage.get("stripe_customer_id"):
        if verify_subscription(usage["stripe_customer_id"]):
            return True, usage, True

    # Free tier check
    if mode == "statement" and usage["statements"] >= FREE_STATEMENT_LIMIT:
        return False, usage, False
    if mode == "receipt" and usage["receipts"] >= FREE_RECEIPT_LIMIT:
        return False, usage, False

    return True, usage, False


def increment_usage(session_id: str, mode: str):
    """Increment usage counter after successful parse."""
    usage = get_usage(session_id)
    if mode == "statement":
        usage["statements"] += 1
    elif mode == "receipt":
        usage["receipts"] += 1
    save_usage(session_id, usage)


def set_session_cookie(response: JSONResponse, session_id: str) -> JSONResponse:
    """Set the session cookie on a response."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # Set to True in production with HTTPS
    )
    return response


# ==========================================================================
# Page Routes
# ==========================================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/landing", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


# ==========================================================================
# Usage / Auth API
# ==========================================================================

@app.get("/api/usage")
async def get_usage_status(request: Request):
    """Get current usage status and subscription state."""
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

    # Ensure session cookie exists
    if not session_id:
        session_id = ensure_session(request)
        set_session_cookie(response, session_id)

    return response


@app.post("/api/restore")
async def restore_subscription(request: Request):
    """
    Restore a subscription by email lookup.
    User enters their email → we look up their Stripe customer → verify subscription
    → link their session to the customer. No password needed.
    """
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    try:
        # Search Stripe for customer by email
        customers = stripe.Customer.search(query=f'email:"{email}"')
        if not customers.data:
            raise HTTPException(
                status_code=404,
                detail="No subscription found for this email. Please check the email you used to subscribe."
            )

        # Find the customer with an active subscription
        active_customer = None
        for customer in customers.data:
            subs = stripe.Subscription.list(customer=customer.id, status="active", limit=1)
            if subs.data:
                active_customer = customer
                break

        if not active_customer:
            raise HTTPException(
                status_code=404,
                detail="No active subscription found. Your subscription may have expired or been cancelled."
            )

        # Link session to this customer
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lookup error: {str(e)}")


# ==========================================================================
# Parse API (with usage gating)
# ==========================================================================

@app.post("/api/parse")
async def parse_statement(request: Request, file: UploadFile = File(...)):
    """Upload and parse a bank statement."""
    session_id = ensure_session(request)
    allowed, usage, is_sub = check_can_use(session_id, "statement")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in [".pdf", ".csv", ".tsv", ".txt"]):
        raise HTTPException(status_code=400, detail="Unsupported file type. Please upload a PDF or CSV file.")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    job_id = str(uuid.uuid4())[:8]
    upload_path = UPLOAD_DIR / f"{job_id}_{file.filename}"

    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        if filename.endswith(".pdf"):
            result = parse_pdf(str(upload_path))
        else:
            result = parse_csv(str(upload_path))

        if not result["transactions"]:
            raise HTTPException(
                status_code=422,
                detail="No transactions found. The file format may not be supported yet, or the statement may be empty."
            )

        output_filename = f"bankparse_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        export_to_xlsx(result, str(output_path))

        # Only count against free tier, not subscribers
        if not is_sub:
            increment_usage(session_id, "statement")

        response = JSONResponse({
            "transactions": result["transactions"],
            "summary": result["summary"],
            "metadata": result["metadata"],
            "download_url": f"/downloads/{output_filename}",
        })
        set_session_cookie(response, session_id)
        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")
    finally:
        if upload_path.exists():
            upload_path.unlink()


IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"]
RECEIPT_EXTENSIONS = [".pdf"] + IMAGE_EXTENSIONS


@app.post("/api/parse-receipt")
async def parse_receipt_endpoint(request: Request, file: UploadFile = File(...)):
    """Upload and parse a store receipt."""
    session_id = ensure_session(request)
    allowed, usage, is_sub = check_can_use(session_id, "receipt")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in RECEIPT_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload a PDF or image (PNG, JPG, TIFF) of your receipt."
        )

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    job_id = str(uuid.uuid4())[:8]
    upload_path = UPLOAD_DIR / f"{job_id}_{file.filename}"

    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        result = parse_receipt(str(upload_path))

        if not result["items"]:
            raise HTTPException(
                status_code=422,
                detail="No items found on the receipt. The format may not be supported, or the image may be unclear."
            )

        output_filename = f"receipt_{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        export_receipt_to_xlsx(result, str(output_path))

        if not is_sub:
            increment_usage(session_id, "receipt")

        response = JSONResponse({
            "items": result["items"],
            "totals": result["totals"],
            "metadata": result["metadata"],
            "download_url": f"/downloads/{output_filename}",
        })
        set_session_cookie(response, session_id)
        return response

    except HTTPException:
        raise
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Receipt parsing error: {str(e)}")
    finally:
        if upload_path.exists():
            upload_path.unlink()


# ==========================================================================
# Stripe Billing Routes
# ==========================================================================

@app.post("/api/create-checkout")
async def create_checkout_session(request: Request):
    """Create a Stripe Checkout session. Requires email for identity."""
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
        # Check if customer already exists in Stripe
        existing = stripe.Customer.search(query=f'email:"{email}"')
        if existing.data:
            customer_id = existing.data[0].id
        else:
            customer = stripe.Customer.create(email=email, metadata={"source": "bankparse"})
            customer_id = customer.id

        # Save customer link to session
        usage = get_usage(session_id)
        usage["stripe_customer_id"] = customer_id
        usage["email"] = email
        save_usage(session_id, usage)

        origin = request.headers.get("origin", "http://localhost:8000")
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


@app.get("/api/verify-session")
async def verify_checkout_session(request: Request, session_id: str):
    """Verify a completed Stripe Checkout and link subscription to browser session."""
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    try:
        checkout = stripe.checkout.Session.retrieve(session_id)

        if checkout.payment_status == "paid" and checkout.customer:
            # Link this browser session to the Stripe customer
            browser_session = checkout.metadata.get("session_id") or ensure_session(request)
            usage = get_usage(browser_session)
            usage["stripe_customer_id"] = checkout.customer
            # Get customer email from Stripe
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

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events (subscription changes)."""
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        return JSONResponse({"status": "ignored"})

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    # Handle subscription cancellation — no action needed since we verify
    # subscription status live via Stripe API on each request.
    # The webhook is here for future use (sending emails, analytics, etc.)

    return JSONResponse({"status": "ok"})


@app.get("/api/config")
async def get_config():
    """Return client-safe configuration."""
    return JSONResponse({
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "free_statement_limit": FREE_STATEMENT_LIMIT,
        "free_receipt_limit": FREE_RECEIPT_LIMIT,
        "plans": {
            "pro": {"price": "£9.99/mo", "name": "BankParse Pro"},
            "business": {"price": "£29.99/mo", "name": "BankParse Business"},
        },
    })


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "2.1.0",
        "stripe_configured": bool(STRIPE_SECRET_KEY),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
