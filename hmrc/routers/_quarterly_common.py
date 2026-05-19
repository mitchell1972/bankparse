"""
Shared helpers for the quarterly-update routers.

Both `quarterly_updates_se` and `quarterly_updates_property` are thin
adapters with the same shape:

  1. Authenticate the user.
  2. Validate the request body.
  3. Build the HMRC payload via the service.
  4. (Submit only) POST to HMRC, return their response + audit id.

Step 4's error translation is identical between the two streams, so it
lives here.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..services import client as _client
from ..services import quarterly_updates as _service


def user(request: Request) -> dict | None:
    """Lazy auth lookup — avoids a circular import on `app`."""
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


def require_user(request: Request) -> dict:
    u = user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return u


def wrap_hmrc_error(exc: Exception):
    """Translate domain exceptions into HTTP responses the UI can render.

    HMRC's quarterly endpoints have well-known error codes
    (RULE_DUPLICATE_SUBMISSION, RULE_PERIOD_NOT_ENDED,
    RULE_BUSINESS_VALIDATION_FAILURE). We pass status + body through
    verbatim so the dashboard can render actionable text.
    """
    if isinstance(exc, _service.NinoNotConfiguredError):
        raise HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, _client.HmrcNotConnectedError):
        raise HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, _client.HmrcApiError):
        status = 502 if exc.status_code == 0 else 400
        raise HTTPException(
            status_code=status,
            detail=f"HMRC returned {exc.status_code}: {exc.body}",
        )
    raise exc  # genuine bug -> default 500 handler
