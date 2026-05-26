"""
Authenticated HMRC HTTP client — attaches OAuth bearer + fraud headers to
every MTD call and writes an immutable audit row.

This is the ONLY place in the codebase that talks to HMRC's REST API. Every
endpoint (obligations, quarterly updates, EOPS, final declaration) calls
through `request()` so we get:

  * automatic Bearer injection from `repositories/tokens`
  * automatic 401 → refresh → retry-once
  * all 13 fraud-prevention headers via `services/fraud_headers`
  * one row in `hmrc_submissions` per call (audit trail HMRC require for
    recognition)
  * consistent timeouts + idempotency-key handling
  * structured error logging so we can debug recognition failures

Sync wrapper. Call from async code with `asyncio.to_thread(...)`.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .. import config as _cfg
from ..repositories import sessions as _sessions
from ..repositories import submissions as _submissions
from ..repositories import tokens as _tokens
from . import fraud_headers as _fraud
from . import monitoring as _monitoring
from . import oauth as _oauth
from . import rate_limiter as _rate_limiter

logger = logging.getLogger("bankparse.hmrc.client")

# Per HMRC API best practice. Most MTD endpoints respond in <2 s but the
# sandbox can occasionally take 10 s under load. We err on the safe side.
_DEFAULT_TIMEOUT = 30.0

# When HMRC returns 401 we refresh the token and retry exactly once. More
# than that and we'd loop on a permanently revoked grant.
_MAX_RETRIES_ON_401 = 1


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HmrcNotConnectedError(RuntimeError):
    """User hasn't completed OAuth — needs to /api/hmrc/connect first."""


class HmrcApiError(RuntimeError):
    """HMRC returned a non-2xx status. Contains the parsed problem details."""
    def __init__(self, status_code: int, body: Any, audit_id: str | None = None):
        self.status_code = status_code
        self.body = body
        self.audit_id = audit_id
        super().__init__(f"HMRC {status_code}: {self._summary(body)}")

    @staticmethod
    def _summary(body: Any) -> str:
        if isinstance(body, dict):
            return body.get("message") or body.get("code") or str(body)[:200]
        return str(body)[:200]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HmrcResponse:
    """Caller-facing wrapper around a successful HMRC API call."""
    status_code: int
    json: Any
    headers: dict[str, str]
    audit_id: str


def request(
    *,
    user_id: int,
    method: str,
    path: str,
    request_obj,
    json_body: Any | None = None,
    accept_version: str = "application/vnd.hmrc.1.0+json",
    idempotency_key: str | None = None,
    timeout: float | None = None,
) -> HmrcResponse:
    """Make an authenticated HMRC API call.

    Args:
        user_id:        BankParse user — used to look up tokens + audit row owner.
        method:         "GET" | "POST" | "PUT" | "DELETE"
        path:           e.g. "/individuals/business/property/AB123456C/AB123456A/obligations"
        request_obj:    incoming FastAPI/Starlette Request — used to extract real
                        fraud-prevention fields from the end user's browser/network.
        json_body:      optional JSON payload (POST/PUT only).
        accept_version: HMRC API version header. Each MTD endpoint pins a version.
        idempotency_key: optional UUID for POST endpoints HMRC requires it on
                        (quarterly updates, EOPS, final declaration). Auto-
                        generated if not provided AND method is POST/PUT.
        timeout:        per-call timeout in seconds (default 30).

    Raises:
        HmrcNotConnectedError if the user hasn't completed OAuth.
        HmrcApiError if HMRC returns non-2xx after one refresh-retry.
    """
    import httpx  # local import: keeps cold-start cheap when HMRC isn't used

    tokens = _tokens.get_tokens(user_id)
    if not tokens:
        raise HmrcNotConnectedError(
            "User has no HMRC connection. Send them to /api/hmrc/connect."
        )

    fraud_ctx = _load_fraud_context(request_obj)
    method = method.upper()
    if method in ("POST", "PUT") and not idempotency_key:
        idempotency_key = str(uuid.uuid4())

    url = f"{_cfg.HMRC_BASE_URL}{path}"

    # First attempt; on 401 we refresh and try again exactly once.
    for attempt in range(_MAX_RETRIES_ON_401 + 1):
        access_token = tokens["access_token"]
        headers = _compose_headers(
            access_token=access_token,
            fraud_ctx=fraud_ctx,
            request_obj=request_obj,
            user_id=user_id,
            accept_version=accept_version,
            idempotency_key=idempotency_key,
        )
        # Throttle outbound HMRC traffic to stay under per-vendor caps. A
        # runaway loop or burst from a single greedy user would otherwise
        # trip the cap and brick the service for everyone. The limiter is
        # a process-local token bucket — see hmrc/services/rate_limiter.py.
        # Raises RateLimitedError after HMRC_OUTBOUND_MAX_WAIT_SEC.
        waited = _rate_limiter.acquire()
        if waited > 0.5:
            logger.info(
                "HMRC outbound throttled: waited %.2fs for token (user=%s path=%s)",
                waited, user_id, path,
            )

        resp, audit_id = _do_call_and_audit(
            method=method, url=url, path=path,
            headers=headers, json_body=json_body,
            user_id=user_id, idempotency_key=idempotency_key,
            timeout=timeout or _DEFAULT_TIMEOUT,
        )
        if resp.status_code != 401 or attempt >= _MAX_RETRIES_ON_401:
            break
        # Refresh the token + persist (HMRC rotates refresh tokens — must save).
        logger.info("HMRC 401 — refreshing token and retrying (user=%s)", user_id)
        try:
            refreshed = _oauth.refresh_access_token(tokens["refresh_token"])
        except Exception:
            logger.exception("Token refresh failed (user=%s)", user_id)
            raise HmrcApiError(401, {"code": "refresh_failed"}, audit_id=audit_id)
        _tokens.save_tokens(
            user_id=user_id,
            access_token=refreshed["access_token"],
            refresh_token=refreshed["refresh_token"],
            expires_in_seconds=int(refreshed.get("expires_in", 14400)),
            scope=refreshed.get("scope", ""),
        )
        tokens = _tokens.get_tokens(user_id) or tokens  # re-read fresh

    body = _safe_json(resp)
    if not (200 <= resp.status_code < 300):
        # Send 5xx (HMRC-side trouble) + 0 (network) events to Sentry so
        # we get paged on the class of failure that signals recognition-
        # rejection-shaped issues. 4xx is user error — never alertable.
        _monitoring.capture_hmrc_failure(
            endpoint=path, method=method, status_code=resp.status_code,
            body=body, user_id=user_id, audit_id=audit_id,
        )
        raise HmrcApiError(resp.status_code, body, audit_id=audit_id)
    return HmrcResponse(
        status_code=resp.status_code,
        json=body,
        headers={k: v for k, v in resp.headers.items()},
        audit_id=audit_id,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _load_fraud_context(request_obj) -> dict:
    """Pull the browser-collected fraud-prevention fields for this session."""
    if request_obj is None:
        return {}
    session_id = request_obj.cookies.get("bp_auth", "") if hasattr(request_obj, "cookies") else ""
    if not session_id:
        return {}
    return _sessions.get_for_session(session_id) or {}


def _compose_headers(
    *,
    access_token: str,
    fraud_ctx: dict,
    request_obj,
    user_id: int,
    accept_version: str,
    idempotency_key: str | None,
) -> dict[str, str]:
    headers: dict[str, str] = {
        "Authorization": f"Bearer {access_token}",
        "Accept": accept_version,
        "Content-Type": "application/json",
    }
    fraud = _fraud.build_headers(
        request=request_obj,
        fraud_context=fraud_ctx,
        user_id=user_id,
    )
    # Filter out empty values — HMRC rejects headers with blank values for
    # mandatory fields, so let the validator catch missing data rather than
    # us silently sending "".
    for k, v in fraud.items():
        if v:
            headers[k] = v
    if idempotency_key:
        # Quarterly-update / EOPS / final-declaration endpoints require this.
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _do_call_and_audit(
    *, method: str, url: str, path: str, headers: dict, json_body: Any | None,
    user_id: int, idempotency_key: str | None, timeout: float,
):
    """Make the HTTP call, persist an audit row, return (response, audit_id).

    The audit row is written even on network failure — we record the
    request we attempted and `response_status=0` so we can replay it later.
    """
    import httpx

    started = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(
                method=method, url=url, headers=headers,
                json=json_body if json_body is not None else None,
            )
    except httpx.RequestError as exc:
        # Network-level failure — still audit the attempt.
        audit_id = _submissions.record(
            user_id=user_id, endpoint=path, method=method,
            request_headers=headers, request_body=json_body,
            response_status=0,
            response_headers={},
            response_body={"network_error": str(exc)},
            idempotency_key=idempotency_key,
        )
        logger.warning("HMRC network failure (user=%s, path=%s): %s", user_id, path, exc)
        # Network failures are tagged status=0 — Sentry alerts on these too.
        _monitoring.capture_hmrc_failure(
            endpoint=path, method=method, status_code=0,
            body={"network_error": str(exc)},
            user_id=user_id, audit_id=audit_id,
        )
        raise HmrcApiError(0, {"network_error": str(exc)}, audit_id=audit_id) from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    audit_id = _submissions.record(
        user_id=user_id, endpoint=path, method=method,
        request_headers=headers, request_body=json_body,
        response_status=resp.status_code,
        response_headers=dict(resp.headers),
        response_body=_safe_json(resp),
        idempotency_key=idempotency_key,
    )
    logger.info(
        "HMRC %s %s -> %d in %dms (user=%s, audit=%s)",
        method, path, resp.status_code, elapsed_ms, user_id, audit_id,
    )
    return resp, audit_id


def _safe_json(resp) -> Any:
    """Try to decode the response as JSON, fall back to the raw text body."""
    try:
        return resp.json()
    except Exception:
        try:
            return {"raw": resp.text[:2000]}
        except Exception:
            return None
