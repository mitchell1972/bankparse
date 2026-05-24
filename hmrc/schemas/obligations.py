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
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    """Top-level response from HMRC's Obligations endpoints.

    Accepts BOTH the flat shape (older HMRC API versions that returned
    ``{obligations: [{periodKey, start, end, due, status}]}``) AND the
    nested shape that the current MTD ITSA APIs return:

    ``{obligations: [{identification: {...}, obligationDetails: [...]}]}``

    where each inner ``obligationDetails`` entry uses HMRC's verbose field
    names (``inboundCorrespondenceFromDate`` / ``inboundCorrespondenceToDate``
    / ``inboundCorrespondenceDueDate`` / ``inboundCorrespondenceDateReceived``
    / ``status`` of ``"O"`` (Open) or ``"F"`` (Fulfilled)).

    Earlier this model only handled the flat shape and ``_fetch_one_business``
    blew up with a 5-error ValidationError against any real sandbox response.
    Caught by the Playwright submit-journey test on 2026-05-24.
    """
    obligations: list[HmrcObligation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _flatten_hmrc_wire_shape(cls, data: Any) -> Any:
        """Normalise the nested HMRC wire shape into our flat ``obligations``
        list. Pass-through for the legacy flat shape so existing fixtures
        and tests don't break."""
        if not isinstance(data, dict):
            return data
        items = data.get("obligations")
        if not isinstance(items, list) or not items:
            return data

        flat: list[dict[str, Any]] = []
        any_nested = False
        for item in items:
            if not isinstance(item, dict):
                continue
            details = item.get("obligationDetails")
            if isinstance(details, list):
                any_nested = True
                for d in details:
                    if not isinstance(d, dict):
                        continue
                    flat.append(_flatten_one(d))
            else:
                # Already in the flat shape — keep it as-is.
                flat.append(item)

        if any_nested:
            return {**data, "obligations": flat}
        return data


def _flatten_one(d: dict[str, Any]) -> dict[str, Any]:
    """Map ONE nested HMRC obligationDetails entry to our flat HmrcObligation."""
    status_raw = (d.get("status") or "").strip()
    status = {"O": "Open", "F": "Fulfilled"}.get(status_raw.upper(), status_raw or "Open")
    return {
        "periodKey": d.get("periodKey") or "",
        "start": d.get("inboundCorrespondenceFromDate") or d.get("start") or "",
        "end": d.get("inboundCorrespondenceToDate") or d.get("end") or "",
        "due": d.get("inboundCorrespondenceDueDate") or d.get("due") or "",
        "status": status,
        "received": (
            d.get("inboundCorrespondenceDateReceived")
            or d.get("received")
            or None
        ),
    }


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
    # Surface the saved NINO when there is one so the dashboard / file
    # page can distinguish "connected, no NINO" from "connected, NINO X
    # but no businesses found" — the two states look the same in `demo`
    # but require different recovery flows.
    nino: str | None = None
