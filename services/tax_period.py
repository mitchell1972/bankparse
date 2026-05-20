"""
Parse UK tax-period labels into concrete (start_iso, end_iso) date ranges.

The accountant export lets the user pick a period from a dropdown ("Q2
2026-27 (Jul-Sep)", "2026-27 tax year", "All time", etc.). Until this
module existed, that selection was a cosmetic label — the pack always
contained every transaction the user had ever uploaded. This is the bit
that turns the label into a real filter.

UK tax year: 6 April YYYY to 5 April YYYY+1.

Quarters within the tax year (HMRC MTD ITSA quarterly periods):
  Q1:  6 Apr  → 5 Jul
  Q2:  6 Jul  → 5 Oct
  Q3:  6 Oct  → 5 Jan (year+1)
  Q4:  6 Jan  → 5 Apr (year+1)

Anything we don't recognise returns None so the export falls back to
"all time" — that's a safer default than silently dropping rows.
"""
from __future__ import annotations

import datetime as _dt
import re


_TAX_YEAR_RE = re.compile(r"\b(\d{4})[-/](\d{2,4})\s+tax\s+year\b", re.IGNORECASE)
_QUARTER_RE = re.compile(
    r"\bQ([1-4])\s+(\d{4})[-/](\d{2,4})\b", re.IGNORECASE,
)


def _normalise_end_year(start_year: int, end_str: str) -> int:
    """Accept both '26' and '2026' for the tail of "2025-26" or "2025-2026"."""
    if len(end_str) == 4:
        return int(end_str)
    # Two-digit form like "26" → 2026 when start is "2025", or 2026 when
    # start is "2025". The convention is that the end year is one greater
    # than the start year. We sanity-check rather than trust the input.
    candidate = int(end_str)
    century = (start_year // 100) * 100
    full = century + candidate
    # If 2026 is more than one year off from start, try the next century
    if abs(full - start_year - 1) > 0:
        full = century + 100 + candidate
    return full


def parse_period_label(label: str | None) -> tuple[str, str] | None:
    """Return (start_iso, end_iso) inclusive bounds for the period, or None
    if the label is empty / unrecognised / means "all time".

    Both bounds use ISO format ``YYYY-MM-DD`` and are inclusive.
    """
    if not label:
        return None
    s = label.strip()
    if not s or s.lower().startswith("all"):
        return None

    # Quarter labels: "Q2 2026-27 (Jul-Sep)"
    m = _QUARTER_RE.search(s)
    if m:
        q = int(m.group(1))
        start_year = int(m.group(2))
        _normalise_end_year(start_year, m.group(3))  # validate
        # Q1: 6 Apr - 5 Jul (start_year)
        # Q2: 6 Jul - 5 Oct
        # Q3: 6 Oct - 5 Jan (+1)
        # Q4: 6 Jan - 5 Apr (+1)
        ranges = {
            1: ((start_year, 4, 6), (start_year, 7, 5)),
            2: ((start_year, 7, 6), (start_year, 10, 5)),
            3: ((start_year, 10, 6), (start_year + 1, 1, 5)),
            4: ((start_year + 1, 1, 6), (start_year + 1, 4, 5)),
        }
        (sy, sm, sd), (ey, em, ed) = ranges[q]
        return (
            _dt.date(sy, sm, sd).isoformat(),
            _dt.date(ey, em, ed).isoformat(),
        )

    # Tax year labels: "2026-27 tax year" or "2026-2027 tax year"
    m = _TAX_YEAR_RE.search(s)
    if m:
        start_year = int(m.group(1))
        _normalise_end_year(start_year, m.group(2))  # validate format
        return (
            _dt.date(start_year, 4, 6).isoformat(),
            _dt.date(start_year + 1, 4, 5).isoformat(),
        )

    return None


def filter_rows_by_iso_date(rows: list[dict], *,
                            date_key: str = "date_iso",
                            bounds: tuple[str, str] | None) -> list[dict]:
    """Apply a (start, end) inclusive ISO-date filter to a list of dict rows.

    Rows without ``date_key`` are dropped when a filter is in force — we
    can't responsibly include an undated row on a quarterly accountant
    pack. Pass ``bounds=None`` for an all-time pass-through.
    """
    if bounds is None:
        return list(rows)
    start, end = bounds
    out: list[dict] = []
    for r in rows:
        v = (r.get(date_key) or "").strip()
        if not v:
            continue
        if start <= v <= end:
            out.append(r)
    return out
