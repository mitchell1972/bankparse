"""
Schemas for the Obligations (MTD) API response.

Endpoints:
    GET /individuals/business/self-employment/{nino}/{businessId}/obligations
    GET /individuals/business/property/{nino}/{businessId}/obligations
"""

from __future__ import annotations

from pydantic import BaseModel


class Obligation(BaseModel):
    periodKey: str            # e.g. '#001' — HMRC's identifier for a period
    start: str                # YYYY-MM-DD
    end: str                  # YYYY-MM-DD
    due: str                  # YYYY-MM-DD
    status: str               # 'Open' | 'Fulfilled'
    received: str | None = None  # YYYY-MM-DD when submission was received


class Obligations(BaseModel):
    obligations: list[Obligation]
