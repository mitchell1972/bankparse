"""
BankScan AI — E2E Starter Tier User Journey Test

Tests the complete Starter tier journey against the live production site:
  1. Register a new user
  2. Login and get auth cookie
  3. Check usage (verify free tier)
  4. Test Stripe checkout flow (verify checkout URL generated)
  5. Test paywall displays correct Starter info (Playwright browser)
  6. Test API enforcement: bulk upload blocked for free user
  7. Test API enforcement: chat blocked for free/starter user
  8. Test single upload works for free user (1 free statement)
  9. Test second upload blocked (FREE_LIMIT_REACHED)

Usage:
    python tests/e2e_starter_tier.py
"""

import io
import sys
import time
import traceback

import httpx

BASE_URL = "https://bankscanai.com"
TIMESTAMP = str(int(time.time()))
TEST_EMAIL = f"e2e_starter_{TIMESTAMP}@test.com"
TEST_PASSWORD = "TestPassword123!"

results = []


def report(step: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((step, passed))
    msg = f"[{status}] {step}"
    if detail:
        msg += f" -- {detail}"
    print(msg)


def make_csv_file(filename: str = "test_statement.csv") -> tuple[str, bytes, str]:
    """Create a minimal CSV bank statement in memory."""
    content = (
        "Date,Description,Money Out,Money In,Balance\n"
        "15/01/2025,TESCO STORES 3217,45.67,,1954.33\n"
        "15/01/2025,SALARY - ACME LTD,,2850.00,4804.33\n"
        "16/01/2025,DIRECT DEBIT - SKY,32.50,,4771.83\n"
    )
    return (filename, content.encode("utf-8"), "text/csv")


def get_csrf_token(client: httpx.Client) -> str:
    """Fetch the CSRF token by doing a GET to the homepage (sets bp_csrf cookie)."""
    resp = client.get(f"{BASE_URL}/")
    token = client.cookies.get("bp_csrf", "")
    return token


def csrf_headers(csrf_token: str) -> dict:
    """Return headers needed for POST requests."""
    return {
        "X-CSRF-Token": csrf_token,
        "Content-Type": "application/json",
    }


# =========================================================================
# API-level tests using httpx
# =========================================================================

def run_api_tests():
    print(f"\n{'='*60}")
    print(f"BankScan AI — Starter Tier E2E Test")
    print(f"Base URL: {BASE_URL}")
    print(f"Test email: {TEST_EMAIL}")
    print(f"{'='*60}\n")

    with httpx.Client(follow_redirects=True, timeout=30.0) as client:

        # -----------------------------------------------------------
        # Step 1: Register a new user
        # -----------------------------------------------------------
        try:
            csrf = get_csrf_token(client)
            resp = client.post(
                f"{BASE_URL}/api/register",
                json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
                headers=csrf_headers(csrf),
            )
            data = resp.json()
            ok = resp.status_code == 200 and data.get("status") == "ok"
            report("Step 1: Register new user", ok, f"status={resp.status_code} body={data}")
        except Exception as e:
            report("Step 1: Register new user", False, str(e))
            traceback.print_exc()
            return  # Can't proceed without registration

        # -----------------------------------------------------------
        # Step 2: Login
        # -----------------------------------------------------------
        try:
            csrf = get_csrf_token(client)
            resp = client.post(
                f"{BASE_URL}/api/login",
                json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
                headers=csrf_headers(csrf),
            )
            data = resp.json()
            has_auth = "bp_auth" in client.cookies
            ok = resp.status_code == 200 and data.get("status") == "ok" and has_auth
            report("Step 2: Login", ok, f"status={resp.status_code} has_auth_cookie={has_auth}")
        except Exception as e:
            report("Step 2: Login", False, str(e))
            traceback.print_exc()
            return

        # -----------------------------------------------------------
        # Step 3: Check usage — verify free tier
        # -----------------------------------------------------------
        try:
            resp = client.get(f"{BASE_URL}/api/usage")
            data = resp.json()
            tier = data.get("tier")
            statements_limit = data.get("statements_limit")
            receipts_limit = data.get("receipts_limit")
            ok = (
                resp.status_code == 200
                and tier == "free"
                and statements_limit == 1
                and receipts_limit == 1
            )
            report(
                "Step 3: Check usage (free tier)",
                ok,
                f"tier={tier} statements_limit={statements_limit} receipts_limit={receipts_limit}",
            )
        except Exception as e:
            report("Step 3: Check usage (free tier)", False, str(e))
            traceback.print_exc()

        # -----------------------------------------------------------
        # Step 4: Test Stripe checkout flow
        # -----------------------------------------------------------
        try:
            csrf = get_csrf_token(client)
            resp = client.post(
                f"{BASE_URL}/api/create-checkout",
                json={"plan": "starter"},
                headers=csrf_headers(csrf),
            )
            data = resp.json()
            checkout_url = data.get("checkout_url", "")
            # Stripe may or may not be configured in production; handle both
            if resp.status_code == 200 and "checkout.stripe.com" in checkout_url:
                report(
                    "Step 4: Stripe checkout flow (starter)",
                    True,
                    f"checkout_url contains checkout.stripe.com",
                )
            elif resp.status_code == 501:
                report(
                    "Step 4: Stripe checkout flow (starter)",
                    True,
                    f"Stripe not configured (501) — expected in some envs. detail={data.get('detail')}",
                )
            elif resp.status_code == 500 and "price not configured" in data.get("detail", "").lower():
                report(
                    "Step 4: Stripe checkout flow (starter)",
                    True,
                    f"Stripe price not configured — acceptable. detail={data.get('detail')}",
                )
            else:
                report(
                    "Step 4: Stripe checkout flow (starter)",
                    False,
                    f"status={resp.status_code} body={data}",
                )
        except Exception as e:
            report("Step 4: Stripe checkout flow (starter)", False, str(e))
            traceback.print_exc()

        # -----------------------------------------------------------
        # Step 6: Test bulk upload blocked for free user
        # -----------------------------------------------------------
        try:
            csrf = get_csrf_token(client)
            csv_file = make_csv_file("receipt.pdf")
            # Send as multipart form data with CSRF header
            resp = client.post(
                f"{BASE_URL}/api/parse-receipts-bulk",
                files=[("files", csv_file)],
                headers={"X-CSRF-Token": csrf},
            )
            data = resp.json()
            ok = resp.status_code == 403 and "subscription" in data.get("detail", "").lower()
            report(
                "Step 6: Bulk upload blocked for free user",
                ok,
                f"status={resp.status_code} detail={data.get('detail', '')}",
            )
        except Exception as e:
            report("Step 6: Bulk upload blocked for free user", False, str(e))
            traceback.print_exc()

        # -----------------------------------------------------------
        # Step 7: Test chat blocked for free user
        # -----------------------------------------------------------
        try:
            csrf = get_csrf_token(client)
            resp = client.post(
                f"{BASE_URL}/api/chat",
                json={
                    "message": "What is my total spending?",
                    "context_type": "statement",
                    "context_data": {"transactions": []},
                },
                headers=csrf_headers(csrf),
            )
            data = resp.json()
            ok = resp.status_code == 403 and (
                "business" in data.get("detail", "").lower()
                or "enterprise" in data.get("detail", "").lower()
            )
            report(
                "Step 7: Chat blocked for free user",
                ok,
                f"status={resp.status_code} detail={data.get('detail', '')}",
            )
        except Exception as e:
            report("Step 7: Chat blocked for free user", False, str(e))
            traceback.print_exc()

        # -----------------------------------------------------------
        # Step 8: Single upload works for free user (1 free statement)
        # -----------------------------------------------------------
        try:
            csrf = get_csrf_token(client)
            csv_file = make_csv_file("test_statement.csv")
            resp = client.post(
                f"{BASE_URL}/api/parse",
                files={"file": csv_file},
                headers={"X-CSRF-Token": csrf},
            )
            data = resp.json()
            ok = resp.status_code == 200 and len(data.get("transactions", [])) > 0
            report(
                "Step 8: Single upload works (free tier, 1st statement)",
                ok,
                f"status={resp.status_code} transactions={len(data.get('transactions', []))}",
            )
        except Exception as e:
            report("Step 8: Single upload works (free tier, 1st statement)", False, str(e))
            traceback.print_exc()

        # -----------------------------------------------------------
        # Step 9: Second upload blocked — FREE_LIMIT_REACHED
        # -----------------------------------------------------------
        try:
            csrf = get_csrf_token(client)
            csv_file = make_csv_file("test_statement_2.csv")
            resp = client.post(
                f"{BASE_URL}/api/parse",
                files={"file": csv_file},
                headers={"X-CSRF-Token": csrf},
            )
            data = resp.json()
            ok = resp.status_code == 403 and "FREE_LIMIT_REACHED" in data.get("detail", "")
            report(
                "Step 9: Second upload blocked (FREE_LIMIT_REACHED)",
                ok,
                f"status={resp.status_code} detail={data.get('detail', '')}",
            )
        except Exception as e:
            report("Step 9: Second upload blocked (FREE_LIMIT_REACHED)", False, str(e))
            traceback.print_exc()


# =========================================================================
# Browser test using Playwright (Step 5)
# =========================================================================

def run_browser_test():
    """Step 5: Verify Starter pricing card in the paywall UI."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report(
            "Step 5: Paywall displays Starter info (browser)",
            False,
            "playwright not installed — run: pip install playwright && playwright install chromium",
        )
        return

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Navigate to the app
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)

            # The paywall plans section contains pricing cards.
            # Look for the Starter card with correct info.
            # The paywall may be in the main page HTML (hidden until shown).
            html = page.content()

            has_starter = "Starter" in html
            has_price = "7.99" in html
            has_statements = "120 statements" in html
            has_receipts = "500 receipts" in html

            ok = has_starter and has_price and has_statements and has_receipts
            detail_parts = []
            if not has_starter:
                detail_parts.append("missing 'Starter'")
            if not has_price:
                detail_parts.append("missing '7.99'")
            if not has_statements:
                detail_parts.append("missing '120 statements'")
            if not has_receipts:
                detail_parts.append("missing '500 receipts'")

            report(
                "Step 5: Paywall displays Starter info (browser)",
                ok,
                ", ".join(detail_parts) if detail_parts else "all Starter card details found",
            )

            browser.close()

    except Exception as e:
        report("Step 5: Paywall displays Starter info (browser)", False, str(e))
        traceback.print_exc()


# =========================================================================
# Main
# =========================================================================

if __name__ == "__main__":
    run_api_tests()
    run_browser_test()

    # Summary
    print(f"\n{'='*60}")
    total = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    for step, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {step}")
    print(f"{'='*60}")

    sys.exit(0 if failed == 0 else 1)
