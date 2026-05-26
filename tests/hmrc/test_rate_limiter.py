"""Unit tests for the HMRC outbound rate limiter.

Uses real time but with millisecond-scale rate parameters, so the whole
file runs in well under a second. Avoids monkeypatching time.sleep
(which appears to interact badly with pytest's collection somehow).
"""

from __future__ import annotations

import time

import pytest

from hmrc.services import rate_limiter as rl


@pytest.fixture(autouse=True)
def _reset_singleton():
    rl.reset_for_tests()
    yield
    rl.reset_for_tests()


# ---------------------------------------------------------------------------
# Token bucket behaviour
# ---------------------------------------------------------------------------


def test_cold_start_burst_passes_without_waiting():
    """A fresh bucket starts FULL — the first `capacity` calls return instantly."""
    bucket = rl.TokenBucket(
        capacity=5, rate_per_sec=10.0, max_wait_sec=1.0, slice_sec=0.001,
    )
    for _ in range(5):
        waited = bucket.acquire()
        assert waited < 0.005, f"unexpected wait: {waited}"


def test_excess_calls_wait_for_refill():
    """6th call into a 5-capacity bucket at 200/s waits ~5 ms before passing.

    200/s = 5 ms / token, easy to measure without flakiness on a busy CI box.
    """
    bucket = rl.TokenBucket(
        capacity=5, rate_per_sec=200.0, max_wait_sec=1.0, slice_sec=0.001,
    )
    for _ in range(5):
        bucket.acquire()
    waited = bucket.acquire()
    # Should wait ~5 ms for the next token to refill.
    assert 0.003 <= waited <= 0.040, f"expected ~5ms, got {waited*1000:.1f}ms"


def test_sustained_rate_throttles_correctly():
    """20 calls at 1000/s into a 2-burst bucket → ~18ms total (2 free + 18@1ms)."""
    bucket = rl.TokenBucket(
        capacity=2, rate_per_sec=1000.0, max_wait_sec=2.0, slice_sec=0.0005,
    )
    start = time.monotonic()
    for _ in range(20):
        bucket.acquire()
    elapsed = time.monotonic() - start
    # 18 tokens at 1000/s = 18 ms; allow generous slop for CI jitter.
    assert 0.010 <= elapsed <= 0.080, (
        f"expected 10-80ms for 20 calls @ 1000/s, got {elapsed*1000:.1f}ms"
    )


def test_max_wait_exceeded_raises():
    """If a single acquire would take longer than max_wait_sec, raise."""
    # rate is so low (1 token / 10s) that the second acquire can't possibly
    # succeed within max_wait_sec=0.05s — we'd need 10s to refill.
    bucket = rl.TokenBucket(
        capacity=1, rate_per_sec=0.1, max_wait_sec=0.05, slice_sec=0.005,
    )
    bucket.acquire()  # consume the only token
    with pytest.raises(rl.RateLimitedError) as exc_info:
        bucket.acquire()
    msg = str(exc_info.value)
    assert "waited" in msg.lower()
    assert "rate=0.1" in msg


def test_partial_refill_between_calls():
    """After draining, a 5 ms quiet period gives back partial tokens."""
    bucket = rl.TokenBucket(
        capacity=2, rate_per_sec=200.0, max_wait_sec=1.0, slice_sec=0.0005,
    )
    bucket.acquire()
    bucket.acquire()
    # Real sleep — 5 ms = 1 token at 200/s, so next acquire is instant.
    time.sleep(0.005)
    waited = bucket.acquire()
    assert waited < 0.005, f"5ms quiet should have refilled, but waited {waited*1000:.1f}ms"


def test_capacity_caps_refill():
    """After a long quiet period, the bucket caps at `capacity`, not more."""
    bucket = rl.TokenBucket(
        capacity=3, rate_per_sec=1000.0, max_wait_sec=1.0, slice_sec=0.0005,
    )
    # 50ms quiet — far longer than needed to fully refill capacity (3 tokens
    # at 1000/s = 3ms refill). Anything beyond capacity should be capped.
    time.sleep(0.05)
    # Drain 3 — instant.
    for _ in range(3):
        assert bucket.acquire() < 0.005
    # 4th must wait — proves we capped at 3, not 50.
    waited = bucket.acquire()
    assert waited > 0.0001, "bucket allowed more than capacity tokens"


# ---------------------------------------------------------------------------
# Env var wiring
# ---------------------------------------------------------------------------


def test_env_vars_drive_singleton(monkeypatch):
    monkeypatch.setenv("HMRC_OUTBOUND_RATE_PER_SEC", "3.5")
    monkeypatch.setenv("HMRC_OUTBOUND_BURST", "7")
    monkeypatch.setenv("HMRC_OUTBOUND_MAX_WAIT_SEC", "4.5")
    rl.reset_for_tests()
    b = rl.get_bucket()
    assert b.rate_per_sec == 3.5
    assert b.capacity == 7
    assert b.max_wait_sec == 4.5


def test_env_defaults_when_unset(monkeypatch):
    for k in ("HMRC_OUTBOUND_RATE_PER_SEC", "HMRC_OUTBOUND_BURST",
              "HMRC_OUTBOUND_MAX_WAIT_SEC"):
        monkeypatch.delenv(k, raising=False)
    rl.reset_for_tests()
    b = rl.get_bucket()
    assert b.rate_per_sec == 8.0
    assert b.capacity == 16
    assert b.max_wait_sec == 10.0


def test_env_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("HMRC_OUTBOUND_RATE_PER_SEC", "nonsense")
    monkeypatch.setenv("HMRC_OUTBOUND_BURST", "-5")  # rejected (must be >0)
    rl.reset_for_tests()
    b = rl.get_bucket()
    assert b.rate_per_sec == 8.0
    assert b.capacity == 16


# ---------------------------------------------------------------------------
# Singleton + reset
# ---------------------------------------------------------------------------


def test_acquire_uses_singleton(monkeypatch):
    """rl.acquire() routes through the same bucket as get_bucket()."""
    monkeypatch.setenv("HMRC_OUTBOUND_BURST", "2")
    monkeypatch.setenv("HMRC_OUTBOUND_RATE_PER_SEC", "200.0")
    rl.reset_for_tests()
    assert rl.acquire() < 0.005
    assert rl.acquire() < 0.005
    waited = rl.acquire()
    assert waited > 0


def test_reset_for_tests_re_reads_env(monkeypatch):
    monkeypatch.setenv("HMRC_OUTBOUND_RATE_PER_SEC", "2.0")
    rl.reset_for_tests()
    assert rl.get_bucket().rate_per_sec == 2.0

    monkeypatch.setenv("HMRC_OUTBOUND_RATE_PER_SEC", "5.0")
    rl.reset_for_tests()
    assert rl.get_bucket().rate_per_sec == 5.0
