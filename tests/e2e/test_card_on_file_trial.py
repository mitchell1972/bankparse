"""
End-to-end test for the card-on-file 7-day trial gate.

What this exercises (against a real uvicorn process via the live_server fixture):

  1. Register a fresh account (NOT grandfathered)
  2. Verify email via OTP — server should reply with redirect_to=/start-trial
  3. /start-trial page renders the "Start free trial" CTA
  4. Navigating to / redirects back to /start-trial (gate works server-side)
  5. POST /api/billing/start-trial-checkout returns 500/501 in test env (no real
     Stripe key) — proves the endpoint is wired
  6. Simulate Stripe webhook delivery via /api/test/set-subscription-state
     (subscription_status='trialing', trial_end_at in the future)
  7. Now navigating to / lands on the dashboard (gate is unblocked)
  8. Set subscription_status=canceled with trial_end_at in the past
  9. / redirects back to /start-trial again (gate re-engages)

This test does NOT go through real Stripe Checkout — the Stripe-side flow is
covered by manual QA per the PR test plan. The unit tests in
tests/test_billing_trial.py cover the webhook handlers with mocked Stripe.
"""

from __future__ import annotations

import re
import time
import httpx
from pathlib import Path
from playwright.sync_api import Page, expect


TEST_EMAIL = f"e2e-cof-{int(time.time())}@example.test"
TEST_PASSWORD = "password12345"


def _peek_otp(base_url: str, email: str) -> str:
    r = httpx.get(f"{base_url}/api/test/peek-otp", params={"email": email}, timeout=5.0)
    r.raise_for_status()
    return r.json()["code"]


def _set_subscription_state(base_url: str, email: str, **fields) -> None:
    r = httpx.post(f"{base_url}/api/test/set-subscription-state",
                   json={"email": email, **fields}, timeout=5.0)
    r.raise_for_status()


def test_card_on_file_gate(page: Page, live_server: str):
    base = live_server

    # ---------- 1. Register fresh account (no grandfather) ----------
    page.goto(f"{base}/login")
    page.locator('button[data-tab="register"]').click()
    page.locator("input[type=email], input[name=email]").first.fill(TEST_EMAIL)
    page.locator("input[type=password], input[name=password]").first.fill(TEST_PASSWORD)
    page.locator("button[type=submit], button#submitBtn").first.click()
    page.wait_for_timeout(2000)

    # Should land on /verify-email
    if "/verify-email" not in page.url:
        page.goto(f"{base}/verify-email")

    # ---------- 2. Verify email ----------
    code = _peek_otp(base, TEST_EMAIL)
    assert len(code) == 6 and code.isdigit()
    page.locator("input#code, input[name=code]").first.fill(code)
    page.locator("button#verify-btn, button[type=submit]").first.click()

    # ---------- 3. Should redirect to /start-trial (NOT /) ----------
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/start-trial(\?.*)?$"),
        timeout=10_000,
    )
    expect(page.locator("h1")).to_contain_text("free trial")
    expect(page.locator("#start-trial-btn")).to_be_visible()

    # ---------- 4. Navigating to / redirects back ----------
    page.goto(f"{base}/")
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/start-trial(\?.*)?$"),
        timeout=5_000,
    )

    # ---------- 5. The trial-checkout endpoint is reachable.
    #     In this isolated test environment there's no real STRIPE_SECRET_KEY
    #     wired, so we expect a 501. What matters is the route is mounted and
    #     the auth + verification gates pass.
    csrf = page.context.cookies()
    csrf_val = next((c["value"] for c in csrf if c["name"] == "bp_csrf"), "")
    r = httpx.post(
        f"{base}/api/billing/start-trial-checkout",
        headers={"X-CSRF-Token": csrf_val},
        cookies={c["name"]: c["value"] for c in csrf},
        timeout=5.0,
    )
    # 501 (no Stripe key) or 500 (creation failed) — both confirm the route
    # exists and got past auth/verify/grandfather gates. 401/403/409 would
    # indicate a gating bug we want to catch here.
    assert r.status_code in (500, 501), (
        f"Unexpected status from start-trial-checkout: {r.status_code} {r.text}"
    )

    # ---------- 6. Simulate Stripe webhook: user is now trialing ----------
    _set_subscription_state(
        base, TEST_EMAIL,
        subscription_status="trialing",
        stripe_subscription_id="sub_test_e2e",
        trial_end_at=time.time() + 6 * 86400,
    )

    # ---------- 7. Dashboard is now reachable ----------
    page.goto(f"{base}/")
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/?(\?.*)?$"),
        timeout=10_000,
    )
    # Sanity: there should be the upload UI somewhere on the dashboard
    expect(page.locator("input[type=file]").first).to_be_attached(timeout=5_000)

    # ---------- 8. Cancel: trial ends ----------
    _set_subscription_state(
        base, TEST_EMAIL,
        subscription_status="canceled",
        trial_end_at=time.time() - 60,
    )

    # ---------- 9. Gate re-engages ----------
    page.goto(f"{base}/")
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/start-trial(\?.*)?$"),
        timeout=5_000,
    )
