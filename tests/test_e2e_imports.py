"""BankParse — end-to-end import tests for statements and receipts.

These tests exercise the FULL HTTP request → parser → XLSX download
path through FastAPI's TestClient. They are deliberately written to
run *unchanged* on both main and the MVC refactor branch:

  - The statement test uses a CSV (no AI dependency, deterministic).
  - The receipt test patches `parsers.ai_parser.parse_receipt_ai` —
    that module path is identical on both branches, so the patch
    target survives the refactor.

Purpose: prove the public surface (POST /api/parse, POST /api/parse-receipt)
behaves identically before and after the MVC refactor. If both branches
return the same JSON shape, same XLSX size order, and same counts for
the same input, the refactor is behaviour-preserving.
"""

import io
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_e2e_imports.db"


# ==========================================================================
# Isolation fixture — same monkey-patch pattern as test_auth.py
# ==========================================================================

@pytest.fixture(autouse=True)
def clean_db():
    """Delete + reinit the test database before each test."""
    import database

    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)

    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
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

    # Reset rate limiter state between tests so /api/parse doesn't hit
    # the 10/min cap when tests are run together.
    from app import app as _app
    if hasattr(_app.state, "limiter"):
        _app.state.limiter.reset()

    yield

    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


from fastapi.testclient import TestClient  # noqa: E402
from app import app  # noqa: E402


# ==========================================================================
# Helpers — auth + a known-good CSV
# ==========================================================================

def _get_csrf(client: TestClient) -> str:
    client.get("/login")
    return client.cookies.get("bp_csrf", "")


def _register_and_verify(client: TestClient, email: str, password: str) -> dict:
    """Register a user, then mark their email verified directly in the DB
    so AI parse quotas don't block tests on the email_verified gate."""
    import database

    csrf = _get_csrf(client)
    resp = client.post(
        "/api/register",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, f"register failed: {resp.status_code} {resp.text}"

    user = database.get_user_by_email(email)
    assert user is not None
    database.mark_email_verified(user["id"])
    return user


def _csv_with_12_transactions() -> bytes:
    """A 12-row UK bank statement CSV.

    Reusing the exact same shape as test_app.py::create_sample_csv so
    expected counts stay aligned with that existing test.
    """
    rows = [
        b"Date,Description,Money Out,Money In,Balance\r\n",
        b"15/01/2025,TESCO STORES 3217,45.67,,1954.33\r\n",
        b"15/01/2025,SALARY - ACME LTD,,2850.00,4804.33\r\n",
        b"16/01/2025,DIRECT DEBIT - VODAFONE,32.00,,4772.33\r\n",
        b"17/01/2025,TRANSFER - J SMITH,,150.00,4922.33\r\n",
        b"18/01/2025,AMAZON UK MARKETPLACE,89.99,,4832.34\r\n",
        b"19/01/2025,STANDING ORDER - RENT,750.00,,4082.34\r\n",
        b"20/01/2025,CONTACTLESS - COSTA COFFEE,4.50,,4077.84\r\n",
        b"21/01/2025,FASTER PAYMENT - FREELANCE,,500.00,4577.84\r\n",
        b"22/01/2025,DIRECT DEBIT - COUNCIL TAX,156.00,,4421.84\r\n",
        b"23/01/2025,CARD PAYMENT - SAINSBURYS,62.30,,4359.54\r\n",
        b"24/01/2025,INTEREST PAID,,1.23,4360.77\r\n",
        b"25/01/2025,ATM WITHDRAWAL - HSBC,200.00,,4160.77\r\n",
    ]
    return b"".join(rows)


# ==========================================================================
# Statement import — CSV (deterministic, no AI)
# ==========================================================================

def test_statement_import_csv_end_to_end():
    """POST /api/parse with a UK bank CSV → 200 with 12 transactions + XLSX URL.

    No AI involved — CSV path is local parsing. The same code runs on main
    and on the MVC refactor branch via include_router → routers.parsing.
    """
    with TestClient(app, raise_server_exceptions=False) as client:
        _register_and_verify(client, "stmt_e2e@test.local", "TestPass123!")

        csrf = client.cookies.get("bp_csrf", "")
        csv_bytes = _csv_with_12_transactions()

        resp = client.post(
            "/api/parse",
            files={"file": ("statement.csv", csv_bytes, "text/csv")},
            headers={"X-CSRF-Token": csrf},
        )

        assert resp.status_code == 200, f"parse failed: {resp.status_code} {resp.text}"
        body = resp.json()

        # Shape contract — same on both branches.
        assert "transactions" in body
        assert "summary" in body
        assert "metadata" in body
        assert "download_url" in body

        # 12 input rows → 12 parsed transactions.
        assert len(body["transactions"]) == 12, body

        # Summary should reflect totals (12 transactions, mix of credits + debits).
        # The CSV parser stores debits as negative — check absolute non-zero.
        summary = body["summary"]
        assert summary.get("total_transactions") == 12
        assert summary.get("total_debits", 0) != 0
        assert summary.get("total_credits", 0) != 0

        # Download URL points at the /downloads mount.
        assert body["download_url"].startswith("/downloads/")
        assert body["download_url"].endswith(".xlsx")

        # Verify the XLSX exists and is non-empty.
        download_resp = client.get(body["download_url"])
        assert download_resp.status_code == 200
        assert len(download_resp.content) > 500   # XLSX zips are always >500 bytes
        # XLSX files start with the ZIP magic bytes PK\x03\x04.
        assert download_resp.content[:2] == b"PK"


def test_statement_import_rejects_unauthenticated():
    """Without a session cookie, /api/parse returns 401 — same on both branches."""
    with TestClient(app, raise_server_exceptions=False) as client:
        # CSRF is enforced — fetch a token first so we hit the auth check, not CSRF.
        csrf = _get_csrf(client)
        resp = client.post(
            "/api/parse",
            files={"file": ("statement.csv", _csv_with_12_transactions(), "text/csv")},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 401, resp.text


def test_statement_import_rejects_unsupported_extension():
    """A .docx file should be rejected with 400 — same on both branches."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _register_and_verify(client, "stmt_ext@test.local", "TestPass123!")
        csrf = client.cookies.get("bp_csrf", "")

        resp = client.post(
            "/api/parse",
            files={"file": ("statement.docx", b"not a real docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        assert "unsupported" in resp.json()["detail"].lower()


# ==========================================================================
# Receipt import — patches the AI parser at its module location (same on both)
# ==========================================================================

# Canned AI parser response so we don't actually call Anthropic in tests.
_FAKE_RECEIPT_RESULT = {
    "items": [
        {"description": "Whole Milk 2L", "quantity": 1, "unit_price": 1.55, "total_price": 1.55},
        {"description": "Hovis Bread 800g", "quantity": 1, "unit_price": 1.35, "total_price": 1.35},
        {"description": "Chicken Breast 500g", "quantity": 1, "unit_price": 3.80, "total_price": 3.80},
    ],
    "totals": {"subtotal": 6.70, "tax": 0.00, "total": 6.70},
    "metadata": {
        "store_name": "TESCO STORES LTD",
        "date": "2025-03-15",
        "currency": "GBP",
        "ai_usage": {
            "model": "claude-haiku-4-5-20251001",
            "input_tokens": 1500,
            "output_tokens": 250,
        },
    },
}


def _tiny_png_bytes() -> bytes:
    """A 1×1 transparent PNG — minimum file we can claim is an image."""
    import base64
    return base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )


def _parse_receipt_patch_target() -> str:
    """Return the right module path to patch `parse_receipt_ai` on.

    On main the route lives in app.py, so `app.parse_receipt_ai` is the
    in-scope binding. On the MVC refactor branch the route moved to
    routers/parsing.py, so that's where the function is looked up. We
    detect which one is live and patch where Python actually resolves
    the call — Python mock idiom: patch where it's looked up, not where
    it's defined.
    """
    import importlib.util
    if importlib.util.find_spec("routers.parsing") is not None:
        return "routers.parsing.parse_receipt_ai"
    return "app.parse_receipt_ai"


def test_receipt_import_end_to_end_mocked():
    """POST /api/parse-receipt with a mocked AI parser → 200 with items + XLSX URL.

    Patch target is chosen at runtime so the test runs on both main
    (where the route lives in app.py) and the MVC refactor branch
    (routers/parsing.py).
    """
    with TestClient(app, raise_server_exceptions=False) as client:
        _register_and_verify(client, "rcpt_e2e@test.local", "TestPass123!")
        csrf = client.cookies.get("bp_csrf", "")

        with patch(_parse_receipt_patch_target(), return_value=_FAKE_RECEIPT_RESULT):
            resp = client.post(
                "/api/parse-receipt",
                files={"file": ("receipt.png", _tiny_png_bytes(), "image/png")},
                headers={"X-CSRF-Token": csrf},
            )

        assert resp.status_code == 200, f"parse-receipt failed: {resp.status_code} {resp.text}"
        body = resp.json()

        # Shape contract.
        assert "items" in body
        assert "totals" in body
        assert "metadata" in body
        assert "download_url" in body

        # Items match the mocked parser output.
        assert len(body["items"]) == 3
        assert body["items"][0]["description"] == "Whole Milk 2L"
        assert body["totals"]["total"] == 6.70

        # Download URL is served.
        assert body["download_url"].startswith("/downloads/")
        assert body["download_url"].endswith(".xlsx")

        download_resp = client.get(body["download_url"])
        assert download_resp.status_code == 200
        assert download_resp.content[:2] == b"PK"


def test_receipt_import_rejects_unauthenticated():
    """Without a session cookie, /api/parse-receipt returns 401 — same on both."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf(client)
        resp = client.post(
            "/api/parse-receipt",
            files={"file": ("receipt.png", _tiny_png_bytes(), "image/png")},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 401, resp.text


def test_receipt_import_rejects_unsupported_extension():
    """A .txt file should be rejected with 400 — same on both branches."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _register_and_verify(client, "rcpt_ext@test.local", "TestPass123!")
        csrf = client.cookies.get("bp_csrf", "")

        resp = client.post(
            "/api/parse-receipt",
            files={"file": ("receipt.txt", b"not an image or pdf", "text/plain")},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        assert "unsupported" in resp.json()["detail"].lower()
