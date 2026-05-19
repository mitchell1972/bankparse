"""
Quarterly Updates service — the first HMRC write endpoint.

Two public functions per business stream:

  build_se_payload(rows, *, period_start, period_end, user_id)
      Aggregate the dashboard's transaction rows into the HMRC
      SE period-summary wire format. Reuses the existing categorisation
      pipeline so totals match the dashboard exactly.

  submit_se_quarter(*, user_id, request_obj, business_id, payload)
      POST the payload to HMRC's sandbox/prod, return their response +
      the audit_id from the immutable hmrc_submissions log.

Property has the mirror pair (build_property_payload / submit_property_quarter)
in this same module so both streams share the aggregation helpers and
follow the same idempotency contract.

HMRC requires `Idempotency-Key` on every POST to these endpoints so retries
don't double-submit a quarter. We generate one per submit; if the caller
provides their own (e.g. for replay safety in a background job), we honour
that.

Wire endpoints (versions pinned via Accept header):
  POST /individuals/business/self-employment/{nino}/{businessId}/period-summaries
       Accept: application/vnd.hmrc.5.0+json
  POST /individuals/business/property/{nino}/{businessId}/uk/period-summaries
       Accept: application/vnd.hmrc.6.0+json
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Iterable

from ..repositories import tokens as _tokens
from ..schemas import categories as _cats
from ..schemas.property_quarterly import (
    PropertyExpenses,
    PropertyIncome,
    PropertyPeriodDates,
    PropertyPeriodSummary,
)
from ..schemas.se_quarterly import (
    SEExpenses,
    SEIncome,
    SEPeriodDates,
    SEPeriodSummary,
)
from . import categorisation as _categorisation
from . import client as _client
from . import mapping as _mapping

logger = logging.getLogger("bankparse.hmrc.quarterly_updates")


# Pinned wire versions. Bump when HMRC publishes a new version we validated
# against the sandbox. Bump = test + commit, never silent.
_SE_API_VERSION = "application/vnd.hmrc.5.0+json"
_PROPERTY_API_VERSION = "application/vnd.hmrc.6.0+json"


# ---------------------------------------------------------------------------
# Errors specific to the submit flow
# ---------------------------------------------------------------------------

class NinoNotConfiguredError(RuntimeError):
    """User hasn't saved their NINO via /api/hmrc/connect-businesses yet."""


# ---------------------------------------------------------------------------
# Public API — Self-Employment
# ---------------------------------------------------------------------------

async def build_se_payload(
    *, rows: list[dict], period_start: str, period_end: str, user_id: int,
) -> tuple[SEPeriodSummary, dict[str, float], int, int]:
    """Build the HMRC SE period-summary payload from raw dashboard rows.

    Returns:
        (payload, category_breakdown, excluded_count, flagged_count)

    Reuses the existing categorisation pipeline (override → cache → AI →
    rule), so totals match what the user sees on the dashboard exactly.

    Async because categorisation calls Claude. In test we patch
    `hmrc.services.categorisation.resolve` so no network happens.
    """
    from ..schemas.categorise import CategoriseRequest

    cat_req = CategoriseRequest(business_type="se", rows=rows or [])
    cat_resp, _metrics = await _categorisation.resolve(cat_req, user_id=user_id)

    income_totals: dict[str, float] = {}
    expense_totals: dict[str, float] = {}
    excluded = 0
    flagged = 0
    for row in cat_resp.rows:
        h = row.hmrc
        if h.category == _cats.EXCLUDE_OWNER_TRANSFER:
            excluded += 1
            continue
        if h.confidence < 0.5:
            flagged += 1
        amount = abs(row.amount)
        bucket = income_totals if _cats.is_income_category(h.category, "se") else expense_totals
        bucket[h.category] = round(bucket.get(h.category, 0.0) + amount, 2)

    payload = SEPeriodSummary(
        periodDates=SEPeriodDates(
            periodStartDate=period_start, periodEndDate=period_end,
        ),
        periodIncome=SEIncome(
            turnover=income_totals.get(_cats.SE_INCOME),
            other=income_totals.get(_cats.SE_OTHER_INCOME),
        ),
        periodExpenses=SEExpenses(
            costOfGoodsBought=expense_totals.get(_cats.SE_EXPENSE_COST_OF_GOODS),
            cisPaymentsToSubcontractors=expense_totals.get(_cats.SE_EXPENSE_CIS),
            staffCosts=expense_totals.get(_cats.SE_EXPENSE_STAFF),
            travelCosts=expense_totals.get(_cats.SE_EXPENSE_TRAVEL),
            premisesRunningCosts=expense_totals.get(_cats.SE_EXPENSE_PREMISES),
            maintenanceCosts=expense_totals.get(_cats.SE_EXPENSE_REPAIRS),
            adminCosts=expense_totals.get(_cats.SE_EXPENSE_ADMIN),
            advertisingCosts=expense_totals.get(_cats.SE_EXPENSE_ADVERTISING),
            businessEntertainmentCosts=expense_totals.get(_cats.SE_EXPENSE_ENTERTAINMENT),
            interest=expense_totals.get(_cats.SE_EXPENSE_INTEREST),
            financialCharges=expense_totals.get(_cats.SE_EXPENSE_FINANCIAL),
            badDebt=expense_totals.get(_cats.SE_EXPENSE_BAD_DEBT),
            professionalFees=expense_totals.get(_cats.SE_EXPENSE_PROFESSIONAL),
            depreciation=expense_totals.get(_cats.SE_EXPENSE_DEPRECIATION),
            other=expense_totals.get(_cats.SE_EXPENSE_OTHER),
        ),
    )
    combined = {**income_totals, **expense_totals}
    return payload, combined, excluded, flagged


def submit_se_quarter(
    *,
    user_id: int,
    request_obj,
    business_id: str,
    payload: SEPeriodSummary,
    idempotency_key: str | None = None,
) -> tuple[dict, str]:
    """POST the SE quarterly update to HMRC. Returns (hmrc_response, audit_id).

    Raises:
        NinoNotConfiguredError if the user hasn't completed business setup.
        client.HmrcNotConnectedError if there's no OAuth connection.
        client.HmrcApiError on any non-2xx from HMRC (including the
            expected validation cases like duplicate-submission).
    """
    nino = _require_nino(user_id)
    path = f"/individuals/business/self-employment/{nino}/{business_id}/period-summaries"

    resp = _client.request(
        user_id=user_id, method="POST", path=path,
        request_obj=request_obj,
        json_body=payload.model_dump(exclude_none=True),
        accept_version=_SE_API_VERSION,
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    )
    return resp.json or {}, resp.audit_id


# ---------------------------------------------------------------------------
# Public API — UK Property
# ---------------------------------------------------------------------------

async def build_property_payload(
    *, rows: list[dict], period_start: str, period_end: str, user_id: int,
) -> tuple[PropertyPeriodSummary, dict[str, float], int, int]:
    """Mirror of build_se_payload for UK property."""
    from ..schemas.categorise import CategoriseRequest

    cat_req = CategoriseRequest(business_type="property", rows=rows or [])
    cat_resp, _metrics = await _categorisation.resolve(cat_req, user_id=user_id)

    income_totals: dict[str, float] = {}
    expense_totals: dict[str, float] = {}
    excluded = 0
    flagged = 0
    for row in cat_resp.rows:
        h = row.hmrc
        if h.category == _cats.EXCLUDE_OWNER_TRANSFER:
            excluded += 1
            continue
        if h.confidence < 0.5:
            flagged += 1
        amount = abs(row.amount)
        bucket = income_totals if _cats.is_income_category(h.category, "property") else expense_totals
        bucket[h.category] = round(bucket.get(h.category, 0.0) + amount, 2)

    payload = PropertyPeriodSummary(
        periodDates=PropertyPeriodDates(
            periodStartDate=period_start, periodEndDate=period_end,
        ),
        periodIncome=PropertyIncome(
            rentIncome=income_totals.get(_cats.PROP_INCOME_RENT),
            premiumsOfLeaseGrant=income_totals.get(_cats.PROP_INCOME_PREMIUMS),
            otherIncome=income_totals.get(_cats.PROP_INCOME_OTHER),
        ),
        periodExpenses=PropertyExpenses(
            premisesRunningCosts=expense_totals.get(_cats.PROP_EXPENSE_PREMISES),
            repairsAndMaintenance=expense_totals.get(_cats.PROP_EXPENSE_REPAIRS),
            financialCosts=expense_totals.get(_cats.PROP_EXPENSE_FINANCIAL),
            professionalFees=expense_totals.get(_cats.PROP_EXPENSE_PROFESSIONAL),
            costOfServices=expense_totals.get(_cats.PROP_EXPENSE_SERVICES),
            travelCosts=expense_totals.get(_cats.PROP_EXPENSE_TRAVEL),
            other=expense_totals.get(_cats.PROP_EXPENSE_OTHER),
            residentialFinancialCost=expense_totals.get(_cats.PROP_EXPENSE_RESIDENTIAL_FINANCIAL),
        ),
    )
    combined = {**income_totals, **expense_totals}
    return payload, combined, excluded, flagged


def submit_property_quarter(
    *,
    user_id: int,
    request_obj,
    business_id: str,
    payload: PropertyPeriodSummary,
    idempotency_key: str | None = None,
) -> tuple[dict, str]:
    """POST the UK Property quarterly update to HMRC."""
    nino = _require_nino(user_id)
    path = f"/individuals/business/property/{nino}/{business_id}/uk/period-summaries"

    resp = _client.request(
        user_id=user_id, method="POST", path=path,
        request_obj=request_obj,
        json_body=payload.model_dump(exclude_none=True),
        accept_version=_PROPERTY_API_VERSION,
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    )
    return resp.json or {}, resp.audit_id


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _require_nino(user_id: int) -> str:
    """Return the user's saved NINO, or raise NinoNotConfiguredError."""
    info = _tokens.get_tokens(user_id) or {}
    nino = info.get("nino")
    if not nino:
        raise NinoNotConfiguredError(
            "User has no NINO saved. Complete business setup "
            "(/api/hmrc/connect-businesses) first."
        )
    return nino
