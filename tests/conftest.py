"""
Project-level test fixtures.

Disables the slowapi rate limiter for the entire test session so accumulated
in-process counters don't cross-contaminate test files. The limiter is
attached to ``app.state`` at module load and persists between tests in the
same pytest process, which means the dozens of /api/register calls across
tests/hmrc, tests/test_api_misc and tests/test_auth otherwise blow through
the 10/min cap and cause spurious 429s in later files.

Each rate-limit code path is unit-tested in isolation in tests/test_auth.py
(via explicit ``limiter.reset()`` calls), so blanket-disabling for the rest
of the suite is safe.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_rate_limiter_every_test(request):
    """Function-scoped so it runs AFTER per-file fixtures that re-enable
    the limiter in their teardown. This is the last word — every test
    starts with the limiter disabled and its counters cleared.

    Tests whose NAME contains 'rate_limit' opt out — they're testing the
    limiter itself and need it on.
    """
    if "rate_limit" in request.node.name:
        # Test wants the limiter ON — restore it from whatever the
        # previous test left it as, and clear stale counters so the
        # test starts with a fresh bucket.
        try:
            from app import app
            lim = getattr(app.state, "limiter", None)
            if lim is not None:
                try: lim.reset()
                except Exception: pass
                lim.enabled = True
        except Exception:
            pass
        yield
        return
    try:
        from app import app
        lim = getattr(app.state, "limiter", None)
        if lim is not None:
            try: lim.reset()
            except Exception: pass
            lim.enabled = False
    except Exception:
        pass
    yield
