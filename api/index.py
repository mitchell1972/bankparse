"""
BankParse — Vercel Serverless Entry Point
Adapts the FastAPI app for Vercel's serverless Python runtime with Stripe billing.
"""

import os
import sys
import uuid
import json
import hashlib
import base64
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

# Optional Stripe import
try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False

# Use /tmp on Vercel
TMP_DIR = Path(tempfile.gettempdir()) / "bankparse"
TMP_DIR.mkdir(exist_ok=True)

# Read the template once at cold start
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "index.html"
TEMPLATE_HTML = TEMPLATE_PATH.read_text()

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

app = FastAPI(title="BankParse", version="2.0.0")


# --- Usage tracking (file-based in /tmp — resets on cold start, fine for serverless) ---

def get_usage_key(request: Request) -> str:
    ip = request.client.host or "unknown"
    ua = request.headers.get("user-agent", "unknown")
    raw = f"{ip}:{ua}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_usage(usage_key: str) -> dict:
    usage_file = TMP_DIR / f"usage_{usage_key}.json"
    if usage_file.exists():
        return json.loads(usage_file.read_text())
    return {"statements": 0, "receipts": 0, "subscription_id": None, "customer_id": None}


def save_usage(usage_key: str, usage: dict):
    usage_file = TMP_DIR / f"usage_{usage_key}.json"
    usage_file.write_text(json.dumps(usage))


def check_can_use(usage_key: str, mode: str) -> tuple:
    usage = get_usage(usage_key)
    if usage.get("subscription_id") and STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
        try:
            sub = stripe.Subscription.retrieve(usage["subscription_id"])
            if sub.status in ("active", "trialing"):
                return True, usage
        except Exception:
            pass
    if mode == "statement" and usage["statements"] >= FREE_STATEMENT_LIMIT:
        return False, usage
    if mode == "receipt" and usage["receipts"] >= FREE_RECEIPT_LIMIT:
        return False, usage
    return True, usage


def increment_usage(usage_key: str, mode: str):
    usage = get_usage(usage_key)
    if mode == "statement":
        usage["statements"] += 1
    elif mode == "receipt":
        usage["receipts"] += 1
    save_usage(usage_key, usage)


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(TEMPLATE_HTML)


@app.get("/api/usage")
async def get_usage_status(request: Request):
    usage_key = get_usage_key(request)
    usage = get_usage(usage_key)
    has_subscription = False
    if usage.get("subscription_id") and STRIPE_AVAILABLE and STRIPE_SECRET_KEY:
        try:
            sub = stripe.Subscription.retrieve(usage["subscription_id"])
            has_subscription = sub.status in ("active", "trialing")
        except Exception:
            pass
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
    usage_key = get_usage_key(request)
    allowed, usage = check_can_use(usage_key, "statement")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in [".pdf", ".csv", ".tsv", ".txt"]):
        raise HTTPException(status_code=400, detail="Unsupported file type.")

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

        increment_usage(usage_key, "statement")

        return JSONResponse({
            "transactions": result["transactions"],
            "summary": result["summary"],
            "metadata": result["metadata"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"bankparse_{job_id}.xlsx",
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")
    finally:
        for f in [upload_path, TMP_DIR / f"bankparse_{job_id}.xlsx"]:
            if f.exists():
                f.unlink()


@app.post("/api/parse-receipt")
async def parse_receipt_endpoint(request: Request, file: UploadFile = File(...)):
    usage_key = get_usage_key(request)
    allowed, usage = check_can_use(usage_key, "receipt")
    if not allowed:
        raise HTTPException(status_code=403, detail="FREE_LIMIT_REACHED")

    filename = file.filename.lower()
    IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"]
    RECEIPT_EXTENSIONS = [".pdf"] + IMAGE_EXTENSIONS
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

        increment_usage(usage_key, "receipt")

        return JSONResponse({
            "items": result["items"],
            "totals": result["totals"],
            "metadata": result["metadata"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"receipt_{job_id}.xlsx",
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Receipt parsing error: {str(e)}")
    finally:
        for f in [upload_path, TMP_DIR / f"receipt_{job_id}.xlsx"]:
            if f.exists():
                f.unlink()


# --- Stripe Routes ---

@app.post("/api/create-checkout")
async def create_checkout_session(request: Request):
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe is not configured.")
    body = await request.json()
    plan = body.get("plan", "pro")
    price_id = STRIPE_PRO_PRICE_ID if plan == "pro" else STRIPE_BUSINESS_PRICE_ID
    if not price_id:
        raise HTTPException(status_code=500, detail="Stripe price not configured.")

    usage_key = get_usage_key(request)
    usage = get_usage(usage_key)
    origin = request.headers.get("origin", "https://bankparse-pi.vercel.app")

    try:
        params = {
            "mode": "subscription",
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{origin}/?session_id={{CHECKOUT_SESSION_ID}}&status=success",
            "cancel_url": f"{origin}/?status=cancelled",
            "metadata": {"usage_key": usage_key},
        }
        if usage.get("customer_id"):
            params["customer"] = usage["customer_id"]
        else:
            params["customer_creation"] = "always"
        session = stripe.checkout.Session.create(**params)
        return JSONResponse({"checkout_url": session.url, "session_id": session.id})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


@app.get("/api/verify-session")
async def verify_checkout_session(request: Request, session_id: str):
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
            return JSONResponse({"status": "active", "subscription_id": session.subscription})
        return JSONResponse({"status": "pending"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {str(e)}")


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_AVAILABLE or not STRIPE_SECRET_KEY:
        return JSONResponse({"status": "ignored"})
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")

    if event.get("type") in ("customer.subscription.deleted", "customer.subscription.updated"):
        sub_data = event["data"]["object"]
        sub_id = sub_data["id"]
        for usage_file in TMP_DIR.glob("usage_*.json"):
            try:
                u = json.loads(usage_file.read_text())
                if u.get("subscription_id") == sub_id and sub_data.get("status") not in ("active", "trialing"):
                    u["subscription_id"] = None
                    usage_file.write_text(json.dumps(u))
            except Exception:
                continue
    return JSONResponse({"status": "ok"})


@app.get("/api/config")
async def get_config():
    return JSONResponse({
        "stripe_publishable_key": STRIPE_PUBLISHABLE_KEY,
        "free_statement_limit": FREE_STATEMENT_LIMIT,
        "free_receipt_limit": FREE_RECEIPT_LIMIT,
    })


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "runtime": "vercel", "stripe_configured": bool(STRIPE_SECRET_KEY)}
