"""
Regression tests for the receipt upload bug Mitchell found on 2026-05-20:

  `/api/receipts/email-in` was receiving attachments but never running the
  AI parser, never creating a ledger_receipts row, and never trying to
  match against the bank-statement transactions. Receipts disappeared
  into the void; the response said "Saved 1" but nothing happened.

The fix: process each attachment through the same pipeline as the in-app
upload — write to UPLOAD_DIR, parse_receipt_ai, ingest_receipt_and_match.

These tests stub parse_receipt_ai so we don't need a real Anthropic key.
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_email_in_parses.db"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")


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


def _client(email: str = "rc@example.com") -> tuple:
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    client.post("/api/register",
                json={"email": email, "password": "password12345"},
                headers={"X-CSRF-Token": csrf})
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], subscription_status="trialing",
                    stripe_subscription_id="sub_rc",
                    trial_end_at=time.time() + 7*86400)
    return client, user, csrf


def _fake_receipt_parse(store: str, date_iso: str, total: float, tax: float = 0.0) -> dict:
    """Builds the dict shape parse_receipt_ai would return for a valid receipt."""
    return {
        "items": [{
            "description": "Test item",
            "quantity": 1,
            "unit_price": total - tax,
            "total_price": total - tax,
        }],
        "totals": {
            "subtotal": total - tax,
            "tax": tax,
            "total": total,
        },
        "summary": {
            "store_name": store,
            "date": date_iso,
            "currency": "GBP",
            "payment_method": "card",
        },
        "metadata": {
            "ai_usage": {"input_tokens": 100, "output_tokens": 50, "model": "haiku"},
        },
    }


# ---------------------------------------------------------------------------
# The actual bug: receipts uploaded via email-in must end up in ledger_receipts
# ---------------------------------------------------------------------------


def test_email_in_writes_receipt_to_ledger():
    """Before the fix this was the literal Mitchell-bug — the endpoint
    said 'saved' but no ledger_receipts row was created."""
    client, user, csrf = _client("ledger@example.com")
    import database as _db

    fake_pdf = b"%PDF-1.4 fake bytes"
    fake = _fake_receipt_parse("Amazon", "2026-05-15", 42.99, 7.16)

    with patch("parsers.ai_parser.parse_receipt_ai", return_value=fake):
        r = client.post("/api/receipts/email-in",
                        json={
                            "from": "billing@stripe.com",
                            "subject": "Invoice",
                            "attachments": [{
                                "filename": "amazon_receipt.pdf",
                                "content_b64": base64.b64encode(fake_pdf).decode(),
                            }],
                        },
                        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 1

    # Crucial: a row in ledger_receipts now exists with the parsed data
    receipts = _db.get_user_ledger_receipts(user["id"])
    assert len(receipts) == 1
    rc = receipts[0]
    assert rc["store_name"] == "Amazon"
    assert rc["date_iso"] == "2026-05-15"
    assert abs(rc["total_amount"] - 42.99) < 0.01
    assert abs(rc["tax_amount"] - 7.16) < 0.01


def test_email_in_auto_matches_to_existing_bank_line():
    """The headline interaction: upload a statement, then upload a matching
    receipt → the receipt should auto-link to the bank line by 'exact'."""
    client, user, csrf = _client("match@example.com")
    import database as _db

    # Seed a bank transaction that the receipt will match
    tx_id = _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-05-15", description="AMAZON UK MARKETPLACE",
        amount=-42.99,
    )

    fake = _fake_receipt_parse("Amazon", "2026-05-15", 42.99, 7.16)
    with patch("parsers.ai_parser.parse_receipt_ai", return_value=fake):
        r = client.post("/api/receipts/email-in",
                        json={
                            "from": "x@example.com",
                            "attachments": [{
                                "filename": "amazon.pdf",
                                "content_b64": base64.b64encode(b"%PDF").decode(),
                            }],
                        },
                        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 1
    assert body["auto_matched"] == 1
    assert body["receipts"][0]["match"]["strategy"] == "exact"

    # The bank transaction now reports matched + VAT inherited
    fresh_tx = _db.get_transaction_by_id(tx_id, user["id"])
    assert fresh_tx["receipt_status"] == "matched"
    assert abs(fresh_tx["vat_amount"] - 7.16) < 0.01


def test_email_in_parse_failure_is_surfaced_not_silent():
    """If the AI parser raises (bad file, missing API key, etc.) the user
    must SEE that — not get a misleading 'Saved 1'."""
    client, user, csrf = _client("err@example.com")

    with patch("parsers.ai_parser.parse_receipt_ai",
               side_effect=RuntimeError("ANTHROPIC_API_KEY missing")):
        r = client.post("/api/receipts/email-in",
                        json={
                            "from": "x@example.com",
                            "attachments": [{
                                "filename": "broken.pdf",
                                "content_b64": base64.b64encode(b"xx").decode(),
                            }],
                        },
                        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 0
    assert len(body["parse_errors"]) == 1
    assert "broken.pdf" in body["parse_errors"][0]


def test_email_in_writes_actual_file_to_disk():
    """The receipt PDF must persist on disk so the accountant ZIP and the
    defence sheet can attach it. Before the fix nothing was written."""
    client, user, csrf = _client("disk@example.com")
    import database as _db

    fake_pdf_bytes = b"%PDF-1.4 actual content"
    fake = _fake_receipt_parse("LocalStore", "2026-05-15", 9.50)

    with patch("parsers.ai_parser.parse_receipt_ai", return_value=fake):
        r = client.post("/api/receipts/email-in",
                        json={
                            "from": "x@example.com",
                            "attachments": [{
                                "filename": "tiny.pdf",
                                "content_b64": base64.b64encode(fake_pdf_bytes).decode(),
                            }],
                        },
                        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200

    rc = _db.get_user_ledger_receipts(user["id"])[0]
    file_path = rc["file_path"]
    assert file_path is not None
    assert os.path.exists(file_path), f"Receipt PDF should be on disk at {file_path}"
    with open(file_path, "rb") as f:
        assert f.read() == fake_pdf_bytes
    # cleanup
    try: os.unlink(file_path)
    except OSError: pass


def test_email_in_response_includes_match_summary_for_ui():
    """The snap-and-send card needs the per-receipt match outcome so the
    user can see 'matched to Amazon £42.99' immediately."""
    client, user, csrf = _client("summary@example.com")

    fake = _fake_receipt_parse("Amazon", "2026-05-15", 42.99, 7.16)
    with patch("parsers.ai_parser.parse_receipt_ai", return_value=fake):
        r = client.post("/api/receipts/email-in",
                        json={
                            "from": "x@example.com",
                            "attachments": [{
                                "filename": "a.pdf",
                                "content_b64": base64.b64encode(b"x").decode(),
                            }],
                        },
                        headers={"X-CSRF-Token": csrf})
    body = r.json()
    assert "receipts" in body
    assert body["receipts"][0]["store"] == "Amazon"
    assert body["receipts"][0]["total"] == 42.99
    assert "match" in body["receipts"][0]


def test_email_in_multiple_attachments_all_parsed():
    """Bulk gallery upload sends N attachments — all must be parsed."""
    client, user, csrf = _client("bulk@example.com")
    import database as _db

    fakes = [
        _fake_receipt_parse("Amazon",   "2026-05-15", 42.99),
        _fake_receipt_parse("Tesco",    "2026-05-16", 15.20),
        _fake_receipt_parse("WHSmith",  "2026-05-17", 8.50),
    ]
    # Use side_effect so each call returns the next fake
    with patch("parsers.ai_parser.parse_receipt_ai", side_effect=fakes):
        r = client.post("/api/receipts/email-in",
                        json={
                            "from": "x@example.com",
                            "attachments": [
                                {"filename": f"r{i}.pdf",
                                 "content_b64": base64.b64encode(f"pdf{i}".encode()).decode()}
                                for i in range(3)
                            ],
                        },
                        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 3
    receipts = _db.get_user_ledger_receipts(user["id"])
    assert len(receipts) == 3
    stores = {r["store_name"] for r in receipts}
    assert stores == {"Amazon", "Tesco", "WHSmith"}


def test_email_in_webhook_path_also_parses():
    """Webhook from Resend/SES — no session cookie. The routing-by-token
    path must ALSO run the parser."""
    client, user, _ = _client("webhook@example.com")
    token = client.get("/api/receipts/forwarding-address").json()["address"].split("@")[0]

    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()

    fake = _fake_receipt_parse("Amazon", "2026-05-15", 99.99, 16.67)
    with patch("parsers.ai_parser.parse_receipt_ai", return_value=fake):
        r = anon.post("/api/receipts/email-in",
                      json={
                          "to": f"{token}@receipts.bankscanai.com",
                          "from": "x@example.com",
                          "attachments": [{
                              "filename": "stripe.pdf",
                              "content_b64": base64.b64encode(b"%PDF").decode(),
                          }],
                      })
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] == 1

    import database as _db
    receipts = _db.get_user_ledger_receipts(user["id"])
    assert len(receipts) == 1
    assert receipts[0]["store_name"] == "Amazon"


def test_email_in_audit_summary_reflects_new_receipt():
    """End-to-end: after the email-in upload, /api/audit-summary should
    show the receipt as backing one of the user's expense rows."""
    client, user, csrf = _client("auditflow@example.com")
    import database as _db

    # Seed a bank transaction
    _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-05-15", description="AMAZON UK MARKETPLACE",
        amount=-42.99, hmrc_category="se_general_admin_costs",
    )

    fake = _fake_receipt_parse("Amazon", "2026-05-15", 42.99, 7.16)
    with patch("parsers.ai_parser.parse_receipt_ai", return_value=fake):
        client.post("/api/receipts/email-in",
                    json={
                        "from": "x@example.com",
                        "attachments": [{
                            "filename": "a.pdf",
                            "content_b64": base64.b64encode(b"x").decode(),
                        }],
                    },
                    headers={"X-CSRF-Token": csrf})

    summary = client.get("/api/audit-summary").json()
    # The expense category should show 100% receipt-backed
    cats = {c["category"]: c for c in summary["categories"]}
    admin = cats.get("se_general_admin_costs")
    assert admin is not None
    assert admin["matched_count"] == 1
    assert admin["audit_ready_pct"] == 100
    assert summary["totals"]["audit_ready_pct"] == 100
