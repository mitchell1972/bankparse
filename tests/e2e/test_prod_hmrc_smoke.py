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


def _seed_csrf_cookie() -> str:
    """GET any page to make the server set ``bp_csrf``. Returns the cookie
    value. The CSRF middleware (csrf.py:CSRFMiddleware.dispatch) only sets
    the cookie on GET/HEAD/OPTIONS, and every state-changing POST requires
    the cookie value echoed back in the X-CSRF-Token header. Without this
    /api/login itself returns 403 'CSRF validation failed.'"""
    req = urllib.request.Request(PROD_URL + "/", method="GET")
    resp = urllib.request.urlopen(req, timeout=15)
    for header_name, header_value in resp.headers.items():
        if header_name.lower() != "set-cookie":
            continue
        pair = header_value.split(";", 1)[0]
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        if k.strip() == "bp_csrf":
            return v.strip()
    raise AssertionError("Server did not set bp_csrf on GET /")


def _login_via_json_api() -> dict[str, str]:
    """POST /api/login with the test creds. Returns the cookie jar as a
    {name: value} dict suitable for adding via Playwright's
    BrowserContext.add_cookies or urllib's Cookie header.

    Seeds a CSRF cookie first (see _seed_csrf_cookie) — the prod CSRF
    middleware rejects state-changing POSTs that don't echo bp_csrf back
    via the X-CSRF-Token header."""
    csrf_token = _seed_csrf_cookie()

    body = json.dumps({
        "email": PROD_TEST_USER_EMAIL,
        "password": PROD_TEST_USER_PASSWORD,
    }).encode()
    req = urllib.request.Request(
        PROD_URL + "/api/login",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Cookie": f"bp_csrf={csrf_token}",
            "X-CSRF-Token": csrf_token,
        },
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
    cookies: dict[str, str] = {"bp_csrf": csrf_token}
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
    # Match the sign-in button specifically — NOT a bare button[type=submit],
    # which on test-www.tax.service.gov.uk first matches the cookies-banner
    # "Accept additional cookies" button and silently breaks the flow.
    'button:has-text("Sign in")',
    'input[value="Sign in"]',
    'form[action*="sign-in"] button[type="submit"]',
    'main button[type="submit"]',
    'form button[type="submit"]:not([name="cookies"])',
]
_GRANT_SELECTORS = [
    # 2026-05-25: live sandbox button text is "Give permission".
    'button:has-text("Give permission")',
    'button:has-text("Grant authority")',
    'button:has-text("Continue")',
    'input[value="Give permission"]',
    'input[value="Grant authority"]',
    'input[value="Continue"]',
    'form[action*="grantscope"] button[type="submit"]',
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
    page.request so cookies flow into the browser context.

    Seeds bp_csrf via a GET first — without echoing the cookie back via
    X-CSRF-Token the CSRF middleware (csrf.py) returns 403 on every POST."""
    csrf = _seed_csrf_via_browser(page)
    resp = page.request.post(
        PROD_URL + "/api/login",
        data=json.dumps({
            "email": PROD_TEST_USER_EMAIL,
            "password": PROD_TEST_USER_PASSWORD,
        }),
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
        },
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


def _complete_oauth_handshake(page: Page) -> dict:
    """Mint a sandbox individual, drive the full OAuth dance, and return
    the minted credentials dict. Idempotent: if the user is already
    connected this short-circuits to a no-op + returns ``{}``.

    Extracted so each tier-3 test can re-establish OAuth at the start of
    its run — the ``authed_browser`` fixture disconnects on teardown, so
    later tests can't rely on tokens left by earlier ones."""
    # Short-circuit if already connected.
    obl_resp = page.request.get(PROD_URL + "/api/hmrc/obligations", timeout=15_000)
    if obl_resp.status == 200 and obl_resp.json().get("connected") is True:
        return {}

    csrf = _seed_csrf_via_browser(page)
    # Disconnect any half-OAuth from a prior interrupted run.
    page.request.post(
        PROD_URL + "/api/hmrc/disconnect",
        headers={"X-CSRF-Token": csrf},
        timeout=10_000,
    )
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

    page.goto(PROD_URL + "/hmrc/connect", wait_until="domcontentloaded", timeout=30_000)
    connect_btn = page.locator(
        'form[action="/api/hmrc/connect"] button[type=submit]'
    ).first
    connect_btn.wait_for(state="visible", timeout=10_000)
    connect_btn.click()

    page.wait_for_url(lambda url: "tax.service.gov.uk" in url, timeout=30_000)
    assert (
        "test-www.tax.service.gov.uk" in page.url
        or "test-api.service.hmrc.gov.uk" in page.url
    ), f"Expected to land on HMRC SANDBOX, got: {page.url}"

    # Walk past interstitials until sign-in form (password input) appears.
    for _ in range(4):
        if page.locator('input[type="password"]').count() > 0:
            break
        gateway_link = page.locator(
            'a:has-text("Sign in to the HMRC online service"), '
            'a:has-text("Sign in"), button:has-text("Sign in"), '
            'a:has-text("Continue"), button:has-text("Continue")'
        ).first
        gateway_link.wait_for(state="visible", timeout=10_000)
        gateway_link.click()
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
    else:
        raise AssertionError(
            f"Walked 4 interstitials and never reached a password input. "
            f"Now on {page.url!r}."
        )

    user_input = _first_visible(page, _GG_USER_ID_SELECTORS)
    user_input.fill(gg_user_id)
    pwd_input = _first_visible(page, _GG_PASSWORD_SELECTORS)
    pwd_input.fill(gg_password)
    sign_in_btn = _first_visible(page, _GG_SIGN_IN_SELECTORS)
    sign_in_btn.click()

    def _signed_in(url: str) -> bool:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.netloc.endswith("bankscanai.com"):
            return True
        path = parsed.path or ""
        return path.startswith("/oauth/grantscope") or "consent" in path.lower()

    page.wait_for_url(_signed_in, timeout=30_000)

    if "bankscanai.com" not in page.url:
        grant_btn = page.locator(
            'form[action*="grantscope"] button[type="submit"], '
            'button:has-text("Give permission"), '
            'button:has-text("Grant authority")'
        ).first
        try:
            grant_btn.wait_for(state="visible", timeout=15_000)
            grant_btn.click()
        except Exception:
            pass
        import time as _time
        deadline = _time.monotonic() + 45.0
        while _time.monotonic() < deadline:
            if "bankscanai.com" in page.url:
                break
            _time.sleep(0.5)
        else:
            raise AssertionError(
                f"After grant click page.url never reached bankscanai.com "
                f"within 45s. Stuck on: {page.url}"
            )
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

    # After OAuth, persist the NINO against the user's tokens. The
    # downstream sandbox/setup-complete + quarterly-update endpoints
    # require info["nino"] to be set — otherwise they 409 with
    # "Enter your sandbox NINO in the dashboard and click
    # 'Discover my businesses' once before using this setup."
    # POST /api/hmrc/connect-businesses also persists the NINO via the
    # 404 fallback (the freshly-minted NINO has no businesses yet),
    # so we expect 404 from connect-businesses — that's fine.
    nino = creds.get("nino")
    if nino:
        csrf_after = _seed_csrf_via_browser(page)
        cb_resp = page.request.post(
            PROD_URL + "/api/hmrc/connect-businesses",
            headers={"X-CSRF-Token": csrf_after, "Content-Type": "application/json"},
            data=json.dumps({"nino": nino}),
            timeout=30_000,
        )
        assert cb_resp.status in (200, 404), (
            f"connect-businesses returned {cb_resp.status} {cb_resp.text()[:300]}. "
            f"Expected 200 (existing businesses) or 404 (no businesses yet, "
            f"but NINO persisted)."
        )

    return creds


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

    # Should land on HMRC sandbox. As of 2026-05-25 the live flow is:
    #   1) /oauth/start             — informational, "Continue" link
    #   2) /oauth/whatYouWillNeed   — "Sign in to the HMRC online service" link
    #   3) /api-test-login/sign-in  — the real GG sandbox sign-in form
    #   4) /oauth/grantscope        — "Grant authority" button (sometimes
    #                                  skipped if HMRC auto-redirects)
    # The interstitial labels can change without notice — match on the
    # presence of a password input to detect the sign-in page rather than
    # by URL.
    page.wait_for_url(
        lambda url: "tax.service.gov.uk" in url,
        timeout=30_000,
    )
    assert "test-www.tax.service.gov.uk" in page.url or "test-api.service.hmrc.gov.uk" in page.url, (
        f"Expected to land on HMRC SANDBOX, got: {page.url}"
    )

    # Walk through up to 4 interstitial pages until we reach a page with
    # a password input (the GG sign-in form). Click the first visible
    # Continue / Sign in link/button on each step. Bail with a useful
    # message if we never hit the sign-in form.
    for _ in range(4):
        if page.locator('input[type="password"]').count() > 0:
            break
        gateway_link = page.locator(
            'a:has-text("Sign in to the HMRC online service"), '
            'a:has-text("Sign in"), button:has-text("Sign in"), '
            'a:has-text("Continue"), button:has-text("Continue")'
        ).first
        try:
            gateway_link.wait_for(state="visible", timeout=10_000)
        except Exception as e:
            raise AssertionError(
                f"On {page.url!r} could not find Continue/Sign-in control: {e}. "
                f"HMRC interstitial UI changed — update the gateway_link "
                f"selector list in test_full_oauth_handshake_with_sandbox_gg."
            )
        gateway_link.click()
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
    else:
        raise AssertionError(
            f"Walked 4 interstitials and never reached a password input. "
            f"Now on {page.url!r}."
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
    # Careful: the sign-in page itself has ``continue=%2Foauth%2Fgrantscope``
    # in its query string, so a naive ``"grantscope" in url`` match resolves
    # IMMEDIATELY (before any navigation). Match on the path component only.
    def _signed_in(url: str) -> bool:
        # Strip the query string before checking — sign-in page has
        # /oauth/grantscope encoded in ?continue= and would false-match.
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.netloc.endswith("bankscanai.com"):
            return True
        path = parsed.path or ""
        return path.startswith("/oauth/grantscope") or "consent" in path.lower()

    page.wait_for_url(_signed_in, timeout=30_000)

    # If we're on a grant-scope page, click through.
    # Note: ``page.wait_for_url`` uses Playwright's expect_navigation
    # semantics and can race with already-completed navigations from a
    # form-POST click. Poll page.url directly instead.
    if "bankscanai.com" not in page.url:
        # Prefer a single very specific selector that can only match the
        # grant button — _first_visible races several selectors and can
        # pick the cookies-banner button by accident.
        grant_btn = page.locator(
            'form[action*="grantscope"] button[type="submit"], '
            'button:has-text("Give permission"), '
            'button:has-text("Grant authority")'
        ).first
        try:
            grant_btn.wait_for(state="visible", timeout=15_000)
            grant_btn.click()
        except Exception:
            # Page might have auto-redirected without showing the form.
            pass
        # Poll for the redirect back to our origin. The callback handler
        # 302s to /hmrc/connect?status=ok, so we accept either URL.
        import time as _time
        deadline = _time.monotonic() + 45.0
        while _time.monotonic() < deadline:
            if "bankscanai.com" in page.url:
                break
            _time.sleep(0.5)
        else:
            raise AssertionError(
                f"After clicking Give permission, page.url never reached "
                f"bankscanai.com within 45s. Stuck on: {page.url}"
            )
        # Settle any in-flight redirect on bankscanai.com itself.
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

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
    SE + property businesses idempotently against the connected NINO.

    Self-contained: re-establishes OAuth via _complete_oauth_handshake
    rather than relying on tokens from a prior test (the authed_browser
    fixture disconnects on teardown)."""
    page = authed_browser

    _complete_oauth_handshake(page)

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

    Self-contained: re-establishes OAuth, runs setup-complete to create
    SE + property businesses, then submits a preview."""
    page = authed_browser

    creds = _complete_oauth_handshake(page)
    minted_nino = creds.get("nino")

    # Ensure SE + property businesses exist on this account.
    csrf = _seed_csrf_via_browser(page)
    setup_resp = page.request.post(
        PROD_URL + "/api/hmrc/sandbox/setup-complete",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        data="{}",
        timeout=30_000,
    )
    assert setup_resp.status == 200, (
        f"setup-complete failed before preview: {setup_resp.status} "
        f"{setup_resp.text()[:300]}"
    )
    setup_body = setup_resp.json()
    all_setup_biz = (setup_body.get("created") or []) + (setup_body.get("already_existed") or [])
    se_setup_biz = [b for b in all_setup_biz if (b.get("type_of_business") or "").lower() == "self-employment"]
    assert se_setup_biz, f"setup-complete did not create an SE business: {setup_body}"

    # Re-run connect-businesses so the SE + property businessIds get
    # persisted to info["businesses"]. setup-complete creates them on
    # HMRC's side but our local copy is still empty from the earlier
    # connect-businesses 404 path.
    if minted_nino:
        cb_resp = page.request.post(
            PROD_URL + "/api/hmrc/connect-businesses",
            headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
            data=json.dumps({"nino": minted_nino}),
            timeout=30_000,
        )
        # Should be 200 now (businesses exist); accept 404 too in case of
        # HMRC sandbox propagation delay — we'll fall back to the setup
        # response's business_id below.
        assert cb_resp.status in (200, 404), (
            f"connect-businesses (post-setup) returned {cb_resp.status} "
            f"{cb_resp.text()[:300]}"
        )

    obl_resp = page.request.get(PROD_URL + "/api/hmrc/obligations", timeout=15_000)
    assert obl_resp.status == 200, (
        f"obligations failed: {obl_resp.status} {obl_resp.text()[:300]}"
    )
    obl_body = obl_resp.json()
    se_obligations = [
        o for o in obl_body.get("obligations", [])
        if o.get("business_type") == "self-employment"
    ]
    if se_obligations:
        se_obl = se_obligations[0]
        biz_id = se_obl.get("business_id")
        period_start = se_obl.get("period_start") or "2026-04-06"
        period_end = se_obl.get("period_end") or "2026-07-05"
    else:
        # Fall back to the businessId from the setup-complete response and
        # the default first-quarter window. Obligations may not be
        # propagated yet on the sandbox immediately after business
        # creation — the preview endpoint doesn't care, it just wants
        # a valid business_id.
        biz_id = se_setup_biz[0].get("business_id") or se_setup_biz[0].get("businessId")
        period_start = "2026-04-06"
        period_end = "2026-07-05"
        assert biz_id, (
            f"No SE business_id available from either obligations or "
            f"setup-complete: obl={obl_body} setup={setup_body}"
        )

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
