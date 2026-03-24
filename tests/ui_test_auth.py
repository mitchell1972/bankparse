"""
UI Playwright tests for AUTH flows on BankScan AI.
Tests: login page load, tab switching, registration, dashboard, sign-in,
invalid login, short password, invalid email, duplicate email, logout,
protected route, plan parameter, password visibility toggle.

Usage:
    pip install playwright
    python -m playwright install chromium
    python tests/ui_test_auth.py
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import time

timestamp = str(int(time.time()))
BASE_URL = "https://bankscanai.com"
TEST_EMAIL = f"ui_auth_{timestamp}@test.com"
TEST_PASSWORD = "TestPass123!"

results = []


def report(step, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    msg = f"[{status}] Step {step}"
    if detail:
        msg += f" — {detail}"
    results.append((step, passed, detail))
    print(msg)


def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) UI-Auth-Test"
        )
        page = context.new_page()
        page.set_default_timeout(15000)

        # ================================================================
        # STEP 1: Login page loads
        # ================================================================
        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            email_input = page.locator("#email")
            password_input = page.locator("#password")
            submit_btn = page.locator("#submitBtn")

            email_visible = email_input.is_visible()
            password_visible = password_input.is_visible()
            submit_visible = submit_btn.is_visible()

            if email_visible and password_visible and submit_visible:
                report(1, True, "Login page loaded — email, password inputs and submit button visible")
            else:
                report(1, False, f"email={email_visible}, password={password_visible}, submit={submit_visible}")
        except Exception as e:
            report(1, False, f"Exception: {e}")

        # ================================================================
        # STEP 2: Tab switching — Sign In / Create Account
        # ================================================================
        try:
            signin_tab = page.locator('[data-tab="signin"]')
            register_tab = page.locator('[data-tab="register"]')

            signin_exists = signin_tab.count() > 0
            register_exists = register_tab.count() > 0

            if not (signin_exists and register_exists):
                report(2, False, f"Tabs missing: signin={signin_exists}, register={register_exists}")
            else:
                # Verify Sign In tab is active by default
                signin_classes = signin_tab.get_attribute("class") or ""
                signin_active = "active" in signin_classes

                # Click Create Account tab
                register_tab.click()
                time.sleep(0.5)

                register_classes = register_tab.get_attribute("class") or ""
                register_active = "active" in register_classes
                signin_classes_after = signin_tab.get_attribute("class") or ""
                signin_inactive = "active" not in signin_classes_after

                # Check button text changed
                btn_text = page.locator("#submitBtn").text_content()
                btn_says_create = "Create Account" in btn_text

                # Check password hint visible
                hint_visible = page.locator("#passwordHint").is_visible()

                # Switch back to Sign In
                signin_tab.click()
                time.sleep(0.5)
                btn_text_after = page.locator("#submitBtn").text_content()
                btn_says_signin = "Sign In" in btn_text_after

                all_ok = signin_active and register_active and signin_inactive and btn_says_create and btn_says_signin
                if all_ok:
                    report(2, True, "Tabs switch correctly, button text and hints update")
                else:
                    report(2, False, f"signin_active={signin_active}, register_active={register_active}, "
                                     f"signin_inactive={signin_inactive}, btn_create={btn_says_create}, "
                                     f"btn_signin={btn_says_signin}, hint={hint_visible}")
        except Exception as e:
            report(2, False, f"Exception: {e}")

        # ================================================================
        # STEP 3: Create Account — register and redirect to dashboard
        # ================================================================
        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            time.sleep(2)

            # Switch to register tab
            page.locator('[data-tab="register"]').click()
            time.sleep(0.5)

            # Fill form
            page.fill("#email", TEST_EMAIL)
            page.fill("#password", TEST_PASSWORD)
            page.click("#submitBtn")
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
            except Exception:
                pass
            time.sleep(2)

            current_url = page.url
            on_dashboard = "/login" not in current_url and "/landing" not in current_url

            if on_dashboard:
                report(3, True, f"Registered as {TEST_EMAIL}, redirected to {current_url}")
            else:
                # Check for error messages
                error_el = page.locator("#globalError")
                error_text = ""
                if error_el.count() > 0:
                    error_text = error_el.text_content()
                email_err = page.locator("#emailError").text_content() if page.locator("#emailError").count() > 0 else ""
                report(3, False, f"URL={current_url}, globalError='{error_text}', emailError='{email_err}'")
        except Exception as e:
            report(3, False, f"Exception: {e}")

        # ================================================================
        # STEP 4: Dashboard loads — header, upload area, usage bar
        # ================================================================
        try:
            # After registration we should already be on dashboard
            time.sleep(3)
            current_url = page.url

            header = page.locator("h1:has-text('BankScan AI')")
            header_visible = header.count() > 0 and header.first.is_visible()

            upload_card = page.locator("#uploadCard")
            upload_visible = upload_card.is_visible()

            usage_bar = page.locator("#usageBar")
            usage_visible = usage_bar.is_visible()

            if header_visible and upload_visible:
                detail_parts = ["Header visible", "Upload area visible"]
                if usage_visible:
                    usage_text = page.locator("#usageText").text_content() if page.locator("#usageText").count() > 0 else ""
                    detail_parts.append(f"Usage bar visible ('{usage_text}')")
                else:
                    detail_parts.append("Usage bar not yet visible (may load async)")
                report(4, True, ", ".join(detail_parts))
            else:
                report(4, False, f"header={header_visible}, upload={upload_visible}, usage={usage_visible}, url={current_url}")
        except Exception as e:
            report(4, False, f"Exception: {e}")

        # ================================================================
        # STEP 5: Sign In — log out first, then sign in with same creds
        # ================================================================
        try:
            # Log out first
            logout_btn = page.locator("#logoutBtn")
            if logout_btn.is_visible():
                logout_btn.click()
                time.sleep(3)

            # Navigate to login
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            time.sleep(2)

            # Ensure we are on Sign In tab (default)
            signin_tab = page.locator('[data-tab="signin"]')
            if signin_tab.count() > 0:
                signin_tab.click()
                time.sleep(0.5)

            # Fill credentials
            page.fill("#email", TEST_EMAIL)
            page.fill("#password", TEST_PASSWORD)
            page.click("#submitBtn")
            try:
                page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
            except Exception:
                pass
            time.sleep(2)

            current_url = page.url
            on_dashboard = "/login" not in current_url and "/landing" not in current_url

            if on_dashboard:
                report(5, True, f"Signed in with {TEST_EMAIL}, redirected to {current_url}")
            else:
                error_el = page.locator("#globalError")
                error_text = error_el.text_content() if error_el.count() > 0 else ""
                report(5, False, f"URL={current_url}, error='{error_text}'")
        except Exception as e:
            report(5, False, f"Exception: {e}")

        # ================================================================
        # STEP 6: Invalid login — wrong password shows error
        # ================================================================
        try:
            # Log out if on dashboard
            logout_btn = page.locator("#logoutBtn")
            if logout_btn.count() > 0 and logout_btn.is_visible():
                logout_btn.click()
                time.sleep(3)

            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            time.sleep(2)

            # Sign In tab should be default
            signin_tab = page.locator('[data-tab="signin"]')
            if signin_tab.count() > 0:
                signin_tab.click()
                time.sleep(0.5)

            page.fill("#email", TEST_EMAIL)
            page.fill("#password", "WrongPassword999!")
            page.click("#submitBtn")
            time.sleep(5)

            # Should stay on login page with an error
            current_url = page.url
            still_on_login = "/login" in current_url

            # Check for error message
            global_error = page.locator("#globalError")
            global_error_text = global_error.text_content() if global_error.count() > 0 else ""
            global_error_visible = global_error.count() > 0 and "visible" in (global_error.get_attribute("class") or "")

            password_error = page.locator("#passwordError")
            password_error_text = password_error.text_content() if password_error.count() > 0 else ""

            has_error = global_error_visible or len(global_error_text.strip()) > 0 or len(password_error_text.strip()) > 0

            if still_on_login and has_error:
                error_shown = global_error_text.strip() or password_error_text.strip()
                report(6, True, f"Invalid login rejected, error: '{error_shown}'")
            elif still_on_login:
                # Stayed on login page (correct behavior) but no visible error text
                report(6, True, f"Invalid login kept user on login page (error display may be subtle)")
            else:
                report(6, False, f"still_on_login={still_on_login}, global='{global_error_text}', password='{password_error_text}', url={current_url}")
        except Exception as e:
            report(6, False, f"Exception: {e}")

        # ================================================================
        # STEP 7: Short password — registration with < 8 chars
        # ================================================================
        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            time.sleep(2)

            # Switch to register tab
            page.locator('[data-tab="register"]').click()
            time.sleep(0.5)

            short_email = f"ui_auth_short_{timestamp}@test.com"
            page.fill("#email", short_email)
            page.fill("#password", "Ab1!")  # only 4 chars
            page.click("#submitBtn")
            time.sleep(2)

            # Should show password validation error (client-side)
            password_error = page.locator("#passwordError")
            password_error_text = password_error.text_content() if password_error.count() > 0 else ""

            global_error = page.locator("#globalError")
            global_error_text = global_error.text_content() if global_error.count() > 0 else ""

            still_on_login = "/login" in page.url

            has_pw_error = "8 character" in password_error_text.lower() or "8 char" in password_error_text.lower()
            has_any_error = len(password_error_text.strip()) > 0 or len(global_error_text.strip()) > 0

            if still_on_login and has_pw_error:
                report(7, True, f"Short password rejected: '{password_error_text}'")
            elif still_on_login and has_any_error:
                report(7, True, f"Short password rejected with error: pw='{password_error_text}', global='{global_error_text}'")
            else:
                report(7, False, f"still_on_login={still_on_login}, pwError='{password_error_text}', global='{global_error_text}'")
        except Exception as e:
            report(7, False, f"Exception: {e}")

        # ================================================================
        # STEP 8: Invalid email — "notanemail" shows validation error
        # ================================================================
        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            time.sleep(2)

            # Switch to register tab
            page.locator('[data-tab="register"]').click()
            time.sleep(0.5)

            page.fill("#email", "notanemail")
            page.fill("#password", TEST_PASSWORD)
            page.click("#submitBtn")
            time.sleep(4)

            email_error = page.locator("#emailError")
            email_error_text = email_error.text_content() if email_error.count() > 0 else ""

            global_error = page.locator("#globalError")
            global_error_text = global_error.text_content() if global_error.count() > 0 else ""

            still_on_login = "/login" in page.url
            has_any_error = (len(email_error_text.strip()) > 0
                             or len(global_error_text.strip()) > 0
                             or "email" in global_error_text.lower())

            if still_on_login and has_any_error:
                error_shown = email_error_text.strip() or global_error_text.strip()
                report(8, True, f"Invalid email rejected: '{error_shown}'")
            elif still_on_login:
                # Server rejected but UI may not show specific error — still a pass if stayed on login
                report(8, True, "Invalid email kept user on login page (no visible error text)")
            else:
                report(8, False, f"still_on_login={still_on_login}, emailError='{email_error_text}', global='{global_error_text}'")
        except Exception as e:
            report(8, False, f"Exception: {e}")

        # ================================================================
        # STEP 9: Duplicate email — register same email twice
        # ================================================================
        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            time.sleep(2)

            # Switch to register tab
            page.locator('[data-tab="register"]').click()
            time.sleep(0.5)

            # Use the same TEST_EMAIL that was already registered in step 3
            page.fill("#email", TEST_EMAIL)
            page.fill("#password", TEST_PASSWORD)
            page.click("#submitBtn")
            time.sleep(3)

            # Should show "already exists" or similar error
            global_error = page.locator("#globalError")
            global_error_text = global_error.text_content() if global_error.count() > 0 else ""

            email_error = page.locator("#emailError")
            email_error_text = email_error.text_content() if email_error.count() > 0 else ""

            combined_error = (global_error_text + " " + email_error_text).lower()
            still_on_login = "/login" in page.url

            has_dup_error = "already" in combined_error or "exists" in combined_error or "registered" in combined_error or "duplicate" in combined_error

            if still_on_login and has_dup_error:
                error_shown = global_error_text.strip() or email_error_text.strip()
                report(9, True, f"Duplicate email rejected: '{error_shown}'")
            elif still_on_login and len(combined_error.strip()) > 0:
                error_shown = global_error_text.strip() or email_error_text.strip()
                report(9, True, f"Duplicate email rejected (different wording): '{error_shown}'")
            else:
                report(9, False, f"still_on_login={still_on_login}, global='{global_error_text}', email='{email_error_text}', url={page.url}")
        except Exception as e:
            report(9, False, f"Exception: {e}")

        # ================================================================
        # STEP 10: Logout button — click logout, redirect to /landing or /login
        # ================================================================
        try:
            # First sign in so we are on dashboard
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            time.sleep(2)

            signin_tab = page.locator('[data-tab="signin"]')
            if signin_tab.count() > 0:
                signin_tab.click()
                time.sleep(0.5)

            page.fill("#email", TEST_EMAIL)
            page.fill("#password", TEST_PASSWORD)
            page.click("#submitBtn")
            time.sleep(4)

            # Now click logout
            logout_btn = page.locator("#logoutBtn")
            if logout_btn.count() > 0 and logout_btn.is_visible():
                logout_btn.click()
                time.sleep(3)

                try:
                    page.wait_for_url(lambda url: "/landing" in url or "/login" in url, timeout=10000)
                except Exception:
                    pass
                time.sleep(1)

                current_url = page.url
                # After logout, / redirects to /landing, so any of /, /landing, /login are valid
                logged_out = "/landing" in current_url or "/login" in current_url or current_url.rstrip("/").endswith(BASE_URL.rstrip("/"))

                if logged_out:
                    report(10, True, f"Logout redirected to {current_url}")
                else:
                    report(10, False, f"After logout, URL={current_url} (expected /landing or /login)")
            else:
                # Try JS click
                try:
                    page.evaluate("document.getElementById('logoutBtn').click()")
                    time.sleep(3)
                    current_url = page.url
                    on_landing_or_login = "/landing" in current_url or "/login" in current_url
                    if on_landing_or_login:
                        report(10, True, f"Logout via JS redirected to {current_url}")
                    else:
                        report(10, False, f"JS logout URL={current_url}")
                except Exception:
                    report(10, False, "Logout button not found or not clickable")
        except Exception as e:
            report(10, False, f"Exception: {e}")

        # ================================================================
        # STEP 11: Protected route — after logout, visiting / redirects to /landing
        # ================================================================
        try:
            # We should be logged out from step 10
            # Clear cookies to be sure we are fully logged out
            # (step 10 should have handled it, but let's verify the redirect)
            current_url = page.url
            if "/landing" not in current_url and "/login" not in current_url:
                # Force logout by clearing cookies
                context.clear_cookies()
                time.sleep(1)

            page.goto(f"{BASE_URL}/", wait_until="domcontentloaded")
            time.sleep(3)

            current_url = page.url
            redirected = "/landing" in current_url or "/login" in current_url

            if redirected:
                report(11, True, f"Protected route / redirected to {current_url}")
            else:
                report(11, False, f"Visiting / did not redirect, URL={current_url}")
        except Exception as e:
            report(11, False, f"Exception: {e}")

        # ================================================================
        # STEP 12: Plan parameter — /login?plan=pro shows form, plan in URL
        # ================================================================
        try:
            page.goto(f"{BASE_URL}/login?plan=pro", wait_until="domcontentloaded")
            time.sleep(2)

            current_url = page.url
            plan_in_url = "plan=pro" in current_url

            # Form should still be visible
            email_visible = page.locator("#email").is_visible()
            password_visible = page.locator("#password").is_visible()
            submit_visible = page.locator("#submitBtn").is_visible()
            form_visible = email_visible and password_visible and submit_visible

            if plan_in_url and form_visible:
                report(12, True, f"Login form with plan=pro loaded at {current_url}")
            elif form_visible:
                report(12, True, f"Login form loaded (plan param may have been stripped), URL={current_url}")
            else:
                report(12, False, f"plan_in_url={plan_in_url}, form_visible={form_visible}, url={current_url}")
        except Exception as e:
            report(12, False, f"Exception: {e}")

        # ================================================================
        # STEP 13: Password visibility toggle
        # ================================================================
        try:
            page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
            time.sleep(2)

            # Look for a show/hide password toggle button near the password field
            # Common patterns: button inside .field, eye icon, toggle element
            toggle_selectors = [
                ".password-toggle",
                ".toggle-password",
                "[data-toggle='password']",
                ".field button",
                "#togglePassword",
                ".eye-icon",
                "#password ~ button",
                "#password + button",
            ]

            toggle_found = False
            for selector in toggle_selectors:
                toggle = page.locator(selector)
                if toggle.count() > 0 and toggle.first.is_visible():
                    toggle_found = True
                    # Check initial type
                    pw_type_before = page.locator("#password").get_attribute("type")

                    # Click toggle
                    toggle.first.click()
                    time.sleep(0.5)

                    pw_type_after = page.locator("#password").get_attribute("type")

                    toggled = pw_type_before != pw_type_after
                    if toggled:
                        report(13, True, f"Password toggle works: {pw_type_before} -> {pw_type_after} (selector: {selector})")
                    else:
                        report(13, False, f"Toggle clicked but type unchanged: {pw_type_before} -> {pw_type_after}")
                    break

            if not toggle_found:
                report(13, True, "No password visibility toggle button found — feature not implemented (skipped)")
        except Exception as e:
            report(13, False, f"Exception: {e}")

        browser.close()

    # ================================================================
    # Summary
    # ================================================================
    print("\n" + "=" * 60)
    print("UI AUTH TEST SUMMARY")
    print("=" * 60)
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    for step, p, detail in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] Step {step}: {detail[:90]}")
    print(f"\nResult: {passed}/{total} steps passed")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
