"""
Self-Employment quarterly update HTTP endpoints.

  POST /api/hmrc/quarterly-updates/se/preview
  POST /api/hmrc/quarterly-updates/se/submit

Thin adapter — substantive logic in `services/quarterly_updates.py`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..schemas.se_quarterly import (
    SEQuarterPreview,
    SubmitSEQuarterRequest,
    SubmitSEQuarterResponse,
)
from ..services import quarterly_updates as _service
from . import _quarterly_common as _common

logger = logging.getLogger("bankparse.hmrc.routes.quarterly_updates.se")
router = APIRouter()


async def _parse_request(request: Request) -> SubmitSEQuarterRequest:
    body = await request.json()
    try:
        return SubmitSEQuarterRequest(**(body or {}))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")


@router.post(
    "/api/hmrc/quarterly-updates/se/preview",
    response_model=SEQuarterPreview,
)
async def preview_se_quarter(request: Request) -> SEQuarterPreview:
    """Build (without submitting) the SE quarterly payload."""
    user = _common.require_user(request)
    req = await _parse_request(request)
    try:
        payload, breakdown, excluded, flagged = await _service.build_se_payload(
            rows=[r.model_dump() for r in req.rows],
            period_start=req.period_start, period_end=req.period_end,
            user_id=user["id"],
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise  # unreachable
    return SEQuarterPreview(
        business_id=req.business_id, payload=payload,
        category_breakdown=breakdown,
        excluded_rows=excluded, flagged_for_review=flagged,
    )


@router.post(
    "/api/hmrc/quarterly-updates/se/submit",
    response_model=SubmitSEQuarterResponse,
)
async def submit_se_quarter(request: Request) -> SubmitSEQuarterResponse:
    """Submit the SE quarterly update to HMRC."""
    user = _common.require_user(request)
    req = await _parse_request(request)

    try:
        payload, _, _, _ = await _service.build_se_payload(
            rows=[r.model_dump() for r in req.rows],
            period_start=req.period_start, period_end=req.period_end,
            user_id=user["id"],
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise

    # Caller-supplied idempotency key, e.g. for replay-safe background jobs.
    body = await request.json()
    idem_key = (body or {}).get("idempotency_key")

    try:
        hmrc_response, audit_id = _service.submit_se_quarter(
            user_id=user["id"], request_obj=request,
            business_id=req.business_id, payload=payload,
            idempotency_key=idem_key,
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise

    return SubmitSEQuarterResponse(
        business_id=req.business_id,
        period_start=req.period_start, period_end=req.period_end,
        hmrc_response=hmrc_response, audit_id=audit_id,
    )
