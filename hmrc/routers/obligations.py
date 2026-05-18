"""
HMRC Obligations HTTP endpoint — thin adapter over the service layer.

The substantive work (calling HMRC, parsing responses, computing
"due in N days" labels, falling back to the demo fixture) lives in
`hmrc/services/obligations.py`. This file just:

  1. Authenticates the user
  2. Calls the service
  3. Returns the JSON

We also expose POST /api/hmrc/obligations/business-setup so the user (or a
test) can save their NINO + business IDs after OAuth completes — without
that data we can't construct the real HMRC URL, so the service stays in
demo mode.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..repositories import tokens as _tokens
from ..schemas.obligations import ObligationsResponse
from ..services import obligations as _service

logger = logging.getLogger("bankparse.hmrc.routes.obligations")
router = APIRouter()


def _user(request: Request) -> dict | None:
    """Lazy-import to avoid a circular dependency with `app`."""
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


@router.get("/api/hmrc/obligations", response_model=ObligationsResponse)
async def get_obligations(request: Request) -> ObligationsResponse:
    """Return the user's HMRC deadlines for the dashboard panel.

    Always returns 200 (never an HMRC-shaped error) — the response itself
    carries `connected` / `demo` / `error` flags so the UI can render the
    right state without parsing exceptions.
    """
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return _service.fetch_for_user(user_id=user["id"], request_obj=request)


@router.post("/api/hmrc/obligations/business-setup")
async def save_business_setup(request: Request):
    """Persist the user's NINO + business IDs so we can hit the real
    HMRC Obligations endpoint.

    Body:
        {
          "nino": "AB123456C",
          "businesses": [
            {"business_id": "XAIS00000000001",
             "type_of_business": "self-employment",
             "label": "Mitoba sole trader"},
            {"business_id": "XPIS00000000002",
             "type_of_business": "property",
             "label": "Ipswich SA portfolio"}
          ]
        }
    """
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    nino = (body.get("nino") or "").strip().upper()
    businesses = body.get("businesses") or []

    if not _looks_like_nino(nino):
        raise HTTPException(
            status_code=400,
            detail="NINO must be UK format AB123456C — letter, letter, 6 digits, letter.",
        )
    if not isinstance(businesses, list):
        raise HTTPException(status_code=400, detail="`businesses` must be a list.")

    cleaned = []
    for b in businesses:
        if not isinstance(b, dict):
            continue
        bid = (b.get("business_id") or "").strip()
        tob = (b.get("type_of_business") or "").strip()
        label = (b.get("label") or "").strip() or None
        if not bid or not tob:
            continue
        cleaned.append({"business_id": bid, "type_of_business": tob, "label": label})

    if not cleaned:
        raise HTTPException(
            status_code=400,
            detail="Need at least one business with `business_id` + `type_of_business`.",
        )

    if not (_tokens.get_tokens(user["id"]) or {}).get("access_token"):
        raise HTTPException(
            status_code=409,
            detail="Connect to HMRC first (/api/hmrc/connect) before saving business setup.",
        )

    _tokens.save_nino_and_businesses(user["id"], nino, cleaned)
    return {"status": "ok", "businesses_saved": len(cleaned)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re as _re

# UK NINO: AA 12 34 56 C, where the leading two letters are a restricted set,
# but we accept the broad pattern here and let HMRC reject if needed.
_NINO_RE = _re.compile(r"^[A-Z]{2}\d{6}[A-D]$")


def _looks_like_nino(value: str) -> bool:
    return bool(_NINO_RE.match((value or "").upper()))
