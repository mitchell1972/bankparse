"""
Production smoke test — proves the live HMRC submission journey is
reachable and auth-gated correctly on ``https://bankscanai.com``.

Two tiers of coverage, gated by env vars so neither runs in normal CI:

  TIER 1 — anonymous (set PROD_SMOKE=1)
    Read-only, no DB writes, no credentials needed. Proves the routes
    are alive and auth-gated:
      - Landing page renders + sign-in CTA visible, no JS errors
      - /login renders email + password form, no JS errors
      - /hmrc/file, /hmrc/connect 302 to /login?next=...
      - /api/hmrc/obligations 401s
      - /api/hmrc/penalty-status 200s (intentionally open)
      - Browser-driven /hmrc/file lands on /login

  TIER 2 — authenticated (also set PROD_TEST_USER_EMAIL + PROD_TEST_USER_PASSWORD)
    Requires a pre-existing verified account on bankscanai.com. Proves
    the deeper flow without filing real returns (prod is wired to HMRC
    SANDBOX — verified via /api/hmrc/sandbox/* returning 403 CSRF on
    prod, not 404):
      - Login round-trip works, session cookie issued
      - /api/hmrc/obligations returns 200 (data or business-setup-required)
      - /api/hmrc/connect (OAuth initiation) redirects to the HMRC
        SANDBOX host test-api.service.hmrc.gov.uk — NOT prod HMRC
      - Sandbox test-user provisioning route is reachable and authed

How to provide the TIER 2 credentials:

    export PROD_SMOKE=1
    export PROD_TEST_USER_EMAIL='you+e2esmoke@yourdomain.com'
    export PROD_TEST_USER_PASSWORD='...'
    pytest tests/e2e/test_prod_hmrc_smoke.py -xvs --browser chromium

If you don't already have a verified account on bankscanai.com, register
one with any email you own (the OTP arrives via real Resend email),
verify, then export those creds before running.

What this test deliberately STILL does NOT do, even at tier 2:

  - Submit a quarterly update / EOPS / final-declaration. Even against
    sandbox these write rows the test would then need to clean up.
  - Drive the full Government-Gateway OAuth handshake on HMRC's own
    sandbox site. That requires minting a sandbox test individual
    first (POST /api/hmrc/sandbox/create-test-user) and then having
    Playwright type those credentials into HMRC's external login page.
    Achievable but flaky — left for the next iteration.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import urllib.error

import pytest
from playwright.sync_api import Page, expect


PROD_URL = os.environ.get("PROD_SMOKE_URL", "https://bankscanai.com")

PROD_TEST_USER_EMAIL = os.environ.get("PROD_TEST_USER_EMAIL", "")
PROD_TEST_USER_PASSWORD = os.environ.get("PROD_TEST_USER_PASSWORD", "")


pytestmark = pytest.mark.skipif(
    os.environ.get("PROD_SMOKE") != "1",
    reason="Set PROD_SMOKE=1 to run prod smoke against bankscanai.com",
)


_authed = pytest.mark.skipif(
    not (PROD_TEST_USER_EMAIL and PROD_TEST_USER_PASSWORD),
    reason="Set PROD_TEST_USER_EMAIL and PROD_TEST_USER_PASSWORD to run authenticated tier",
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


# ---------------------------------------------------------------------------
# TIER 2 — authenticated smoke (requires PROD_TEST_USER_EMAIL + PASSWORD)
# ---------------------------------------------------------------------------


def _login_via_json_api() -> dict[str, str]:
    """POST /api/login with the test creds. Returns the cookie jar as a
    {name: value} dict suitable for adding via Playwright's
    BrowserContext.add_cookies or urllib's Cookie header."""
    body = json.dumps({
        "email": PROD_TEST_USER_EMAIL,
        "password": PROD_TEST_USER_PASSWORD,
    }).encode()
    req = urllib.request.Request(
        PROD_URL + "/api/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        raise AssertionError(
            f"Login failed with {e.code}: {e.read().decode()[:200]}. "
            f"Check PROD_TEST_USER_EMAIL/PROD_TEST_USER_PASSWORD are correct "
            f"and the user is email-verified."
        ) from e

    # urllib doesn't surface Set-Cookie headers via headers.get_all reliably
    # on some versions — use raw header iteration.
    cookies: dict[str, str] = {}
    for header_name, header_value in resp.headers.items():
        if header_name.lower() != "set-cookie":
            continue
        # Cookie syntax: name=value; Path=/; ... — just grab name=value.
        pair = header_value.split(";", 1)[0]
        if "=" in pair:
            k, v = pair.split("=", 1)
            cookies[k.strip()] = v.strip()
    assert "bp_auth" in cookies, (
        f"Login response missing bp_auth cookie. Got: {list(cookies)}"
    )
    return cookies


def _authed_get(path: str, cookies: dict[str, str]) -> tuple[int, str]:
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(
        PROD_URL + path,
        method="GET",
        headers={"Cookie": cookie_header},
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=15)
        return resp.status, resp.read().decode(errors="replace")[:2000]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:2000]


@_authed
def test_login_round_trip_issues_auth_cookie():
    """The test account can sign in and we get bp_auth back."""
    cookies = _login_via_json_api()
    assert cookies.get("bp_auth"), "Missing bp_auth cookie after login"


@_authed
def test_authenticated_obligations_returns_sane_response():
    """Once logged in, /api/hmrc/obligations returns a structured response
    (either 200 with obligations data, or 200 with a 'connect to HMRC'
    state, or 400 with 'business not set up'). Anything 500+ is a real
    bug."""
    cookies = _login_via_json_api()
    status, body = _authed_get("/api/hmrc/obligations", cookies)
    assert status < 500, f"/api/hmrc/obligations 500ed: status={status} body={body}"
    # 200/400/401 all acceptable here — the point is the route doesn't crash.
    assert status in (200, 400, 401), (
        f"Unexpected status {status} from /api/hmrc/obligations. Body: {body}"
    )


@_authed
def test_hmrc_oauth_redirect_targets_sandbox_not_production():
    """CRITICAL: prod must redirect to test-api.service.hmrc.gov.uk
    (sandbox). If it redirects to api.service.hmrc.gov.uk (production
    HMRC) — STOP. We are not HMRC-recognised yet, that would fail every
    OAuth call AND any successful submission would be a real return."""
    cookies = _login_via_json_api()
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(
        PROD_URL + "/api/hmrc/connect",
        method="GET",
        headers={"Cookie": cookie_header},
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        resp = opener.open(req, timeout=15)
        status, loc = resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        status, loc = e.code, e.headers.get("Location", "")

    assert status in (302, 307), (
        f"/api/hmrc/connect expected redirect, got {status} (loc={loc})"
    )
    assert loc, "Redirect missing Location header"

    parsed = urllib.parse.urlparse(loc)
    assert parsed.netloc == "test-api.service.hmrc.gov.uk", (
        f"DANGER: /api/hmrc/connect redirects to {parsed.netloc}, "
        f"expected test-api.service.hmrc.gov.uk (sandbox). "
        f"If this is api.service.hmrc.gov.uk you're talking to production "
        f"HMRC — stop and reconfigure HMRC_ENV before going further."
    )

    # OAuth URL must carry the four mandatory params.
    q = urllib.parse.parse_qs(parsed.query)
    for required in ("client_id", "redirect_uri", "scope", "state"):
        assert required in q, (
            f"OAuth URL missing ?{required}=. Full URL: {loc}"
        )
    # State should be opaque + non-empty — sanity-check.
    assert q["state"][0] and len(q["state"][0]) >= 8, (
        f"OAuth state param too short / empty: {q.get('state')}"
    )


@_authed
def test_sandbox_create_test_user_route_is_reachable():
    """Once logged in, POST /api/hmrc/sandbox/create-test-user should
    either succeed (mints a sandbox individual) or fail with a known
    HMRC error — NOT 404 (route missing) and NOT 500+ (server crash).

    We send no CSRF token, so we expect 403 from the CSRF middleware —
    that itself proves the route is registered AND auth-gated AND the
    sandbox guard passes. Anything else (404, 500) is a real problem."""
    cookies = _login_via_json_api()
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(
        PROD_URL + "/api/hmrc/sandbox/create-test-user",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "Cookie": cookie_header},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        status = 200
    except urllib.error.HTTPError as e:
        status = e.code

    # 403 (CSRF) is the expected good signal — route is alive and gated.
    # 200 would mean it minted a user (possible if CSRF was bypassed).
    # 502 would mean it tried HMRC and HMRC said no (still proves wired).
    assert status in (200, 403, 502), (
        f"/api/hmrc/sandbox/create-test-user returned {status}. "
        f"404 would mean the route isn't deployed; 500 a server crash; "
        f"401 would mean auth failed even though we sent bp_auth."
    )
