"""HMRC user-facing pages — currently just the connect status page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..repositories import tokens as _tokens

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/hmrc/connect", response_class=HTMLResponse)
async def hmrc_connect_page(request: Request):
    try:
        from app import get_current_user  # type: ignore
        user = get_current_user(request)
    except Exception:
        user = None
    if not user:
        return RedirectResponse(url="/login?next=/hmrc/connect", status_code=302)

    connected = _tokens.get_tokens(user["id"]) is not None
    return templates.TemplateResponse(
        request,
        "hmrc/connect.html",
        {
            "connected": connected,
            "status": request.query_params.get("status", ""),
            "detail": request.query_params.get("detail", ""),
        },
    )
