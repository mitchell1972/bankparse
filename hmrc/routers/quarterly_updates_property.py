"""
UK Property quarterly update HTTP endpoints.

  POST /api/hmrc/quarterly-updates/property/preview
  POST /api/hmrc/quarterly-updates/property/submit

Mirrors `quarterly_updates_se.py` — the two streams are separate router
files so each stays well under the 150-line architecture cap.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..schemas.property_quarterly import (
    PropertyQuarterPreview,
    SubmitPropertyQuarterRequest,
    SubmitPropertyQuarterResponse,
)
from ..services import quarterly_updates as _service
from . import _quarterly_common as _common

logger = logging.getLogger("bankparse.hmrc.routes.quarterly_updates.property")
router = APIRouter()


async def _parse_request(request: Request) -> SubmitPropertyQuarterRequest:
    body = await request.json()
    try:
        return SubmitPropertyQuarterRequest(**(body or {}))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")


@router.post(
    "/api/hmrc/quarterly-updates/property/preview",
    response_model=PropertyQuarterPreview,
)
async def preview_property_quarter(request: Request) -> PropertyQuarterPreview:
    """Build (without submitting) the Property quarterly payload."""
    user = _common.require_user(request)
    req = await _parse_request(request)
    try:
        payload, breakdown, excluded, flagged = await _service.build_property_payload(
            rows=[r.model_dump() for r in req.rows],
            period_start=req.period_start, period_end=req.period_end,
            user_id=user["id"],
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise
    return PropertyQuarterPreview(
        business_id=req.business_id, payload=payload,
        category_breakdown=breakdown,
        excluded_rows=excluded, flagged_for_review=flagged,
    )


@router.post(
    "/api/hmrc/quarterly-updates/property/submit",
    response_model=SubmitPropertyQuarterResponse,
)
async def submit_property_quarter(request: Request) -> SubmitPropertyQuarterResponse:
    """Submit the UK Property quarterly update to HMRC."""
    user = _common.require_user(request)
    req = await _parse_request(request)

    try:
        payload, _, _, _ = await _service.build_property_payload(
            rows=[r.model_dump() for r in req.rows],
            period_start=req.period_start, period_end=req.period_end,
            user_id=user["id"],
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise

    body = await request.json()
    idem_key = (body or {}).get("idempotency_key")

    try:
        hmrc_response, audit_id = _service.submit_property_quarter(
            user_id=user["id"], request_obj=request,
            business_id=req.business_id, payload=payload,
            idempotency_key=idem_key,
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise

    return SubmitPropertyQuarterResponse(
        business_id=req.business_id,
        period_start=req.period_start, period_end=req.period_end,
        hmrc_response=hmrc_response, audit_id=audit_id,
    )
