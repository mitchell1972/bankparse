"""
HMRC sandbox-only HTTP helpers.

These routes ONLY work when `HMRC_ENV != production`. They exist to let a
developer (or an end-user on the sandbox-pointed deploy) seed test data —
specifically MTD ITSA businesses against their sandbox NINO — without
copy-pasting curl commands from the runbook.

Production builds intentionally return 404 from every endpoint here so
this surface area can never be used against real HMRC data.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..repositories import tokens as _tokens
from ..schemas.business_details import UiBusiness
from ..services import client as _client
from ..services import sandbox as _sandbox

logger = logging.getLogger("bankparse.hmrc.routes.sandbox")
router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas (kept local — narrow surface, not reused elsewhere)
# ---------------------------------------------------------------------------

class CreateTestBusinessRequest(BaseModel):
    type_of_business: Literal["self-employment", "property"] = "self-employment"
    trading_name: str | None = Field(default=None, max_length=105)


class CreateTestBusinessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    business: UiBusiness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(request: Request) -> dict | None:
    """Lazy import to avoid circular dependency on `app`."""
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


def _refuse_in_production():
    if not _sandbox.is_sandbox():
        raise HTTPException(
            status_code=404,
            detail="Not available in production. This route is sandbox-only.",
        )


# ---------------------------------------------------------------------------
# Endpoint — POST /api/hmrc/sandbox/create-test-business
# ---------------------------------------------------------------------------

@router.post(
    "/api/hmrc/sandbox/create-test-business",
    response_model=CreateTestBusinessResponse,
)
async def create_test_business(request: Request) -> CreateTestBusinessResponse:
    """Provision one HMRC sandbox business for the connected user's NINO.

    Body: {"type_of_business": "self-employment" | "property",
           "trading_name": "optional"}

    Returns the freshly-created `UiBusiness` and appends it to the user's
    persisted businesses list so the dashboard panel can immediately flip
    to Live (HMRC).
    """
    _refuse_in_production()

    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    try:
        req = CreateTestBusinessRequest(**(body or {}))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request body: {e}")

    try:
        result = _sandbox.create_test_business(
            user_id=user["id"], request_obj=request,
            type_of_business=req.type_of_business,
            trading_name=req.trading_name,
        )
    except _client.HmrcNotConnectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except _client.HmrcApiError as exc:
        # Surface HMRC's status/body — the most common failure is HMRC
        # moving the test-only endpoint between API versions, in which
        # case we want the dev to see exactly what came back.
        raise HTTPException(
            status_code=502 if exc.status_code == 0 else 400,
            detail=f"HMRC returned {exc.status_code}: {exc.body}",
        )

    # HMRC's wire shape for create-business is documented as:
    #   { "businessId": "XAIS00000000001", ... }
    # but the sandbox occasionally returns it nested. Look in both.
    biz_id = result.get("businessId") or (result.get("business") or {}).get("businessId")
    if not biz_id:
        raise HTTPException(
            status_code=502,
            detail=f"HMRC accepted the request but didn't return a businessId. Raw: {result}",
        )

    new_business = UiBusiness(
        business_id=biz_id,
        type_of_business=req.type_of_business,
        label=req.trading_name or _default_label(req.type_of_business),
    )

    # Append (don't replace) to the user's existing business list so they
    # can build up an SE + property pair across two clicks.
    info = _tokens.get_tokens(user["id"]) or {}
    nino = info.get("nino")
    existing = info.get("businesses") or []
    if not any(b.get("business_id") == biz_id for b in existing):
        existing.append(new_business.model_dump())
        _tokens.save_nino_and_businesses(user["id"], nino, existing)

    return CreateTestBusinessResponse(business=new_business)


def _default_label(type_of_business: str) -> str:
    return "Sandbox property" if type_of_business == "property" else "Sandbox sole trader"


# Endpoint — POST /api/hmrc/sandbox/setup-complete
# Thin HTTP adapter; orchestration in services.sandbox.setup_complete_sandbox.


class SetupCompleteResponse(BaseModel):
    status: Literal["ok"] = "ok"
    created: list[UiBusiness] = Field(default_factory=list)
    already_existed: list[UiBusiness] = Field(default_factory=list)
    nino: str | None = None


@router.post(
    "/api/hmrc/sandbox/setup-complete",
    response_model=SetupCompleteResponse,
)
async def setup_complete(request: Request) -> SetupCompleteResponse:
    """One-click sandbox bootstrap. Creates whichever of (SE, property)
    test businesses the user doesn't already have. Idempotent."""
    _refuse_in_production()
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        result = _sandbox.setup_complete_sandbox(
            user_id=user["id"], request_obj=request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except _client.HmrcNotConnectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except _client.HmrcApiError as exc:
        raise HTTPException(
            status_code=502 if exc.status_code == 0 else 400,
            detail=(
                f"HMRC returned {exc.status_code} during sandbox setup: "
                f"{exc.body}"
            ),
        )
    return SetupCompleteResponse(
        created=[UiBusiness(**b) for b in result["created"]],
        already_existed=[UiBusiness(**b) for b in result["already_existed"]],
        nino=result["nino"],
    )
