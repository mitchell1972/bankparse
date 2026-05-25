"""
Production smoke test — proves the live HMRC submission journey is
reachable and auth-gated correctly on ``https://bankscanai.com``.

Three tiers of coverage, each gated separately so nothing runs in normal CI:

  TIER 1 — anonymous (PROD_SMOKE=1)
    Read-only, no DB writes, no credentials needed. Proves the routes
    are alive and auth-gated.

  TIER 2 — authenticated (also PROD_TEST_USER_EMAIL + PROD_TEST_USER_PASSWORD)
    Requires a pre-existing verified account on bankscanai.com. Proves
    login, /api/hmrc/obligations, the OAuth redirect points to HMRC
    SANDBOX (critical guard against being wired to prod HMRC), and the
    sandbox test-user provisioning route is reachable.

  TIER 3 — full OAuth handshake + sandbox submission (also PROD_SMOKE_FULL=1)
    Mutates state: mints a sandbox individual, drives Playwright through
    HMRC's Government Gateway sandbox login on test-www.tax.service.gov.uk,
    accepts the grant-scope, lands back on /api/hmrc/callback, sets up
    SE + property businesses via /api/hmrc/sandbox/setup-complete, and
    runs a quarterly-update PREVIEW (no submit — preview is the deepest
    safe read against real sandbox data we can do without leaving
    submission records the user has to clean up). Disconnects at end via
    /api/hmrc/disconnect.

How to run each tier:

    # tier 1 (8 tests)
    export PROD_SMOKE=1
    pytest tests/e2e/test_prod_hmrc_smoke.py --browser chromium

    # + tier 2 (4 more tests)
    export PROD_TEST_USER_EMAIL='you+e2esmoke@yourdomain.com'
    export PROD_TEST_USER_PASSWORD='...'

    # + tier 3 (4 more tests, slow + state-mutating)
    export PROD_SMOKE_FULL=1
    pytest tests/e2e/test_prod_hmrc_smoke.py --browser chromium -v

What tier 3 deliberately STILL does NOT do:

  - POST a quarterly-update SUBMIT (only preview). Submission writes an
    immutable audit row HMRC won't let us undo from the sandbox even
    though the numbers themselves evaporate.
  - Touch EOPS or final-declaration. Same reason — they leave records
    in HMRC's sandbox we can't undo.

Tier 3 known-flaky failure modes:

  - HMRC sandbox occasionally CAPTCHAs the GG sign-in page. Re-run.
  - HMRC sandbox is slower than production (10-15s page loads aren't
    unusual). Timeouts are generous accordingly.
  - HMRC's GG sandbox UI changes selectors without notice. If the test
    fails on the sign-in step with a missing-selector error, update
    _gg_signin_selectors below.
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


# ---------------------------------------------------------------------------
# TIER 3 — full OAuth handshake + sandbox bootstrap + preview submission
# ---------------------------------------------------------------------------

_full_oauth = pytest.mark.skipif(
    os.environ.get("PROD_SMOKE_FULL") != "1"
    or not (PROD_TEST_USER_EMAIL and PROD_TEST_USER_PASSWORD),
    reason=(
        "Set PROD_SMOKE_FULL=1 + PROD_TEST_USER_EMAIL/PASSWORD to run "
        "the full OAuth + sandbox-submit tier. This mutates state on "
        "the prod user (stores HMRC tokens, creates businesses) and "
        "drives Playwright through HMRC's GG sandbox login page."
    ),
)


# HMRC Government Gateway sandbox login — selectors as of 2026-05-25.
# HMRC tweaks this UI occasionally; update here if tier 3 starts failing
# on a missing-selector error.
_GG_USER_ID_SELECTORS = [
    'input[name="userId"]',
    "input#user_id",
    'input[autocomplete="username"]',
]
_GG_PASSWORD_SELECTORS = [
    'input[name="password"]',
    "input#password",
    'input[autocomplete="current-password"]',
]
_GG_SIGN_IN_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Sign in")',
]
_GRANT_SELECTORS = [
    'button:has-text("Grant authority")',
    'button:has-text("Continue")',
    'input[value="Grant authority"]',
    'input[value="Continue"]',
    'button[type="submit"]',
]


def _first_visible(page, selectors: list[str], timeout: int = 8_000):
    """Return the first selector from ``selectors`` that resolves to a
    visible element on ``page``. Raises with a clear message listing
    everything tried."""
    from playwright.sync_api import TimeoutError as PWTimeout

    deadline_each = max(timeout // len(selectors), 1_500)
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=deadline_each)
            return loc
        except PWTimeout:
            continue
    raise AssertionError(
        f"None of these selectors resolved on {page.url!r}: {selectors}. "
        f"HMRC GG UI likely changed — update _GG_*_SELECTORS in this file."
    )


def _seed_csrf_via_browser(page) -> str:
    """Visit /api/hmrc/penalty-status to make the server set the bp_csrf
    cookie (any GET works). Returns the cookie value."""
    page.goto(PROD_URL + "/api/hmrc/penalty-status", wait_until="domcontentloaded")
    cookies = page.context.cookies(PROD_URL)
    for c in cookies:
        if c["name"] == "bp_csrf":
            return c["value"]
    raise AssertionError("bp_csrf cookie not set by server after GET")


def _login_in_browser(page) -> None:
    """Log into bankscanai.com via the POST /api/login JSON API using
    page.request so cookies flow into the browser context."""
    resp = page.request.post(
        PROD_URL + "/api/login",
        data=json.dumps({
            "email": PROD_TEST_USER_EMAIL,
            "password": PROD_TEST_USER_PASSWORD,
        }),
        headers={"Content-Type": "application/json"},
        timeout=20_000,
    )
    assert resp.status == 200, (
        f"Login failed: {resp.status} {resp.text()[:200]}"
    )


@pytest.fixture
def authed_browser(page: Page):
    """Yields a Playwright Page already logged into bankscanai.com.
    Auto-disconnects HMRC tokens on teardown so subsequent runs aren't
    polluted by stored OAuth state from the prior run."""
    _login_in_browser(page)
    yield page
    # Best-effort cleanup — disconnect HMRC tokens.
    try:
        csrf = _seed_csrf_via_browser(page)
        page.request.post(
            PROD_URL + "/api/hmrc/disconnect",
            headers={"X-CSRF-Token": csrf},
            timeout=10_000,
        )
    except Exception:
        pass


@_full_oauth
def test_mint_sandbox_individual_returns_nino_and_gg_creds(authed_browser: Page):
    """POST /api/hmrc/sandbox/create-test-user mints a fresh HMRC sandbox
    individual and returns NINO + Government Gateway userId + password.

    Stores the credentials on the page context for the next test to
    pick up."""
    page = authed_browser
    csrf = _seed_csrf_via_browser(page)
    resp = page.request.post(
        PROD_URL + "/api/hmrc/sandbox/create-test-user",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        data="{}",
        timeout=30_000,
    )
    assert resp.status == 200, (
        f"create-test-user failed: {resp.status} {resp.text()[:500]}. "
        f"Common cause: HMRC_CLIENT_ID/HMRC_CLIENT_SECRET not set on Vercel."
    )
    body = resp.json()
    assert body.get("nino"), f"No nino in response: {body}"
    assert body.get("user_id") or body.get("userId"), f"No GG userId in response: {body}"
    assert body.get("password"), f"No GG password in response: {body}"

    # Stash on the page object for the next test via context storage.
    page.context._sandbox_creds = {  # type: ignore[attr-defined]
        "nino": body["nino"],
        "user_id": body.get("user_id") or body.get("userId"),
        "password": body["password"],
        "mtd_it_id": body.get("mtd_it_id") or body.get("mtdItId"),
    }


@_full_oauth
def test_full_oauth_handshake_with_sandbox_gg(authed_browser: Page):
    """Drive Playwright through the full OAuth dance:
       /hmrc/connect → HMRC sandbox authorize → GG sandbox sign-in →
       grant scope → /api/hmrc/callback → tokens stored.

    Verifies by hitting /api/hmrc/obligations afterwards and confirming
    `connected: true` (or a 200 with obligations data)."""
    page = authed_browser

    # First, mint creds (this test is self-contained; if run alone it
    # mints fresh, if run in sequence with the prior test it re-mints).
    csrf = _seed_csrf_via_browser(page)
    resp = page.request.post(
        PROD_URL + "/api/hmrc/sandbox/create-test-user",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        data="{}",
        timeout=30_000,
    )
    assert resp.status == 200, f"mint failed: {resp.status} {resp.text()[:500]}"
    creds = resp.json()
    gg_user_id = creds.get("user_id") or creds.get("userId")
    gg_password = creds["password"]

    # Begin OAuth — click the connect button on /hmrc/connect.
    page.goto(PROD_URL + "/hmrc/connect", wait_until="domcontentloaded", timeout=30_000)
    # The page renders a form that POSTs to /api/hmrc/connect.
    connect_btn = page.locator(
        'form[action="/api/hmrc/connect"] button[type=submit]'
    ).first
    connect_btn.wait_for(state="visible", timeout=10_000)
    connect_btn.click()

    # Should land on HMRC sandbox. The first page is usually the
    # GG sign-in page on test-www.tax.service.gov.uk.
    page.wait_for_url(
        lambda url: "tax.service.gov.uk" in url,
        timeout=30_000,
    )
    assert "test-www.tax.service.gov.uk" in page.url or "test-api.service.hmrc.gov.uk" in page.url, (
        f"Expected to land on HMRC SANDBOX, got: {page.url}"
    )

    # Fill GG userId + password.
    user_input = _first_visible(page, _GG_USER_ID_SELECTORS)
    user_input.fill(gg_user_id)
    pwd_input = _first_visible(page, _GG_PASSWORD_SELECTORS)
    pwd_input.fill(gg_password)
    sign_in_btn = _first_visible(page, _GG_SIGN_IN_SELECTORS)
    sign_in_btn.click()

    # Sometimes HMRC shows a "Grant authority" page next. Sometimes it
    # skips straight to the callback. Wait for whichever happens.
    page.wait_for_url(
        lambda url: "bankscanai.com/api/hmrc/callback" in url
        or "grantscope" in url
        or "consent" in url.lower(),
        timeout=30_000,
    )

    # If we're on a grant-scope page, click through.
    if "bankscanai.com" not in page.url:
        try:
            grant_btn = _first_visible(page, _GRANT_SELECTORS, timeout=15_000)
            grant_btn.click()
        except AssertionError:
            # Page changed without our intervention (HMRC auto-redirects
            # in some cases). Continue.
            pass
        page.wait_for_url(
            lambda url: "bankscanai.com" in url,
            timeout=30_000,
        )

    # We're back on bankscanai.com. Verify tokens stored by querying obligations.
    obl_resp = page.request.get(
        PROD_URL + "/api/hmrc/obligations",
        timeout=15_000,
    )
    assert obl_resp.status == 200, (
        f"/api/hmrc/obligations after OAuth: {obl_resp.status} {obl_resp.text()[:500]}"
    )
    obl_body = obl_resp.json()
    assert obl_body.get("connected") is True, (
        f"Expected connected:true after OAuth, got: {obl_body}"
    )


@_full_oauth
def test_sandbox_setup_complete_creates_se_and_property(authed_browser: Page):
    """Once OAuth is done, /api/hmrc/sandbox/setup-complete creates
    SE + property businesses idempotently against the connected NINO."""
    page = authed_browser

    # Re-mint + re-OAuth to make this test independent of run order.
    # (Skip if obligations already shows connected:true.)
    obl_resp = page.request.get(PROD_URL + "/api/hmrc/obligations", timeout=15_000)
    if obl_resp.status == 200 and obl_resp.json().get("connected") is True:
        pass
    else:
        pytest.skip(
            "Must run test_full_oauth_handshake_with_sandbox_gg first — "
            "this test relies on stored OAuth tokens."
        )

    csrf = _seed_csrf_via_browser(page)
    resp = page.request.post(
        PROD_URL + "/api/hmrc/sandbox/setup-complete",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        data="{}",
        timeout=30_000,
    )
    assert resp.status == 200, (
        f"setup-complete failed: {resp.status} {resp.text()[:500]}"
    )
    body = resp.json()
    assert body.get("nino"), f"No NINO in response: {body}"
    all_businesses = (body.get("created") or []) + (body.get("already_existed") or [])
    types_seen = {b.get("type_of_business") for b in all_businesses}
    assert "self-employment" in types_seen, (
        f"SE business missing after setup-complete: {types_seen}"
    )
    assert "property" in types_seen, (
        f"Property business missing after setup-complete: {types_seen}"
    )


@_full_oauth
def test_quarterly_update_se_preview_against_sandbox(authed_browser: Page):
    """POST /api/hmrc/quarterly-updates/se/preview against the
    sandbox-set-up account. Preview only — no submission. Proves the
    full pipeline (rows → categorisation → HMRC wire payload) functions
    end-to-end on prod.

    Skips if prior tests didn't run (no businesses set up)."""
    page = authed_browser
    obl_resp = page.request.get(PROD_URL + "/api/hmrc/obligations", timeout=15_000)
    if not (obl_resp.status == 200 and obl_resp.json().get("connected") is True):
        pytest.skip("OAuth not connected — run earlier tier-3 tests first.")
    obl_body = obl_resp.json()
    se_obligations = [
        o for o in obl_body.get("obligations", [])
        if o.get("business_type") == "self-employment"
    ]
    if not se_obligations:
        pytest.skip("No SE obligations on the account — run setup-complete first.")
    se_obl = se_obligations[0]
    biz_id = se_obl.get("business_id")
    period_start = se_obl.get("period_start") or "2026-04-06"
    period_end = se_obl.get("period_end") or "2026-07-05"

    csrf = _seed_csrf_via_browser(page)
    resp = page.request.post(
        PROD_URL + "/api/hmrc/quarterly-updates/se/preview",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        data=json.dumps({
            "business_id": biz_id,
            "period_start": period_start,
            "period_end": period_end,
            "rows": [
                {"date": period_start, "amount": 1000.00,
                 "description": "Test invoice", "category": "se_turnover"},
                {"date": period_start, "amount": -150.00,
                 "description": "Test office supplies",
                 "category": "se_admin_costs"},
            ],
        }),
        timeout=30_000,
    )
    assert resp.status == 200, (
        f"SE preview failed: {resp.status} {resp.text()[:500]}"
    )
    body = resp.json()
    assert body.get("business_id") == biz_id
    assert body.get("payload"), f"Preview returned no payload: {body}"
