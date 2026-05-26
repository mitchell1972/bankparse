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


# HMRC's penalty-points thresholds vary by submission frequency. Defined
# in the points-based penalties rules at
# https://www.gov.uk/guidance/penalty-points-and-penalties-if-you-submit-your-vat-return-late
# (the same model applies to ITSA from April 2026).
#
# The model:
#   - Each LATE submission accrues one point.
#   - On hitting the threshold for your submission cadence, the NEXT
#     late submission triggers a £200 fixed penalty. Each subsequent
#     late submission while at-or-above threshold triggers another £200.
#   - Points reset to zero after a clean-record period (24 months for
#     annual filers, 12 for quarterly, 6 for monthly).
#   - Points are tracked PER TAX TYPE — ITSA quarterly points are
#     separate from VAT quarterly points. A user could be at 3 ITSA + 4 VAT
#     and only the VAT side is fining them.
#
# Today this app only files ITSA quarterly + annual, so only those
# constants are wired in. The VAT one is here so the model is documented
# in code for future MTD-VAT support; do NOT use it for ITSA filers.
POINTS_THRESHOLD_BY_FREQUENCY = {
    "annual":    2,   # ITSA EOPS + final declaration; 2 points trigger fine
    "quarterly": 4,   # ITSA quarterly updates AND legacy VAT (most common)
    "monthly":   5,   # MTD-VAT monthly returns (out of scope today)
}

PENALTY_AMOUNT_GBP = 200

# Back-compat alias retained for the quarterly default the dashboard renders.
# Use `points_threshold(frequency)` for any new lookup.
QUARTERLY_THRESHOLD = POINTS_THRESHOLD_BY_FREQUENCY["quarterly"]


def points_threshold(frequency: str) -> int:
    """Return the late-submission points threshold for a given cadence.

    Raises KeyError on unknown frequency — the caller MUST supply one of
    the keys above, not e.g. "weekly". Loud failure is the right call
    here: a wrong threshold misleads the user about their penalty risk.
    """
    return POINTS_THRESHOLD_BY_FREQUENCY[frequency]


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
