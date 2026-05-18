"""
Schemas for the HMRC MTD Business Details API.

We use this endpoint to ENUMERATE the businesses a NINO is registered for,
so the user types their NINO once and we discover their self-employment
+ property business IDs automatically — no copy-paste from the HMRC
developer hub, no JS snippets in the browser console.

HMRC docs:
  GET /individuals/business/details/{nino}/list

Response shape (abbreviated; real spec has many optional fields):
  {
    "listOfBusinesses": [
      {
        "businessId": "XAIS00000000001",
        "typeOfBusiness": "self-employment",
        "tradingName": "Mitoba Consulting",
        "accountingType": "CASH"
      },
      {
        "businessId": "XPIS00000000002",
        "typeOfBusiness": "uk-property",
        ...
      }
    ]
  }
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Wire-format model — what HMRC sends us
# ---------------------------------------------------------------------------

class HmrcBusiness(BaseModel):
    """One business entry as it appears in HMRC's list response."""
    # `extra="allow"` because HMRC may add fields between API versions; we
    # don't want pydantic to drop them silently.
    model_config = ConfigDict(extra="allow")

    businessId: str
    typeOfBusiness: str       # "self-employment" | "uk-property" | "foreign-property"
    tradingName: str | None = None
    accountingType: str | None = None      # "CASH" | "ACCRUALS"


class HmrcBusinessList(BaseModel):
    listOfBusinesses: list[HmrcBusiness] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# UI / repository shape — what we persist + send to the dashboard
# ---------------------------------------------------------------------------

NormalisedBusinessType = Literal["self-employment", "property"]


class UiBusiness(BaseModel):
    """The shape we store in hmrc_connections.businesses_json AND return to
    the dashboard. Matches the input that obligations expects."""
    business_id: str
    type_of_business: NormalisedBusinessType   # path-segment for HMRC URLs
    label: str                                 # human-friendly label for the UI


class ConnectBusinessesRequest(BaseModel):
    """Body for POST /api/hmrc/connect-businesses."""
    nino: str = Field(..., min_length=9, max_length=9)


class ConnectBusinessesResponse(BaseModel):
    """Response from POST /api/hmrc/connect-businesses."""
    status: Literal["ok"] = "ok"
    businesses_found: int
    businesses: list[UiBusiness] = Field(default_factory=list)
