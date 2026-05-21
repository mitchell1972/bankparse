"""
Annual finalisation service — EOPS + Tax Calculation + Final Declaration.

The user-facing flow at year-end is:

  1. (already done) 4 quarterly updates submitted via services/quarterly_updates
  2. submit_eops(business_id, ...)        – one per business
  3. trigger_calculation(tax_year)        – HMRC computes the tax bill
  4. get_calculation(tax_year, calc_id)   – fetch the result for review
  5. submit_final_declaration(...)        – the user agrees, files the return

Every call goes through the existing client (bearer + 13 fraud headers +
Idempotency-Key + audit log).

API versions pinned via Accept header. Bump explicitly when HMRC publishes
a new version we've validated against the sandbox.

Wire endpoints
  EOPS                POST /individuals/business/{nino}/{businessId}/end-of-period-statements
                      Accept: application/vnd.hmrc.3.0+json
  Trigger calc        POST /individuals/calculations/{nino}/self-assessment/{taxYear}
                      Accept: application/vnd.hmrc.7.0+json
  Get calc            GET  /individuals/calculations/{nino}/self-assessment/{taxYear}/{calculationId}
                      Accept: application/vnd.hmrc.7.0+json
  Final declaration   POST /individuals/calculations/{nino}/self-assessment/{taxYear}/{calculationId}/final-declaration
                      Accept: application/vnd.hmrc.7.0+json
"""

from __future__ import annotations

import logging
import uuid

from ..repositories import tokens as _tokens
from . import client as _client
from .quarterly_updates import NinoNotConfiguredError

logger = logging.getLogger("bankparse.hmrc.annual")


_EOPS_API_VERSION = "application/vnd.hmrc.3.0+json"
_CALC_API_VERSION = "application/vnd.hmrc.7.0+json"


def _require_nino(user_id: int) -> str:
    info = _tokens.get_tokens(user_id) or {}
    nino = info.get("nino")
    if not nino:
        raise NinoNotConfiguredError(
            "No NINO on file for this account. Open your dashboard, type "
            "your National Insurance Number into the 'Your HMRC deadlines' "
            "card, click 'Discover my businesses', then come back here."
        )
    return nino


# ---------------------------------------------------------------------------
# End of Period Statement
# ---------------------------------------------------------------------------

def submit_eops(
    *,
    user_id: int,
    request_obj,
    business_id: str,
    type_of_business: str,
    period_start: str,
    period_end: str,
    idempotency_key: str | None = None,
) -> tuple[dict, str]:
    """POST an End of Period Statement to HMRC. Returns (hmrc_response, audit_id)."""
    nino = _require_nino(user_id)
    path = f"/individuals/business/{nino}/{business_id}/end-of-period-statements"
    body = {
        "typeOfBusiness": type_of_business,
        "businessId": business_id,
        "accountingPeriod": {
            "startDate": period_start,
            "endDate": period_end,
        },
        "finalised": True,
    }
    resp = _client.request(
        user_id=user_id, method="POST", path=path,
        request_obj=request_obj,
        json_body=body,
        accept_version=_EOPS_API_VERSION,
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    )
    return resp.json or {}, resp.audit_id


# ---------------------------------------------------------------------------
# Tax Calculation
# ---------------------------------------------------------------------------

def trigger_calculation(
    *,
    user_id: int,
    request_obj,
    tax_year: str,
    idempotency_key: str | None = None,
) -> tuple[str, dict, str]:
    """Tell HMRC to compute the tax bill. Returns (calculationId, raw, audit_id).

    The actual numbers aren't ready immediately — caller should poll
    `get_calculation` until HMRC's response carries the populated body.
    """
    nino = _require_nino(user_id)
    path = f"/individuals/calculations/{nino}/self-assessment/{tax_year}"
    resp = _client.request(
        user_id=user_id, method="POST", path=path,
        request_obj=request_obj,
        json_body=None,
        accept_version=_CALC_API_VERSION,
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    )
    raw = resp.json or {}
    calc_id = raw.get("calculationId") or ""
    return calc_id, raw, resp.audit_id


def get_calculation(
    *,
    user_id: int,
    request_obj,
    tax_year: str,
    calculation_id: str,
) -> tuple[dict, str]:
    """Fetch the result of a previously-triggered tax calculation."""
    nino = _require_nino(user_id)
    path = (
        f"/individuals/calculations/{nino}/self-assessment/"
        f"{tax_year}/{calculation_id}"
    )
    resp = _client.request(
        user_id=user_id, method="GET", path=path,
        request_obj=request_obj,
        accept_version=_CALC_API_VERSION,
    )
    return resp.json or {}, resp.audit_id


def summarise_calculation(raw: dict) -> dict:
    """Pluck headline numbers out of HMRC's calculation body.

    HMRC's response is dense (50+ fields). We map the four numbers a
    sole trader actually wants to see at-a-glance to flat keys, and
    leave the rest in `raw` for power users.
    """
    # The shape varies slightly between calculation states (in-progress
    # vs complete). We walk defensively.
    calc = raw or {}
    summary = (calc.get("calculation") or {}).get("taxCalculation") or {}
    inc = summary.get("incomeTax") or {}
    nics = summary.get("nics") or {}
    return {
        "income_tax_amount": _safe_float(inc.get("payPensionsProfit", {}).get("incomeTaxAmount")),
        "nics_amount": _safe_float(nics.get("class4Nics", {}).get("nicsAmount")),
        "total_amount_payable": _safe_float(summary.get("totalTaxAmount")),
        "total_taxable_income": _safe_float(
            (calc.get("calculation") or {}).get("totalIncome", {}).get("totalIncomeReceived")
        ),
    }


def _safe_float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Final Declaration
# ---------------------------------------------------------------------------

def submit_final_declaration(
    *,
    user_id: int,
    request_obj,
    tax_year: str,
    calculation_id: str,
    idempotency_key: str | None = None,
) -> tuple[dict, str]:
    """Submit the final declaration — i.e. file the annual tax return.

    HMRC's endpoint takes no body, just the path + headers. The
    Idempotency-Key on this one is critical: a duplicate submit would be
    rejected by HMRC but only after going over the wire, so we keep our
    own dedupe via the audit log too.
    """
    nino = _require_nino(user_id)
    path = (
        f"/individuals/calculations/{nino}/self-assessment/"
        f"{tax_year}/{calculation_id}/final-declaration"
    )
    resp = _client.request(
        user_id=user_id, method="POST", path=path,
        request_obj=request_obj,
        json_body=None,
        accept_version=_CALC_API_VERSION,
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    )
    return resp.json or {}, resp.audit_id
