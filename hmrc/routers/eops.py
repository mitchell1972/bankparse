"""
End of Period Statement HTTP endpoint.

  POST /api/hmrc/eops/submit

The user confirms their quarterly numbers for a business are correct and
should be finalised. One EOPS per business per tax year.

Thin adapter — logic in `services/annual.py`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..schemas.annual import SubmitEopsRequest, SubmitEopsResponse
from ..services import annual as _service
from . import _quarterly_common as _common

logger = logging.getLogger("bankparse.hmrc.routes.eops")
router = APIRouter()


async def _parse_request(request: Request) -> SubmitEopsRequest:
    body = await request.json()
    try:
        return SubmitEopsRequest(**(body or {}))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")


@router.post("/api/hmrc/eops/submit", response_model=SubmitEopsResponse)
async def submit_eops(request: Request) -> SubmitEopsResponse:
    """Submit the End of Period Statement for one business."""
    user = _common.require_user(request)
    req = await _parse_request(request)

    body = await request.json()
    idem_key = (body or {}).get("idempotency_key")

    try:
        hmrc_response, audit_id = _service.submit_eops(
            user_id=user["id"], request_obj=request,
            business_id=req.business_id,
            type_of_business=req.type_of_business,
            period_start=req.period_start, period_end=req.period_end,
            idempotency_key=idem_key,
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise

    return SubmitEopsResponse(
        business_id=req.business_id,
        period_start=req.period_start, period_end=req.period_end,
        hmrc_response=hmrc_response, audit_id=audit_id,
    )
