"""
Browser-collected fraud-prevention context endpoint.

The browser-side collector (`static/hmrc/fraud-collect.js`) POSTs a JSON
payload to /api/hmrc/fraud-context on first login / when fields change.
We persist it keyed by the user's bp_auth session id; every outbound HMRC
call then merges these values with server-collected ones (IP, port, etc.)
in `services/fraud_headers.build_headers()`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..repositories import sessions as _sessions

logger = logging.getLogger("bankparse.hmrc.routes.fraud_context")
router = APIRouter()


@router.post("/api/hmrc/fraud-context")
async def post_fraud_context(request: Request):
    """Persist the browser-collected fraud-prevention fields for this session."""
    user = getattr(request.state, "user", None) or _get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    session_id = request.cookies.get("bp_auth", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="No session cookie.")

    body = await request.json()
    # Defensive trimming: cap absurdly large values to avoid header inflation.
    body = {
        "device_id": str(body.get("device_id", ""))[:64],
        "browser_user_agent": str(body.get("browser_user_agent", ""))[:512],
        "timezone": str(body.get("timezone", ""))[:32],
        "screens": (body.get("screens") or [])[:6],
        "window": body.get("window") or {},
        "mfa": (body.get("mfa") or [])[:5],
    }

    _sessions.upsert(session_id, body)
    return JSONResponse({"status": "ok"})


def _get_user(request: Request) -> dict | None:
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None
