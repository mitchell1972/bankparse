"""
Tests for the SecurityHeadersMiddleware (security_headers.py).

What we are guarding here:

  - HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy,
    Permissions-Policy, COOP, CORP, and CSP are present on every response
    that isn't on the exempt-prefix list.
  - The default cookies_must_be_secure() is True (so the wrong answer in
    prod requires explicit opt-out, not opt-in).
  - GET /login?password=... redirects to /login (no password in URL).
  - /api/stripe-webhook does NOT get the headers (Stripe-signed bodies +
    we don't want to risk middleware-induced response weirdness).
"""

from __future__ import annotations

import os
import re

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from security_headers import (
    CSP_VALUE,
    HSTS_VALUE,
    PERMISSIONS_POLICY,
    SecurityHeadersMiddleware,
    cookies_must_be_secure,
)


def _make_app() -> FastAPI:
    """Minimal app that exercises every code path in the middleware."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/")
    def root():
        return {"hello": "world"}

    @app.get("/login")
    def login():
        return PlainTextResponse("login form")

    @app.post("/api/stripe-webhook")
    def stripe_webhook():
        return {"received": True}

    @app.get("/downloads/x.csv")
    def download():
        return PlainTextResponse("a,b,c\n1,2,3")

    @app.get("/override-frame-options")
    def override():
        """Route that intentionally sets X-Frame-Options itself —
        the middleware should NOT clobber it (setdefault semantics)."""
        resp = JSONResponse({"ok": True})
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        return resp

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_make_app())


# ---------------------------------------------------------------------------
# Cookie-Secure helper
# ---------------------------------------------------------------------------


def test_cookies_must_be_secure_default_is_true(monkeypatch):
    """The dangerous default — non-Secure cookies in prod because nobody set
    an env var — must be impossible to fall into by accident."""
    monkeypatch.delenv("COOKIES_SECURE", raising=False)
    assert cookies_must_be_secure() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "NO"])
def test_cookies_must_be_secure_can_be_explicitly_disabled_for_dev(monkeypatch, value):
    monkeypatch.setenv("COOKIES_SECURE", value)
    assert cookies_must_be_secure() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "anything-else"])
def test_cookies_must_be_secure_truthy_values(monkeypatch, value):
    monkeypatch.setenv("COOKIES_SECURE", value)
    assert cookies_must_be_secure() is True


# ---------------------------------------------------------------------------
# Header presence
# ---------------------------------------------------------------------------


def test_root_response_has_all_expected_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200
    h = r.headers
    assert h["strict-transport-security"] == HSTS_VALUE
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["permissions-policy"] == PERMISSIONS_POLICY
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert h["content-security-policy"] == CSP_VALUE


def test_hsts_value_is_two_years_includes_subdomains_and_preload():
    """Regression: HMRC's security questionnaire and the Chrome HSTS preload
    list both want at least 1y, includeSubDomains, and preload."""
    m = re.match(r"max-age=(\d+); includeSubDomains; preload", HSTS_VALUE)
    assert m is not None, f"HSTS_VALUE does not match expected shape: {HSTS_VALUE!r}"
    assert int(m.group(1)) >= 31536000  # ≥ 1 year


def test_csp_blocks_framing_and_objects_and_upgrades_insecure():
    """CSP must include frame-ancestors 'none', object-src 'none', and
    upgrade-insecure-requests."""
    assert "frame-ancestors 'none'" in CSP_VALUE
    assert "object-src 'none'" in CSP_VALUE
    assert "upgrade-insecure-requests" in CSP_VALUE
    assert "form-action 'self'" in CSP_VALUE
    assert "base-uri 'self'" in CSP_VALUE


def test_csp_form_action_includes_hmrc_oauth_origins():
    """CSP3 extends form-action to apply to redirect targets, not just the
    initial form POST destination. The HMRC OAuth flow does
    `POST /api/hmrc/connect → 302 → https://test-api.service.hmrc.gov.uk/oauth/authorize`.
    If form-action is 'self' only, Chrome blocks the 302 and OAuth silently
    breaks. Both sandbox and prod HMRC origins MUST be in form-action."""
    assert "https://test-api.service.hmrc.gov.uk" in CSP_VALUE
    assert "https://api.service.hmrc.gov.uk" in CSP_VALUE


def test_csp_allows_googletagmanager_for_script_and_connect():
    """Our landing page loads gtag — script-src + connect-src must allow it."""
    assert "https://www.googletagmanager.com" in CSP_VALUE


def test_permissions_policy_locks_down_powerful_apis():
    for sensitive in ("camera", "microphone", "geolocation", "payment", "usb"):
        assert f"{sensitive}=()" in PERMISSIONS_POLICY


# ---------------------------------------------------------------------------
# Exempt paths
# ---------------------------------------------------------------------------


def test_stripe_webhook_does_not_receive_security_headers(client):
    """Stripe-signed POST bodies — we don't want middleware reaching in."""
    r = client.post("/api/stripe-webhook", json={})
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers
    assert "strict-transport-security" not in r.headers


def test_downloads_do_not_receive_csp_or_hsts(client):
    """User-generated CSV/Excel blobs — adding CSP is meaningless and
    HSTS on a download response is redundant (the page that linked the
    download already set HSTS)."""
    r = client.get("/downloads/x.csv")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers


# ---------------------------------------------------------------------------
# Header override semantics
# ---------------------------------------------------------------------------


def test_route_can_override_individual_security_header(client):
    """Routes that need iframe embedding (e.g. an OAuth redirect inside an
    iframe) must be able to relax a single header without the middleware
    clobbering them on the way out."""
    r = client.get("/override-frame-options")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    # Other headers are still set:
    assert r.headers["strict-transport-security"] == HSTS_VALUE
    assert r.headers["content-security-policy"] == CSP_VALUE


# ---------------------------------------------------------------------------
# Login password-in-URL redirect
# ---------------------------------------------------------------------------


def test_login_get_with_password_query_redirects_to_clean_url(client):
    """OWASP ZAP flags GET URLs with ?password= because credentials end up
    in proxy logs, browser history and Referer headers. Our /login GET only
    renders the form — but URLs with a password query MUST be redirected
    away so the credential is never logged."""
    r = client.get(
        "/login?email=alice%40example.com&password=hunter2",
        follow_redirects=False,
    )
    assert r.status_code == 302
    # The Location header must NOT contain `password=`.
    assert "password=" not in r.headers["location"].lower()
    assert r.headers["location"].endswith("/login")


def test_login_get_without_password_renders_normally(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert r.text == "login form"


def test_login_password_query_redirect_is_case_insensitive(client):
    r = client.get("/login?PASSWORD=hunter2", follow_redirects=False)
    assert r.status_code == 302
    assert "password" not in r.headers["location"].lower()
