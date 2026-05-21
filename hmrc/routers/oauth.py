"""
HMRC OAuth router — /api/hmrc/connect and /api/hmrc/callback.

Routes wire up like this in app.py:

    from hmrc.routers import oauth as hmrc_oauth
    app.include_router(hmrc_oauth.router)

`state` is held in a short-lived signed cookie so we can verify it survived
the round-trip without needing a server-side session table.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import config as _cfg
from ..services import oauth as _oauth
from ..repositories import tokens as _tokens

logger = logging.getLogger("bankparse.hmrc.routes.oauth")

router = APIRouter()


@router.get("/api/hmrc/connect")
async def hmrc_connect(request: Request):
    """Begin the OAuth flow — redirect the user to HMRC's authorize URL.

    Auth requirement is enforced by the calling app via `get_current_user`;
    we expect `request.state.user` to be populated, OR we bounce to /login.
    """
    if not _cfg.is_configured():
        raise HTTPException(status_code=501, detail="HMRC integration not configured.")

    user = getattr(request.state, "user", None) or _get_user_via_app(request)
    if not user:
        return RedirectResponse(url="/login?next=/api/hmrc/connect", status_code=302)

    state = _oauth.new_state()
    url = _oauth.build_authorize_url(state)

    response = RedirectResponse(url=url, status_code=302)
    # Bind `state` to the user's browser with a short-lived cookie.
    response.set_cookie(
        "bp_hmrc_state",
        state,
        max_age=600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/hmrc",
    )
    return response


@router.get("/api/hmrc/callback")
async def hmrc_callback(request: Request):
    """HMRC redirects here with `?code=...&state=...` after the user consents."""
    if not _cfg.is_configured():
        raise HTTPException(status_code=501, detail="HMRC integration not configured.")

    user = getattr(request.state, "user", None) or _get_user_via_app(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    expected_state = request.cookies.get("bp_hmrc_state")

    if not code:
        err = request.query_params.get("error", "missing_code")
        logger.warning("HMRC callback missing code for user %s: %s", user["id"], err)
        return RedirectResponse(url=f"/hmrc/connect?status=error&detail={err}", status_code=302)

    if not state or state != expected_state:
        logger.warning("HMRC callback state mismatch for user %s", user["id"])
        return RedirectResponse(url="/hmrc/connect?status=error&detail=state_mismatch", status_code=302)

    try:
        tokens = _oauth.exchange_code_for_tokens(code)
    except Exception:
        logger.exception("HMRC token exchange failed for user %s", user["id"])
        return RedirectResponse(url="/hmrc/connect?status=error&detail=token_exchange", status_code=302)

    _tokens.save_tokens(
        user_id=user["id"],
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_in_seconds=int(tokens.get("expires_in", 14400)),
        scope=tokens.get("scope", ""),
    )

    response = RedirectResponse(url="/hmrc/connect?status=ok", status_code=302)
    response.delete_cookie("bp_hmrc_state", path="/api/hmrc")
    return response


@router.post("/api/hmrc/disconnect")
async def hmrc_disconnect(request: Request):
    """Forget this user's stored HMRC tokens so they can re-OAuth as a
    different Government Gateway user. Used by the sandbox flow when the
    developer mints a new test individual and needs the OAuth identity
    to match the new NINO. Also useful in production if tokens are
    irrecoverably stale.

    Wipes the entire `hmrc_connections` row (tokens, NINO, businesses) —
    safe because re-OAuth + 'Discover my businesses' rebuilds it.
    """
    user = getattr(request.state, "user", None) or _get_user_via_app(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    _tokens.revoke(user["id"])
    return JSONResponse({"ok": True})


def _get_user_via_app(request: Request) -> dict | None:
    """Best-effort fallback to read the logged-in user without a hard coupling."""
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None
