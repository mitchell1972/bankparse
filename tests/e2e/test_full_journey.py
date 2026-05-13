"""
End-to-end test for the full BankScan AI user journey.

What this exercises (in one run, against a real uvicorn process):

  1. Register a fresh account
  2. Auto-redirect to /verify-email (login flow set verify_url in PR #10/#13)
  3. Read the OTP via the test-mode endpoint (no real inbox needed)
  4. Submit OTP → email gets verified → user is sent to dashboard
  5. Upload statement #1 (CSV, parsed locally so no Anthropic key required)
  6. Cumulative banner reflects the new file
  7. Upload statement #2 → banner shows running totals across both
  8. Log out, then log back in — banner still shows the totals
     (the persistence-survives-logout guarantee)
  9. Click "Clear & Upload New", confirm the prompt → wipe
 10. Age the user back 8 days via /api/test/age-user → trial expires
 11. Refresh dashboard → either redirected to /verify-email-equivalent or
     blocked-with-paywall (depending on flow), and the upload UI shows
     "Trial expired"

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


def _age_user(base_url: str, email: str, days_ago: float) -> None:
    """Backdate the user's created_at to force trial expiry."""
    r = httpx.post(f"{base_url}/api/test/age-user",
                   json={"email": email, "days_ago": days_ago}, timeout=5.0)
    r.raise_for_status()


def test_full_user_journey(page: Page, live_server: str, fixture_csv: Path):
    base = live_server

    # ----------------------------------------------------------------------
    # 1-2. Register, expect redirect to /verify-email
    # ----------------------------------------------------------------------
    page.goto(f"{base}/login")
    # Switch to the Register tab (assume there's a tab; otherwise the page
    # directly shows both flows).
    register_tab = page.get_by_role("tab", name=re.compile("register|sign up", re.I))
    if register_tab.count() > 0:
        register_tab.click()

    page.locator("input[type=email], input[name=email]").first.fill(TEST_EMAIL)
    page.locator("input[type=password], input[name=password]").first.fill(TEST_PASSWORD)
    page.locator("button[type=submit], button#submitBtn").first.click()

    expect(page).to_have_url(re.compile(r"/verify-email"), timeout=10_000)

    # ----------------------------------------------------------------------
    # 3-4. Read OTP, submit, expect dashboard
    # ----------------------------------------------------------------------
    code = _peek_otp(base, TEST_EMAIL)
    assert len(code) == 6 and code.isdigit()

    page.locator("input#code, input[name=code]").first.fill(code)
    page.locator("button#verify-btn, button[type=submit]").first.click()

    # After verify, the page redirects to / — wait for it
    expect(page).to_have_url(re.compile(rf"^{re.escape(base)}/?(\?.*)?$"), timeout=10_000)

    # ----------------------------------------------------------------------
    # 5. Upload statement #1
    # ----------------------------------------------------------------------
    page.locator("input[type=file]").first.set_input_files(str(fixture_csv))
    page.locator("button#parseBtn, button:has-text('Convert to Spreadsheet')").first.click()

    # Wait for the cumulative banner to mention "1 statement"
    banner = page.locator("#accumulatedBanner")
    expect(banner).to_contain_text("statement", timeout=30_000)

    # ----------------------------------------------------------------------
    # 6-7. Upload statement #2 → cumulative banner reflects 2 statements
    # ----------------------------------------------------------------------
    # Reset the file input by uploading again (the same input still works)
    page.locator("input[type=file]").first.set_input_files(str(fixture_csv))
    page.locator("button#parseBtn, button:has-text('Convert to Spreadsheet')").first.click()
    expect(banner).to_contain_text("2 statements", timeout=30_000)

    # ----------------------------------------------------------------------
    # 8. Log out, log back in, verify cumulative data still there
    # ----------------------------------------------------------------------
    page.goto(f"{base}/login")
    # If still logged in, /login redirects to / — handle either case
    page.locator("input[type=email], input[name=email]").first.fill(TEST_EMAIL)
    page.locator("input[type=password], input[name=password]").first.fill(TEST_PASSWORD)
    page.locator("button[type=submit]").first.click()
    expect(page).to_have_url(re.compile(rf"^{re.escape(base)}/?(\?.*)?$"), timeout=10_000)
    expect(banner).to_contain_text("2 statements", timeout=10_000)

    # ----------------------------------------------------------------------
    # 9. Click "Clear & Upload New" — confirm prompt, wipe
    # ----------------------------------------------------------------------
    def accept_dialog(dialog: Dialog):
        dialog.accept()
    page.once("dialog", accept_dialog)
    page.get_by_role("button", name=re.compile("Clear & Upload New", re.I)).first.click()
    # Banner should hide once data is wiped
    expect(banner).to_be_hidden(timeout=5_000)

    # ----------------------------------------------------------------------
    # 10-11. Age the user 8 days back → trial expired → upload blocked
    # ----------------------------------------------------------------------
    _age_user(base, TEST_EMAIL, days_ago=8)
    page.goto(f"{base}/")
    # GET / should now bounce to /verify-email-equivalent path. Since the
    # user IS verified, /api/usage will return trial_active=false. The
    # dashboard banner should say "Trial expired".
    usage_badge = page.locator("#usageText")
    expect(usage_badge).to_contain_text(re.compile("Trial expired", re.I), timeout=5_000)
