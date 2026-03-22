"""
BankParse — FastAPI Application
Bank statement and receipt to spreadsheet converter with Stripe billing.
"""

import os
import uuid
import hmac
import hashlib
import json
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Header
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

# Stripe keys (set via environment variables)
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

# App
app = FastAPI(title="BankParse", version="2.0.0")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Serve static output files
app.mount("/downloads", StaticFiles(directory=str(OUTPUT_DIR)), name="downloads")


# --- Usage Tracking (file-based, simple) ---

def get_usage_key(request: Request) -> str:
    """Generate a usage fingerprint from IP + User-Agent."""
    ip = request.client.host or "unknown"
    ua = request.headers.get("user-agent", "unknown")
    raw = f"{ip}:{ua}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_usage(usage_key: str) -> dict:
    """Get usage data for a fingerprint."""
    usage_file = USAGE_DIR / f"{usage_key}.json"
    if usage_file.exists():
        return json.loads(usage_file.read_text())
    return {"statements": 0, "receipts": 0, "subscription_id": None, "customer_id": None}


def save_usage(usage_key: str, usage: dict):
    """Save usage data."""
    usage_file = USAGE_DIR / f"{usage_key}.json"
    usage_file.write_text(json.dumps(usage))


def check_can_use(usage_key: str, mode: str) -> tuple[bool, dict]:
    """Check if user can use the service. Returns (allowed, usage_data)."""
    usage = get_usage(usage_key)

    # Check for active Stripe subscription
    if usage.get("subscription_id") and STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
        try:
            sub = stripe.Subscription.retrieve(usage["subscription_id"])
            if sub.status in ("active", "trialing"):
                return True, usage
        except Exception:
            pass

    # Free tier check
    if mode == "statement" and usage["statements"] >= FREE_STATEMENT_LIMIT:
        return False, usage
    if mode == "receipt" and usage["receipts"] >= FREE_RECEIPT_LIMIT:
        return False, usage

    return True, usage


def increment_usage(usage_key: str, mode: str):
    """Increment usage counter after successful parse."""
    usage = get_usage(usage_key)
    if mode == "statement":
        usage["statements"] += 1
    elif mode == "receipt":
        usage["receipts"] += 1
    save_usage(usage_key, usage)


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/usage")
async def get_usage_status(request: Request):
    """Get current usage status and limits."""
    usage_key = get_usage_key(request)
    usage = get_usage(usage_key)

    has_subscription = False
    if usage.get("subscription_id") and STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
        try:
            sub = stripe.Subscription.retrieve(usage["subscription_id"])
            has_subscription = sub.status in ("active", "trialing")
        except Exception:
            has_subscription = False

    return JSONResponse({
        "statements_used": usage["statements"],
        "receipts_used": usage["receipts"],
        "statements_limit": FREE_STATEMENT_LIMIT,
        "receipts_limit": FREE_RECEIPT_LIMIT,
        "has_subscription": has_subscription,
        "stripe_configured": bool(STRIPE_PUBLISHABLE_KEY),
    })


@app.post("/api/parse")
async def parse_statement(request: Request, file: UploadFile = File(...)):
    """Upload and parse a bank statement."""

    # Check usage limits
    usage_key = get_usage_key(request)
    allowed, usage = check_can_use(usage_key, "statement")
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="FREE_LIMIT_REACHED"
        )

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

        # Increment usage on success
        increment_usage(usage_key, "statement")

        return JSONResponse({
            "transactions": result["transactions"],
            "summary": result["summary"],
            "metadata": result["metadata"],
            "download_url": f"/downloads/{output_filename}",
        })

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

    # Check usage limits
    usage_key = get_usage_key(request)
    allowed, usage = check_can_use(usage_key, "receipt")
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="FREE_LIMIT_REACHED"
        )

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

        # Increment usage on success
        increment_usage(usage_key, "receipt")

        return JSONResponse({
            "items": result["items"],
            "totals": result["totals"],
            "metadata": result["metadata"],
            "download_url": f"/downloads/{output_filename}",
        })

    except HTTPException:
        raise
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Receipt parsing error: {str(e)}")
    finally:
        if upload_path.exists():
            upload_path.unlink()


# --- Stripe Billing Routes ---

@app.post("/api/create-checkout")
async def create_checkout_session(request: Request):
    """Create a Stripe Checkout session for subscription."""
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    body = await request.json()
    plan = body.get("plan", "pro")

    price_id = STRIPE_PRO_PRICE_ID if plan == "pro" else STRIPE_BUSINESS_PRICE_ID
    if not price_id:
        raise HTTPException(status_code=500, detail="Stripe price not configured for this plan.")

    # Get or create customer
    usage_key = get_usage_key(request)
    usage = get_usage(usage_key)

    try:
        origin = request.headers.get("origin", "http://localhost:8000")
        checkout_params = {
            "mode": "subscription",
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{origin}/?session_id={{CHECKOUT_SESSION_ID}}&status=success",
            "cancel_url": f"{origin}/?status=cancelled",
            "metadata": {"usage_key": usage_key},
        }

        if usage.get("customer_id"):
            checkout_params["customer"] = usage["customer_id"]
        else:
            checkout_params["customer_creation"] = "always"

        session = stripe.checkout.Session.create(**checkout_params)

        return JSONResponse({"checkout_url": session.url, "session_id": session.id})

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


@app.get("/api/verify-session")
async def verify_checkout_session(request: Request, session_id: str):
    """Verify a completed Stripe Checkout session and activate subscription."""
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")

    try:
        session = stripe.checkout.Session.retrieve(session_id)

        if session.payment_status == "paid" and session.subscription:
            usage_key = session.metadata.get("usage_key") or get_usage_key(request)
            usage = get_usage(usage_key)
            usage["subscription_id"] = session.subscription
            usage["customer_id"] = session.customer
            save_usage(usage_key, usage)

            return JSONResponse({
                "status": "active",
                "subscription_id": session.subscription,
            })

        return JSONResponse({"status": "pending"})

    except stripe.error.StripeError as e:
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

    # Handle subscription cancellation
    if event.get("type") in ("customer.subscription.deleted", "customer.subscription.updated"):
        sub_data = event["data"]["object"]
        sub_id = sub_data["id"]

        # Find and update usage files with this subscription
        for usage_file in USAGE_DIR.glob("*.json"):
            try:
                usage = json.loads(usage_file.read_text())
                if usage.get("subscription_id") == sub_id:
                    if sub_data.get("status") not in ("active", "trialing"):
                        usage["subscription_id"] = None
                    save_usage(usage_file.stem, usage)
            except Exception:
                continue

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
        "version": "2.0.0",
        "stripe_configured": bool(STRIPE_SECRET_KEY),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
