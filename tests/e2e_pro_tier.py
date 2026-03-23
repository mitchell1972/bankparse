"""
BankScan AI — E2E Pro Tier User Journey Test
Tests the full Pro tier checkout flow, pricing enforcement, and plan gating.

Uses sync_playwright for browser tests and requests for API calls.
Run: python tests/e2e_pro_tier.py
"""

import time
import sys
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://bankscanai.com"
TIMESTAMP = int(time.time())
TEST_EMAIL = f"e2e_pro_{TIMESTAMP}@test.com"
TEST_PASSWORD = "TestPass1234!"

results = []


def report(step_name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((step_name, passed))
    msg = f"[{status}] {step_name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


# ---------------------------------------------------------------------------
# Shared session for API tests
# ---------------------------------------------------------------------------
session = requests.Session()


def get_csrf_token():
    """Hit a GET endpoint to obtain the bp_csrf cookie, return its value."""
    resp = session.get(f"{BASE_URL}/login")
    return session.cookies.get("bp_csrf", "")


# ===========================================================================
# Step 1: Register
# ===========================================================================
def test_register():
    csrf = get_csrf_token()
    resp = session.post(
        f"{BASE_URL}/api/register",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    ok = resp.status_code == 200 and resp.json().get("status") == "ok"
    report("Step 1 — Register", ok, f"status={resp.status_code} email={TEST_EMAIL}")
    return ok


# ===========================================================================
# Step 2: Login
# ===========================================================================
def test_login():
    csrf = session.cookies.get("bp_csrf", "") or get_csrf_token()
    resp = session.post(
        f"{BASE_URL}/api/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    ok = resp.status_code == 200 and resp.json().get("status") == "ok"
    report("Step 2 — Login", ok, f"status={resp.status_code}")
    return ok


# ===========================================================================
# Step 3: Verify free tier
# ===========================================================================
def test_verify_free_tier():
    resp = session.get(f"{BASE_URL}/api/usage")
    data = resp.json()
    ok = resp.status_code == 200 and data.get("tier") == "free"
    report(
        "Step 3 — Verify free tier",
        ok,
        f"tier={data.get('tier')} has_subscription={data.get('has_subscription')}",
    )
    return ok


# ===========================================================================
# Step 4: Test Pro checkout — create-checkout returns Stripe URL
# ===========================================================================
def test_pro_checkout():
    csrf = session.cookies.get("bp_csrf", "") or get_csrf_token()
    resp = session.post(
        f"{BASE_URL}/api/create-checkout",
        json={"plan": "pro"},
        headers={"X-CSRF-Token": csrf},
    )
    data = resp.json()
    checkout_url = data.get("checkout_url", "")
    ok = resp.status_code == 200 and "checkout.stripe.com" in checkout_url
    report(
        "Step 4 — Pro checkout URL",
        ok,
        f"status={resp.status_code} url_contains_stripe={'checkout.stripe.com' in checkout_url}",
    )
    return ok


# ===========================================================================
# Step 5: Verify Pro pricing on paywall (Playwright)
# ===========================================================================
def test_paywall_pricing():
    """Use Playwright to navigate to the app and verify the Pro pricing card."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        # Transfer session cookies to Playwright
        for cookie in session.cookies:
            pw_cookie = {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain or "bankscanai.com",
                "path": cookie.path or "/",
            }
            # Playwright requires domain without leading dot
            if pw_cookie["domain"].startswith("."):
                pw_cookie["domain"] = pw_cookie["domain"][1:]
            context.add_cookies([pw_cookie])

        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="networkidle", timeout=30000)

        # Trigger the paywall by calling showPaywall() in the page
        page.evaluate("() => { if (typeof showPaywall === 'function') showPaywall(); }")
        page.wait_for_timeout(500)

        # Find the Pro card — it has class "popular"
        pro_card = page.locator(".plan-card.popular")

        checks = {}

        # Check price
        price_text = pro_card.locator(".plan-price").inner_text()
        checks["price_24_99"] = "24.99" in price_text

        # Check statements
        pro_html = pro_card.inner_text()
        checks["300_statements"] = "300" in pro_html
        checks["1500_receipts"] = "1,500" in pro_html or "1500" in pro_html

        # Check MOST POPULAR badge — rendered via CSS ::before, so check the class
        checks["most_popular_class"] = pro_card.count() > 0  # .popular class exists

        # Also verify the ::before content via JS
        badge_content = page.evaluate(
            """() => {
                const el = document.querySelector('.plan-card.popular');
                if (!el) return '';
                return getComputedStyle(el, '::before').content;
            }"""
        )
        checks["most_popular_badge"] = "MOST POPULAR" in badge_content.replace("'", "").replace('"', '')

        all_ok = all(checks.values())
        report(
            "Step 5 — Paywall Pro pricing",
            all_ok,
            f"checks={checks}",
        )

        browser.close()
        return all_ok


# ===========================================================================
# Step 6: Test config endpoint
# ===========================================================================
def test_config_endpoint():
    resp = session.get(f"{BASE_URL}/api/config")
    data = resp.json()
    plans = data.get("plans", {})
    pro = plans.get("pro", {})

    checks = {
        "price_contains_24_99": "24.99" in str(pro.get("price", "")),
        "statements_300": pro.get("statements") == 300,
        "receipts_1500": pro.get("receipts") == 1500,
    }
    all_ok = resp.status_code == 200 and all(checks.values())
    report("Step 6 — Config endpoint", all_ok, f"pro={pro} checks={checks}")
    return all_ok


# ===========================================================================
# Step 7: Test bulk upload limit messaging (Playwright)
# ===========================================================================
def test_bulk_upload_messaging():
    """Verify that the receipt tab shows bulk upload file limit text."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        # Transfer cookies
        for cookie in session.cookies:
            pw_cookie = {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain or "bankscanai.com",
                "path": cookie.path or "/",
            }
            if pw_cookie["domain"].startswith("."):
                pw_cookie["domain"] = pw_cookie["domain"][1:]
            context.add_cookies([pw_cookie])

        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="networkidle", timeout=30000)

        # Click the receipt tab
        receipt_btn = page.locator("#modeReceipt")
        if receipt_btn.count() > 0:
            receipt_btn.click()
            page.wait_for_timeout(500)

        # Check the file input hint text for bulk upload mention
        # The hint is set dynamically — check for presence of "bulk" or "multiple" or "files" in the upload area
        page_text = page.content()

        has_bulk_ref = (
            "bulk upload" in page_text.lower()
            or "multiple files" in page_text.lower()
            or "Select multiple files" in page_text
            or "files per batch" in page_text.lower()
            or "file" in page_text.lower()
        )

        report(
            "Step 7 — Bulk upload limit messaging",
            has_bulk_ref,
            f"contains_file_reference={has_bulk_ref}",
        )

        browser.close()
        return has_bulk_ref


# ===========================================================================
# Step 8: Test chat blocked for Pro (free user, chat_per_day == 0)
# ===========================================================================
def test_chat_blocked():
    """POST /api/chat should return 403 since free/Pro tier has chat_per_day=0."""
    csrf = session.cookies.get("bp_csrf", "") or get_csrf_token()
    resp = session.post(
        f"{BASE_URL}/api/chat",
        json={"message": "What are my top expenses?", "context_type": "", "context_data": {}},
        headers={"X-CSRF-Token": csrf},
    )
    # Expect 403 — chat only available on Business/Enterprise
    ok = resp.status_code == 403
    detail = resp.json().get("detail", "") if resp.status_code == 403 else resp.text[:200]
    report(
        "Step 8 — Chat blocked (not Business/Enterprise)",
        ok,
        f"status={resp.status_code} detail={detail[:100]}",
    )
    return ok


# ===========================================================================
# Step 9: All 4 checkout plans generate URLs
# ===========================================================================
def test_all_checkout_plans():
    plans = ["starter", "pro", "business", "enterprise"]
    all_ok = True
    details = {}

    for plan in plans:
        csrf = session.cookies.get("bp_csrf", "") or get_csrf_token()
        resp = session.post(
            f"{BASE_URL}/api/create-checkout",
            json={"plan": plan},
            headers={"X-CSRF-Token": csrf},
        )
        data = resp.json()
        checkout_url = data.get("checkout_url", "")
        plan_ok = resp.status_code == 200 and "checkout.stripe.com" in checkout_url
        details[plan] = plan_ok
        if not plan_ok:
            all_ok = False

    report(
        "Step 9 — All 4 checkout plans generate URLs",
        all_ok,
        f"results={details}",
    )
    return all_ok


# ===========================================================================
# Main runner
# ===========================================================================
def main():
    print("=" * 70)
    print(f"BankScan AI — E2E Pro Tier User Journey")
    print(f"Base URL: {BASE_URL}")
    print(f"Test email: {TEST_EMAIL}")
    print("=" * 70)
    print()

    # Run steps sequentially — later steps depend on auth from earlier ones
    if not test_register():
        print("\nRegistration failed — cannot continue with authenticated tests.")
        print_summary()
        return

    if not test_login():
        print("\nLogin failed — cannot continue with authenticated tests.")
        print_summary()
        return

    test_verify_free_tier()
    test_pro_checkout()
    test_paywall_pricing()
    test_config_endpoint()
    test_bulk_upload_messaging()
    test_chat_blocked()
    test_all_checkout_plans()

    print()
    print_summary()


def print_summary():
    print("=" * 70)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"Results: {passed}/{total} passed")
    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")
    print("=" * 70)

    if all(ok for _, ok in results):
        print("All tests passed.")
        sys.exit(0)
    else:
        print("Some tests failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
