"""
Pydantic schemas for UK Property Business (MTD) v6.0 quarterly submission.

Endpoint:
    POST /individuals/business/property/{nino}/{businessId}/uk/period-summaries
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PropertyIncome(BaseModel):
    rentIncome: float | None = Field(default=None, ge=0)
    premiumsOfLeaseGrant: float | None = Field(default=None, ge=0)
    reversePremiums: float | None = Field(default=None, ge=0)
    otherIncome: float | None = Field(default=None, ge=0)


class PropertyExpenses(BaseModel):
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
    periodStartDate: str
    periodEndDate: str


class PropertyPeriodSummary(BaseModel):
    periodDates: PropertyPeriodDates
    periodIncome: PropertyIncome
    periodExpenses: PropertyExpenses
