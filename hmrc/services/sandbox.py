"""
Sandbox-only test-data helpers.

HMRC's sandbox doesn't seed a freshly-created test individual with any
MTD ITSA businesses. The result is that the FIRST time a developer plugs
their sandbox NINO into BankScan AI, the Business Details API returns
`404 MATCHING_RESOURCE_NOT_FOUND`. That's correct — there's nothing
registered for that NINO — but it's a dead-end UX.

This service calls HMRC's Test Support endpoint to provision a test
self-employment OR property business so the rest of the dashboard can
light up. Production builds NEVER call this — the router gate (see
`routers/sandbox.py`) refuses the request when `HMRC_ENV=production`.

Reference (subject to HMRC versioning — update the path if HMRC moves it):
  POST /individuals/business/details/{nino}/test-only/create-business

Body expected by HMRC's sandbox:
  {
    "typeOfBusiness": "self-employment" | "uk-property",
    "tradingName":  "...",
    "firstAccountingPeriodStartDate": "YYYY-MM-DD",
    "firstAccountingPeriodEndDate":   "YYYY-MM-DD",
    "accountingType": "CASH"
  }

Returns the freshly-created business id which we can immediately stash
under `hmrc_connections.businesses_json` so the panel flips to Live.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from ..repositories import tokens as _tokens
from . import client as _client

logger = logging.getLogger("bankparse.hmrc.sandbox")


# HMRC's Business Details API ships its test-support endpoints behind a
# distinct media-type version. Bump this when HMRC publishes a new version
# of the Test Support API we validated against.
_TEST_API_VERSION = "application/vnd.hmrc.1.0+json"


def is_sandbox() -> bool:
    """Refuse to run outside sandbox to prevent accidental prod calls."""
    return os.environ.get("HMRC_ENV", "sandbox").lower() != "production"


def create_test_business(
    *,
    user_id: int,
    request_obj,
    type_of_business: str = "self-employment",
    trading_name: str | None = None,
) -> dict:
    """Provision one HMRC sandbox business for the user's connected NINO.

    Returns the HMRC response (typically the new businessId). Raises
    `HmrcNotConnectedError` if the user hasn't OAuthed yet, or
    `HmrcApiError` on a non-2xx from HMRC.
    """
    info = _tokens.get_tokens(user_id) or {}
    nino = info.get("nino")
    if not nino:
        raise _client.HmrcNotConnectedError(
            "Need a NINO before we can create a test business. "
            "Type a NINO into the dashboard first (we'll persist it then call this)."
        )

    # Normalise our internal `property` to HMRC's wire value `uk-property`.
    if type_of_business == "property":
        wire_type = "uk-property"
        default_name = "Sandbox property"
    else:
        wire_type = "self-employment"
        default_name = "Sandbox sole trader"

    today = date.today()
    body = {
        "typeOfBusiness": wire_type,
        "tradingName": (trading_name or default_name)[:105],
        # MTD ITSA tax year runs 6 April → 5 April. Pick the current
        # tax-year window so HMRC happily accepts the dates.
        "firstAccountingPeriodStartDate": _tax_year_start(today).isoformat(),
        "firstAccountingPeriodEndDate":   _tax_year_end(today).isoformat(),
        "accountingType": "CASH",
    }

    path = f"/individuals/business/details/{nino}/test-only/create-business"
    resp = _client.request(
        user_id=user_id, method="POST", path=path,
        request_obj=request_obj,
        json_body=body,
        accept_version=_TEST_API_VERSION,
    )
    return resp.json or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tax_year_start(d: date) -> date:
    """Most recent 6 April on or before `d`."""
    cutoff = date(d.year, 4, 6)
    return cutoff if d >= cutoff else date(d.year - 1, 4, 6)


def _tax_year_end(d: date) -> date:
    """5 April that closes the tax year `_tax_year_start(d)` began."""
    start = _tax_year_start(d)
    return date(start.year + 1, 4, 5)


# ---------------------------------------------------------------------------
# Bulk bootstrap — one-click "create both SE + property" for a fresh NINO.
# Used by the dashboard's "Set me up with a complete sandbox" button.
# ---------------------------------------------------------------------------


WANTED_BUSINESS_TYPES: tuple[tuple[str, str], ...] = (
    ("self-employment", "Sandbox sole trader"),
    ("property",        "Sandbox property"),
)


def setup_complete_sandbox(*, user_id: int, request_obj) -> dict:
    """Idempotent. Creates whichever of (SE, property) businesses the user
    doesn't already have. Returns a structured response listing what was
    newly created vs what already existed, plus the user's NINO.

    Raises:
      ValueError       — if the user hasn't entered + saved their NINO.
      HmrcApiError     — from underlying HMRC client on a non-2xx.
    """
    info = _tokens.get_tokens(user_id) or {}
    nino = info.get("nino")
    if not nino:
        raise ValueError(
            "Enter your sandbox NINO in the dashboard and click "
            "'Discover my businesses' once before using this setup."
        )

    existing = list(info.get("businesses") or [])
    existing_types = {(b.get("type_of_business") or "").lower() for b in existing}

    created: list[dict] = []
    already: list[dict] = []

    for type_of_business, default_name in WANTED_BUSINESS_TYPES:
        if type_of_business in existing_types:
            for b in existing:
                if (b.get("type_of_business") or "").lower() == type_of_business:
                    already.append(b)
                    break
            continue

        try:
            result = create_test_business(
                user_id=user_id, request_obj=request_obj,
                type_of_business=type_of_business,
                trading_name=default_name,
            )
        except _client.HmrcApiError as exc:
            # Annotate the error with WHICH business type failed so the
            # endpoint surfaces something diagnostic to the dashboard.
            raise _client.HmrcApiError(
                status_code=exc.status_code,
                body=f"creating the {type_of_business} business: {exc.body}",
            ) from exc
        biz_id = (
            result.get("businessId")
            or (result.get("business") or {}).get("businessId")
        )
        if not biz_id:
            # HMRC accepted the call but the shape's unexpected — surface
            # the raw response so the caller can show it to the dev.
            raise _client.HmrcApiError(
                status_code=0,
                body=f"HMRC accepted the {type_of_business} create but "
                     f"returned no businessId. Raw: {result}",
            )

        new_business = {
            "business_id": biz_id,
            "type_of_business": type_of_business,
            "label": default_name,
        }
        existing.append(new_business)
        existing_types.add(type_of_business)
        created.append(new_business)

    # One persistence flush at the end regardless of how many we created.
    if created:
        _tokens.save_nino_and_businesses(user_id, nino, existing)

    return {"created": created, "already_existed": already, "nino": nino}
