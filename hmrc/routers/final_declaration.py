"""
Final Declaration HTTP endpoint — the annual tax return submit.

  POST /api/hmrc/final-declaration/submit

This is the action that REPLACES the old Self Assessment tax return.
After triggering + reviewing the calculation, the user submits this once
per tax year and they're done.

The request body requires `finalised: true` — pydantic enforces it as a
Literal so a misclicked request without explicit confirmation can't get
through.

Thin adapter — logic in `services/annual.py`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..schemas.annual import (
    SubmitFinalDeclarationRequest,
    SubmitFinalDeclarationResponse,
)
from ..services import annual as _service
from . import _quarterly_common as _common

logger = logging.getLogger("bankparse.hmrc.routes.final_declaration")
router = APIRouter()


async def _parse_request(request: Request) -> SubmitFinalDeclarationRequest:
    body = await request.json()
    try:
        return SubmitFinalDeclarationRequest(**(body or {}))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")


@router.post(
    "/api/hmrc/final-declaration/submit",
    response_model=SubmitFinalDeclarationResponse,
)
async def submit_final_declaration(request: Request) -> SubmitFinalDeclarationResponse:
    """Submit the annual final declaration — the actual MTD tax return."""
    user = _common.require_user(request)
    req = await _parse_request(request)

    body = await request.json()
    idem_key = (body or {}).get("idempotency_key")

    try:
        hmrc_response, audit_id = _service.submit_final_declaration(
            user_id=user["id"], request_obj=request,
            tax_year=req.tax_year, calculation_id=req.calculation_id,
            idempotency_key=idem_key,
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise

    return SubmitFinalDeclarationResponse(
        tax_year=req.tax_year,
        calculation_id=req.calculation_id,
        hmrc_response=hmrc_response, audit_id=audit_id,
    )
