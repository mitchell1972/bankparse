"""
Unit tests for the penalty-points tracker.

Covers:

  1. Anonymous response defaults (no auth → safe zero state)
  2. Authenticated response shape contract — dashboard depends on it
  3. The HMRC penalty model constants — wrong values here would mislead
     users about their fine risk, which has legal implications
  4. points_threshold() raises on unknown frequency (loud-fail design)
  5. Threshold + remaining math at every step 0..N
  6. EXPECTED-FAILURE: real late-filing computation is not yet wired
     (stub returns 0). Documented as xfail so it shows up as a known
     gap in CI output and is impossible to forget about.

Reference: https://www.gov.uk/guidance/penalty-points-and-penalties-if-you-submit-your-vat-return-late
(the same model applies to ITSA from April 2026).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hmrc.routers import penalties as p


# ---------------------------------------------------------------------------
# Constants — wrong values here mislead users about fine risk
# ---------------------------------------------------------------------------


def test_quarterly_threshold_matches_hmrc_published_value():
    """4 points for quarterly ITSA / VAT filers per the published rules."""
    assert p.QUARTERLY_THRESHOLD == 4


def test_penalty_amount_is_published_200_gbp():
    """The fixed penalty per threshold-overshoot is £200."""
    assert p.PENALTY_AMOUNT_GBP == 200


def test_all_three_frequencies_present():
    """ITSA needs annual + quarterly; VAT needs monthly. Don't drop any."""
    assert set(p.POINTS_THRESHOLD_BY_FREQUENCY) == {"annual", "quarterly", "monthly"}


def test_thresholds_match_hmrc_per_frequency():
    """The actual values matter — wrong threshold = wrong fine warning.

    From HMRC's points-based penalties guidance:
      - annual:    2 points  (only 2 submissions/yr, so 2 = threshold)
      - quarterly: 4 points  (default for ITSA quarterly updates)
      - monthly:   5 points  (MTD-VAT monthly returns)
    """
    assert p.POINTS_THRESHOLD_BY_FREQUENCY["annual"] == 2
    assert p.POINTS_THRESHOLD_BY_FREQUENCY["quarterly"] == 4
    assert p.POINTS_THRESHOLD_BY_FREQUENCY["monthly"] == 5


def test_points_threshold_lookup_returns_correct_value():
    assert p.points_threshold("annual") == 2
    assert p.points_threshold("quarterly") == 4
    assert p.points_threshold("monthly") == 5


def test_points_threshold_raises_on_unknown_frequency():
    """Loud-fail design — silently returning a default would mislead the
    user about their penalty risk."""
    with pytest.raises(KeyError):
        p.points_threshold("weekly")
    with pytest.raises(KeyError):
        p.points_threshold("")
    with pytest.raises(KeyError):
        p.points_threshold("ITSA")  # tax-type confusion, not a frequency


# ---------------------------------------------------------------------------
# Endpoint behaviour
# ---------------------------------------------------------------------------


def _client():
    """Build a TestClient against the real FastAPI app for end-to-end shape
    checks. We use this instead of unit-stubbing because the endpoint shape
    is what the dashboard reads — drift here would break the UI silently."""
    from app import app
    return TestClient(app, raise_server_exceptions=False)


def test_anonymous_returns_zero_state_with_full_contract():
    """No auth cookie → return safe zero defaults. Dashboard renders the
    'Connect HMRC to track penalties' state from this response."""
    r = _client().get("/api/hmrc/penalty-status")
    assert r.status_code == 200
    body = r.json()
    # The full contract — every key the dashboard might read.
    assert body == {
        "connected": False,
        "points": 0,
        "threshold": p.QUARTERLY_THRESHOLD,
        "remaining": p.QUARTERLY_THRESHOLD,
        "next_fine_gbp": p.PENALTY_AMOUNT_GBP,
        "next_deadline": None,
    }


def test_authenticated_user_with_no_late_filings(monkeypatch):
    """Authenticated user, no late filings → 0 points, remaining = threshold.

    We patch the auth resolver to return a stub user — replicating the
    real /login cookie dance here is brittle (it depends on internal
    session-state shape). The auth dance itself is exercised by
    tests/test_auth.py; this test only cares about the penalties shape."""
    from hmrc.routers import _quarterly_common
    monkeypatch.setattr(_quarterly_common, "user", lambda _req: {"id": 999})

    r = _client().get("/api/hmrc/penalty-status")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["points"] == 0
    assert body["threshold"] == p.QUARTERLY_THRESHOLD
    assert body["remaining"] == p.QUARTERLY_THRESHOLD
    assert body["next_fine_gbp"] == p.PENALTY_AMOUNT_GBP


def test_authenticated_response_includes_next_deadline_key(monkeypatch):
    """The contract requires `next_deadline`. The frontend reads it; even
    when null today, the key must exist so the frontend doesn't crash."""
    from hmrc.routers import _quarterly_common
    monkeypatch.setattr(_quarterly_common, "user", lambda _req: {"id": 999})

    body = _client().get("/api/hmrc/penalty-status").json()
    assert "next_deadline" in body, (
        "missing next_deadline key — dashboard JS will break"
    )


# ---------------------------------------------------------------------------
# Threshold + remaining math at every step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "points,frequency,expected_remaining",
    [
        # Annual filer — threshold 2
        (0, "annual",    2),
        (1, "annual",    1),
        (2, "annual",    0),
        (3, "annual",    0),  # over threshold → still 0 (can't go negative)
        # Quarterly filer — threshold 4
        (0, "quarterly", 4),
        (1, "quarterly", 3),
        (2, "quarterly", 2),
        (3, "quarterly", 1),
        (4, "quarterly", 0),
        (5, "quarterly", 0),  # over threshold
        # Monthly filer — threshold 5
        (0, "monthly",   5),
        (4, "monthly",   1),
        (5, "monthly",   0),
        (6, "monthly",   0),
    ],
)
def test_remaining_math_across_frequencies(points, frequency, expected_remaining):
    """remaining = max(0, threshold - points). Caps at 0 — never negative."""
    threshold = p.points_threshold(frequency)
    assert max(0, threshold - points) == expected_remaining


# ---------------------------------------------------------------------------
# Known gaps — surface explicitly so they can't quietly stay broken
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason=(
    "Late-filing computation is a stub returning 0. Real implementation "
    "needs (a) persisting obligation due-dates locally so we can compare "
    "to submission timestamps, and (b) wiring HMRC's /penalties/{nino} "
    "endpoint when it lands in the Developer Hub. Tracked as a separate "
    "improvement — until then users with real late filings will see 0 "
    "points on the dashboard, which is an UNDER-report (safer than over)."
), strict=True)
def test_late_filing_computation_returns_actual_count():
    """If we file a Q1 submission AFTER its due date, _count_late_filings
    should return 1, not 0. Today it returns 0 (stubbed). This xfail
    fires the day the stub is replaced — that's when the test should be
    flipped from xfail to a real assertion."""
    # We can't construct a real audit-log entry without writing into
    # hmrc_submissions + having an obligation cached. The stub guarantees
    # this returns 0 unconditionally. Flip to a real scenario once the
    # implementation lands.
    user_id = 99999  # synthetic
    assert p._count_late_filings(user_id) >= 1, (
        "stub still returning 0 — real computation not yet implemented"
    )
