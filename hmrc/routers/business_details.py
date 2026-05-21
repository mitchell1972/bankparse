"""
HMRC Business Details HTTP endpoint — auto-discovers a user's businesses.

POST /api/hmrc/connect-businesses
  - Body: {"nino": "AB123456C"}
  - Calls HMRC's Business Details API (services/business_details)
  - Persists the discovered businesses to hmrc_connections
  - Returns the list so the UI can render confirmation immediately

Thin adapter — substantive logic lives in `services/business_details.py`.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request

from ..repositories import tokens as _tokens
from ..schemas.business_details import ConnectBusinessesResponse
from ..services import business_details as _bd_service
from ..services import client as _client

logger = logging.getLogger("bankparse.hmrc.routes.business_details")
router = APIRouter()


# UK NINO: AA 12 34 56 C (broad pattern — HMRC enforces the restricted
# leading letter pairs at the API layer).
_NINO_RE = re.compile(r"^[A-Z]{2}\d{6}[A-D]$")


def _user(request: Request) -> dict | None:
    """Lazy import to avoid circular dep on `app`."""
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


def _looks_like_nino(value: str) -> bool:
    return bool(_NINO_RE.match((value or "").upper()))


@router.post(
    "/api/hmrc/connect-businesses",
    response_model=ConnectBusinessesResponse,
)
async def connect_businesses(request: Request) -> ConnectBusinessesResponse:
    """Friendlier setup flow — user types only their NINO, we auto-discover
    every business HMRC has for them via the Business Details API and
    persist the resulting list in one step.

    Body: {"nino": "AB123456C"}
    """
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    nino = (body.get("nino") or "").strip().upper()
    if not _looks_like_nino(nino):
        raise HTTPException(
            status_code=400,
            detail="NINO must be UK format AB123456C — letter, letter, 6 digits, letter.",
        )
    if not (_tokens.get_tokens(user["id"]) or {}).get("access_token"):
        raise HTTPException(
            status_code=409,
            detail="Connect to HMRC first (/api/hmrc/connect) before discovering businesses.",
        )

    try:
        businesses = _bd_service.fetch_for_nino(
            user_id=user["id"], nino=nino, request_obj=request,
        )
    except _client.HmrcApiError as exc:
        # 404 = no businesses on this NINO yet. Persist the NINO anyway
        # so the sandbox-setup follow-up call has it; without this the
        # user gets bounced with "Need a NINO before we can create a
        # test business" even though they just typed one.
        if exc.status_code == 404:
            _bd_service.persist_nino_only(user["id"], nino)
        raise HTTPException(
            status_code=_status_for_hmrc(exc),
            detail=_friendly_detail_for_hmrc(exc),
        )
    except _client.HmrcNotConnectedError:
        raise HTTPException(
            status_code=409,
            detail="HMRC connection not found — click 'Connect to HMRC' first.",
        )

    if not businesses:
        # Empty list path — same fix as the 404 branch: persist NINO.
        _bd_service.persist_nino_only(user["id"], nino)
        raise HTTPException(
            status_code=404,
            detail=(
                "HMRC has no MTD ITSA businesses registered for that NINO. "
                "Click 'Set me up with a complete sandbox' to provision one, "
                "or register a business in your HMRC account before connecting."
            ),
        )

    _tokens.save_nino_and_businesses(
        user["id"], nino, [b.model_dump() for b in businesses],
    )
    return ConnectBusinessesResponse(
        businesses_found=len(businesses), businesses=businesses,
    )


# ---------------------------------------------------------------------------
# Friendly HMRC error mapping
# ---------------------------------------------------------------------------

def _status_for_hmrc(exc: _client.HmrcApiError) -> int:
    """Translate HMRC's status code into one our UI knows how to display."""
    if exc.status_code == 0:
        return 502
    if exc.status_code == 404:
        # 404 = "no businesses on this NINO". Not a server error; the user's
        # NINO just isn't matched. We bubble 404 so the UI can show the
        # specific MATCHING_RESOURCE_NOT_FOUND hint below.
        return 404
    return 400


def _friendly_detail_for_hmrc(exc: _client.HmrcApiError) -> str:
    """Plain-English replacement for HMRC's raw error body.

    HMRC's MATCHING_RESOURCE_NOT_FOUND is THE most common first-run failure
    on the sandbox (a freshly-created test individual has no businesses
    against the NINO yet). The raw HMRC body is opaque to non-developers —
    swap it for an actionable hint pointing at the sandbox helper route
    (when we're on sandbox) or registering a business in HMRC directly.
    """
    code = ""
    body = exc.body or {}
    if isinstance(body, dict):
        code = (body.get("code") or "").upper()

    if exc.status_code == 404 and code == "MATCHING_RESOURCE_NOT_FOUND":
        # Sandbox builds get a one-click escape hatch; production users
        # have to register the business via HMRC's own account.
        import os
        if os.environ.get("HMRC_ENV", "sandbox").lower() != "production":
            return (
                "HMRC has no MTD ITSA businesses against this NINO yet. "
                "Click 'Create sandbox test business' below to provision one, "
                "or register a business in your HMRC test account first."
            )
        return (
            "HMRC has no MTD ITSA businesses against this NINO. Register at "
            "least one self-employment or UK property business with HMRC "
            "before connecting."
        )

    if exc.status_code == 403:
        return (
            "HMRC refused the request (403). Most often this means you signed "
            "in with the wrong Government Gateway user — make sure it's the "
            "MTD-enabled one."
        )

    # Default: pass through enough detail to debug without exposing internals.
    return f"HMRC returned {exc.status_code}: {body}"
