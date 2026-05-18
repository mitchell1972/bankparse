"""
Schemas for the HMRC MTD ITSA Obligations API.

HMRC's wire format (`HmrcObligation`) is normalised by our service layer
into `UiObligation`, which is friendlier for the dashboard panel:
  - human deadline phrasing ("due in 8 days" / "5 days overdue")
  - status flag the UI keys off ("upcoming" | "open" | "overdue" | "filed")
  - business label (e.g. "Self-employment", "Property — UK")

HMRC docs:
  GET /obligations/details/{nino}/income-and-expenditure
  GET /individuals/business/{businessType}/{nino}/{businessId}/obligations
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Wire-format models — what HMRC sends us
# ---------------------------------------------------------------------------

class HmrcObligation(BaseModel):
    """One quarterly / EOPS / final-declaration obligation per HMRC's API."""
    periodKey: str            # HMRC's identifier for the period, e.g. "#001"
    start: str                # YYYY-MM-DD inclusive
    end: str                  # YYYY-MM-DD inclusive
    due: str                  # YYYY-MM-DD by which we must submit
    status: Literal["Open", "Fulfilled"]
    received: str | None = None  # YYYY-MM-DD when HMRC received our submission


class HmrcObligations(BaseModel):
    obligations: list[HmrcObligation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# UI-facing models — what we send to the dashboard
# ---------------------------------------------------------------------------

UiStatus = Literal["filed", "upcoming", "open", "overdue"]
BusinessType = Literal["self-employment", "property"]


class UiObligation(BaseModel):
    """One row in the dashboard's "your HMRC deadlines" panel."""
    business_type: BusinessType
    business_label: str       # e.g. "Self-employment" / "Property — UK"
    business_id: str          # HMRC business id this obligation belongs to
    period_key: str
    period_start: str         # YYYY-MM-DD
    period_end: str           # YYYY-MM-DD
    due: str                  # YYYY-MM-DD
    received: str | None = None  # YYYY-MM-DD when HMRC received our submission
    status: UiStatus
    days_until_due: int       # negative = overdue, 0 = today, positive = future
    human_due: str            # "due in 8 days" / "filed 2 Aug 2026" / "23 days overdue"


class ObligationsResponse(BaseModel):
    """Response shape for GET /api/hmrc/obligations."""
    connected: bool           # is the user connected to HMRC at all?
    demo: bool = False        # using a static fixture (no real HMRC call)
    obligations: list[UiObligation] = Field(default_factory=list)
    error: str | None = None  # human-friendly error if we couldn't fetch
