"""
Process-local token-bucket rate limiter for outbound HMRC API calls.

HMRC enforces per-vendor rate caps on the MTD APIs. The published policy is
"reasonable use" with explicit per-endpoint limits documented per API (most
are in the range 3–10 req/s steady-state with bursts of 30–50). One runaway
script, batch job, or single greedy user can exhaust the cap and brick the
service for everyone — at 09:30 on 31 January, that's a customer-visible
outage at exactly the moment users CAN'T afford it.

This limiter gates every call routed through ``services/client.request()``.
It's a classic token bucket:

  - ``capacity``  — burst tolerance (default 16)
  - ``rate``      — sustained req/sec (default 8)

Tunable via env vars (no redeploy required if you set them on Railway):

  HMRC_OUTBOUND_RATE_PER_SEC   — sustained allowance (float, default 8.0)
  HMRC_OUTBOUND_BURST          — burst capacity (int,   default 16)
  HMRC_OUTBOUND_MAX_WAIT_SEC   — how long to block before giving up
                                 (float, default 10.0). On giveup we raise
                                 RateLimitedError so the caller can return
                                 a 503 to the end user rather than hang.

Design notes:

  - Process-local. We're on Railway with a single uvicorn process per
    container, so this is a sufficient backstop. If we ever multi-process,
    move to a Redis-backed bucket — the math doesn't change.
  - Lock-protected so the limiter is safe across the threadpool that
    FastAPI uses for `asyncio.to_thread()` calls.
  - Sleeps in 50 ms slices so a long wait doesn't block forever and so
    we can re-check the env vars at next-acquire on every cycle (useful
    for emergency tuning without redeploy).
  - On `max_wait_sec` exceeded, raises ``RateLimitedError`` rather than
    silently dropping the call. The caller wraps this in a 503 with
    Retry-After.

Test seam: ``_now()`` is monotonic time; tests monkeypatch it.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("bankparse.hmrc.rate_limiter")


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------

class RateLimitedError(RuntimeError):
    """Caller waited longer than ``max_wait_sec`` for a token. The caller
    should map this to a 503 with a Retry-After header so the end user
    knows to back off and retry (HMRC themselves do this on their side
    too)."""


# ---------------------------------------------------------------------------
# Bucket
# ---------------------------------------------------------------------------

def _now() -> float:
    """Test seam — monkeypatched in unit tests."""
    return time.monotonic()


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, default))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, default))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


class TokenBucket:
    """Refilling bucket. NOT safe to share across processes — see module
    docstring. Safe across threads."""

    # 50 ms wait slice in prod: tight enough to be responsive under load,
    # loose enough to keep CPU near-zero when idle. Injectable so tests
    # can crank it to a microsecond and exercise the loop without
    # tedious clock-monkeypatching.
    DEFAULT_SLICE_SEC = 0.05

    def __init__(
        self, *,
        capacity: float,
        rate_per_sec: float,
        max_wait_sec: float,
        slice_sec: float = DEFAULT_SLICE_SEC,
        sleep_fn=time.sleep,
    ):
        self.capacity = capacity
        self.rate_per_sec = rate_per_sec
        self.max_wait_sec = max_wait_sec
        self.slice_sec = slice_sec
        self._sleep = sleep_fn
        self._tokens = capacity  # start full so cold starts don't queue
        self._last = _now()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = _now()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_sec)
        self._last = now

    def acquire(self, *, cost: float = 1.0) -> float:
        """Block until ``cost`` tokens are available, or raise RateLimitedError.

        Returns the wall-clock time waited (for observability).
        """
        start = _now()
        slept_total = 0.0
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= cost:
                    self._tokens -= cost
                    return _now() - start
                # Time until we'll have enough tokens, given the refill rate.
                deficit = cost - self._tokens
                wait_for = deficit / self.rate_per_sec
            this_slice = min(wait_for, self.slice_sec)
            if slept_total + this_slice > self.max_wait_sec:
                logger.warning(
                    "HMRC outbound rate limit exceeded — gave up after %.2fs "
                    "(capacity=%s rate=%s/s)",
                    slept_total, self.capacity, self.rate_per_sec,
                )
                raise RateLimitedError(
                    f"HMRC outbound rate limiter: waited {slept_total:.1f}s "
                    f"(cap={self.capacity}, rate={self.rate_per_sec}/s). "
                    f"Try again in a moment."
                )
            self._sleep(this_slice)
            slept_total = _now() - start


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bucket: TokenBucket | None = None
_bucket_lock = threading.Lock()


def get_bucket() -> TokenBucket:
    """Lazy-initialise the singleton from env vars on first use.

    Re-uses the same bucket process-wide so the rate is shared across
    every caller of `services.client.request()`. Test code may call
    ``reset_for_tests()`` between scenarios.
    """
    global _bucket
    if _bucket is not None:
        return _bucket
    with _bucket_lock:
        if _bucket is None:
            _bucket = TokenBucket(
                capacity=_env_int("HMRC_OUTBOUND_BURST", 16),
                rate_per_sec=_env_float("HMRC_OUTBOUND_RATE_PER_SEC", 8.0),
                max_wait_sec=_env_float("HMRC_OUTBOUND_MAX_WAIT_SEC", 10.0),
            )
            logger.info(
                "HMRC outbound rate limiter initialised: %s/s burst=%s max_wait=%.1fs",
                _bucket.rate_per_sec, _bucket.capacity, _bucket.max_wait_sec,
            )
    return _bucket


def reset_for_tests() -> None:
    """Clear the singleton — tests use this to re-read env vars per case."""
    global _bucket
    with _bucket_lock:
        _bucket = None


def acquire() -> float:
    """Convenience wrapper used by services/client.py. Returns wait time."""
    return get_bucket().acquire()
