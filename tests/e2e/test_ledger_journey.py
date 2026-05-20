"""
End-to-end Playwright test for the unified HMRC ledger.

What this exercises (against a real uvicorn process):

   1. Register a fresh account
   2. Verify email via test-peek OTP
   3. Seed Stripe subscription state so the paywall lets us through
   4. Seed a bank transaction + matched receipt directly into the ledger
      (so we don't need the AI parser)
   5. Navigate to /ledger
   6. Assert the audit-readiness hero shows 100% (1/1 matched)
   7. Assert the per-category bar renders with the matched receipt
   8. Assert the tax-forecast widget shows the tax year + a number
   9. Click "Explain" → new tab with HMRC manual reference visible
  10. Click "Download Audit Certificate" → certificate page loads with hash
  11. Click "Download accountant ZIP" → triggers ZIP download

Runs against the live_server fixture in conftest.py.

The fixture-style ledger seeding uses /api/test/seed-ledger (added in
this PR) so we don't have to wire raw SQL into the browser context.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
Page = playwright_sync.Page
expect = playwright_sync.expect


TEST_EMAIL = f"ledger-e2e-{int(time.time())}@example.test"
TEST_PASSWORD = "password12345"


def _peek_otp(base_url: str, email: str) -> str:
    r = httpx.get(f"{base_url}/api/test/peek-otp", params={"email": email}, timeout=5.0)
    r.raise_for_status()
    return r.json()["code"]


def _seed_subscribed_user_via_test_api(base_url: str, email: str) -> int:
    """Use the test-mode admin endpoint to mark a user as trialing +
    seed a transaction + receipt + link, all in one shot."""
    r = httpx.post(
        f"{base_url}/api/test/seed-ledger-fixture",
        json={"email": email},
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()["user_id"]


def test_ledger_page_full_journey(live_server: str, page: Page):
    base_url = live_server

    # 1. Register
    page.goto(f"{base_url}/login")
    page.locator('button[data-tab="register"]').click()
    page.locator("input[type=email], input[name=email]").first.fill(TEST_EMAIL)
    page.locator("input[type=password], input[name=password]").first.fill(TEST_PASSWORD)
    page.locator("button[type=submit], button#submitBtn").first.click()
    page.wait_for_timeout(3000)
    if "/verify-email" not in page.url:
        page.goto(f"{base_url}/verify-email")

    # 2. Verify
    code = _peek_otp(base_url, TEST_EMAIL)
    page.locator("input#code, input[name=code]").first.fill(code)
    page.locator("button#verify-btn, button[type=submit]").first.click()
    # After verifying, we'll be redirected to /start-trial (no Stripe sub yet)
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base_url)}/start-trial(\?.*)?$"),
        timeout=10_000,
    )

    # 3 + 4. Seed Stripe sub + ledger fixtures via test API
    _seed_subscribed_user_via_test_api(base_url, TEST_EMAIL)

    # 5. Navigate to /ledger
    page.goto(f"{base_url}/ledger")
    expect(page.locator("text=HMRC Ledger")).to_be_visible(timeout=10_000)

    # 6. Audit-readiness hero shows 100% (fixture seeded 1 tx + matched receipt)
    expect(page.locator("#auditPct")).to_contain_text("100%", timeout=10_000)
    expect(page.locator("#auditLabel")).to_contain_text("Excellent")

    # 7. Per-category table renders
    expect(page.locator(".cat-table")).to_be_visible()

    # 8. Tax-forecast widget shows tax year
    expect(page.locator("#forecastTaxYear")).to_contain_text(re.compile(r"20\d\d-\d\d"))

    # 9. "Explain" link works — opens explain page in new tab
    with page.context.expect_page() as new_page_info:
        page.locator(".explain-link").first.click()
    explain_page = new_page_info.value
    explain_page.wait_for_load_state()
    expect(explain_page.locator("text=HMRC defence sheet")).to_be_visible()
    # The seeded transaction has a known HMRC manual ref
    body = explain_page.content()
    assert "BIM" in body or "PIM" in body
    explain_page.close()

    # 10. Audit certificate
    page.goto(f"{base_url}/api/audit-certificate?period=Q2-2026")
    expect(page.locator("h1", has_text="Audit Confidence Certificate")).to_be_visible()
    # The certificate has a stamp div with the SHA-256 hash
    expect(page.locator(".stamp")).to_be_visible()
    expect(page.locator(".stamp")).to_contain_text(re.compile(r"[a-f0-9]{64}"))

    # 11. Accountant ZIP — verify it's served with the right content type
    r = page.request.get(f"{base_url}/api/accountant-export?period=Q2-2026")
    assert r.status == 200
    assert "application/zip" in r.headers["content-type"]
    # And the bytes start with the ZIP magic number
    body = r.body()
    assert body[:2] == b"PK"
