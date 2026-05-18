"""
HMRC categorisation HTTP endpoints — thin adapter over the service layer.

Everything substantive lives in `hmrc/services/categorisation.py`:
  - resolution-order policy
  - parallel batching
  - cache lookup + write
  - rule fallback

This file does three things only:
  1. Validate the request via pydantic (`CategoriseRequest` etc.)
  2. Authenticate + extract the user
  3. Hand off to the service and return its response

Keeping this file small and dumb is the whole point — see PR description.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..repositories import categorisation_events as _events
from ..repositories import overrides as _overrides
from ..schemas.categorise import (
    CategoriseRequest,
    CategoriseResponse,
    OverrideRequest,
    OverrideResponse,
    SummaryResponse,
)
from ..services import categorisation as _service

logger = logging.getLogger("bankparse.hmrc.routes.categorise")
router = APIRouter()


def _user(request: Request) -> dict | None:
    """Lazy-import the auth helper so we don't take a hard dep on `app` at
    module import time (would create a circular import)."""
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


def _normalise_business_type(raw: str | None) -> str:
    if (raw or "").lower() in ("property", "uk-property", "ukproperty", "landlord"):
        return "property"
    return "se"


def _parse_request(body: dict) -> CategoriseRequest:
    """Coerce the legacy alias values (e.g. 'landlord') before pydantic
    validation, since the schema only accepts 'se' | 'property'."""
    bt = _normalise_business_type(body.get("business_type"))
    rows = body.get("rows") or []
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="`rows` must be a list.")
    return CategoriseRequest(business_type=bt, rows=rows)


@router.post("/api/hmrc/categorise", response_model=CategoriseResponse)
async def categorise(request: Request) -> CategoriseResponse:
    """AI-first categorisation. See `services/categorisation.py` for the
    resolution order."""
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    req = _parse_request(body)

    resp, metrics = await _service.resolve(req, user_id=user["id"])

    # Best-effort observability — never let metric persistence break the
    # response. We just want "how often does the cache hit?" to be a SQL
    # question instead of a guess.
    try:
        _events.record(user_id=user["id"], business_type=req.business_type, metrics=metrics)
    except Exception:
        logger.exception("Failed to record categorisation_event")

    return resp


@router.post("/api/hmrc/categorise/override", response_model=OverrideResponse)
async def save_override(request: Request) -> OverrideResponse:
    """Save a manual category correction (per-user). NOT written into the
    global cache — one user's preference isn't necessarily right for everyone
    (one sole trader treats AMAZON as admin, another as cost-of-goods)."""
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    body = await request.json()
    body["business_type"] = _normalise_business_type(body.get("business_type"))
    try:
        ov = OverrideRequest(**body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid override payload: {e}")
    _overrides.save(user["id"], ov.description, ov.business_type, ov.category)
    return OverrideResponse(merchant_key=_overrides.merchant_key(ov.description))


@router.post("/api/hmrc/categorise/summary", response_model=SummaryResponse)
async def summary(request: Request) -> SummaryResponse:
    """Aggregate rows by HMRC category. Re-uses the resolve() path so totals
    match what the user sees in the table — no second categorisation path."""
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    req = _parse_request(body)
    resp, metrics = await _service.summarise(req, user_id=user["id"])

    try:
        _events.record(user_id=user["id"], business_type=req.business_type, metrics=metrics)
    except Exception:
        logger.exception("Failed to record categorisation_event (summary)")

    return resp
