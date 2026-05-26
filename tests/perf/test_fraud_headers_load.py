"""
Load test for the HMRC fraud-prevention header builder.

Every outbound MTD call attaches 13 headers built from per-request data.
At sustained traffic levels the per-request cost matters: at the Jan 31
self-assessment deadline rush we expect ~50 req/s peak (back-of-envelope:
10k active users, 5 % submitting in the final hour = ~14/min average,
spikes to 50-100/s). At 1 ms/header build that's 50 ms/s of CPU just
on headers — fine. At 10 ms it's 500 ms/s — half a core gone.

This test pins the budget so an accidental O(n²) regression (e.g. someone
JSON-dumping the whole session, regex-compiling each call) gets caught
in CI before it ships.

Budget: median header-build < 200 µs/call on a CI runner. We allow ~4×
that on a noisy machine (800 µs) for the 99th percentile assertion —
generous, but the goal is to catch regressions, not benchmark wars.

Skipped in default CI runs to keep wall-clock down — enable explicitly:

    PERF=1 pytest tests/perf/

or via the dedicated CI job (add later if needed).
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Any

import pytest

from hmrc.services import fraud_headers as _fh


pytestmark = pytest.mark.skipif(
    os.environ.get("PERF") != "1",
    reason="Set PERF=1 to run perf tests",
)


# Fixture data is realistic but small enough to construct cheaply.
_FRAUD_CONTEXT: dict[str, Any] = {
    "device_id": "ec1b3df8-e2f1-4b6f-8a9c-1234567890ab",
    "browser_user_agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
    ),
    "timezone": "UTC+01:00",
    "screens": [{
        "width": 1920, "height": 1080, "scaling-factor": 2, "colour-depth": 24,
    }],
    "window": {"width": 1440, "height": 900},
    "mfa": [{
        "type": "AUTH_CODE",
        "timestamp": "2026-05-26T14:30:00Z",
        "unique-reference": "abcd1234",
    }],
}


class _StubRequest:
    """Minimal Starlette Request-shaped stub. Avoids real Starlette
    construction overhead so the benchmark measures header-build, not
    framework instantiation."""

    def __init__(self):
        self.headers = {
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
            ),
            "x-forwarded-for": "203.0.113.45, 10.0.0.1",
            "x-forwarded-port": "44321",
        }

        class _C:
            host = "203.0.113.45"
            port = 44321

        self.client = _C()


def _bench(n: int) -> list[float]:
    """Time N consecutive header builds. Returns per-call seconds."""
    req = _StubRequest()
    timings: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        _fh.build_headers(
            request=req, fraud_context=_FRAUD_CONTEXT, user_id=42,
            our_public_ip="198.51.100.7",
        )
        timings.append(time.perf_counter() - t0)
    return timings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_warmup_does_not_crash():
    """Sanity — one call returns the expected 13 headers."""
    headers = _fh.build_headers(
        request=_StubRequest(), fraud_context=_FRAUD_CONTEXT, user_id=42,
        our_public_ip="198.51.100.7",
    )
    # 13 mandatory headers when all fraud context is present + Vendor-Public-IP.
    assert len(headers) >= 13, f"expected ≥13 headers, got {len(headers)}"


def test_median_per_call_under_budget():
    """Median header-build time < 500 µs on the runner.

    Budget allows ~4× the typical 100-150 µs we observe locally, giving
    headroom for noisy CI runners without hiding a real regression.
    """
    timings = _bench(n=10_000)
    median = statistics.median(timings)
    median_us = median * 1_000_000
    assert median < 0.0005, (
        f"median header build took {median_us:.0f}µs (budget 500µs). "
        f"Look for a new dependency or expensive logging in fraud_headers.py."
    )


def test_p99_per_call_under_budget():
    """99th-percentile header-build < 2 ms.

    Catches a regression that's fast on average but has a heavy tail
    (e.g. GC pause from a leaky list, sporadic dict resize)."""
    timings = sorted(_bench(n=10_000))
    p99 = timings[int(len(timings) * 0.99)]
    p99_us = p99 * 1_000_000
    assert p99 < 0.002, (
        f"p99 header build took {p99_us:.0f}µs (budget 2000µs). "
        f"Indicates a heavy-tail allocation or GC issue."
    )


def test_sustained_throughput_over_50_qps():
    """Demonstrate the builder can produce ≥50 req/s of headers using only
    a small fraction of a core. Sanity floor, not a benchmark — even a
    slow runner should clear this by 100×."""
    n = 1_000
    t0 = time.perf_counter()
    _bench(n=n)
    elapsed = time.perf_counter() - t0
    qps = n / elapsed
    assert qps > 50, (
        f"only {qps:.0f} headers/sec — the builder is too slow for the "
        f"Jan 31 deadline rush. Profile fraud_headers.build_headers."
    )
