"""
Pydantic schemas for UK Property Business (MTD) v6.0 quarterly submission.

Wire endpoint:
    POST /individuals/business/property/{nino}/{businessId}/uk/period-summaries

Mirror of `se_quarterly.py` — same shape, different category set. Read the
docstring there for the two-step preview/submit rationale.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Re-use the transaction row from the SE schema — both streams accept the
# same dashboard payload, only the category mapping differs.
from .se_quarterly import _TransactionRow


# ---------------------------------------------------------------------------
# HMRC wire format
# ---------------------------------------------------------------------------

class PropertyIncome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rentIncome: float | None = Field(default=None, ge=0)
    premiumsOfLeaseGrant: float | None = Field(default=None, ge=0)
    reversePremiums: float | None = Field(default=None, ge=0)
    otherIncome: float | None = Field(default=None, ge=0)


class PropertyExpenses(BaseModel):
    model_config = ConfigDict(extra="forbid")

    premisesRunningCosts: float | None = Field(default=None)
    repairsAndMaintenance: float | None = Field(default=None)
    financialCosts: float | None = Field(default=None)        # commercial mortgage interest, etc.
    professionalFees: float | None = Field(default=None)
    costOfServices: float | None = Field(default=None)
    travelCosts: float | None = Field(default=None)
    other: float | None = Field(default=None)
    # Residential mortgage interest — restricted to basic-rate relief:
    residentialFinancialCost: float | None = Field(default=None)
    residentialFinancialCostsCarriedForward: float | None = Field(default=None)


class PropertyPeriodDates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periodStartDate: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    periodEndDate: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class PropertyPeriodSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    periodDates: PropertyPeriodDates
    periodIncome: PropertyIncome
    periodExpenses: PropertyExpenses


# ---------------------------------------------------------------------------
# Bankparse-facing request / response
# ---------------------------------------------------------------------------

class SubmitPropertyQuarterRequest(BaseModel):
    """Same shape as SubmitSEQuarterRequest — different business stream."""
    business_id: str = Field(..., min_length=1)
    period_start: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    period_end: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    rows: list[_TransactionRow] = Field(default_factory=list)


class PropertyQuarterPreview(BaseModel):
    business_id: str
    payload: PropertyPeriodSummary
    category_breakdown: dict[str, float]
    excluded_rows: int
    flagged_for_review: int


class SubmitPropertyQuarterResponse(BaseModel):
    status: Literal["ok"] = "ok"
    business_id: str
    period_start: str
    period_end: str
    hmrc_response: dict
    audit_id: str
