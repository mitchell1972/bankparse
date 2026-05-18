"""
Pydantic schemas for Self-Employment Business (MTD) v5.0 quarterly submission.

Endpoint:
    POST /individuals/business/self-employment/{nino}/{businessId}/period-summaries

These are the field names exactly as HMRC expects them. Don't rename — the
JSON keys are part of the API contract.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SEIncome(BaseModel):
    turnover: float | None = Field(default=None, ge=0)
    other: float | None = Field(default=None, ge=0)


class SEExpenses(BaseModel):
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
    periodStartDate: str  # YYYY-MM-DD
    periodEndDate: str    # YYYY-MM-DD


class SEPeriodSummary(BaseModel):
    """The full body for POST .../period-summaries."""
    periodDates: SEPeriodDates
    periodIncome: SEIncome
    periodExpenses: SEExpenses
