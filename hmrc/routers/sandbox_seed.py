"""
HMRC sandbox-only — POST /api/hmrc/sandbox/seed-sample-data.

Drops ~18 pre-categorised ledger transactions into the user's account
spread across the current MTD ITSA quarter, so a freshly-Mint'd sandbox
user lands on a fully-populated dashboard (real totals on the obligations
card, non-£0.00 quarterly submit payloads) without having to upload a
bank statement first.

Sandbox-only. The route returns 404 in production — see
`hmrc.services.sandbox.is_sandbox` — so customer ledgers can never be
poisoned with fixture data.

Lives in its own router file (rather than alongside create-test-business
in `sandbox.py`) to keep each router under the 150-non-blank-line cap
enforced by `tests/hmrc/test_architecture.py`.
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services import sandbox as _sandbox

logger = logging.getLogger("bankparse.hmrc.routes.sandbox.seed")
router = APIRouter()


class SeedSampleDataResponse(BaseModel):
    status: Literal["ok"] = "ok"
    inserted: int = 0
    skipped_existing: int = 0
    period_start: str | None = None
    period_end: str | None = None


def _user(request: Request) -> dict | None:
    """Lazy import to avoid circular dep on `app`."""
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


def _refuse_in_production():
    if not _sandbox.is_sandbox():
        raise HTTPException(
            status_code=404,
            detail="Not available in production. Sandbox-only route.",
        )


@router.post(
    "/api/hmrc/sandbox/seed-sample-data",
    response_model=SeedSampleDataResponse,
)
async def seed_sample_data(request: Request) -> SeedSampleDataResponse:
    """One-click sample-data seeder for sandbox demos.

    Inserts ~18 pre-categorised transactions across the current quarter
    so the dashboard shows realistic income/expense totals and the
    quarterly submit buttons send non-zero payloads. Idempotent via
    content_hash on (date, description, amount).

    NB: does NOT require the user to have an HMRC connection — it only
    writes to our own ledger. This lets developers seed before OAuth so
    the dashboard preview works without going through HMRC at all.
    """
    _refuse_in_production()
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    result = _sandbox.seed_sample_transactions(user["id"])
    return SeedSampleDataResponse(**result)
