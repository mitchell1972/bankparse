"""
Pydantic schemas for /api/hmrc/categorise* endpoints.

These give us:
  - Request validation at the framework layer (no more `body.get(...)` dict
    juggling in the route handler)
  - Auto-generated OpenAPI docs on /docs
  - IDE autocomplete + type checking through the service layer
  - One place to evolve the wire contract when HMRC's API changes

The router stays HTTP-only; everything below uses these models or plain
dataclasses, never raw dicts.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


BusinessType = Literal["se", "property"]
ClassificationSource = Literal["override", "ai_cached", "ai", "rule"]


class TransactionIn(BaseModel):
    """A single bank-statement row as it arrives from the dashboard.

    Extra fields (date, balance, raw_line) are preserved verbatim so the
    response can echo them back — the client sometimes attaches its own
    metadata.
    """
    model_config = ConfigDict(extra="allow")

    description: str = Field("", description="Raw bank-statement description.")
    amount: float = Field(0.0, description="Signed amount: positive = credit, negative = debit.")


class CategoriseRequest(BaseModel):
    business_type: BusinessType = Field("se", description="HMRC business stream this batch belongs to.")
    rows: list[TransactionIn] = Field(default_factory=list)


class HmrcClassification(BaseModel):
    """The HMRC block attached to each row in the response."""
    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    is_income: bool
    reasoning: str
    source: ClassificationSource


class TransactionOut(BaseModel):
    """Echo of the input row with the HMRC classification appended."""
    model_config = ConfigDict(extra="allow")

    description: str = ""
    amount: float = 0.0
    hmrc: HmrcClassification


class CategoriseResponse(BaseModel):
    business_type: BusinessType
    rows: list[TransactionOut]


# ---------------------------------------------------------------------------
# Override
# ---------------------------------------------------------------------------

class OverrideRequest(BaseModel):
    description: str = Field(..., min_length=1)
    business_type: BusinessType = "se"
    category: str = Field(..., min_length=1)


class OverrideResponse(BaseModel):
    status: Literal["ok"] = "ok"
    merchant_key: str


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class CategorySummary(BaseModel):
    """The structured summary returned by /api/hmrc/categorise/summary."""
    income: dict[str, float] = Field(default_factory=dict)
    expenses: dict[str, float] = Field(default_factory=dict)
    flagged_for_review: list[dict] = Field(default_factory=list)
    excluded: list[dict] = Field(default_factory=list)


class SummaryResponse(BaseModel):
    summary: CategorySummary
    business_type: BusinessType
