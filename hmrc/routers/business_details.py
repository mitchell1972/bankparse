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
        # Pass HMRC's status through so the UI can render a useful hint
        # (e.g. "NINO not registered for MTD ITSA").
        raise HTTPException(
            status_code=502 if exc.status_code == 0 else 400,
            detail=f"HMRC returned {exc.status_code}: {exc.body}",
        )
    except _client.HmrcNotConnectedError:
        raise HTTPException(
            status_code=409,
            detail="HMRC connection not found — click 'Connect to HMRC' first.",
        )

    if not businesses:
        raise HTTPException(
            status_code=404,
            detail=(
                "HMRC has no MTD ITSA businesses registered for that NINO. "
                "Register at least one self-employment or property business in "
                "your HMRC account before connecting."
            ),
        )

    _tokens.save_nino_and_businesses(
        user["id"], nino, [b.model_dump() for b in businesses],
    )
    return ConnectBusinessesResponse(
        businesses_found=len(businesses), businesses=businesses,
    )
