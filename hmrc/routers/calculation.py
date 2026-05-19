"""
Tax Calculation HTTP endpoints — two operations on HMRC's Calculations API.

  POST /api/hmrc/calculation/trigger     - tell HMRC to compute the tax bill
  POST /api/hmrc/calculation/get         - fetch the result (POST so we can
                                            pass tax_year + calc_id in JSON)

The trigger call returns a calculationId immediately; the actual numbers
take a few seconds to populate. Caller polls `/get` until HMRC's body
carries the calculation details.

Thin adapter — logic in `services/annual.py`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..schemas.annual import (
    GetCalculationRequest,
    TaxCalculationSummary,
    TriggerCalculationRequest,
    TriggerCalculationResponse,
)
from ..services import annual as _service
from . import _quarterly_common as _common

logger = logging.getLogger("bankparse.hmrc.routes.calculation")
router = APIRouter()


async def _parse_trigger(request: Request) -> TriggerCalculationRequest:
    body = await request.json()
    try:
        return TriggerCalculationRequest(**(body or {}))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")


async def _parse_get(request: Request) -> GetCalculationRequest:
    body = await request.json()
    try:
        return GetCalculationRequest(**(body or {}))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")


@router.post(
    "/api/hmrc/calculation/trigger",
    response_model=TriggerCalculationResponse,
)
async def trigger_calculation(request: Request) -> TriggerCalculationResponse:
    """Tell HMRC to compute this user's tax for the given tax year."""
    user = _common.require_user(request)
    req = await _parse_trigger(request)

    body = await request.json()
    idem_key = (body or {}).get("idempotency_key")

    try:
        calc_id, raw, audit_id = _service.trigger_calculation(
            user_id=user["id"], request_obj=request,
            tax_year=req.tax_year, idempotency_key=idem_key,
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise

    return TriggerCalculationResponse(
        tax_year=req.tax_year,
        calculation_id=calc_id,
        hmrc_response=raw,
        audit_id=audit_id,
    )


@router.post(
    "/api/hmrc/calculation/get",
    response_model=TaxCalculationSummary,
)
async def get_calculation(request: Request) -> TaxCalculationSummary:
    """Fetch the result of a previously-triggered calculation."""
    user = _common.require_user(request)
    req = await _parse_get(request)

    try:
        raw, audit_id = _service.get_calculation(
            user_id=user["id"], request_obj=request,
            tax_year=req.tax_year, calculation_id=req.calculation_id,
        )
    except Exception as exc:
        _common.wrap_hmrc_error(exc)
        raise

    summary = _service.summarise_calculation(raw)
    return TaxCalculationSummary(
        tax_year=req.tax_year,
        calculation_id=req.calculation_id,
        income_tax_amount=summary["income_tax_amount"],
        nics_amount=summary["nics_amount"],
        total_amount_payable=summary["total_amount_payable"],
        total_taxable_income=summary["total_taxable_income"],
        raw=raw,
        audit_id=audit_id,
    )
