"""
Immutable audit log of every HMRC API call we make on a user's behalf.

HMRC software recognition requires we can prove what we sent on every
submission. This table is append-only. The `request_body_json` /
`response_body_json` columns hold the literal payloads. Sensitive headers
(bearer tokens) are stripped before storage; fraud headers are retained
because HMRC sometimes asks to see them.
"""

from __future__ import annotations

import json
import time
import uuid


def record(
    *,
    user_id: int,
    endpoint: str,
    method: str,
    request_headers: dict,
    request_body: object,
    response_status: int,
    response_headers: dict,
    response_body: object,
    idempotency_key: str | None = None,
) -> str:
    """Insert one immutable audit row. Returns the generated UUID."""
    from database import _execute_insert

    audit_id = str(uuid.uuid4())
    # Strip the bearer token — never persist it.
    safe_req_headers = {k: v for k, v in request_headers.items() if k.lower() != "authorization"}

    _execute_insert(
        """
        INSERT INTO hmrc_submissions
          (audit_id, user_id, endpoint, method,
           request_headers_json, request_body_json,
           response_status, response_headers_json, response_body_json,
           idempotency_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            user_id,
            endpoint,
            method,
            json.dumps(safe_req_headers),
            json.dumps(request_body) if request_body is not None else None,
            int(response_status),
            json.dumps(dict(response_headers)),
            json.dumps(response_body) if response_body is not None else None,
            idempotency_key,
            time.time(),
        ),
    )
    return audit_id
