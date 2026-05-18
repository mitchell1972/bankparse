"""
Categorisation observability — one row per /api/hmrc/categorise call.

We need to answer the following questions in production WITHOUT bolting on
a paid observability service:

  - What's our cache hit rate?
  - How often does the AI actually fire vs cache?
  - How long does the median categorise call take?
  - Which users are most expensive in AI tokens?

This repository writes ONE small row per call. With ~1000 daily calls and
~80 bytes per row, that's about ~30 MB/year — trivially queryable from
SQL, and we can ship a tiny `/admin/hmrc-metrics` page later.

The router calls `record()` in a `try/except` — metric persistence MUST
NOT break a user response.
"""

from __future__ import annotations

import time

from ..services.categorisation import CategorisationMetrics


def record(*, user_id: int, business_type: str, metrics: CategorisationMetrics) -> None:
    """Persist one event row. Silent no-op if the DB write fails."""
    from database import _execute  # local import to avoid circular at app start

    _execute(
        """
        INSERT INTO hmrc_categorisation_events
          (user_id, business_type, total_rows, overrides, cache_hits,
           ai_calls, rule_fallbacks, elapsed_ms, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id), business_type,
            metrics.total_rows, metrics.overrides, metrics.cache_hits,
            metrics.ai_calls, metrics.rule_fallbacks, metrics.elapsed_ms,
            time.time(),
        ),
    )


def aggregate_last_n_days(days: int = 7) -> dict:
    """Quick aggregate for an admin dashboard. Returns hit rate + counts.

    Returned shape:
        {"calls": 1234, "total_rows": 98765, "cache_hit_rate": 0.83,
         "ai_call_rate": 0.12, "p50_elapsed_ms": 180, ...}
    """
    from database import _fetchone_dict, _fetchall_dicts

    cutoff = time.time() - (days * 86400)
    agg = _fetchone_dict(
        """
        SELECT
          COUNT(*)                      AS calls,
          COALESCE(SUM(total_rows),0)   AS total_rows,
          COALESCE(SUM(overrides),0)    AS overrides,
          COALESCE(SUM(cache_hits),0)   AS cache_hits,
          COALESCE(SUM(ai_calls),0)     AS ai_calls,
          COALESCE(SUM(rule_fallbacks),0) AS rule_fallbacks,
          COALESCE(AVG(elapsed_ms),0)   AS avg_elapsed_ms
        FROM hmrc_categorisation_events
        WHERE created_at >= ?
        """,
        (cutoff,),
    ) or {}

    total_rows = int(agg.get("total_rows") or 0) or 1
    return {
        "calls": int(agg.get("calls") or 0),
        "total_rows": int(agg.get("total_rows") or 0),
        "cache_hit_rate": round((agg.get("cache_hits") or 0) / total_rows, 3),
        "ai_call_rate": round((agg.get("ai_calls") or 0) / total_rows, 3),
        "override_rate": round((agg.get("overrides") or 0) / total_rows, 3),
        "rule_fallback_rate": round((agg.get("rule_fallbacks") or 0) / total_rows, 3),
        "avg_elapsed_ms": int(agg.get("avg_elapsed_ms") or 0),
    }
