"""
BankParse -- File Upload Edge Case Tests
Tests for /api/parse, /api/parse-receipt, and /api/parse-receipts-bulk endpoints.
Covers auth enforcement, file type validation, size limits, empty files,
valid uploads, path traversal, unicode filenames, and usage gating.
"""

import os
import sys
import pytest

# Ensure the project root is on sys.path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_uploads.db"


@pytest.fixture(autouse=True)
def clean_db():
    """Delete and reinit the test database before each test.
    Also disables the slowapi rate limiter so tests are not throttled."""
    import database

    # Remove old test db if present
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)

    # Close existing connection if any
    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
    database._sqlite_conn = None

    # Monkey-patch _get_sqlite to use the test db path
    import sqlite3

    def _get_sqlite_test():
        if database._sqlite_conn is None:
            conn = sqlite3.connect(TEST_DB_PATH, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            database._sqlite_conn = conn
        return database._sqlite_conn

    database._get_sqlite = _get_sqlite_test

    # Re-initialize schema on test db
    database.init_db()

    # Disable slowapi rate limiter to avoid 429s during testing
    from app import app as _app
    limiter = getattr(_app.state, "limiter", None)
    if limiter is not None:
        limiter.enabled = False

    yield

    # Re-enable rate limiter
    if limiter is not None:
        limiter.enabled = True

    # Cleanup after test
    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


from fastapi.testclient import TestClient
from app import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_CSV_CONTENT = b"Date,Description,Amount\n01/01/2025,Test Transaction,100.00\n"

VALID_CSV_MULTI = (
    b"Date,Description,Amount\n"
    b"01/01/2025,Grocery Store,45.99\n"
    b"02/01/2025,Electric Bill,-120.00\n"
    b"03/01/2025,Salary,3000.00\n"
)


def _get_csrf_token(client: TestClient) -> str:
    """Hit GET /login to get a CSRF cookie into the client jar, return its value."""
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _register(client: TestClient, email: str, password: str):
    """Register a user, handling CSRF automatically. Returns the response."""
    csrf = _get_csrf_token(client)
    return client.post(
        "/api/register",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )


def _login(client: TestClient, email: str, password: str):
    """Login a user, handling CSRF automatically. Returns the response."""
    csrf = client.cookies.get("bp_csrf", "") or _get_csrf_token(client)
    return client.post(
        "/api/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )


def _authenticated_client():
    """Create a TestClient with a registered and authenticated user.

    Returns (client, csrf_token) inside a context-manager-ready TestClient.
    The caller must use the returned client directly (it is already entered).
    """
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    resp = _register(client, "testuser@example.com", "securepass123")
    assert resp.status_code == 200, f"Registration failed: {resp.text}"
    csrf = client.cookies.get("bp_csrf", "")
    return client, csrf


def _upload_file(client, csrf, endpoint, filename, content, content_type="text/csv"):
    """Helper to POST a file upload with CSRF."""
    return client.post(
        endpoint,
        files={"file": (filename, content, content_type)},
        headers={"X-CSRF-Token": csrf},
    )


# ---------------------------------------------------------------------------
# 1. Auth required -- POST /api/parse without auth -> 401
# ---------------------------------------------------------------------------

def test_parse_requires_auth():
    """POST /api/parse without authentication returns 401."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        resp = client.post(
            "/api/parse",
            files={"file": ("test.csv", VALID_CSV_CONTENT, "text/csv")},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 401
        assert "Authentication required" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 2. Invalid file type -- Upload .exe file -> 400
# ---------------------------------------------------------------------------

def test_parse_rejects_exe_file():
    """POST /api/parse with .exe file returns 400 (unsupported type)."""
    client, csrf = _authenticated_client()
    try:
        resp = _upload_file(
            client, csrf, "/api/parse",
            "malware.exe", b"MZ\x90\x00", "application/octet-stream",
        )
        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 3. Oversized file -- Upload 21MB file -> 400
# ---------------------------------------------------------------------------

def test_parse_rejects_oversized_file():
    """POST /api/parse with file > 20MB returns 400."""
    client, csrf = _authenticated_client()
    try:
        # 21 MB of zeros with a valid CSV header so it passes the extension check
        oversized = b"Date,Description,Amount\n" + (b"0" * (21 * 1024 * 1024))
        resp = _upload_file(
            client, csrf, "/api/parse",
            "big.csv", oversized, "text/csv",
        )
        assert resp.status_code == 400
        assert "too large" in resp.json()["detail"].lower() or "20MB" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 4. Empty file -- Upload empty CSV -> 422
# ---------------------------------------------------------------------------

def test_parse_rejects_empty_csv():
    """POST /api/parse with an empty CSV returns 422 (no transactions)."""
    client, csrf = _authenticated_client()
    try:
        # A file with only a header row and no data rows
        empty_csv = b"Date,Description,Amount\n"
        resp = _upload_file(
            client, csrf, "/api/parse",
            "empty.csv", empty_csv, "text/csv",
        )
        # The parser should find no transactions -> 422
        assert resp.status_code == 422
        assert "No transactions found" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 5. Valid CSV upload -- Upload valid CSV -> 200 with transactions
# ---------------------------------------------------------------------------

def test_parse_valid_csv():
    """POST /api/parse with a valid CSV returns 200 and transaction data."""
    client, csrf = _authenticated_client()
    try:
        resp = _upload_file(
            client, csrf, "/api/parse",
            "statement.csv", VALID_CSV_MULTI, "text/csv",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "transactions" in data
        assert len(data["transactions"]) > 0
        # Check that at least one transaction has expected fields
        tx = data["transactions"][0]
        assert "date" in tx or "Date" in tx or any("date" in k.lower() for k in tx)
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 6. Valid receipt upload -- Upload a valid PDF receipt -> 200 with items
# ---------------------------------------------------------------------------

def test_parse_receipt_valid_pdf():
    """POST /api/parse-receipt with a minimal PDF containing receipt text
    returns 200 with parsed items, or 422 if the parser cannot extract items
    from a minimal PDF (which is acceptable -- we verify the endpoint is reachable
    and processes the file without crashing)."""
    client, csrf = _authenticated_client()
    try:
        # Build a minimal valid PDF with receipt-like text content.
        # This is a bare-bones PDF 1.4 with a single page containing receipt text.
        receipt_text = (
            "GROCERY STORE\\n"
            "01/01/2025\\n"
            "Milk                  2.50\\n"
            "Bread                 1.99\\n"
            "Eggs                  3.49\\n"
            "TOTAL                 7.98\\n"
        )
        # Minimal PDF structure
        pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
            b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
            b"4 0 obj\n<< /Length 200 >>\nstream\n"
            b"BT\n/F1 12 Tf\n50 700 Td\n"
            b"(GROCERY STORE) Tj\n0 -20 Td\n"
            b"(01/01/2025) Tj\n0 -20 Td\n"
            b"(Milk                  2.50) Tj\n0 -20 Td\n"
            b"(Bread                 1.99) Tj\n0 -20 Td\n"
            b"(Eggs                  3.49) Tj\n0 -20 Td\n"
            b"(TOTAL                 7.98) Tj\n"
            b"ET\n"
            b"endstream\nendobj\n"
            b"xref\n0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000314 00000 n \n"
            b"0000000266 00000 n \n"
            b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
            b"startxref\n566\n%%EOF\n"
        )

        resp = _upload_file(
            client, csrf, "/api/parse-receipt",
            "receipt.pdf", pdf_content, "application/pdf",
        )
        # Accept 200 (items found) or 422 (no items from minimal PDF) --
        # the important thing is it does not return 400 or 500.
        assert resp.status_code in (200, 422), (
            f"Expected 200 or 422 but got {resp.status_code}: {resp.text}"
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "items" in data
            assert len(data["items"]) > 0
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 7. Wrong file type for receipt -- Upload .exe to /api/parse-receipt -> 400
# ---------------------------------------------------------------------------

def test_parse_receipt_rejects_exe():
    """POST /api/parse-receipt with .exe file returns 400."""
    client, csrf = _authenticated_client()
    try:
        resp = _upload_file(
            client, csrf, "/api/parse-receipt",
            "payload.exe", b"MZ\x90\x00", "application/octet-stream",
        )
        assert resp.status_code == 400
        assert "Unsupported file type" in resp.json()["detail"]
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 8. Bulk upload auth required -- POST /api/parse-receipts-bulk without auth -> 401
# ---------------------------------------------------------------------------

def test_bulk_receipts_requires_auth():
    """POST /api/parse-receipts-bulk without auth returns 401."""
    with TestClient(app, raise_server_exceptions=False) as client:
        csrf = _get_csrf_token(client)
        resp = client.post(
            "/api/parse-receipts-bulk",
            files=[("files", ("r1.pdf", b"%PDF-1.4 fake", "application/pdf"))],
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 401
        assert "Authentication required" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 9. Bulk upload empty list -- POST /api/parse-receipts-bulk with no files -> 400/403
#    (Free tier cannot use bulk at all, so we expect 403 first.)
# ---------------------------------------------------------------------------

def test_bulk_receipts_free_tier_blocked():
    """POST /api/parse-receipts-bulk as free tier returns 403 (bulk requires paid)."""
    client, csrf = _authenticated_client()
    try:
        resp = client.post(
            "/api/parse-receipts-bulk",
            files=[("files", ("r1.pdf", b"%PDF-1.4 fake", "application/pdf"))],
            headers={"X-CSRF-Token": csrf},
        )
        # Free tier users cannot use bulk upload at all
        assert resp.status_code == 403
        assert "Bulk upload requires" in resp.json()["detail"] or "subscription" in resp.json()["detail"].lower()
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 10. Path traversal in filename -- file named "../../etc/passwd.csv"
#     should be handled safely (stripped to "passwd.csv")
# ---------------------------------------------------------------------------

def test_path_traversal_filename_is_safe():
    """Upload a file with path traversal in the name; the server should
    strip it and process normally (not write to ../../etc/passwd)."""
    client, csrf = _authenticated_client()
    try:
        resp = _upload_file(
            client, csrf, "/api/parse",
            "../../etc/passwd.csv", VALID_CSV_MULTI, "text/csv",
        )
        # The server uses Path(filename).name to strip traversal components.
        # Should process normally: 200 (transactions found) or 422 (none found).
        # It must NOT return 500 from a broken path.
        assert resp.status_code in (200, 422), (
            f"Path traversal filename caused unexpected status {resp.status_code}: {resp.text}"
        )
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 11. Special characters in filename -- Upload file with unicode name
# ---------------------------------------------------------------------------

def test_unicode_filename():
    """Upload a CSV with a unicode filename; server should handle it safely."""
    client, csrf = _authenticated_client()
    try:
        resp = _upload_file(
            client, csrf, "/api/parse",
            "\u00e9tat_de_compte_\u00fc\u00f1\u00ed\u00e7\u00f8d\u00e9.csv",
            VALID_CSV_MULTI, "text/csv",
        )
        # Should process normally, not crash on the unicode filename
        assert resp.status_code in (200, 422), (
            f"Unicode filename caused unexpected status {resp.status_code}: {resp.text}"
        )
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 12. Multiple uploads increment usage -- Upload twice as free tier;
#     the 2nd should fail with 403 (monthly limit of 1 statement)
# ---------------------------------------------------------------------------

def test_free_tier_second_upload_blocked():
    """Free tier allows 1 statement per month. The second upload returns 403."""
    client, csrf = _authenticated_client()
    try:
        # First upload -- should succeed
        resp1 = _upload_file(
            client, csrf, "/api/parse",
            "statement1.csv", VALID_CSV_MULTI, "text/csv",
        )
        assert resp1.status_code == 200, (
            f"First upload should succeed but got {resp1.status_code}: {resp1.text}"
        )

        # Second upload -- should be blocked (free tier: 1 statement/month)
        resp2 = _upload_file(
            client, csrf, "/api/parse",
            "statement2.csv", VALID_CSV_MULTI, "text/csv",
        )
        assert resp2.status_code == 403
        assert "FREE_LIMIT_REACHED" in resp2.json()["detail"]
    finally:
        client.__exit__(None, None, None)
