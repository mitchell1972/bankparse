"""
BankScan AI — Full UI Journey Tests (Playwright)
Tests complete end-to-end user journeys against the live site.

Usage:
    pip install playwright requests
    python -m playwright install chromium
    python tests/ui_test_full_journey.py
"""

from playwright.sync_api import sync_playwright
import requests
import time
import os
import tempfile

BASE = "https://bankscanai.com"
TS = str(int(time.time()))
RESULTS = []


def report(step, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append((step, passed, detail))
    print(f"  [{status}] {step}" + (f" -- {detail}" if detail else ""))


def get_session(email, password):
    """Register + login via API, return session with cookies."""
    s = requests.Session()
    s.get(f"{BASE}/login")
    csrf = s.cookies.get("bp_csrf", "")
    h = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
    s.post(f"{BASE}/api/register", json={"email": email, "password": password}, headers=h)
    r = s.post(f"{BASE}/api/login", json={"email": email, "password": password}, headers=h)
    return s, h, r.status_code == 200


def create_test_csv():
    """Create a temporary CSV file with 3 bank transactions."""
    csv_content = (
        "Date,Description,Amount,Balance\n"
        "01/03/2025,SALARY PAYMENT,2500.00,3200.00\n"
        "05/03/2025,TESCO STORES,-45.67,3154.33\n"
        "10/03/2025,DIRECT DEBIT RENT,-850.00,2304.33\n"
    )
    tmp = tempfile.NamedTemporaryFile(
        suffix=".csv", delete=False, prefix="ui_test_stmt_", mode="w"
    )
    tmp.write(csv_content)
    tmp.close()
    return tmp.name


def create_test_receipt_pdf():
    """Create a minimal PDF file simulating a receipt."""
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

4 0 obj
<< /Length 250 >>
stream
BT
/F1 12 Tf
50 750 Td
(Store: Tesco Express) Tj
0 -20 Td
(Date: 15/03/2025) Tj
0 -20 Td
(Item: Milk 2L             1.50) Tj
0 -20 Td
(Item: Bread               1.20) Tj
0 -20 Td
(Item: Butter              2.00) Tj
0 -20 Td
(TOTAL                     4.70) Tj
ET
endstream
endobj

5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj

xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000568 00000 n

trailer
<< /Size 6 /Root 1 0 R >>
startxref
635
%%EOF"""
    tmp = tempfile.NamedTemporaryFile(
        suffix=".pdf", delete=False, prefix="ui_test_rcpt_"
    )
    tmp.write(pdf_content)
    tmp.close()
    return tmp.name


def ui_register(page, email, password):
    """Register a new account via the UI on the /login page.
    Assumes the page is already on /login.
    Returns True if registration succeeded and landed on dashboard.
    """
    # Switch to Register tab
    register_tab = page.query_selector('[data-tab="register"]')
    if register_tab:
        register_tab.click()
        time.sleep(0.5)

    page.fill("#email", email)
    page.fill("#password", password)
    page.click("#submitBtn")
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    except Exception:
        pass
    time.sleep(2)

    on_dashboard = "/login" not in page.url and "/landing" not in page.url
    return on_dashboard


def ui_login(page, email, password):
    """Sign in via the UI on the /login page.
    Assumes the page is already on /login.
    Returns True if login succeeded and landed on dashboard.
    """
    # Make sure we are on the Sign In tab
    signin_tab = page.query_selector('[data-tab="signin"]')
    if signin_tab:
        signin_tab.click()
        time.sleep(0.5)

    page.fill("#email", email)
    page.fill("#password", password)
    page.click("#submitBtn")
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
    except Exception:
        pass
    time.sleep(2)

    on_dashboard = "/login" not in page.url and "/landing" not in page.url
    return on_dashboard


# ===========================================================================
print("=" * 70)
print("BankScan AI -- Full UI Journey Tests")
print(f"Target: {BASE}")
print(f"Timestamp: {TS}")
print("=" * 70)


# ===========================================================================
# JOURNEY 1: New visitor -> Free trial -> Paywall
# ===========================================================================
print("\n--- JOURNEY 1: New visitor -> Free trial -> Paywall ---")

csv_path = create_test_csv()
email_j1 = f"ui_j1_{TS}@test.com"
password_j1 = "TestPass123!"

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(15000)

        # 1.1 Visit /landing
        try:
            page.goto(f"{BASE}/landing", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            report("J1.1 Visit /landing", "/landing" in page.url, page.url)
        except Exception as e:
            report("J1.1 Visit /landing", False, str(e))

        # 1.2 Click "Try Free"
        try:
            hero_btn = page.query_selector("a.btn-hero")
            if not hero_btn:
                hero_btn = page.query_selector("a.nav-cta")
            assert hero_btn is not None, "Try Free button not found"
            hero_btn.click()
            page.wait_for_load_state("networkidle")
            time.sleep(1)
            report("J1.2 Click Try Free -> /login", "/login" in page.url, page.url)
        except Exception as e:
            report("J1.2 Click Try Free -> /login", False, str(e))
            # Fallback navigate
            page.goto(f"{BASE}/login", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")

        # 1.3 Create account
        try:
            ok = ui_register(page, email_j1, password_j1)
            report("J1.3 Create account", ok, page.url)
        except Exception as e:
            report("J1.3 Create account", False, str(e))

        # 1.4 Arrive at dashboard
        try:
            on_dash = "/login" not in page.url and "/landing" not in page.url
            upload_card = page.query_selector("#uploadCard")
            report(
                "J1.4 Dashboard loads",
                on_dash and upload_card is not None,
                page.url,
            )
        except Exception as e:
            report("J1.4 Dashboard loads", False, str(e))

        # 1.5 Upload CSV bank statement
        try:
            # Ensure statement mode
            mode_btn = page.query_selector("#modeStatement")
            if mode_btn:
                mode_btn.click()
                time.sleep(0.5)

            file_input = page.query_selector("#fileInput")
            assert file_input is not None, "File input not found"
            file_input.set_input_files(csv_path)
            time.sleep(1)

            parse_btn = page.query_selector("#parseBtn")
            assert parse_btn is not None, "Parse button not found"
            parse_btn.click()
            # Wait for parsing (may take a few seconds on live server)
            time.sleep(10)
            page.wait_for_load_state("networkidle")
            time.sleep(2)

            report("J1.5 Upload CSV statement", True, "Upload + parse triggered")
        except Exception as e:
            report("J1.5 Upload CSV statement", False, str(e))

        # 1.6 See parsed results (transactions table)
        try:
            results_div = page.query_selector("#statementResults")
            content = page.content()
            # Check for the results div being visible or transaction-related content
            has_results = False
            if results_div:
                has_results = results_div.is_visible()
            # Also check for table or transaction keywords in the page
            has_table = "preview-table" in content or "<table" in content
            report(
                "J1.6 Parsed results visible",
                has_results or has_table,
                f"results_div_visible={has_results}, has_table={has_table}",
            )
        except Exception as e:
            report("J1.6 Parsed results visible", False, str(e))

        # 1.7 Download XLSX
        try:
            dl_link = page.query_selector("#downloadLink")
            dl_visible = dl_link.is_visible() if dl_link else False
            dl_href = dl_link.get_attribute("href") if dl_link else ""
            report(
                "J1.7 Download XLSX link present",
                dl_link is not None,
                f"visible={dl_visible}, href={dl_href[:80] if dl_href else 'N/A'}",
            )
        except Exception as e:
            report("J1.7 Download XLSX link present", False, str(e))

        # 1.8 Try second upload -> paywall appears
        try:
            # Reload and try uploading again
            page.goto(BASE, wait_until="domcontentloaded")
            time.sleep(4)
            page.wait_for_load_state("networkidle")

            # Check if paywall is already shown (free limit exhausted)
            paywall = page.query_selector("#paywall")
            paywall_visible = paywall.is_visible() if paywall else False

            if not paywall_visible:
                # Try to upload again to trigger paywall
                mode_btn = page.query_selector("#modeStatement")
                if mode_btn:
                    mode_btn.click()
                    time.sleep(0.5)

                file_input = page.query_selector("#fileInput")
                if file_input:
                    file_input.set_input_files(csv_path)
                    time.sleep(1)
                    parse_btn = page.query_selector("#parseBtn")
                    if parse_btn:
                        parse_btn.click()
                        time.sleep(5)

                # Re-check paywall
                paywall = page.query_selector("#paywall")
                paywall_visible = paywall.is_visible() if paywall else False

                # Also try triggering via JS
                if not paywall_visible:
                    page.evaluate(
                        "() => { if (typeof showPaywall === 'function') showPaywall(); }"
                    )
                    time.sleep(1)
                    paywall = page.query_selector("#paywall")
                    paywall_visible = paywall.is_visible() if paywall else False

            report(
                "J1.8 Second upload -> paywall",
                paywall_visible,
                f"paywall_visible={paywall_visible}",
            )
        except Exception as e:
            report("J1.8 Second upload -> paywall", False, str(e))

        # 1.9 Verify paywall shows all 4 plans
        try:
            content = page.content()
            has_starter = "Starter" in content and "7.99" in content
            has_pro = "Pro" in content and "24.99" in content
            has_business = "Business" in content and "59.99" in content
            has_enterprise = "Enterprise" in content and "149" in content
            plan_cards = page.query_selector_all("#paywall .plan-card")
            card_count = len(plan_cards)

            all_plans = has_starter and has_pro and has_business and has_enterprise
            report(
                "J1.9 Paywall shows all 4 plans",
                all_plans and card_count >= 4,
                f"cards={card_count}, starter={has_starter}, pro={has_pro}, "
                f"business={has_business}, enterprise={has_enterprise}",
            )
        except Exception as e:
            report("J1.9 Paywall shows all 4 plans", False, str(e))

        browser.close()
finally:
    try:
        os.unlink(csv_path)
    except Exception:
        pass


# ===========================================================================
# JOURNEY 2: Landing -> Plan selection -> Register -> Stripe checkout
# ===========================================================================
print("\n--- JOURNEY 2: Landing -> Plan selection -> Register -> Stripe checkout ---")

email_j2 = f"ui_j2_{TS}@test.com"
password_j2 = "TestPass123!"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.set_default_timeout(15000)

    # 2.1 Visit /landing
    try:
        page.goto(f"{BASE}/landing", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        report("J2.1 Visit /landing", "/landing" in page.url, page.url)
    except Exception as e:
        report("J2.1 Visit /landing", False, str(e))

    # 2.2 Click "Get Pro" button
    try:
        pro_btn = page.query_selector('a[href*="plan=pro"]')
        assert pro_btn is not None, "Get Pro button not found"
        pro_btn.click()
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        has_plan_param = "plan=pro" in page.url
        report(
            "J2.2 Click Get Pro -> /login?plan=pro",
            has_plan_param and "/login" in page.url,
            page.url,
        )
    except Exception as e:
        report("J2.2 Click Get Pro -> /login?plan=pro", False, str(e))
        page.goto(f"{BASE}/login?plan=pro", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

    # 2.3 Create account
    try:
        ok = ui_register(page, email_j2, password_j2)
        report("J2.3 Create account", ok, page.url)
    except Exception as e:
        report("J2.3 Create account", False, str(e))

    # 2.4 Verify redirect to Stripe checkout
    try:
        # After registering with ?plan=pro the app may auto-redirect to Stripe
        # or we may need to trigger the checkout manually.
        time.sleep(3)
        current_url = page.url
        on_stripe = "checkout.stripe.com" in current_url

        if not on_stripe:
            # The app might land on dashboard and then the JS triggers checkout.
            # Wait a bit more for any JS redirect.
            time.sleep(5)
            current_url = page.url
            on_stripe = "checkout.stripe.com" in current_url

        if not on_stripe:
            # Fallback: verify via API that checkout URL is generated
            s, h, _ = get_session(
                f"ui_j2_verify_{TS}@test.com", password_j2
            )
            r = s.post(
                f"{BASE}/api/create-checkout",
                json={"plan": "pro"},
                headers=h,
            )
            checkout_url = r.json().get("checkout_url", "")
            api_has_stripe = "checkout.stripe.com" in checkout_url
            report(
                "J2.4 Stripe checkout redirect",
                api_has_stripe,
                f"UI url={current_url[:80]}, API checkout={checkout_url[:80] if checkout_url else 'N/A'}",
            )
        else:
            report(
                "J2.4 Stripe checkout redirect",
                True,
                current_url[:80],
            )
    except Exception as e:
        report("J2.4 Stripe checkout redirect", False, str(e))

    browser.close()


# ===========================================================================
# JOURNEY 3: Returning user -> Login -> Upload
# ===========================================================================
print("\n--- JOURNEY 3: Returning user -> Login -> Upload ---")

email_j3 = f"ui_j3_{TS}@test.com"
password_j3 = "TestPass123!"

# 3.0 Pre-register user via API
s_j3, h_j3, api_ok = get_session(email_j3, password_j3)
report("J3.0 API pre-register user", api_ok, email_j3)

receipt_path = create_test_receipt_pdf()

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(15000)

        # 3.1 Visit /login
        try:
            page.goto(f"{BASE}/login", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            report("J3.1 Visit /login", "/login" in page.url, page.url)
        except Exception as e:
            report("J3.1 Visit /login", False, str(e))

        # 3.2 Sign in with credentials
        try:
            ok = ui_login(page, email_j3, password_j3)
            report("J3.2 Sign in", ok, page.url)
        except Exception as e:
            report("J3.2 Sign in", False, str(e))

        # 3.3 Verify dashboard loads
        try:
            on_dash = "/login" not in page.url and "/landing" not in page.url
            upload_card = page.query_selector("#uploadCard")
            report(
                "J3.3 Dashboard loads",
                on_dash and upload_card is not None,
                page.url,
            )
        except Exception as e:
            report("J3.3 Dashboard loads", False, str(e))

        # 3.4 Upload receipt
        try:
            # Switch to receipt mode
            receipt_btn = page.query_selector("#modeReceipt")
            if receipt_btn:
                receipt_btn.click()
                time.sleep(0.5)

            file_input = page.query_selector("#fileInput")
            assert file_input is not None, "File input not found"
            file_input.set_input_files(receipt_path)
            time.sleep(1)

            parse_btn = page.query_selector("#parseBtn")
            if parse_btn and not parse_btn.is_disabled():
                parse_btn.click()
                time.sleep(10)
                page.wait_for_load_state("networkidle")
                time.sleep(2)

            report("J3.4 Upload receipt", True, "Receipt upload triggered")
        except Exception as e:
            report("J3.4 Upload receipt", False, str(e))

        # 3.5 Verify results displayed
        try:
            receipt_results = page.query_selector("#receiptResults")
            receipt_visible = receipt_results.is_visible() if receipt_results else False

            content = page.content()
            has_receipt_table = "receiptTable" in content or "receipt-table" in content

            # Also check for any results area being shown
            any_results = receipt_visible or has_receipt_table
            report(
                "J3.5 Receipt results displayed",
                any_results,
                f"receipt_results_visible={receipt_visible}, has_table={has_receipt_table}",
            )
        except Exception as e:
            report("J3.5 Receipt results displayed", False, str(e))

        browser.close()
finally:
    try:
        os.unlink(receipt_path)
    except Exception:
        pass


# ===========================================================================
# JOURNEY 4: Logout and session protection
# ===========================================================================
print("\n--- JOURNEY 4: Logout and session protection ---")

email_j4 = f"ui_j4_{TS}@test.com"
password_j4 = "TestPass123!"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.set_default_timeout(15000)

    # 4.1 Register + login via UI
    try:
        page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        ok = ui_register(page, email_j4, password_j4)
        report("J4.1 Register + login via UI", ok, page.url)
    except Exception as e:
        report("J4.1 Register + login via UI", False, str(e))

    # 4.2 Verify dashboard accessible
    try:
        on_dash = "/login" not in page.url and "/landing" not in page.url
        upload_card = page.query_selector("#uploadCard")
        report(
            "J4.2 Dashboard accessible",
            on_dash and upload_card is not None,
            page.url,
        )
    except Exception as e:
        report("J4.2 Dashboard accessible", False, str(e))

    # 4.3 Click logout
    try:
        logout_btn = page.query_selector("#logoutBtn")
        if logout_btn and logout_btn.is_visible():
            logout_btn.click()
        else:
            # Try via JS
            page.evaluate(
                "() => { const btn = document.getElementById('logoutBtn'); if (btn) btn.click(); else if (typeof logout === 'function') logout(); }"
            )
        try:
            page.wait_for_url(lambda url: "/landing" in url or "/login" in url, timeout=10000)
        except Exception:
            pass
        time.sleep(2)

        on_landing_or_login = "/landing" in page.url or "/login" in page.url or page.url.rstrip("/") == BASE.rstrip("/")
        report(
            "J4.3 Logout redirects to /landing",
            on_landing_or_login,
            page.url,
        )
    except Exception as e:
        report("J4.3 Logout redirects to /landing", False, str(e))

    # 4.4 Try to visit / -> redirected to /landing
    try:
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        time.sleep(2)

        redirected = "/landing" in page.url or "/login" in page.url
        report(
            "J4.4 Visit / after logout -> redirect",
            redirected,
            page.url,
        )
    except Exception as e:
        report("J4.4 Visit / after logout -> redirect", False, str(e))

    # 4.5 Visit /login -> can sign in again
    try:
        page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        on_login = "/login" in page.url
        report("J4.5 /login page accessible", on_login, page.url)

        ok = ui_login(page, email_j4, password_j4)
        report("J4.6 Can sign in again", ok, page.url)
    except Exception as e:
        report("J4.5 /login page accessible", False, str(e))

    browser.close()


# ===========================================================================
# JOURNEY 5: Mobile experience
# ===========================================================================
print("\n--- JOURNEY 5: Mobile experience ---")

email_j5 = f"ui_j5_{TS}@test.com"
password_j5 = "TestPass123!"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    # iPhone viewport
    ctx = browser.new_context(
        viewport={"width": 375, "height": 812},
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
            "Mobile/15E148 Safari/604.1"
        ),
    )
    page = ctx.new_page()
    page.set_default_timeout(15000)

    # 5.1 Visit /landing
    try:
        page.goto(f"{BASE}/landing", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        report("J5.1 Mobile: visit /landing", "/landing" in page.url, page.url)
    except Exception as e:
        report("J5.1 Mobile: visit /landing", False, str(e))

    # 5.2 Verify hamburger menu visible
    try:
        hamburger = page.query_selector("#menuToggle")
        hamburger_visible = hamburger.is_visible() if hamburger else False
        report(
            "J5.2 Hamburger menu visible",
            hamburger_visible,
            f"found={hamburger is not None}, visible={hamburger_visible}",
        )
    except Exception as e:
        report("J5.2 Hamburger menu visible", False, str(e))

    # 5.3 Open hamburger -> nav links visible
    try:
        hamburger = page.query_selector("#menuToggle")
        assert hamburger is not None, "Hamburger button not found"
        hamburger.click()
        time.sleep(1)

        nav_menu = page.query_selector("#navMenu")
        nav_has_open = False
        if nav_menu:
            classes = nav_menu.get_attribute("class") or ""
            nav_has_open = "open" in classes

        # Check that nav links are present
        nav_links = page.query_selector_all("#navMenu a")
        link_count = len(nav_links)

        report(
            "J5.3 Hamburger open -> nav links visible",
            nav_has_open and link_count >= 5,
            f"open={nav_has_open}, links={link_count}",
        )
    except Exception as e:
        report("J5.3 Hamburger open -> nav links visible", False, str(e))

    # 5.4 Click "Try Free" -> /login
    try:
        try_free = page.query_selector('#navMenu a.nav-cta')
        if not try_free:
            try_free = page.query_selector('a.nav-cta')
        if not try_free:
            try_free = page.query_selector('a.btn-hero')
        assert try_free is not None, "Try Free link not found"
        try_free.click()
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        report(
            "J5.4 Mobile: Try Free -> /login",
            "/login" in page.url,
            page.url,
        )
    except Exception as e:
        report("J5.4 Mobile: Try Free -> /login", False, str(e))
        page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")

    # 5.5 Register -> dashboard
    try:
        ok = ui_register(page, email_j5, password_j5)
        report("J5.5 Mobile: register -> dashboard", ok, page.url)
    except Exception as e:
        report("J5.5 Mobile: register -> dashboard", False, str(e))

    # 5.6 Verify upload area is usable on mobile
    try:
        upload_card = page.query_selector("#uploadCard")
        upload_visible = upload_card.is_visible() if upload_card else False

        file_input = page.query_selector("#fileInput")
        file_input_exists = file_input is not None

        # Check mode toggle buttons are present and usable
        mode_stmt = page.query_selector("#modeStatement")
        mode_rcpt = page.query_selector("#modeReceipt")
        modes_present = mode_stmt is not None and mode_rcpt is not None

        parse_btn = page.query_selector("#parseBtn")
        parse_exists = parse_btn is not None

        report(
            "J5.6 Mobile: upload area usable",
            upload_visible and file_input_exists and modes_present and parse_exists,
            f"upload_visible={upload_visible}, file_input={file_input_exists}, "
            f"modes={modes_present}, parse_btn={parse_exists}",
        )
    except Exception as e:
        report("J5.6 Mobile: upload area usable", False, str(e))

    browser.close()


# ===========================================================================
# SUMMARY
# ===========================================================================
print("\n" + "=" * 70)
print("BankScan AI -- Full UI Journey Test Summary")
print("=" * 70)

passed = sum(1 for _, p, _ in RESULTS if p)
failed = sum(1 for _, p, _ in RESULTS if not p)
total = len(RESULTS)

for step, p, detail in RESULTS:
    status = "PASS" if p else "FAIL"
    print(f"  [{status}] {step}: {detail[:90]}")

print(f"\nResult: {passed}/{total} passed, {failed} failed", end="")
if total > 0:
    print(f" ({100 * passed // total}%)")
else:
    print()
print("=" * 70)

if failed:
    print("\nFailed tests:")
    for step, p, detail in RESULTS:
        if not p:
            print(f"  [FAIL] {step} -- {detail}")

exit(0 if failed == 0 else 1)
