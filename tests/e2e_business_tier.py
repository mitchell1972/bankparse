"""
BankScan AI — End-to-End Test: Business Tier User Journey
Tests registration, login, config verification, checkout, chat access,
paywall cards, health endpoint, robots.txt, sitemap.xml, and landing page SEO.

Uses sync_playwright for browser tests and requests for API calls.
"""

import time
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://bankscanai.com"
TIMESTAMP = str(int(time.time()))
TEST_EMAIL = f"e2e_biz_{TIMESTAMP}@test.com"
TEST_PASSWORD = "SecurePass123!"

results = []


def report(step: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    msg = f"[{status}] {step}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append((step, passed, detail))


def get_csrf_token(session: requests.Session) -> str:
    """Hit a GET endpoint to obtain the bp_csrf cookie, return its value."""
    resp = session.get(f"{BASE_URL}/login", allow_redirects=True)
    return session.cookies.get("bp_csrf", "")


# ==========================================================================
# Step 1: Register
# ==========================================================================
def test_register():
    session = requests.Session()
    csrf = get_csrf_token(session)
    resp = session.post(
        f"{BASE_URL}/api/register",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    if resp.status_code == 200:
        data = resp.json()
        ok = data.get("status") == "ok" and data.get("email") == TEST_EMAIL
        report("Step 1: Register", ok, f"status={resp.status_code}, email={data.get('email')}")
    else:
        report("Step 1: Register", False, f"status={resp.status_code}, body={resp.text[:200]}")
    return session


# ==========================================================================
# Step 2: Login
# ==========================================================================
def test_login(session: requests.Session):
    csrf = session.cookies.get("bp_csrf", "") or get_csrf_token(session)
    resp = session.post(
        f"{BASE_URL}/api/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    if resp.status_code == 200:
        data = resp.json()
        ok = data.get("status") == "ok" and data.get("email") == TEST_EMAIL
        has_auth = "bp_auth" in session.cookies
        report("Step 2: Login", ok and has_auth, f"status={resp.status_code}, has_cookie={has_auth}")
    else:
        report("Step 2: Login", False, f"status={resp.status_code}, body={resp.text[:200]}")
    return session


# ==========================================================================
# Step 3: Verify config — Business plan details
# ==========================================================================
def test_config(session: requests.Session):
    resp = session.get(f"{BASE_URL}/api/config")
    if resp.status_code != 200:
        report("Step 3: Verify config", False, f"status={resp.status_code}")
        return

    data = resp.json()
    plans = data.get("plans", {})
    biz = plans.get("business", {})

    checks = []
    checks.append(("price", biz.get("price") == "\u00a359.99/mo"))
    checks.append(("statements", biz.get("statements") == 840))
    checks.append(("receipts", biz.get("receipts") == 5000))
    checks.append(("clients", biz.get("clients") == "26-70"))

    all_ok = all(c[1] for c in checks)
    failed = [c[0] for c in checks if not c[1]]
    detail = f"business={biz}"
    if failed:
        detail += f" | FAILED: {failed}"
    report("Step 3: Verify config (Business plan)", all_ok, detail)


# ==========================================================================
# Step 4: Test Business checkout
# ==========================================================================
def test_checkout(session: requests.Session):
    csrf = session.cookies.get("bp_csrf", "")
    resp = session.post(
        f"{BASE_URL}/api/create-checkout",
        json={"plan": "business"},
        headers={"X-CSRF-Token": csrf},
    )
    if resp.status_code == 200:
        data = resp.json()
        has_url = "checkout_url" in data and data["checkout_url"]
        report("Step 4: Business checkout", has_url, f"has_checkout_url={has_url}")
    elif resp.status_code == 501:
        # Stripe may not be configured in test env — that's informational, not a code bug
        report("Step 4: Business checkout", False, f"Stripe not configured (501): {resp.text[:150]}")
    else:
        report("Step 4: Business checkout", False, f"status={resp.status_code}, body={resp.text[:200]}")


# ==========================================================================
# Step 5: Test chat blocked for free user
# ==========================================================================
def test_chat_blocked(session: requests.Session):
    csrf = session.cookies.get("bp_csrf", "")
    resp = session.post(
        f"{BASE_URL}/api/chat",
        json={
            "message": "What are my biggest expenses?",
            "context_type": "statement",
            "context_data": {"transactions": []},
        },
        headers={"X-CSRF-Token": csrf},
    )
    ok = resp.status_code == 403
    detail_text = resp.json().get("detail", "") if resp.status_code == 403 else resp.text[:200]
    report("Step 5: Chat blocked for free user", ok, f"status={resp.status_code}, detail={detail_text}")


# ==========================================================================
# Step 6 & 7: Verify paywall cards (Business + Enterprise) via Playwright
# ==========================================================================
def test_paywall_cards_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Navigate to the app (landing page has pricing section)
        page.goto(f"{BASE_URL}/landing", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        content = page.content()

        # Step 6: Business card
        biz_checks = []
        biz_checks.append(("26-70 clients", "26-70" in content))
        biz_checks.append(("59.99", "59.99" in content))
        biz_checks.append(("AI Chat (50/day)", "AI Chat (50/day)" in content))
        biz_checks.append(("Business heading", ">Business<" in content))

        all_biz = all(c[1] for c in biz_checks)
        failed_biz = [c[0] for c in biz_checks if not c[1]]
        report(
            "Step 6: Paywall Business card",
            all_biz,
            f"failed={failed_biz}" if failed_biz else "all checks passed",
        )

        # Step 7: Enterprise card
        ent_checks = []
        ent_checks.append(("71-1,000 clients", "71-1,000" in content or "71-1,000 clients" in content))
        ent_checks.append(("149", "149" in content))
        ent_checks.append(("Enterprise heading", ">Enterprise<" in content))
        ent_checks.append(("unlimited", "unlimited" in content.lower()))

        all_ent = all(c[1] for c in ent_checks)
        failed_ent = [c[0] for c in ent_checks if not c[1]]
        report(
            "Step 7: Enterprise card",
            all_ent,
            f"failed={failed_ent}" if failed_ent else "all checks passed",
        )

        browser.close()


# ==========================================================================
# Step 8: Health endpoint
# ==========================================================================
def test_health():
    resp = requests.get(f"{BASE_URL}/api/health")
    if resp.status_code == 200:
        data = resp.json()
        ok = data.get("status") == "ok" and data.get("stripe_configured") is True
        report("Step 8: Health endpoint", ok, f"data={data}")
    else:
        report("Step 8: Health endpoint", False, f"status={resp.status_code}")


# ==========================================================================
# Step 9: robots.txt
# ==========================================================================
def test_robots():
    resp = requests.get(f"{BASE_URL}/robots.txt")
    ok = resp.status_code == 200 and "Sitemap: https://bankscanai.com/sitemap.xml" in resp.text
    report("Step 9: robots.txt", ok, f"status={resp.status_code}, contains_sitemap={'Sitemap:' in resp.text}")


# ==========================================================================
# Step 10: sitemap.xml
# ==========================================================================
def test_sitemap():
    resp = requests.get(f"{BASE_URL}/sitemap.xml")
    text = resp.text
    has_landing = "https://bankscanai.com/landing" in text
    has_login = "https://bankscanai.com/login" in text
    ok = resp.status_code == 200 and has_landing and has_login
    report("Step 10: sitemap.xml", ok, f"status={resp.status_code}, landing={has_landing}, login={has_login}")


# ==========================================================================
# Step 11: Landing page SEO via Playwright
# ==========================================================================
def test_landing_seo():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{BASE_URL}/landing", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        seo_checks = []

        # Title contains "BankScan AI"
        title = page.title()
        seo_checks.append(("title contains BankScan AI", "BankScan AI" in title))

        # Meta description exists
        meta_desc = page.locator('meta[name="description"]')
        has_desc = meta_desc.count() > 0 and meta_desc.first.get_attribute("content") not in (None, "")
        seo_checks.append(("meta description exists", has_desc))

        # Open Graph tags present
        og_title = page.locator('meta[property="og:title"]')
        og_desc = page.locator('meta[property="og:description"]')
        og_type = page.locator('meta[property="og:type"]')
        has_og = og_title.count() > 0 and og_desc.count() > 0 and og_type.count() > 0
        seo_checks.append(("Open Graph tags present", has_og))

        # google-site-verification meta tag
        gsv = page.locator('meta[name="google-site-verification"]')
        has_gsv = gsv.count() > 0 and gsv.first.get_attribute("content") not in (None, "")
        seo_checks.append(("google-site-verification present", has_gsv))

        all_ok = all(c[1] for c in seo_checks)
        failed = [c[0] for c in seo_checks if not c[1]]
        detail = f"title='{title}'"
        if failed:
            detail += f" | FAILED: {failed}"
        report("Step 11: Landing page SEO", all_ok, detail)

        browser.close()


# ==========================================================================
# Main runner
# ==========================================================================
if __name__ == "__main__":
    print("=" * 70)
    print(f"BankScan AI — Business Tier E2E Test")
    print(f"BASE_URL: {BASE_URL}")
    print(f"Test email: {TEST_EMAIL}")
    print("=" * 70)
    print()

    # API tests (steps 1-5)
    session = test_register()
    test_login(session)
    test_config(session)
    test_checkout(session)
    test_chat_blocked(session)

    # Browser tests (steps 6-7)
    test_paywall_cards_browser()

    # Static endpoint tests (steps 8-10)
    test_health()
    test_robots()
    test_sitemap()

    # SEO browser test (step 11)
    test_landing_seo()

    # Summary
    print()
    print("=" * 70)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"RESULTS: {passed}/{total} passed")
    for step, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {step}")
    print("=" * 70)

    if passed < total:
        exit(1)
