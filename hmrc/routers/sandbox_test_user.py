"""
HMRC sandbox-only — POST /api/hmrc/sandbox/create-test-user.

Mints a fresh sandbox individual via HMRC's Create Test User API and
returns the credentials in the response so the user can plug them into
the dashboard NINO field + OAuth with the Government Gateway userId/
password the API hands back. Sandbox-only.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..services import client as _client
from ..services import sandbox as _sandbox

logger = logging.getLogger("bankparse.hmrc.routes.sandbox.test_user")
router = APIRouter()


class TestUserResponse(BaseModel):
    """Plain-data echo of HMRC's create-test-user/individuals response.
    Extra fields HMRC adds in future are preserved verbatim under
    ``raw`` so we don't drop them silently."""
    status: str = "ok"
    nino: str | None = None
    user_id: str | None = Field(None, alias="userId")
    password: str | None = None
    mtd_it_id: str | None = Field(None, alias="mtdItId")
    sa_utr: str | None = Field(None, alias="saUtr")
    user_full_name: str | None = Field(None, alias="userFullName")
    email: str | None = Field(None, alias="emailAddress")
    group_identifier: str | None = Field(None, alias="groupIdentifier")
    raw: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


def _refuse_in_production():
    if not _sandbox.is_sandbox():
        raise HTTPException(
            status_code=404,
            detail="Not available in production. Sandbox-only route.",
        )


@router.post(
    "/api/hmrc/sandbox/create-test-user",
    response_model=TestUserResponse,
)
async def create_test_user(request: Request) -> TestUserResponse:
    """Mint a fresh sandbox test individual. Returns NINO + GG creds."""
    _refuse_in_production()
    try:
        from app import get_current_user  # type: ignore
        user = get_current_user(request)
    except Exception:
        user = None
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    try:
        data = _sandbox.create_test_individual()
    except _client.HmrcApiError as exc:
        raise HTTPException(
            status_code=502 if exc.status_code == 0 else 400,
            detail=f"HMRC returned {exc.status_code}: {exc.body}",
        )

    return TestUserResponse(
        nino=data.get("nino"),
        userId=data.get("userId"),
        password=data.get("password"),
        mtdItId=data.get("mtdItId"),
        saUtr=data.get("saUtr"),
        userFullName=data.get("userFullName"),
        emailAddress=data.get("emailAddress"),
        groupIdentifier=data.get("groupIdentifier"),
        raw=data,
    )
