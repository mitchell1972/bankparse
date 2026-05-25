"""
Production smoke test — proves the live HMRC submission journey is
reachable and auth-gated correctly on ``https://bankscanai.com``.

Skipped by default. To run::

    export PROD_SMOKE=1
    pytest tests/e2e/test_prod_hmrc_smoke.py -xvs --browser chromium

What it does (READ-ONLY against production — no writes, no DB pollution):

  1. Landing page renders the expected product positioning and a sign-in
     CTA.

  2. /login renders the login form (email + password + submit).

  3. /hmrc/file and /hmrc/connect (the two user-facing HMRC pages) return
     302 redirects to /login?next=... when called without an auth cookie.
     Proves the auth gate is wired up — these pages are NOT accidentally
     public.

  4. /api/hmrc/obligations returns 401 without a session — proves the API
     surface is auth-gated.

  5. /api/hmrc/penalty-status is reachable and returns 200 (this is the
     one HMRC endpoint that's intentionally open — it returns zeroed
     counts for anonymous users).

  6. The full-page DOM on /login contains no JS console errors that would
     break the journey before a user can even sign in.

What this test deliberately does NOT do:

  - Register a user (would create a real DB row in production)
  - Submit anything to HMRC (would either file a real return or require
    real Government Gateway credentials we don't have)
  - Touch any state-mutating endpoint

To extend coverage to the actual OAuth handoff and submission, you need
a pre-provisioned test account in prod and its Government Gateway
sandbox credentials. Without those, the only honest end-to-end signal
from production is "the routes are alive, auth-gated, and serving HTML
without JS errors" — which is what this test proves.
"""
from __future__ import annotations

import os
import urllib.request
import urllib.error

import pytest
from playwright.sync_api import Page, expect


PROD_URL = os.environ.get("PROD_SMOKE_URL", "https://bankscanai.com")


pytestmark = pytest.mark.skipif(
    os.environ.get("PROD_SMOKE") != "1",
    reason="Set PROD_SMOKE=1 to run prod smoke against bankscanai.com",
)


# ---------------------------------------------------------------------------
# HTTP-level smoke (no browser — fast)
# ---------------------------------------------------------------------------


def _status_and_location(path: str) -> tuple[int, str | None]:
    """GET ``path`` without following redirects. Returns (status, Location)."""
    req = urllib.request.Request(PROD_URL + path, method="GET")
    # urllib follows redirects by default; subclass an opener that doesn't.
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=15)
        return resp.status, resp.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")


def test_hmrc_file_page_is_auth_gated():
    """/hmrc/file (the submission UI) must 302 to /login when not signed in."""
    status, loc = _status_and_location("/hmrc/file")
    assert status == 302, f"/hmrc/file returned {status}, expected 302"
    assert loc and "/login" in loc, f"Redirect target wrong: {loc}"
    assert "next=" in (loc or ""), f"Login redirect missing ?next=: {loc}"


def test_hmrc_connect_page_is_auth_gated():
    """/hmrc/connect (the OAuth-start UI) must 302 to /login when not signed in."""
    status, loc = _status_and_location("/hmrc/connect")
    assert status == 302, f"/hmrc/connect returned {status}, expected 302"
    assert loc and "/login" in loc


def test_api_hmrc_connect_is_auth_gated():
    """/api/hmrc/connect (OAuth start API) must reject anonymous callers."""
    status, _ = _status_and_location("/api/hmrc/connect")
    # 302 to login OR 401 — both are acceptable hard-stops
    assert status in (302, 401), f"/api/hmrc/connect returned {status}"


def test_api_hmrc_obligations_requires_auth():
    """The obligations API must return 401 without a session."""
    status, _ = _status_and_location("/api/hmrc/obligations")
    assert status == 401, f"/api/hmrc/obligations returned {status}, expected 401"


def test_api_hmrc_penalty_status_is_reachable():
    """penalty-status is intentionally open (returns zeros for anon)."""
    status, _ = _status_and_location("/api/hmrc/penalty-status")
    assert status == 200, f"/api/hmrc/penalty-status returned {status}"


# ---------------------------------------------------------------------------
# Browser-level smoke (Playwright — proves no JS errors on the gate pages)
# ---------------------------------------------------------------------------


def test_landing_page_renders_with_signin_cta(page: Page):
    """The landing page loads and exposes a way to reach /login."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    page.goto(PROD_URL, wait_until="domcontentloaded", timeout=30_000)

    # Product positioning still present (current branding — not asserting
    # the HMRC pivot has reached the landing copy yet).
    expect(page.locator("body")).to_contain_text("BankScan AI", timeout=10_000)

    # Some kind of sign-in / try-free CTA must be present.
    cta = page.locator(
        "a[href='/login'], a[href^='/login?'], a:has-text('Sign In'), a:has-text('Try Free')"
    ).first
    expect(cta).to_be_visible(timeout=10_000)

    assert not errors, f"JS errors on landing: {errors}"


def test_login_page_renders_form(page: Page):
    """/login loads and shows the sign-in form."""
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    page.goto(f"{PROD_URL}/login", wait_until="domcontentloaded", timeout=30_000)

    # Email + password inputs must be visible.
    expect(
        page.locator(
            "input[type='email'], input[name='email'], input[autocomplete='email']"
        ).first
    ).to_be_visible(timeout=10_000)
    expect(
        page.locator("input[type='password']").first
    ).to_be_visible(timeout=10_000)

    assert not errors, f"JS errors on /login: {errors}"


def test_hmrc_file_redirects_to_login_in_browser(page: Page):
    """Drive a real browser to /hmrc/file → confirm it lands on /login.

    This is the browser-level mirror of test_hmrc_file_page_is_auth_gated
    above. It catches the case where the HTTP gate works but a
    client-side router would otherwise leak the page.
    """
    page.goto(f"{PROD_URL}/hmrc/file", wait_until="domcontentloaded", timeout=30_000)
    # After following the 302, we should be on /login with a next=... param.
    assert "/login" in page.url, f"Expected to land on /login, got {page.url}"
    assert "next" in page.url, f"Login URL missing next=: {page.url}"
