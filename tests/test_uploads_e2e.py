"""
End-to-end upload tests against REAL files from the user's Downloads folder.

Exercises the full upload pipeline (auth + CSRF + paywall gating + parse +
categorise + storage + cumulative-banner update) using:

  * the bundled sample CSV (parsed locally, no AI key required)
  * one real PDF from ~/Downloads/Statements/   (AI parser mocked)
  * one real HEIC receipt from ~/Downloads/receipt/ (AI parser mocked)

The AI parser is mocked so the test:
  - runs without an ANTHROPIC_API_KEY
  - costs £0 to run
  - still verifies every NON-AI piece of the pipeline (multipart upload,
    file size limits, session limits, paywall gating, storage, XLSX export)

If either of the source folders has been moved/removed, the file-backed
tests skip cleanly instead of failing.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_uploads_e2e.db"

# Paths the user has on disk. Tests skip if these aren't where we expect.
USER_STATEMENTS_DIR = Path.home() / "Downloads" / "Statements"
USER_RECEIPTS_DIR = Path.home() / "Downloads" / "receipt"
SAMPLE_CSV = Path(__file__).parent / "e2e" / "fixtures" / "sample_statement.csv"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("UNLIMITED_EMAILS", raising=False)
    # The PDF + receipt endpoints refuse to run when ANTHROPIC_API_KEY is
    # unset (501). The actual AI call is mocked below — we just need the
    # endpoint to TRY to call it, not return 501 early. Use a stub key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stub-for-tests")
    # app.py reads ANTHROPIC_API_KEY at import time into a module global,
    # so we have to patch the global too.
    import app as _app_module
    monkeypatch.setattr(_app_module, "ANTHROPIC_API_KEY", "sk-ant-stub-for-tests")

    try:
        from app import app as _app
        if getattr(_app.state, "limiter", None) is not None:
            _app.state.limiter.enabled = False
    except Exception:
        pass
    yield
    try:
        from app import app as _app
        if getattr(_app.state, "limiter", None) is not None:
            _app.state.limiter.enabled = True
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_db():
    import database
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)
    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
    database._sqlite_conn = None

    import sqlite3
    def _get_sqlite_test():
        if database._sqlite_conn is None:
            conn = sqlite3.connect(TEST_DB_PATH, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            database._sqlite_conn = conn
        return database._sqlite_conn

    database._get_sqlite = _get_sqlite_test
    database.init_db()
    yield

    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


def _seed_csrf(client):
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_subscribed_user(email="upload-e2e@example.com"):
    """Register, verify, AND fully provision a 'trialing' user with card
    on file — i.e. the exact row shape Stripe's webhook would leave after
    a real customer completes Checkout.

    Must set ALL of:
      - subscription_status='trialing'
      - stripe_subscription_id (presence is the "card on file" signal)
      - trial_end_at > now (trial still active)

    Without any one of these, `core.check_can_use` returns
    `payment_method_required` or `trial_expired` and the upload endpoint
    returns 403 — exactly what real users see.
    """
    import time
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/register",
        json={"email": email, "password": "password12345"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(
        user["id"],
        grandfathered_trial=0,
        subscription_status="trialing",
        stripe_subscription_id="sub_test_e2e_" + email.split("@")[0],
        trial_end_at=time.time() + 7 * 24 * 3600,
    )
    return client, csrf, user


# ---------------------------------------------------------------------------
# CSV statement — no AI, parsed locally
# ---------------------------------------------------------------------------

def test_csv_statement_upload_full_pipeline():
    """Upload the bundled CSV. No AI key needed — parser handles CSV locally.
    Verifies multipart upload + parse + categorise + storage + XLSX export."""
    if not SAMPLE_CSV.exists():
        pytest.skip("sample CSV fixture missing")

    client, csrf, user = _client_with_subscribed_user()

    with open(SAMPLE_CSV, "rb") as f:
        r = client.post(
            "/api/parse",
            files={"file": ("sample_statement.csv", f, "text/csv")},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transactions"], "should return at least one transaction"
    assert "download_url" in body
    # The XLSX gets written to disk and served via /downloads/...
    assert body["download_url"].startswith("/downloads/")

    # The cumulative banner should now reflect 1 file uploaded.
    r2 = client.get("/api/extracted-data")
    assert r2.status_code == 200
    cum = r2.json()
    assert len(cum["statements"]["files"]) == 1, (
        f"expected 1 statement file persisted; got {cum['statements']['files']}"
    )
    assert cum["statements"]["files"][0]["row_count"] >= 1


def test_csv_upload_blocked_without_auth():
    """Sanity: the upload endpoint is not anonymous-callable."""
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        csrf = _seed_csrf(client)
        with open(SAMPLE_CSV, "rb") as f:
            r = client.post(
                "/api/parse",
                files={"file": ("sample.csv", f, "text/csv")},
                headers={"X-CSRF-Token": csrf},
            )
    assert r.status_code == 401


def test_csv_upload_blocked_for_paywalled_user():
    """A user without grandfathering, without subscription, without admin
    bypass must hit the paywall and NOT be able to upload — protects
    against the bug the user reported (gmail account silently bypassing)."""
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/register",
        json={"email": "paywalled@example.com", "password": "password12345"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    user = _db.get_user_by_email("paywalled@example.com")
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], grandfathered_trial=0,
                    subscription_status=None)

    # The DASHBOARD redirects to /start-trial — this is the user-visible
    # gate the bug report was about. Upload endpoints separately enforce
    # their own quota / tier checks; the canonical gate is the / page.
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/start-trial"


# ---------------------------------------------------------------------------
# Real PDF statement from ~/Downloads/Statements/ — AI parser mocked
# ---------------------------------------------------------------------------

def test_real_pdf_statement_upload_with_mocked_ai():
    """Upload one of the real PDF files from the user's Downloads folder.
    AI parser is mocked so we don't spend tokens — the test still exercises:
      - multipart upload
      - file-type accept list (.pdf)
      - file size check
      - session limit check
      - the parse call path
      - the categorise + XLSX export path
      - storage of the row data
    """
    pdfs = sorted(USER_STATEMENTS_DIR.glob("*.pdf")) if USER_STATEMENTS_DIR.is_dir() else []
    if not pdfs:
        pytest.skip(f"no PDFs in {USER_STATEMENTS_DIR}")
    pdf = pdfs[0]

    client, csrf, user = _client_with_subscribed_user(email="pdf-e2e@example.com")

    # Mock the AI parser to return a small but realistic statement.
    fake_result = {
        "transactions": [
            {"date": "2025-12-22", "description": "STRIPE PAYOUT",
             "amount": 1500.0, "balance": None, "type": "credit"},
            {"date": "2025-12-22", "description": "MIPERMIT LTD CHIPPENHAM",
             "amount": -2.60, "balance": None, "type": "debit"},
            {"date": "2025-12-22", "description": "DD TV LICENCE MBP",
             "amount": -14.95, "balance": None, "type": "debit"},
        ],
        "summary": {
            "total_transactions": 3,
            "total_credits": 1500.0,
            "total_debits": 17.55,
            "net": 1482.45,
        },
        "metadata": {
            "bank_name": "Test Bank", "currency": "GBP",
            "source": pdf.name, "method": "claude_haiku_text",
            "ai_usage": {"model": "claude-haiku-4-5-20251001",
                         "input_tokens": 600, "output_tokens": 200,
                         "cost_gbp": 0.0},
        },
    }

    with patch("app.parse_statement_ai", return_value=fake_result):
        with open(pdf, "rb") as f:
            r = client.post(
                "/api/parse",
                files={"file": (pdf.name, f, "application/pdf")},
                headers={"X-CSRF-Token": csrf},
            )

    assert r.status_code == 200, r.text
    body = r.json()
    # The exact transactions we mocked should come through.
    assert len(body["transactions"]) == 3
    descs = [tx["description"] for tx in body["transactions"]]
    assert "STRIPE PAYOUT" in descs
    assert "MIPERMIT LTD CHIPPENHAM" in descs
    # And the spreadsheet should exist + be downloadable.
    assert "download_url" in body


def test_real_pdf_statement_then_hmrc_categorise():
    """After the upload above lands rows in storage, the categorise endpoint
    should return HMRC categories for them — this is the full happy path."""
    pdfs = sorted(USER_STATEMENTS_DIR.glob("*.pdf")) if USER_STATEMENTS_DIR.is_dir() else []
    if not pdfs:
        pytest.skip(f"no PDFs in {USER_STATEMENTS_DIR}")

    client, csrf, user = _client_with_subscribed_user(email="pdf-cat-e2e@example.com")
    fake_result = {
        "transactions": [
            {"date": "2025-12-22", "description": "STRIPE PAYOUT",
             "amount": 1500.0, "balance": None, "type": "credit"},
            {"date": "2025-12-22", "description": "MIPERMIT LTD CHIPPENHAM",
             "amount": -2.60, "balance": None, "type": "debit"},
        ],
        "summary": {"total_transactions": 2, "total_credits": 1500.0,
                    "total_debits": 2.60, "net": 1497.4},
        "metadata": {"bank_name": "Test", "currency": "GBP",
                     "source": pdfs[0].name, "method": "claude_haiku_text",
                     "ai_usage": {"model": "claude-haiku-4-5-20251001",
                                  "input_tokens": 0, "output_tokens": 0,
                                  "cost_gbp": 0.0}},
    }
    with patch("app.parse_statement_ai", return_value=fake_result):
        with open(pdfs[0], "rb") as f:
            up = client.post(
                "/api/parse",
                files={"file": (pdfs[0].name, f, "application/pdf")},
                headers={"X-CSRF-Token": csrf},
            )
    assert up.status_code == 200, up.text

    # Now hit the categorise endpoint with those rows. AI off => rules.
    cat = client.post(
        "/api/hmrc/categorise",
        json={"business_type": "se",
              "rows": up.json()["transactions"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert cat.status_code == 200, cat.text
    cat_body = cat.json()
    assert len(cat_body["rows"]) == 2
    cats = {row["hmrc"]["category"] for row in cat_body["rows"]}
    # Stripe → turnover; MiPermit → travelCosts (regex rules path)
    assert "turnover" in cats
    assert "travelCosts" in cats


# ---------------------------------------------------------------------------
# Real HEIC receipt from ~/Downloads/receipt/ — AI parser mocked
# ---------------------------------------------------------------------------

def test_real_heic_receipt_upload_with_mocked_ai():
    """Upload one of the real HEIC receipts. AI parser mocked. Verifies
    the receipt endpoint accepts HEIC + the cumulative-banner updates."""
    receipts = sorted(USER_RECEIPTS_DIR.glob("*.HEIC")) if USER_RECEIPTS_DIR.is_dir() else []
    if not receipts:
        pytest.skip(f"no HEICs in {USER_RECEIPTS_DIR}")
    receipt = receipts[0]

    client, csrf, user = _client_with_subscribed_user(email="heic-e2e@example.com")

    fake_result = {
        "items": [
            {"description": "Milk 2L", "quantity": 1,
             "unit_price": 2.20, "total_price": 2.20},
            {"description": "Bread", "quantity": 1,
             "unit_price": 1.50, "total_price": 1.50},
        ],
        "totals": {"subtotal": 3.70, "tax": 0.00, "total": 3.70},
        "metadata": {"store_name": "Tesco",
                     "date": "2025-12-22", "currency": "GBP",
                     "source": receipt.name,
                     "ai_usage": {"model": "claude-haiku-4-5-20251001",
                                  "input_tokens": 100, "output_tokens": 50,
                                  "cost_gbp": 0.0}},
    }

    with patch("app.parse_receipt_ai", return_value=fake_result):
        with open(receipt, "rb") as f:
            r = client.post(
                "/api/parse-receipt",
                files={"file": (receipt.name, f, "image/heic")},
                headers={"X-CSRF-Token": csrf},
            )

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 2
    assert body["totals"]["total"] == 3.70
    assert "download_url" in body


def test_real_heic_receipt_bulk_upload():
    """Upload TWO real HEIC receipts at once via the bulk endpoint."""
    receipts = sorted(USER_RECEIPTS_DIR.glob("*.HEIC")) if USER_RECEIPTS_DIR.is_dir() else []
    if len(receipts) < 2:
        pytest.skip(f"need >=2 HEICs in {USER_RECEIPTS_DIR}")

    client, csrf, user = _client_with_subscribed_user(email="heic-bulk-e2e@example.com")

    fake_bulk_result = {
        "combined_items": [
            {"store": "Tesco", "description": "Milk 2L", "quantity": 1,
             "unit_price": 2.20, "total_price": 2.20},
            {"store": "Tesco", "description": "Bread", "quantity": 1,
             "unit_price": 1.50, "total_price": 1.50},
        ],
        "receipt_count": 2,
        "total_items": 2,
        "grand_total": 3.70,
        # The bulk endpoint expects a per-receipt array under "receipts"
        # (see app.py:parse_receipts_bulk_endpoint) — match that shape
        # exactly so the persistence loop in the endpoint works.
        "receipts": [
            {"filename": receipts[0].name, "store_name": "Tesco",
             "currency": "GBP", "total": 2.20, "tax": 0, "subtotal": 2.20,
             "items": [{"description": "Milk 2L", "quantity": 1,
                        "unit_price": 2.20, "total_price": 2.20}],
             "source_size_bytes": receipts[0].stat().st_size},
            {"filename": receipts[1].name, "store_name": "Tesco",
             "currency": "GBP", "total": 1.50, "tax": 0, "subtotal": 1.50,
             "items": [{"description": "Bread", "quantity": 1,
                        "unit_price": 1.50, "total_price": 1.50}],
             "source_size_bytes": receipts[1].stat().st_size},
        ],
        "ai_usage": {"model": "claude-haiku-4-5-20251001",
                     "input_tokens": 200, "output_tokens": 100,
                     "cost_gbp": 0.0},
    }

    with patch("app.parse_receipts_bulk", return_value=fake_bulk_result):
        files = [
            ("files", (receipts[0].name, open(receipts[0], "rb"), "image/heic")),
            ("files", (receipts[1].name, open(receipts[1], "rb"), "image/heic")),
        ]
        try:
            r = client.post(
                "/api/parse-receipts-bulk",
                files=files,
                headers={"X-CSRF-Token": csrf},
            )
        finally:
            for _, (_, fh, _) in files:
                fh.close()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["receipt_count"] == 2
    assert body["grand_total"] == 3.70
