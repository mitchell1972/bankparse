"""
Quarterly submission orchestrator.

Stub for the first scaffold PR. The full version will:
  1. Pull rows from `user_extracted_data` for [from_date, to_date].
  2. Run them through `mapping.aggregate_self_employment()` or
     `aggregate_property()` based on `business_type`.
  3. Build the HMRC payload via `schemas.se_quarterly` / `schemas.property_quarterly`.
  4. POST via `client.call()` with an idempotency key derived from user_id +
     business_id + period_key.
  5. Persist a copy of the full request + HMRC response to
     `hmrc_submissions` (immutable audit log).

This file exists so callers can `from hmrc.services import submissions` and
the import succeeds even though the body is unimplemented.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("bankparse.hmrc.submissions")


def build_se_quarterly_preview(user_id: int, business_id: str, from_date: str, to_date: str) -> dict:
    """STUB. Will return aggregate income/expense by category for the period."""
    raise NotImplementedError


def submit_se_quarterly(user_id: int, business_id: str, from_date: str, to_date: str, request, fraud_context: dict) -> dict:
    """STUB. Will POST the quarterly summary to HMRC and persist the receipt."""
    raise NotImplementedError


def build_property_quarterly_preview(user_id: int, business_id: str, from_date: str, to_date: str) -> dict:
    """STUB. Same as SE but for UK property."""
    raise NotImplementedError


def submit_property_quarterly(user_id: int, business_id: str, from_date: str, to_date: str, request, fraud_context: dict) -> dict:
    """STUB."""
    raise NotImplementedError
