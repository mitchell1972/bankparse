"""
User-facing submission history.

HMRC recognition requires we can show the user every API call we've made
on their behalf — and the user themselves wants reassurance that their
quarterly update actually went through. This router exposes both as a
single GET.

  GET /api/hmrc/submissions
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from ..repositories import submissions as _repo
from . import _quarterly_common as _common

logger = logging.getLogger("bankparse.hmrc.routes.submissions")
router = APIRouter()


@router.get("/api/hmrc/submissions")
async def list_my_submissions(request: Request):
    """Return the authenticated user's HMRC submissions newest-first.

    Each row carries a human-readable label, a derived success flag, and
    (where parseable) the HMRC reference + period covered, so the UI can
    render a clean "Submission history" list without needing the raw API
    responses."""
    user = _common.require_user(request)
    rows = _repo.list_for_user(user["id"], limit=100)
    return {"submissions": rows, "count": len(rows)}
