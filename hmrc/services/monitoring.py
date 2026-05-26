"""
Lightweight monitoring + alerting hook for HMRC-side failures.

Wraps Sentry's SDK behind a tiny init that's a NO-OP when SENTRY_DSN is
unset. That keeps the dev/CI experience zero-config (no extra signups
or quotas) while production gets full structured error capture the
moment you paste the DSN into Railway's env vars.

What gets sent to Sentry:

  - Every uncaught exception in any FastAPI route (default behaviour of
    sentry-sdk's FastAPI integration).
  - HMRC API 5xx or 0 (network) responses, tagged with:
        endpoint   — the HMRC path (PII-stripped of NINOs etc.)
        status     — HTTP status from HMRC
        code       — HMRC's machine-readable error code (e.g. SERVER_ERROR)
        user_hash  — short hash of bankparse user_id (no PII)
        audit_id   — our internal audit-log row id, lets us correlate
                     the Sentry event back to the immutable submission
                     record.

Why HMRC 5xx specifically: HMRC's MTD APIs are HMRC's responsibility,
but a 5xx burst is the first signal of recognition-rejection-class
issues (auth misconfig, fraud-header drift, sandbox vs prod base URL
mix-up). We need an alert pipeline trained on these BEFORE we go live
in April 2027 — adding it now means we'll have alerting history when it
matters.

Env vars:
    SENTRY_DSN          — paste from Sentry project settings. Without it
                          everything in this module is a no-op.
    SENTRY_ENV          — 'production' | 'sandbox' | 'dev'. Defaults to
                          HMRC_ENV so the same DSN can host both.
    SENTRY_TRACES_RATE  — float 0-1, fraction of requests to trace
                          (default 0.0 — errors only, no perf overhead).
    SENTRY_RELEASE      — optional git SHA so errors can be tied back to
                          a deploy. Railway injects RAILWAY_GIT_COMMIT_SHA.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

logger = logging.getLogger("bankparse.hmrc.monitoring")


# Module-level guard so init_sentry() is idempotent (FastAPI lifespan +
# uvicorn reloader can both call it).
_initialised = False


def init_sentry() -> None:
    """Idempotent. Safe to call multiple times. No-op without SENTRY_DSN."""
    global _initialised
    if _initialised:
        return

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry: SENTRY_DSN not set, monitoring disabled.")
        _initialised = True
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning(
            "Sentry: sentry-sdk not installed; pip install sentry-sdk[fastapi]"
        )
        _initialised = True
        return

    env = os.environ.get("SENTRY_ENV") or os.environ.get("HMRC_ENV", "dev")
    release = (
        os.environ.get("SENTRY_RELEASE")
        or os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:12]
        or None
    )
    try:
        traces = float(os.environ.get("SENTRY_TRACES_RATE", "0.0"))
    except (TypeError, ValueError):
        traces = 0.0

    sentry_sdk.init(
        dsn=dsn,
        environment=env,
        release=release,
        integrations=[FastApiIntegration(), StarletteIntegration()],
        traces_sample_rate=traces,
        # Send personally-identifiable data only when the operator has
        # explicitly enabled it via SENTRY_SEND_PII=1. Off by default —
        # we're handling NINOs.
        send_default_pii=os.environ.get("SENTRY_SEND_PII") == "1",
        before_send=_before_send,
    )
    logger.info(
        "Sentry initialised (env=%s release=%s traces=%s)",
        env, release, traces,
    )
    _initialised = True


# ---------------------------------------------------------------------------
# PII scrubber — strip NINOs from event payloads regardless of SEND_PII
# ---------------------------------------------------------------------------

# NINO format: AA######A. Match defensively — anywhere in a string.
_NINO_PATTERN = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b")


def _scrub_ninos(value: Any) -> Any:
    """Recursively replace any NINO-looking strings with [NINO]. Cheap.
    Runs on every Sentry event before send."""
    if isinstance(value, str):
        return _NINO_PATTERN.sub("[NINO]", value)
    if isinstance(value, dict):
        return {k: _scrub_ninos(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_ninos(v) for v in value]
    return value


def _before_send(event: dict, hint: dict) -> dict | None:
    """Sentry callback. Runs on every event right before transmission.
    We use it to strip NINOs and other UK PII from messages and frames."""
    try:
        return _scrub_ninos(event)
    except Exception:  # pragma: no cover — never let scrubbing crash the app
        return event


# ---------------------------------------------------------------------------
# Explicit HMRC error capture — called from the client
# ---------------------------------------------------------------------------

def _user_hash(user_id: int) -> str:
    """Short, stable, non-reversible hash of a user_id. Enough to correlate
    multiple events to the same user without exposing the database id."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:12]


def capture_hmrc_failure(
    *,
    endpoint: str,
    method: str,
    status_code: int,
    body: Any,
    user_id: int | None = None,
    audit_id: str | None = None,
) -> None:
    """Send a structured Sentry event when HMRC returns 5xx or network 0.

    No-op when sentry-sdk isn't installed or DSN isn't set. Safe to call
    on every HmrcApiError — we filter to >= 500 here.
    """
    if status_code < 500 and status_code != 0:
        return  # 4xx is user error (NINO mismatch, validation) — not alertable

    try:
        import sentry_sdk
    except ImportError:
        return

    code = ""
    if isinstance(body, dict):
        code = body.get("code") or body.get("error") or ""

    # PII-strip the endpoint path — replace NINO segments with [NINO].
    safe_endpoint = _NINO_PATTERN.sub("[NINO]", endpoint)

    with sentry_sdk.push_scope() as scope:
        scope.set_tag("hmrc.endpoint", safe_endpoint)
        scope.set_tag("hmrc.method", method)
        scope.set_tag("hmrc.status", str(status_code))
        scope.set_tag("hmrc.code", code or "unknown")
        if user_id is not None:
            scope.set_tag("user_hash", _user_hash(user_id))
        if audit_id:
            scope.set_tag("audit_id", audit_id)
        scope.set_level("error" if status_code >= 500 else "warning")
        msg = (
            f"HMRC {status_code} on {method} {safe_endpoint} "
            f"(code={code or 'none'})"
        )
        sentry_sdk.capture_message(msg)
