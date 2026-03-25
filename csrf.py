"""
BankParse — CSRF Protection (double-submit cookie pattern)

The browser receives a `bp_csrf` cookie (readable by JS because httponly=False).
Every POST request must echo that value back in the `X-CSRF-Token` header.
The middleware compares the two using hmac.compare_digest to prevent timing attacks.
"""

import hmac
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

IS_PRODUCTION = os.environ.get("ENVIRONMENT", "development") == "production"

CSRF_COOKIE = "bp_csrf"
CSRF_HEADER = "X-CSRF-Token"
CSRF_MAX_AGE = 60 * 60 * 24 * 30  # 30 days — matches session cookie

# Paths that skip CSRF validation (they use their own auth mechanism)
CSRF_EXEMPT_PATHS = {"/api/stripe-webhook", "/api/web-vitals", "/api/indexnow"}


def generate_csrf_token() -> str:
    """Return a cryptographically random URL-safe token."""
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response, token: str):
    """Set the bp_csrf cookie on a response. httponly=False so JS can read it."""
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        max_age=CSRF_MAX_AGE,
        httponly=False,
        samesite="strict",
        secure=IS_PRODUCTION,
    )
    return response


def validate_csrf(request: Request) -> bool:
    """Compare the csrf cookie value against the X-CSRF-Token header (timing-safe)."""
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get(CSRF_HEADER, "")
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    Double-submit cookie CSRF middleware.

    GET/HEAD/OPTIONS: if no csrf cookie exists, generate one and set it.
    POST/PUT/PATCH/DELETE: validate the csrf token. Return 403 on mismatch.
    """

    async def dispatch(self, request: Request, call_next):
        method = request.method.upper()

        if method in ("GET", "HEAD", "OPTIONS"):
            response = await call_next(request)
            # Ensure a csrf cookie is always present
            if not request.cookies.get(CSRF_COOKIE):
                token = generate_csrf_token()
                set_csrf_cookie(response, token)
            return response

        # State-changing methods — validate CSRF (skip exempt paths)
        if request.url.path in CSRF_EXEMPT_PATHS:
            return await call_next(request)

        if not validate_csrf(request):
            return JSONResponse(
                {"detail": "CSRF validation failed."},
                status_code=403,
            )

        return await call_next(request)
