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


# ---------------------------------------------------------------------------
# Read-side helpers — for the user-facing "Submission history" screen.
# ---------------------------------------------------------------------------


# Endpoint → friendly label + submission type. Keyed on `endpoint` substring
# (we record the full URL but the suffix is enough to identify the action).
_KIND_RULES: list[tuple[str, str, str]] = [
    # (endpoint_substring, kind_label, plain-english status verb)
    ("self-employment-business",        "Quarterly update (Self-employment)", "filed"),
    ("uk-property-business",            "Quarterly update (UK property)",     "filed"),
    ("/individuals/business/end-of-period-statement", "End of period statement", "submitted"),
    ("eops",                            "End of period statement",            "submitted"),
    ("crystallisation",                 "Final declaration",                  "filed"),
    ("final-declaration",               "Final declaration",                  "filed"),
    ("calculations",                    "Tax calculation",                    "calculated"),
    ("obligations",                     "Obligations check",                  "checked"),
    ("business-details",                "Business details lookup",            "looked up"),
]


def _classify(endpoint: str) -> tuple[str, str]:
    """Map a raw endpoint URL to a (human label, status verb) pair."""
    e = (endpoint or "").lower()
    for needle, label, verb in _KIND_RULES:
        if needle in e:
            return label, verb
    return "HMRC call", "called"


def list_for_user(user_id: int, *, limit: int = 100) -> list[dict]:
    """Return the user's submissions newest-first with a human-readable
    label and a derived success flag (2xx response status)."""
    from database import _fetchall_dicts
    import json as _json

    rows = _fetchall_dicts(
        "SELECT audit_id, endpoint, method, response_status, "
        "       request_body_json, response_body_json, "
        "       idempotency_key, created_at "
        "FROM hmrc_submissions "
        "WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (int(user_id), int(limit)),
    )
    out: list[dict] = []
    for r in rows:
        label, verb = _classify(r["endpoint"] or "")
        status = int(r.get("response_status") or 0)
        ok = 200 <= status < 300
        # Try to pull a useful identifier from the response body.
        ref = None
        try:
            body = _json.loads(r.get("response_body_json") or "null")
            if isinstance(body, dict):
                # MTD ITSA responses include either `transactionReference`,
                # `submissionId`, or `calculationId` depending on the op.
                ref = (
                    body.get("transactionReference")
                    or body.get("submissionId")
                    or body.get("calculationId")
                    or body.get("id")
                )
        except (TypeError, _json.JSONDecodeError):
            pass
        # Period start/end (where we have them in the request body)
        period_start = period_end = None
        try:
            req = _json.loads(r.get("request_body_json") or "null")
            if isinstance(req, dict):
                period_start = (
                    req.get("periodStartDate")
                    or req.get("periodFromDate")
                    or req.get("from")
                )
                period_end = (
                    req.get("periodEndDate")
                    or req.get("periodToDate")
                    or req.get("to")
                )
        except (TypeError, _json.JSONDecodeError):
            pass

        out.append({
            "audit_id": r["audit_id"],
            "endpoint": r["endpoint"],
            "method": r["method"],
            "label": label,
            "status_verb": verb,
            "response_status": status,
            "ok": ok,
            "hmrc_reference": ref,
            "period_start": period_start,
            "period_end": period_end,
            "idempotency_key": r.get("idempotency_key"),
            "created_at": r["created_at"],
        })
    return out
