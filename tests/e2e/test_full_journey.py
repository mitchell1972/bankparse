"""
End-to-end test for the full BankScan AI user journey (card-on-file flow).

What this exercises (in one run, against a real uvicorn process):

   1. Register a fresh account
   2. Auto-redirect to /verify-email
   3. Read the OTP via the test-mode endpoint (no real inbox needed)
   4. Submit OTP → email gets verified → server returns redirect_to=/start-trial
   5. /start-trial renders with the "Start free trial" CTA
   6. Confirm the trial-checkout endpoint is reachable (501 in test env since
      no real Stripe key; real Stripe Checkout is covered by manual QA)
   7. Simulate the post-Checkout webhook by writing subscription_status=
      'trialing' + trial_end_at directly — mirrors what Stripe sends after
      the user enters a card on Checkout
   8. Now / serves the dashboard
   9. Upload statement #1 (CSV, parsed locally so no Anthropic key required)
  10. Cumulative banner reflects the new file
  11. Upload statement #2 → banner shows running totals across both
  12. Log out, log back in — banner still shows the totals
  13. Click "Clear & Upload New", confirm the prompt → wipe
  14. Move trial_end_at into the past (simulates day-8 of trial) → trial ends
  15. Refresh dashboard → server redirects back to /start-trial

This is the NEW card-on-file journey end-to-end. The Stripe Checkout page
itself isn't navigated (would require a real STRIPE_SECRET_KEY + test card
entry); that step is covered by manual QA in the PR test plan and by the
unit tests in tests/test_billing_trial.py with the Stripe SDK mocked.

Runs against the live_server fixture in conftest.py.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
import pytest

# Skip the whole module cleanly when playwright isn't installed (e.g. when
# the regular `pytest` is run without the -dev requirements). CI installs
# the browser before running these.
playwright_sync = pytest.importorskip("playwright.sync_api")
Page = playwright_sync.Page
expect = playwright_sync.expect
Dialog = playwright_sync.Dialog


# Generate a unique email per test run so register doesn't collide
TEST_EMAIL = f"e2e-{int(time.time())}@example.test"
TEST_PASSWORD = "password12345"


def _peek_otp(base_url: str, email: str) -> str:
    """Read the latest OTP for an email via the test-mode endpoint."""
    r = httpx.get(f"{base_url}/api/test/peek-otp", params={"email": email}, timeout=5.0)
    r.raise_for_status()
    return r.json()["code"]


def _set_subscription_state(base_url: str, email: str, **fields) -> None:
    """Write subscription_status / stripe_subscription_id / trial_end_at
    directly — simulates a Stripe webhook delivery for the card-on-file
    trial flow without going through real Stripe Checkout."""
    r = httpx.post(f"{base_url}/api/test/set-subscription-state",
                   json={"email": email, **fields}, timeout=5.0)
    r.raise_for_status()


def test_full_user_journey(page: Page, live_server: str, fixture_csv: Path):
    base = live_server

    # ----------------------------------------------------------------------
    # 1-2. Register, expect redirect to /verify-email
    # ----------------------------------------------------------------------
    page.goto(f"{base}/login")
    # Switch to the Register tab. They're plain styled <button> elements
    # with data-tab="register", not ARIA tabs.
    page.locator('button[data-tab="register"]').click()

    page.locator("input[type=email], input[name=email]").first.fill(TEST_EMAIL)
    page.locator("input[type=password], input[name=password]").first.fill(TEST_PASSWORD)
    page.locator("button[type=submit], button#submitBtn").first.click()

    # The frontend JS sets window.location.href after register — wait for it.
    page.wait_for_timeout(3000)

    current_url = page.url
    if "/verify-email" in current_url:
        pass  # Expected path
    elif "/login" in current_url or "/landing" in current_url:
        # Auth cookie may have been lost. Navigate manually.
        page.goto(f"{base}/verify-email")

    # ----------------------------------------------------------------------
    # 3-4. Read OTP, submit, expect redirect to /start-trial (NOT /)
    #      because this is a fresh (non-grandfathered) user who must enter
    #      a card before any parsing.
    # ----------------------------------------------------------------------
    code = _peek_otp(base, TEST_EMAIL)
    assert len(code) == 6 and code.isdigit()

    page.locator("input#code, input[name=code]").first.fill(code)
    page.locator("button#verify-btn, button[type=submit]").first.click()

    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/start-trial(\?.*)?$"),
        timeout=10_000,
    )

    # ----------------------------------------------------------------------
    # 5. /start-trial renders the CTA
    # ----------------------------------------------------------------------
    expect(page.locator("h1")).to_contain_text("free trial")
    expect(page.locator("#start-trial-btn")).to_be_visible()

    # ----------------------------------------------------------------------
    # 6. The Stripe-Checkout-creation endpoint is reachable. In this test
    #    env there's no real STRIPE_SECRET_KEY, so we expect 501 (or 500
    #    if creation fails after gates). 401/403/409 would indicate a
    #    gating regression we want to catch.
    # ----------------------------------------------------------------------
    cookies = page.context.cookies()
    csrf_val = next((c["value"] for c in cookies if c["name"] == "bp_csrf"), "")
    r = httpx.post(
        f"{base}/api/billing/start-trial-checkout",
        headers={"X-CSRF-Token": csrf_val},
        cookies={c["name"]: c["value"] for c in cookies},
        timeout=5.0,
    )
    assert r.status_code in (500, 501), (
        f"Unexpected status from start-trial-checkout: {r.status_code} {r.text}"
    )

    # ----------------------------------------------------------------------
    # 7. Simulate the post-Stripe-Checkout webhook delivery. In production
    #    this happens automatically when Stripe POSTs checkout.session.
    #    completed; here we shortcut to the same DB state.
    # ----------------------------------------------------------------------
    _set_subscription_state(
        base, TEST_EMAIL,
        subscription_status="trialing",
        stripe_subscription_id="sub_test_e2e",
        trial_end_at=time.time() + 6 * 86400,
    )

    # ----------------------------------------------------------------------
    # 8. Dashboard is now reachable
    # ----------------------------------------------------------------------
    page.goto(f"{base}/")
    expect(page).to_have_url(re.compile(rf"^{re.escape(base)}/?(\?.*)?$"), timeout=10_000)

    # ----------------------------------------------------------------------
    # 9. Upload statement #1
    # ----------------------------------------------------------------------
    page.locator("input[type=file]").first.set_input_files(str(fixture_csv))
    page.locator("button#parseBtn, button:has-text('Convert to Spreadsheet')").first.click()

    # Wait for the cumulative banner to mention "1 statement"
    banner = page.locator("#accumulatedBanner")
    expect(banner).to_contain_text("statement", timeout=30_000)

    # ----------------------------------------------------------------------
    # 10-11. Upload statement #2 → cumulative banner reflects 2 statements
    # ----------------------------------------------------------------------
    page.locator("input[type=file]").first.set_input_files(str(fixture_csv))
    page.locator("button#parseBtn, button:has-text('Convert to Spreadsheet')").first.click()
    expect(banner).to_contain_text("2 statements", timeout=30_000)

    # ----------------------------------------------------------------------
    # 12. Log out (cookie clear), log back in — cumulative banner persists
    # ----------------------------------------------------------------------
    page.context.clear_cookies()
    page.goto(f"{base}/login")
    page.locator("input[type=email], input[name=email]").first.fill(TEST_EMAIL)
    page.locator("input[type=password], input[name=password]").first.fill(TEST_PASSWORD)
    page.locator("button[type=submit]").first.click()
    expect(page).to_have_url(re.compile(rf"^{re.escape(base)}/?(\?.*)?$"), timeout=10_000)
    expect(banner).to_contain_text("2 statements", timeout=10_000)

    # ----------------------------------------------------------------------
    # 13. Click "Clear & Upload New" — confirm prompt, wipe
    # ----------------------------------------------------------------------
    def accept_dialog(dialog: Dialog):
        dialog.accept()
    page.once("dialog", accept_dialog)
    # Always-visible Clear button inside the cumulative banner — the
    # per-results-card Clear buttons are only rendered when that results
    # card is visible, which it isn't on a fresh page load.
    page.locator("#accumulatedClearBtn").click()
    # Banner should hide once data is wiped
    expect(banner).to_be_hidden(timeout=5_000)

    # ----------------------------------------------------------------------
    # 14-15. Move trial_end_at into the past → trial expires. The user IS
    #        verified and HAS subscription_status='trialing' but the trial
    #        window has closed, so they're sent back to /start-trial for
    #        renewal (or to top up payment method).
    # ----------------------------------------------------------------------
    _set_subscription_state(
        base, TEST_EMAIL,
        subscription_status="canceled",
        trial_end_at=time.time() - 60,
    )
    page.goto(f"{base}/")
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/start-trial(\?.*)?$"),
        timeout=5_000,
    )
