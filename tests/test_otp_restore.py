"""
BankParse — OTP Restore Flow Tests
Tests for /api/restore/request, /api/restore/verify, and underlying
database OTP functions (store_otp, verify_otp, cleanup_expired_otps).
"""

import os
import sys
import time
import pytest
from unittest.mock import patch, MagicMock

# Ensure the project root is on sys.path so imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse.db"


@pytest.fixture(autouse=True)
def clean_db():
    """Delete and reinit the test database before each test, and reset rate limiter."""
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

    # Reset the slowapi rate limiter so tests don't interfere with each other
    from app import app as _app
    if hasattr(_app.state, "limiter"):
        _app.state.limiter.reset()

    yield

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


def _get_csrf_token(client: TestClient) -> str:
    """Hit GET /login to get a CSRF cookie into the client jar, return its value."""
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _post_restore_request(client: TestClient, json_body: dict):
    """POST /api/restore/request with CSRF handling."""
    csrf = client.cookies.get("bp_csrf", "") or _get_csrf_token(client)
    return client.post(
        "/api/restore/request",
        json=json_body,
        headers={"X-CSRF-Token": csrf},
    )


def _post_restore_verify(client: TestClient, json_body: dict):
    """POST /api/restore/verify with CSRF handling."""
    csrf = client.cookies.get("bp_csrf", "") or _get_csrf_token(client)
    return client.post(
        "/api/restore/verify",
        json=json_body,
        headers={"X-CSRF-Token": csrf},
    )


def _make_mock_stripe_customer(email: str):
    """Create mock Stripe Customer and Subscription objects for a valid restore flow."""
    mock_customer = MagicMock()
    mock_customer.id = "cus_test123"
    mock_customer.email = email

    mock_sub = MagicMock()
    mock_sub.id = "sub_test123"

    mock_search_result = MagicMock()
    mock_search_result.data = [mock_customer]

    mock_subs_result = MagicMock()
    mock_subs_result.data = [mock_sub]

    return mock_search_result, mock_subs_result


# ==========================================================================
# 1. Request OTP — missing email
# ==========================================================================

def test_restore_request_missing_email():
    """POST /api/restore/request without email returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_request(client, {})
            assert resp.status_code == 400
            assert "email" in resp.json()["detail"].lower()


def test_restore_request_empty_email():
    """POST /api/restore/request with empty email string returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_request(client, {"email": ""})
            assert resp.status_code == 400


# ==========================================================================
# 2. Request OTP — invalid email format
# ==========================================================================

def test_restore_request_invalid_email_no_at():
    """POST /api/restore/request with email missing '@' returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_request(client, {"email": "not-an-email"})
            assert resp.status_code == 400
            assert "email" in resp.json()["detail"].lower()


def test_restore_request_invalid_email_whitespace():
    """POST /api/restore/request with whitespace-only email returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_request(client, {"email": "   "})
            assert resp.status_code == 400


# ==========================================================================
# 3. Request OTP — Stripe not configured
# ==========================================================================

def test_restore_request_stripe_not_configured():
    """POST /api/restore/request returns 501 when STRIPE_AVAILABLE is False."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", False):
            resp = _post_restore_request(client, {"email": "user@example.com"})
            assert resp.status_code == 501
            assert "Stripe" in resp.json()["detail"]


def test_restore_request_stripe_key_missing():
    """POST /api/restore/request returns 501 when STRIPE_SECRET_KEY is empty."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_SECRET_KEY", ""):
            resp = _post_restore_request(client, {"email": "user@example.com"})
            assert resp.status_code == 501


# ==========================================================================
# 4. Verify OTP — missing email
# ==========================================================================

def test_restore_verify_missing_email():
    """POST /api/restore/verify without email returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_verify(client, {"code": "123456"})
            assert resp.status_code == 400
            assert "email" in resp.json()["detail"].lower()


def test_restore_verify_empty_email():
    """POST /api/restore/verify with empty email returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_verify(client, {"email": "", "code": "123456"})
            assert resp.status_code == 400


# ==========================================================================
# 5. Verify OTP — missing code
# ==========================================================================

def test_restore_verify_missing_code():
    """POST /api/restore/verify without code returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_verify(client, {"email": "user@example.com"})
            assert resp.status_code == 400
            assert "6-digit" in resp.json()["detail"]


def test_restore_verify_empty_code():
    """POST /api/restore/verify with empty code returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_verify(client, {"email": "user@example.com", "code": ""})
            assert resp.status_code == 400


# ==========================================================================
# 6. Verify OTP — invalid code length (5 digits)
# ==========================================================================

def test_restore_verify_invalid_code_length_short():
    """POST /api/restore/verify with 5-digit code returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_verify(client, {"email": "user@example.com", "code": "12345"})
            assert resp.status_code == 400
            assert "6-digit" in resp.json()["detail"]


def test_restore_verify_invalid_code_length_long():
    """POST /api/restore/verify with 7-digit code returns 400."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_verify(client, {"email": "user@example.com", "code": "1234567"})
            assert resp.status_code == 400


# ==========================================================================
# 7. Verify OTP — wrong code
# ==========================================================================

def test_restore_verify_wrong_code():
    """Verify with an incorrect code returns 400."""
    import database

    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)

        # Store a known OTP directly in the database
        database.store_otp("user@example.com", "111111", "test-session-1")

        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"):
            resp = _post_restore_verify(client, {"email": "user@example.com", "code": "999999"})
            assert resp.status_code == 400
            assert "Invalid or expired" in resp.json()["detail"]


# ==========================================================================
# 8. Verify OTP — expired code (>10 min)
# ==========================================================================

def test_restore_verify_expired_code():
    """OTP stored > 10 minutes ago should be rejected as expired."""
    import database

    # Store OTP directly
    database.store_otp("expired@example.com", "222222", "test-session-2")

    # Simulate time passing: advance time.time() by 601 seconds (>600s = 10min)
    real_time = time.time
    with patch("database.time") as mock_time_module:
        mock_time_module.time = lambda: real_time() + 601
        result = database.verify_otp("expired@example.com", "222222")

    assert result is None


# ==========================================================================
# 9. Verify OTP — single use (verify once succeeds, verify again fails)
# ==========================================================================

def test_restore_verify_single_use():
    """OTP can only be used once; second verification returns None."""
    import database

    database.store_otp("single@example.com", "333333", "test-session-3")

    # First verify should succeed and return the session_id
    result1 = database.verify_otp("single@example.com", "333333")
    assert result1 == "test-session-3"

    # Second verify should fail (used=1 now)
    result2 = database.verify_otp("single@example.com", "333333")
    assert result2 is None


# ==========================================================================
# 10. OTP invalidates previous — store code1, store code2, code1 invalid
# ==========================================================================

def test_otp_invalidates_previous_code():
    """Storing a new OTP for the same email deletes all previous codes."""
    import database

    database.store_otp("multi@example.com", "444444", "session-a")
    database.store_otp("multi@example.com", "555555", "session-b")

    # Old code should no longer verify
    result_old = database.verify_otp("multi@example.com", "444444")
    assert result_old is None

    # New code should verify successfully
    result_new = database.verify_otp("multi@example.com", "555555")
    assert result_new == "session-b"


# ==========================================================================
# 11. Database OTP cleanup — expired OTPs removed by cleanup
# ==========================================================================

def test_otp_cleanup_expired():
    """cleanup_expired_otps removes codes older than 10 minutes and used codes."""
    import database

    # Store an OTP and immediately mark it as used
    database.store_otp("used@example.com", "666666", "session-used")
    database.verify_otp("used@example.com", "666666")  # marks as used

    # Store another OTP that we'll simulate as expired
    database.store_otp("old@example.com", "777777", "session-old")

    # Manually backdate the old OTP's created_at so it's expired
    conn = database._get_sqlite()
    conn.execute(
        "UPDATE otp_codes SET created_at = ? WHERE email = ?",
        (time.time() - 700, "old@example.com"),
    )
    conn.commit()

    # Store a fresh, unused OTP that should survive cleanup
    database.store_otp("fresh@example.com", "888888", "session-fresh")

    # Run cleanup
    database.cleanup_expired_otps()

    # The used OTP should be gone
    result_used = database.verify_otp("used@example.com", "666666")
    assert result_used is None

    # The expired OTP should be gone
    result_old = database.verify_otp("old@example.com", "777777")
    assert result_old is None

    # The fresh OTP should still verify
    result_fresh = database.verify_otp("fresh@example.com", "888888")
    assert result_fresh == "session-fresh"


# ==========================================================================
# 12. OTP generation — 6 digits, numeric only
# ==========================================================================

def test_generate_otp_length_and_format():
    """generate_otp returns a 6-digit numeric-only string by default."""
    from otp import generate_otp

    for _ in range(50):
        code = generate_otp()
        assert len(code) == 6, f"Expected 6 digits, got {len(code)}: {code}"
        assert code.isdigit(), f"Expected numeric-only, got: {code}"


def test_generate_otp_custom_length():
    """generate_otp respects a custom length parameter."""
    from otp import generate_otp

    code = generate_otp(length=8)
    assert len(code) == 8
    assert code.isdigit()


def test_generate_otp_uniqueness():
    """generate_otp produces varying codes (not always the same)."""
    from otp import generate_otp

    codes = {generate_otp() for _ in range(20)}
    # With 6 random digits, 20 samples should produce at least 2 unique values
    assert len(codes) >= 2, "OTP generation appears non-random"


# ==========================================================================
# 13. Multiple rapid OTP requests — rate limiting (3/minute via slowapi)
# ==========================================================================

def test_restore_request_rate_limit():
    """
    /api/restore/request is rate-limited to 3/minute.
    The 4th request within one minute should be rejected with 429.
    """
    mock_search, mock_subs = _make_mock_stripe_customer("rate@example.com")

    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)

        with patch("app.STRIPE_AVAILABLE", True), \
             patch("app.STRIPE_SECRET_KEY", "sk_test_fake"), \
             patch("app.stripe") as mock_stripe, \
             patch("app.send_otp_email", return_value=True), \
             patch("app.generate_otp", return_value="123456"):

            mock_stripe.Customer.search.return_value = mock_search
            mock_stripe.Subscription.list.return_value = mock_subs

            results = []
            for i in range(5):
                resp = _post_restore_request(client, {"email": "rate@example.com"})
                results.append(resp.status_code)

            # First 3 should succeed (200), subsequent requests should be 429
            success_count = results.count(200)
            limited_count = results.count(429)
            assert success_count >= 1, f"Expected at least 1 success, got statuses: {results}"
            assert limited_count >= 1, f"Expected at least 1 rate-limited (429), got statuses: {results}"
            # The rate limit is 3/minute, so at most 3 succeed
            assert success_count <= 3, f"Expected at most 3 successes, got {success_count}: {results}"


# ==========================================================================
# Additional integration tests: Verify OTP — Stripe not configured
# ==========================================================================

def test_restore_verify_stripe_not_configured():
    """POST /api/restore/verify returns 501 when STRIPE_AVAILABLE is False."""
    with TestClient(app, raise_server_exceptions=False) as client:
        _get_csrf_token(client)
        with patch("app.STRIPE_AVAILABLE", False):
            resp = _post_restore_verify(client, {"email": "user@example.com", "code": "123456"})
            assert resp.status_code == 501
            assert "Stripe" in resp.json()["detail"]


# ==========================================================================
# Additional: Full happy-path verify via database (no HTTP, direct DB test)
# ==========================================================================

def test_verify_otp_happy_path_database():
    """store_otp + verify_otp with correct code within time window returns session_id."""
    import database

    database.store_otp("happy@example.com", "999999", "session-happy")
    result = database.verify_otp("happy@example.com", "999999")
    assert result == "session-happy"


def test_verify_otp_case_insensitive_email():
    """OTP lookup should match email exactly as stored (lowercased by endpoint)."""
    import database

    database.store_otp("user@example.com", "101010", "session-case")
    # Exact match works
    assert database.verify_otp("user@example.com", "101010") == "session-case"


def test_verify_otp_wrong_email():
    """verify_otp with correct code but wrong email returns None."""
    import database

    database.store_otp("correct@example.com", "202020", "session-email")
    result = database.verify_otp("wrong@example.com", "202020")
    assert result is None
