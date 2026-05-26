"""
BankParse — Security headers middleware.

Adds the response headers that Mozilla Observatory, OWASP ZAP, and HMRC's
security questionnaire expect from a production web app:

  - Strict-Transport-Security        force HTTPS for 2 years incl. subdomains
  - X-Content-Type-Options           stop MIME sniffing
  - X-Frame-Options                  block clickjacking
  - Referrer-Policy                  don't leak origins on outbound links
  - Permissions-Policy               disable powerful APIs we don't use
  - Cross-Origin-Opener-Policy       isolate window groups
  - Cross-Origin-Resource-Policy     stop other origins embedding our assets
  - Content-Security-Policy          default-src 'self', script allowlist for
                                     googletagmanager + 'unsafe-inline' for our
                                     inline analytics bootstrap. Reported in
                                     enforce mode so violations BLOCK, not warn.

The CSP is conservative — it MUST allow:
  - inline <script> for our gtag bootstrap (we'd need a nonce/hash to drop
    'unsafe-inline', and inline-style is everywhere in templates so style-src
    must also allow 'unsafe-inline')
  - https://www.googletagmanager.com (script tag + connect for analytics beacon)
  - https://www.google-analytics.com (connect, img beacon)

It is INTENTIONALLY NOT applied to:
  - /api/stripe-webhook                 — must accept any origin, no body rewrite
  - /downloads/*                        — Excel/CSV blobs the user just generated;
                                          CSP isn't meaningful on those responses
  - /static/*                           — already isolated, served by StaticFiles
                                          (we add nosniff + COOP via this MW too,
                                          which is fine)

Header values are conservative enough that they should hold for any UK SaaS,
but the constants below are the single point of truth — bump them here, not
inline in routes.
"""

from __future__ import annotations

import os
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


# --- Header values ----------------------------------------------------------

# 2-year HSTS with includeSubDomains. `preload` is included so we can submit
# bankscanai.com to the Chrome HSTS preload list — only safe once we are sure
# we will never need a non-HTTPS subdomain.
HSTS_VALUE = "max-age=63072000; includeSubDomains; preload"

# Permissions-Policy: opt-out of every powerful API we never use. New entries
# can be added without breaking older browsers (unknown directives are ignored).
PERMISSIONS_POLICY = ", ".join(
    f"{directive}=()"
    for directive in [
        "accelerometer",
        "ambient-light-sensor",
        "autoplay",
        "battery",
        "camera",
        "display-capture",
        "document-domain",
        "encrypted-media",
        "fullscreen",
        "geolocation",
        "gyroscope",
        "hid",
        "idle-detection",
        "magnetometer",
        "microphone",
        "midi",
        "payment",
        "picture-in-picture",
        "publickey-credentials-get",
        "screen-wake-lock",
        "serial",
        "sync-xhr",
        "usb",
        "web-share",
        "xr-spatial-tracking",
    ]
)

# Form-action allowlist. CSP3 extends form-action to apply to the entire
# redirect chain triggered by a form submission, not just the initial POST
# target. The HMRC OAuth round-trip is `POST /api/hmrc/connect` → 302 →
# HMRC authorize URL → … → `GET /api/hmrc/callback`. If form-action is
# 'self' only, Chrome blocks the 302 to HMRC because it's a different
# origin, and OAuth silently breaks. Allow the HMRC API origins explicitly,
# and additionally any origin set in HMRC_BASE_URL at boot (used by tests
# pointing at a local stub, and by HMRC's own sandbox/prod swap).
_DEFAULT_HMRC_FORM_ACTION_HOSTS = (
    "https://test-api.service.hmrc.gov.uk",
    "https://api.service.hmrc.gov.uk",
)


def _form_action_value() -> str:
    """Build the `form-action` directive value at module import.

    Always includes 'self' and the two real HMRC API origins. If
    HMRC_BASE_URL is set (tests / sandbox), its scheme+host+port is added
    so the e2e stub at e.g. http://127.0.0.1:12345 is allowed too.
    """
    sources = ["'self'", *_DEFAULT_HMRC_FORM_ACTION_HOSTS]
    base = os.environ.get("HMRC_BASE_URL", "").strip()
    if base:
        from urllib.parse import urlsplit
        parts = urlsplit(base)
        if parts.scheme and parts.netloc:
            origin = f"{parts.scheme}://{parts.netloc}"
            if origin not in sources:
                sources.append(origin)
    return "form-action " + " ".join(sources)


# CSP — enforce mode. Built with:
#   - default-src 'self'      everything defaults to same-origin
#   - script-src               allow Google Tag Manager + inline (gtag bootstrap)
#   - style-src                inline styles are pervasive in our templates
#   - img-src 'self' data:    inline data: for SVG icons, plus GTM beacons
#   - connect-src             XHR/fetch — self + GA + Sentry (when configured)
#   - frame-ancestors 'none'  defence-in-depth for X-Frame-Options
#   - form-action              self + HMRC OAuth origins (form submissions
#                              redirect cross-origin during the OAuth
#                              authorize step — see _form_action_value)
#   - base-uri 'self'         defeats <base href> hijacking
#   - object-src 'none'       no Flash/Java/etc.
#   - upgrade-insecure-requests
CSP_DIRECTIVES = [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://www.googletagmanager.com https://www.google-analytics.com",
    "font-src 'self' data:",
    "connect-src 'self' https://www.google-analytics.com https://www.googletagmanager.com",
    "frame-ancestors 'none'",
    _form_action_value(),
    "base-uri 'self'",
    "object-src 'none'",
    "upgrade-insecure-requests",
]
CSP_VALUE = "; ".join(CSP_DIRECTIVES)


# --- Path exemptions --------------------------------------------------------

# Paths where we DO NOT want to inject our headers. /api/stripe-webhook only
# accepts JSON from Stripe and they verify the signature on the request body —
# we don't want to risk middleware-induced response weirdness on it. /downloads
# are user-generated Excel/CSV blobs. Static is already isolated.
_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/stripe-webhook",
    "/downloads/",
)


_PASSWORD_QUERY_RE = re.compile(r"(?:^|[?&])password=", re.IGNORECASE)


def _path_is_exempt(path: str) -> bool:
    return any(path.startswith(p) for p in _EXEMPT_PREFIXES)


# --- Middleware --------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Append the security headers above to every response that isn't exempt.

    Also redirects GET /login when a `password` query parameter is present —
    OWASP ZAP flagged the URL pattern because GET-with-password is a real
    operational hazard (proxy logs, browser history, Referer leaks) even though
    our /login route only renders a form and never auths via the query string.
    """

    async def dispatch(self, request: Request, call_next):
        # Defence: GET /login?password=... → redirect to /login with the
        # password stripped from the URL. The actual login API is POST-only
        # so this only ever happens when something rewrites the URL (form
        # autofill bug, copy-paste, etc.).
        if (
            request.method == "GET"
            and request.url.path == "/login"
            and _PASSWORD_QUERY_RE.search(request.url.query or "")
        ):
            from starlette.responses import RedirectResponse
            return RedirectResponse(url="/login", status_code=302)

        response = await call_next(request)

        if _path_is_exempt(request.url.path):
            return response

        # Set headers, but only if the route didn't already set them
        # (lets route-level overrides win — e.g. an iframe-embed endpoint
        # could relax X-Frame-Options).
        headers = response.headers
        headers.setdefault("Strict-Transport-Security", HSTS_VALUE)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        headers.setdefault("Content-Security-Policy", CSP_VALUE)

        return response


# --- Helpers used by the cookie setters --------------------------------------

def cookies_must_be_secure() -> bool:
    """
    Return True if Set-Cookie responses should set the `Secure` flag.

    Defaults to True so the wrong answer (insecure cookies in prod) is the
    one that needs to be explicitly opted in to. Local dev sets
    `COOKIES_SECURE=0` to allow http://localhost.
    """
    return os.environ.get("COOKIES_SECURE", "1").lower() not in {"0", "false", "no"}
