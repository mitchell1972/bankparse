"""
Unit tests for the UK tax-period parser used by the accountant export.

The UK tax year runs 6 Apr → 5 Apr the following year. The accountant
export lets the user pick a period from a dropdown; if we get the
boundaries wrong we send the client's accountant a pack with the wrong
quarter's data on it, which is the worst possible bug. So these tests
are deliberately exhaustive.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.tax_period import filter_rows_by_iso_date, parse_period_label


# ---------------------------------------------------------------------------
# Tax year boundaries
# ---------------------------------------------------------------------------


def test_tax_year_2026_27_runs_6_apr_to_5_apr():
    """The defining boundary of the UK tax year. Off-by-one here is a
    P0 bug — Mitchell's accountant would file the wrong period."""
    assert parse_period_label("2026-27 tax year") == ("2026-04-06", "2027-04-05")


def test_tax_year_accepts_four_digit_end_year():
    assert parse_period_label("2026-2027 tax year") == ("2026-04-06", "2027-04-05")


def test_tax_year_is_case_insensitive():
    assert parse_period_label("2025-26 Tax Year") == ("2025-04-06", "2026-04-05")
    assert parse_period_label("2025-26 TAX YEAR") == ("2025-04-06", "2026-04-05")


# ---------------------------------------------------------------------------
# Quarters — MTD ITSA quarterly periods, anchored to the 6th of the month
# ---------------------------------------------------------------------------


def test_q1_runs_6_apr_to_5_jul():
    assert parse_period_label("Q1 2026-27 (Apr-Jun)") == ("2026-04-06", "2026-07-05")


def test_q2_runs_6_jul_to_5_oct():
    assert parse_period_label("Q2 2026-27 (Jul-Sep)") == ("2026-07-06", "2026-10-05")


def test_q3_runs_6_oct_to_5_jan_next_year():
    """Q3 straddles the calendar year boundary — common off-by-one source."""
    assert parse_period_label("Q3 2026-27 (Oct-Dec)") == ("2026-10-06", "2027-01-05")


def test_q4_runs_6_jan_to_5_apr_next_year():
    assert parse_period_label("Q4 2026-27 (Jan-Mar)") == ("2027-01-06", "2027-04-05")


def test_quarter_label_without_paren_suffix_still_parses():
    assert parse_period_label("Q2 2026-27") == ("2026-07-06", "2026-10-05")


# ---------------------------------------------------------------------------
# "All time" / unknown → None (export shows everything)
# ---------------------------------------------------------------------------


def test_empty_label_means_all_time():
    assert parse_period_label("") is None
    assert parse_period_label(None) is None
    assert parse_period_label("   ") is None


def test_all_time_keyword_means_all_time():
    assert parse_period_label("All time (default)") is None
    assert parse_period_label("all") is None


def test_unrecognised_label_returns_none_not_raise():
    """We never want to crash the export over a label we don't grok —
    falling back to all-time is the safe default."""
    assert parse_period_label("garbage") is None
    assert parse_period_label("Calendar 2026") is None


# ---------------------------------------------------------------------------
# filter_rows_by_iso_date
# ---------------------------------------------------------------------------


def test_filter_keeps_only_rows_within_bounds():
    rows = [
        {"id": 1, "date_iso": "2026-04-05"},  # day before tax year start — out
        {"id": 2, "date_iso": "2026-04-06"},  # exactly on boundary — IN
        {"id": 3, "date_iso": "2026-09-15"},
        {"id": 4, "date_iso": "2027-04-05"},  # exactly on end boundary — IN
        {"id": 5, "date_iso": "2027-04-06"},  # day after — out
    ]
    out = filter_rows_by_iso_date(rows, bounds=("2026-04-06", "2027-04-05"))
    assert [r["id"] for r in out] == [2, 3, 4]


def test_filter_drops_rows_with_no_date_when_filtered():
    """Undated rows can't be responsibly placed in a quarterly pack."""
    rows = [
        {"id": 1, "date_iso": "2026-09-15"},
        {"id": 2, "date_iso": ""},
        {"id": 3},
        {"id": 4, "date_iso": "   "},
    ]
    out = filter_rows_by_iso_date(rows, bounds=("2026-04-06", "2027-04-05"))
    assert [r["id"] for r in out] == [1]


def test_filter_bounds_none_passes_everything_through_unchanged():
    rows = [
        {"id": 1, "date_iso": "2020-01-01"},
        {"id": 2},  # undated rows survive when there's no filter
    ]
    assert filter_rows_by_iso_date(rows, bounds=None) == rows
