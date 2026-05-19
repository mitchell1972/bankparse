"""
Schemas for the HMRC MTD ITSA annual finalisation flow.

Three HMRC APIs sit behind this single user journey:

  1. End of Period Statement (EOPS)
       POST /individuals/business/{nino}/{businessId}/end-of-period-statements
       The user confirms "my quarterly numbers for this tax year are
       correct, please finalise them". Submitted once per business.

  2. Tax Calculation (two-step)
       POST /individuals/calculations/{nino}/self-assessment/{taxYear}
            → returns calculationId immediately, calculation runs async
       GET  /individuals/calculations/{nino}/self-assessment/{taxYear}/{calculationId}
            → poll for the actual tax owed

  3. Final Declaration
       POST /individuals/calculations/{nino}/self-assessment/{taxYear}/{calculationId}/final-declaration
       The user agrees the calculation is correct and submits the annual
       return. This is what replaces the old Self Assessment return.

Tax-year format: HMRC uses "YYYY-YY" (e.g. "2026-27"). We accept that
verbatim from the user.

Wire JSON keys MUST stay camelCase exactly as HMRC expects.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Tax year regex: "2026-27" style.
_TAX_YEAR_PATTERN = r"^\d{4}-\d{2}$"


# ---------------------------------------------------------------------------
# End of Period Statement
# ---------------------------------------------------------------------------

class SubmitEopsRequest(BaseModel):
    """Body for /api/hmrc/eops/submit."""
    business_id: str = Field(..., min_length=1)
    type_of_business: Literal["self-employment", "uk-property", "foreign-property"]
    period_start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    finalised: Literal[True] = True  # HMRC requires confirmation


class SubmitEopsResponse(BaseModel):
    status: Literal["ok"] = "ok"
    business_id: str
    period_start: str
    period_end: str
    hmrc_response: dict
    audit_id: str


# ---------------------------------------------------------------------------
# Tax Calculation
# ---------------------------------------------------------------------------

class TriggerCalculationRequest(BaseModel):
    """Body for /api/hmrc/calculation/trigger."""
    tax_year: str = Field(..., pattern=_TAX_YEAR_PATTERN)


class TriggerCalculationResponse(BaseModel):
    status: Literal["ok"] = "ok"
    tax_year: str
    calculation_id: str
    audit_id: str
    # HMRC returns links the client should poll. We pass them through so
    # the dashboard can render "Check back in a few seconds" without
    # baking in any HMRC URL knowledge.
    hmrc_response: dict


class GetCalculationRequest(BaseModel):
    """Body for /api/hmrc/calculation/get."""
    tax_year: str = Field(..., pattern=_TAX_YEAR_PATTERN)
    calculation_id: str = Field(..., min_length=1)


class TaxCalculationSummary(BaseModel):
    """UI-friendly summary built on top of HMRC's raw calculation body.

    HMRC's response is huge (50+ fields across multiple sections). We pull
    out the headline numbers a sole trader actually wants to see, and
    pass the full body in `raw` for power users / debugging.
    """
    tax_year: str
    calculation_id: str
    income_tax_amount: float | None = None
    nics_amount: float | None = None
    total_amount_payable: float | None = None
    total_taxable_income: float | None = None
    raw: dict = Field(default_factory=dict)
    audit_id: str


# ---------------------------------------------------------------------------
# Final Declaration
# ---------------------------------------------------------------------------

class SubmitFinalDeclarationRequest(BaseModel):
    """Body for /api/hmrc/final-declaration/submit."""
    tax_year: str = Field(..., pattern=_TAX_YEAR_PATTERN)
    calculation_id: str = Field(..., min_length=1)
    # HMRC requires the user to explicitly confirm — we require it on the
    # wire too so a misclick can't accidentally submit a tax return.
    finalised: Literal[True] = True


class SubmitFinalDeclarationResponse(BaseModel):
    status: Literal["ok"] = "ok"
    tax_year: str
    calculation_id: str
    hmrc_response: dict
    audit_id: str
