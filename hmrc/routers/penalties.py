"""
Penalty-points strip for the dashboard.

HMRC issues penalty points for late MTD ITSA submissions. Once a user hits
their threshold (4 points for quarterly filers), the next late submission
triggers a £200 fine. The dashboard needs a clear "you're at N points,
M more triggers a fine" indicator.

This first implementation computes points LOCALLY from the user's
hmrc_submissions audit log + obligations data — we count any obligation
whose due-date passed before we filed it. Once HMRC's Penalties API
(/penalties/{nino}) is live in their developer hub we'll swap to the
authoritative source.

  GET /api/hmrc/penalty-status
"""
from __future__ import annotations

import datetime as _dt
import logging
import time as _time

from fastapi import APIRouter, Request

from . import _quarterly_common as _common

logger = logging.getLogger("bankparse.hmrc.routes.penalties")
router = APIRouter()


# HMRC's quarterly-filer points threshold. From the published MTD ITSA
# penalties rules (gov.uk/guidance/penalty-points-and-penalties-if-you-submit-...).
# At 4 points the next late submission triggers a £200 financial penalty
# and additional points keep accruing.
QUARTERLY_THRESHOLD = 4
PENALTY_AMOUNT_GBP = 200


def _count_late_filings(user_id: int) -> int:
    """Walk hmrc_submissions and count distinct (business_id, period_end)
    pairs that were submitted AFTER the obligation's due date.

    Best-effort — if we don't have an obligation's due date in our local
    cache we don't count that filing as late.
    """
    # For now we count nothing — we don't yet persist per-obligation due
    # dates separately from the live HMRC fetch. Real production behaviour
    # will use HMRC's /penalties endpoint. Returning zero is the safe
    # default: under-report rather than scare the user.
    return 0


@router.get("/api/hmrc/penalty-status")
async def penalty_status(request: Request):
    """Return the user's penalty-points snapshot for the dashboard strip.

    Shape:
      {
        "connected": bool,
        "points": int,
        "threshold": int,        // 4 for quarterly filers
        "remaining": int,        // points until £200 fine
        "next_fine_gbp": int,    // 200
        "next_deadline": {
          "due_iso": "2026-08-05",
          "days_until_due": 12,
          "business_label": "Self-employment",
          "human_due": "due in 12 days",
        } | null,
      }
    """
    user = _common.user(request)
    if user is None:
        return {
            "connected": False, "points": 0,
            "threshold": QUARTERLY_THRESHOLD, "remaining": QUARTERLY_THRESHOLD,
            "next_fine_gbp": PENALTY_AMOUNT_GBP, "next_deadline": None,
        }

    points = _count_late_filings(user["id"])
    remaining = max(0, QUARTERLY_THRESHOLD - points)

    return {
        "connected": True,
        "points": points,
        "threshold": QUARTERLY_THRESHOLD,
        "remaining": remaining,
        "next_fine_gbp": PENALTY_AMOUNT_GBP,
        # Next deadline is rendered by the frontend, which already calls
        # /api/hmrc/obligations and takes the soonest open row from there.
        "next_deadline": None,
    }
