"""
Per-session storage of browser-collected fraud-prevention fields.

Used by the OAuth + every MTD call path:
    1. On login the browser POSTs `/api/hmrc/fraud-context` with the 5
       browser-collected fields (device_id, ua, screens, window, timezone).
    2. We persist them keyed by bp_auth session.
    3. Every outbound HMRC call calls `get_for_session()` to fetch them and
       hand them to `fraud_headers.build_headers()`.
"""

from __future__ import annotations

import json
import time


def upsert(session_id: str, fraud_context: dict) -> None:
    from database import _execute
    _execute(
        """
        INSERT INTO hmrc_fraud_sessions (session_id, fraud_context_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
          fraud_context_json = excluded.fraud_context_json,
          updated_at = excluded.updated_at
        """,
        (session_id, json.dumps(fraud_context), time.time()),
    )


def get_for_session(session_id: str) -> dict | None:
    from database import _fetchone_dict
    row = _fetchone_dict(
        "SELECT fraud_context_json FROM hmrc_fraud_sessions WHERE session_id = ?",
        (session_id,),
    )
    if not row:
        return None
    try:
        return json.loads(row["fraud_context_json"])
    except (TypeError, ValueError):
        return None
