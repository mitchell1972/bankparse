"""
HMRC Business Details service — enumerates the businesses linked to a NINO.

Wraps `GET /individuals/business/details/{nino}/list` (Business Details API
v1.0) and maps the response into the same `UiBusiness` shape that the
obligations + tokens layers already expect.

Why this exists
---------------
Without this endpoint, our setup flow required the user to:

  1. Sign into the HMRC developer hub
  2. Find their business IDs by hand
  3. Paste them into the dashboard

With it, the user types a NINO once and we discover every business HMRC
has on file for them.

Env knobs:
    HMRC_BUSINESS_DETAILS_API_VERSION   (default "application/vnd.hmrc.1.0+json")
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from ..schemas.business_details import (
    HmrcBusinessList,
    NormalisedBusinessType,
    UiBusiness,
)
from . import client as _client

logger = logging.getLogger("bankparse.hmrc.business_details")


_API_VERSION = "application/vnd.hmrc.1.0+json"


def persist_nino_only(user_id: int, nino: str) -> None:
    """Save the NINO so downstream sandbox-bootstrap endpoints can find it.

    Refuses to overwrite when the user is already in a Live state — i.e.
    has businesses saved against an existing NINO. Without this guard, a
    probe call (or stale auto-Discover round) with a different NINO can
    silently demote a working setup to demo mode, then the next Set-me-up
    fails with OAUTH_NINO_MISMATCH against the wrong NINO. Discovered
    during the bankscanai.com production journey on 2026-05-22.

    Switching NINOs is still possible via:
      - Successful connect-businesses (the happy path — saves new NINO +
        the businesses HMRC returns for it)
      - Explicit /api/hmrc/disconnect → re-OAuth → re-Discover
    """
    from ..repositories import tokens as _tokens
    info = _tokens.get_tokens(user_id) or {}
    existing_nino = info.get("nino")
    existing_businesses = info.get("businesses") or []
    # If we already have businesses saved against a different NINO, don't
    # demote. The caller's HTTP path still 404s; the user can fix via the
    # documented Disconnect-and-retry flow.
    if existing_businesses and existing_nino and existing_nino != nino:
        return
    _tokens.save_nino_and_businesses(user_id, nino, existing_businesses)


def fetch_for_nino(*, user_id: int, nino: str, request_obj) -> list[UiBusiness]:
    """Call HMRC and return the user's businesses in our UI shape.

    Raises `client.HmrcApiError` / `HmrcNotConnectedError` on failure —
    callers (router / setup flow) decide how to surface it.
    """
    path = f"/individuals/business/details/{nino}/list"
    resp = _client.request(
        user_id=user_id, method="GET", path=path,
        request_obj=request_obj,
        accept_version=os.environ.get(
            "HMRC_BUSINESS_DETAILS_API_VERSION", _API_VERSION,
        ),
    )
    parsed = HmrcBusinessList(**(resp.json or {"listOfBusinesses": []}))
    return [_map_to_ui(b) for b in parsed.listOfBusinesses]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _map_to_ui(business) -> UiBusiness:
    """HMRC → repository/UI shape."""
    return UiBusiness(
        business_id=business.businessId,
        type_of_business=_normalise_type(business.typeOfBusiness),
        label=_label(business),
    )


def _normalise_type(raw: str) -> NormalisedBusinessType:
    """Collapse HMRC's three types into the two we model in the URL paths.

    HMRC uses:
      - "self-employment"
      - "uk-property"
      - "foreign-property"

    The MTD ITSA URL paths use `self-employment` and `property`. We treat
    foreign-property as property too — submissions for foreign property
    go to the same Property Business API endpoint with a different field
    in the payload.
    """
    r = (raw or "").lower()
    if "property" in r:
        return "property"
    return "self-employment"


def _label(business) -> str:
    """Human-friendly label for the dashboard panel.

    Prefer the trading name HMRC has on file. Fall back to "<type> business"
    so the user can still tell rows apart.
    """
    name = (business.tradingName or "").strip()
    if name:
        return name
    if "property" in (business.typeOfBusiness or "").lower():
        return "UK property"
    return "Self-employment"
