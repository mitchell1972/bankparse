"""
Pydantic schemas for Self-Employment Business (MTD) v5.0 quarterly submission.

Wire endpoint:
    POST /individuals/business/self-employment/{nino}/{businessId}/period-summaries

The HMRC-facing JSON keys (camelCase) are part of the API contract — don't
rename them. Our own request/response wrappers use snake_case as is
conventional for the rest of this codebase.

Two-step UX:
    1. Caller hits /api/hmrc/quarterly-updates/se/preview with the rows for
       the period — we aggregate, validate, and return what would be sent
       so the user can sanity-check before any HMRC call.
    2. Caller hits /api/hmrc/quarterly-updates/se/submit with the same body
       — we POST to HMRC, persist the response with an idempotency key,
       and return the HMRC transaction reference.

This split protects against accidental double-submission (Idempotency-Key
+ HMRC server-side dedupe handle the rest).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# HMRC wire format — what we send TO HMRC
# ---------------------------------------------------------------------------

class SEIncome(BaseModel):
    """HMRC self-employment income block."""
    model_config = ConfigDict(extra="forbid")

    turnover: float | None = Field(default=None, ge=0)
    other: float | None = Field(default=None, ge=0)


class SEExpenses(BaseModel):
    """HMRC self-employment expenses block. All optional — only include
    what was actually spent in this period."""
    model_config = ConfigDict(extra="forbid")

    costOfGoodsBought: float | None = Field(default=None)
    cisPaymentsToSubcontractors: float | None = Field(default=None)
    staffCosts: float | None = Field(default=None)
    travelCosts: float | None = Field(default=None)
    premisesRunningCosts: float | None = Field(default=None)
    maintenanceCosts: float | None = Field(default=None)
    adminCosts: float | None = Field(default=None)
    advertisingCosts: float | None = Field(default=None)
    businessEntertainmentCosts: float | None = Field(default=None)
    interest: float | None = Field(default=None)
    financialCharges: float | None = Field(default=None)
    badDebt: float | None = Field(default=None)
    professionalFees: float | None = Field(default=None)
    depreciation: float | None = Field(default=None)
    other: float | None = Field(default=None)


class SEPeriodDates(BaseModel):
    """The quarter this submission covers."""
    model_config = ConfigDict(extra="forbid")

    periodStartDate: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    periodEndDate: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class SEPeriodSummary(BaseModel):
    """The full HMRC POST body for a Self-Employment period summary."""
    model_config = ConfigDict(extra="forbid")

    periodDates: SEPeriodDates
    periodIncome: SEIncome
    periodExpenses: SEExpenses


# ---------------------------------------------------------------------------
# Bankparse-facing request/response — what the dashboard sends/gets
# ---------------------------------------------------------------------------

class _TransactionRow(BaseModel):
    """One bank-statement row exactly as the dashboard already passes it
    around. Extra fields preserved verbatim."""
    model_config = ConfigDict(extra="allow")

    description: str = ""
    amount: float = 0.0


class SubmitSEQuarterRequest(BaseModel):
    """Caller body for preview AND submit endpoints — identical shape so the
    user can preview, eyeball the payload, then submit the same input."""
    business_id: str = Field(..., min_length=1)
    period_start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    rows: list[_TransactionRow] = Field(default_factory=list)


class SEQuarterPreview(BaseModel):
    """Pre-submission summary returned by /preview.

    `payload` is exactly what we'd POST to HMRC. `category_breakdown` mirrors
    the category-summary the dashboard already shows so the user sees the
    same numbers before pressing submit.
    """
    business_id: str
    payload: SEPeriodSummary
    category_breakdown: dict[str, float]  # category code -> total
    excluded_rows: int                    # owner transfers etc. dropped from total
    flagged_for_review: int               # low-confidence rows the user should eyeball


class SubmitSEQuarterResponse(BaseModel):
    """Returned after a successful HMRC submit."""
    status: Literal["ok"] = "ok"
    business_id: str
    period_start: str
    period_end: str
    # HMRC returns a submissionId / transactionReference depending on the
    # version. Whichever they send is echoed verbatim plus our internal
    # audit_id so the user can correlate the row in hmrc_submissions.
    hmrc_response: dict
    audit_id: str
