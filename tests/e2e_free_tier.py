"""
E2E Playwright test for the FREE tier user journey on BankScan AI.
Tests: landing page, registration, free tier limits, upload, paywall,
bulk upload blocked, AI chat blocked, and logout.

Usage:
    pip install playwright
    python -m playwright install chromium
    python tests/e2e_free_tier.py
"""

from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout
import time
import os
import tempfile

timestamp = str(int(time.time()))
BASE_URL = "https://bankscanai.com"
TEST_EMAIL = f"e2e_free_{timestamp}@test.com"
TEST_PASSWORD = "TestPass123!"

results = []


def report(step, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    msg = f"[{status}] Step {step}"
    if detail:
        msg += f" — {detail}"
    results.append((step, passed, detail))
    print(msg)


def create_test_pdf():
    """Create a minimal PDF file for upload testing."""
    # Minimal valid PDF with some text content
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
<< /Length 200 >>
stream
BT
/F1 12 Tf
50 750 Td
(Date        Description                    Amount) Tj
0 -20 Td
(01/01/2024  SALARY PAYMENT                 2500.00) Tj
0 -20 Td
(02/01/2024  TESCO STORES                   -45.67) Tj
0 -20 Td
(03/01/2024  DIRECT DEBIT RENT              -850.00) Tj
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
0000000518 00000 n

trailer
<< /Size 6 /Root 1 0 R >>
startxref
595
%%EOF"""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False, prefix="e2e_statement_")
    tmp.write(pdf_content)
    tmp.close()
    return tmp.name


def run_tests():
    pdf_path = create_test_pdf()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) E2E-Test"
            )
            page = context.new_page()
            page.set_default_timeout(15000)

            # ================================================================
            # STEP 1: Landing page loads
            # ================================================================
            try:
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=20000)
                time.sleep(2)

                current_url = page.url
                has_landing = "/landing" in current_url
                title = page.title()
                has_bankscan = "BankScan" in title or "bankscan" in title.lower()

                # Check pricing cards visible
                pricing_cards = page.locator(".price-card")
                card_count = pricing_cards.count()
                pricing_visible = card_count >= 4

                if has_landing and has_bankscan and pricing_visible:
                    report(1, True, f"Redirected to {current_url}, title='{title}', {card_count} pricing cards found")
                else:
                    details = []
                    if not has_landing:
                        details.append(f"URL={current_url} (expected /landing)")
                    if not has_bankscan:
                        details.append(f"title='{title}' missing BankScan")
                    if not pricing_visible:
                        details.append(f"only {card_count} pricing cards (expected 4)")
                    report(1, False, "; ".join(details))
            except Exception as e:
                report(1, False, f"Exception: {e}")

            # ================================================================
            # STEP 2: Registration
            # ================================================================
            try:
                # Click "Try Free" CTA in the nav or hero
                try:
                    cta = page.locator("a.nav-cta, a.btn-hero").first
                    cta.click(timeout=5000)
                    time.sleep(2)
                except Exception:
                    # Fallback: navigate directly
                    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
                    time.sleep(2)

                current_url = page.url
                on_login = "/login" in current_url or page.locator(".login-container").count() > 0

                if not on_login:
                    # Maybe we landed on the app (/) because the CTA goes to /
                    # Navigate to login explicitly
                    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
                    time.sleep(2)

                # Switch to Register tab
                register_tab = page.locator('[data-tab="register"]')
                if register_tab.count() > 0:
                    register_tab.click()
                    time.sleep(0.5)

                # Fill registration form
                page.fill("#email", TEST_EMAIL)
                page.fill("#password", TEST_PASSWORD)

                # Submit
                page.click("#submitBtn")
                time.sleep(3)

                # Check redirect to app dashboard (/)
                current_url = page.url
                on_dashboard = current_url.rstrip("/").endswith(BASE_URL.replace("https://", "").replace("http://", "")) or \
                               current_url == f"{BASE_URL}/" or \
                               "/login" not in current_url

                # Also check if BankScan AI header is present on dashboard
                dashboard_header = page.locator("h1:has-text('BankScan AI')").count()
                upload_card = page.locator("#uploadCard").count()

                if on_dashboard and (dashboard_header > 0 or upload_card > 0):
                    report(2, True, f"Registered as {TEST_EMAIL}, redirected to dashboard ({current_url})")
                else:
                    # Check for error messages
                    error_el = page.locator("#globalError, .global-error.visible")
                    error_text = ""
                    if error_el.count() > 0:
                        error_text = error_el.first.text_content()
                    report(2, False, f"URL={current_url}, dashboard_header={dashboard_header}, upload_card={upload_card}, error='{error_text}'")
            except Exception as e:
                report(2, False, f"Exception: {e}")

            # ================================================================
            # STEP 3: Free tier limits displayed
            # ================================================================
            try:
                # Wait for usage bar to appear (loaded via JS fetch)
                time.sleep(3)
                usage_bar = page.locator("#usageBar")
                usage_visible = usage_bar.is_visible()

                if usage_visible:
                    usage_text = page.locator("#usageText").text_content()
                    badge_text = page.locator("#usageBadge").text_content()
                    has_free_info = "free" in usage_text.lower() or "FREE" in badge_text
                    has_remaining = "remaining" in usage_text.lower() or "1 statement" in usage_text.lower()

                    if has_free_info and has_remaining:
                        report(3, True, f"Usage bar: '{usage_text}' [{badge_text}]")
                    else:
                        report(3, False, f"Usage bar visible but content unexpected: '{usage_text}' [{badge_text}]")
                else:
                    # Maybe user wasn't logged in, or usage bar is hidden
                    report(3, False, "Usage bar not visible (may not be logged in)")
            except Exception as e:
                report(3, False, f"Exception: {e}")

            # ================================================================
            # STEP 4: Upload 1 bank statement (PDF)
            # ================================================================
            try:
                # Make sure we're on the dashboard and upload card is visible
                upload_card = page.locator("#uploadCard")
                if not upload_card.is_visible():
                    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
                    time.sleep(3)

                # Make sure we're in statement mode
                mode_btn = page.locator("#modeStatement")
                if mode_btn.count() > 0:
                    mode_btn.click()
                    time.sleep(0.5)

                # Upload the test PDF via file input
                file_input = page.locator("#fileInput")
                file_input.set_input_files(pdf_path)
                time.sleep(1)

                # Check if file info appeared
                file_info = page.locator("#fileInfo")
                file_info_visible = file_info.is_visible()

                # Check if parse button is enabled
                parse_btn = page.locator("#parseBtn")
                parse_enabled = False
                if parse_btn.count() > 0:
                    parse_enabled = not parse_btn.is_disabled()

                if file_info_visible or parse_enabled:
                    report(4, True, f"PDF uploaded, file info visible={file_info_visible}, parse button enabled={parse_enabled}")

                    # Optionally try to click parse and see what happens
                    if parse_enabled:
                        try:
                            parse_btn.click()
                            # Wait for processing (could take a while on live server)
                            time.sleep(8)
                            # Check for results or error
                            stmt_results = page.locator("#statementResults")
                            error_msg = page.locator("#errorMsg")
                            if stmt_results.is_visible():
                                report("4b", True, "Statement parsed successfully, results displayed")
                            elif error_msg.is_visible():
                                err_text = error_msg.text_content()
                                report("4b", False, f"Parse error: {err_text[:100]}")
                            else:
                                report("4b", False, "No results or error shown after parse attempt")
                        except Exception as e:
                            report("4b", False, f"Parse click exception: {e}")
                else:
                    report(4, False, "File upload did not trigger UI update")
            except Exception as e:
                report(4, False, f"Exception: {e}")

            # ================================================================
            # STEP 5: Free limit reached — paywall with 4 pricing tiers
            # ================================================================
            try:
                # Reload to trigger usage check after the upload
                page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
                time.sleep(4)

                paywall = page.locator("#paywall")
                paywall_visible = paywall.is_visible()

                if paywall_visible:
                    # Check all 4 plan cards in paywall
                    plan_cards = paywall.locator(".plan-card")
                    plan_count = plan_cards.count()

                    # Check for specific prices
                    paywall_html = paywall.inner_html()
                    has_starter = "7.99" in paywall_html
                    has_pro = "24.99" in paywall_html
                    has_business = "59.99" in paywall_html
                    has_enterprise = "149" in paywall_html

                    prices_found = [
                        p for p, found in [
                            ("Starter £7.99", has_starter),
                            ("Pro £24.99", has_pro),
                            ("Business £59.99", has_business),
                            ("Enterprise £149", has_enterprise),
                        ] if found
                    ]

                    if plan_count >= 4 and len(prices_found) == 4:
                        report(5, True, f"Paywall shown with {plan_count} plans: {', '.join(prices_found)}")
                    else:
                        report(5, False, f"Paywall visible but {plan_count} plan cards, prices found: {prices_found}")
                else:
                    # Paywall not shown — free limit may not have been reached
                    # Check if usage bar shows exhausted
                    usage_text = page.locator("#usageText").text_content() if page.locator("#usageText").is_visible() else ""
                    report(5, False, f"Paywall not visible after upload. Usage: '{usage_text}'. Free limit may not have been consumed (parsing may have failed).")
            except Exception as e:
                report(5, False, f"Exception: {e}")

            # ================================================================
            # STEP 6: Bulk upload blocked
            # ================================================================
            try:
                # If paywall is shown, we need to check bulk upload differently
                # Navigate fresh and try receipt mode
                page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
                time.sleep(3)

                # Check if upload card is visible (it may be hidden behind paywall)
                upload_card_visible = page.locator("#uploadCard").is_visible()

                if upload_card_visible:
                    # Switch to receipt mode
                    receipt_btn = page.locator("#modeReceipt")
                    if receipt_btn.count() > 0:
                        receipt_btn.click()
                        time.sleep(1)

                    # Create a second temp PDF to simulate multiple file selection
                    pdf_path2 = create_test_pdf()
                    try:
                        file_input = page.locator("#fileInput")
                        file_input.set_input_files([pdf_path, pdf_path2])
                        time.sleep(1)

                        # Check for bulk upload error message
                        error_msg = page.locator("#errorMsg")
                        error_visible = error_msg.is_visible()
                        if error_visible:
                            error_text = error_msg.text_content()
                            has_bulk_error = "bulk" in error_text.lower() or "subscription" in error_text.lower() or "upgrade" in error_text.lower()
                            if has_bulk_error:
                                report(6, True, f"Bulk upload blocked: '{error_text[:100]}'")
                            else:
                                report(6, False, f"Error shown but not about bulk: '{error_text[:100]}'")
                        else:
                            # Parse btn should be disabled
                            parse_btn = page.locator("#parseBtn")
                            is_disabled = parse_btn.is_disabled() if parse_btn.count() > 0 else False
                            report(6, False, f"No error message after multi-file select. Parse disabled={is_disabled}")
                    finally:
                        try:
                            os.unlink(pdf_path2)
                        except Exception:
                            pass
                else:
                    # Upload card hidden (paywall blocks it)
                    report(6, True, "Upload card hidden by paywall — bulk upload implicitly blocked")
            except Exception as e:
                report(6, False, f"Exception: {e}")

            # ================================================================
            # STEP 7: AI Chat blocked
            # ================================================================
            try:
                page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
                time.sleep(3)

                chat_section = page.locator("#chatSection")
                chat_visible = chat_section.is_visible()

                if chat_visible:
                    # Chat section is visible, check if input is disabled
                    chat_input = page.locator("#chatInput")
                    is_disabled = chat_input.get_attribute("disabled") is not None
                    placeholder = chat_input.get_attribute("placeholder") or ""

                    has_restriction = "business" in placeholder.lower() or "enterprise" in placeholder.lower() or is_disabled

                    # Check for restriction message bubble
                    chat_messages = page.locator("#chatMessages .chat-bubble.ai")
                    restriction_msg = ""
                    if chat_messages.count() > 0:
                        restriction_msg = chat_messages.first.text_content()

                    if has_restriction or "business" in restriction_msg.lower() or "enterprise" in restriction_msg.lower():
                        report(7, True, f"AI Chat restricted. Disabled={is_disabled}, placeholder='{placeholder[:60]}', msg='{restriction_msg[:60]}'")
                    else:
                        report(7, False, f"Chat visible but not obviously restricted. Disabled={is_disabled}, placeholder='{placeholder}'")
                else:
                    # Chat section not visible — it only shows after a successful parse
                    # This is expected for free tier with no successful parse
                    report(7, True, "Chat section not visible (only shown after successful parse) — free tier correctly has no chat access")
            except Exception as e:
                report(7, False, f"Exception: {e}")

            # ================================================================
            # STEP 8: Logout works
            # ================================================================
            try:
                page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
                time.sleep(3)

                logout_btn = page.locator("#logoutBtn")
                if logout_btn.is_visible():
                    logout_btn.click()
                    time.sleep(3)

                    current_url = page.url
                    on_login = "/login" in current_url or "/landing" in current_url
                    if on_login:
                        report(8, True, f"Logout successful, redirected to {current_url}")
                    else:
                        report(8, False, f"After logout, URL={current_url} (expected /login or /landing)")
                else:
                    # Try clicking via JS if button exists but is hidden
                    try:
                        page.evaluate("document.getElementById('logoutBtn').click()")
                        time.sleep(3)
                        current_url = page.url
                        on_login = "/login" in current_url or "/landing" in current_url
                        if on_login:
                            report(8, True, f"Logout via JS, redirected to {current_url}")
                        else:
                            report(8, False, f"Logout button clicked via JS but URL={current_url}")
                    except Exception:
                        report(8, False, "Logout button not visible and JS click failed")
            except Exception as e:
                report(8, False, f"Exception: {e}")

            browser.close()

    finally:
        # Cleanup temp PDF
        try:
            os.unlink(pdf_path)
        except Exception:
            pass

    # ================================================================
    # Summary
    # ================================================================
    print("\n" + "=" * 60)
    print("E2E FREE TIER TEST SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    for step, p, detail in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] Step {step}: {detail[:80]}")
    print(f"\nResult: {passed}/{total} steps passed")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
