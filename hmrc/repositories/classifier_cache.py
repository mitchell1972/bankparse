"""
Global merchant → HMRC category cache.

Populated by the AI classifier when it returns a high-confidence result
for a previously-unseen merchant. Shared across ALL users — once Costa
Coffee has been resolved once, every subsequent user gets it for free.

Resolution order in the categorise endpoint:
  1. User's saved override (per-user, ★ saved)
  2. This global cache (✨ AI cached — instant)
  3. Fresh AI call (✨ AI — slow, populates the cache)

User overrides do NOT write into this global cache — one user's preference
isn't necessarily right for everyone (e.g. one sole trader treats AMAZON
purchases as admin, another as cost-of-goods). The AI classifier is the
sole writer; user overrides live separately.
"""

from __future__ import annotations

import time

from .overrides import merchant_key as _merchant_key


# Only cache when the AI returned this much confidence or higher. Below this
# we re-ask the AI next time, in case context or model behaviour improves.
_MIN_CACHE_CONFIDENCE = 0.7


def lookup_many(merchant_keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Bulk-fetch cache entries for a list of (merchant_key, business_type).

    Groups by business_type and issues one IN query per group — sidesteps
    SQLite's lack of row-value IN tuple support. In practice every call
    from the categorise endpoint passes the same business_type, so this is
    one query per call.
    """
    if not merchant_keys:
        return {}
    from database import _fetchall_dicts

    by_bt: dict[str, list[str]] = {}
    for k, bt in merchant_keys:
        by_bt.setdefault(bt, []).append(k)

    out: dict[tuple[str, str], dict] = {}
    chunk = 500  # SQLite default SQLITE_MAX_VARIABLE_NUMBER is 999
    for bt, keys in by_bt.items():
        # Deduplicate to keep the placeholder count down.
        for i in range(0, len(keys), chunk):
            sub = list(dict.fromkeys(keys[i:i + chunk]))
            placeholders = ",".join("?" for _ in sub)
            rows = _fetchall_dicts(
                f"SELECT merchant_key, business_type, category, confidence, reasoning "
                f"FROM hmrc_merchant_cache "
                f"WHERE business_type = ? AND merchant_key IN ({placeholders})",
                tuple([bt, *sub]),
            )
            for r in rows:
                out[(r["merchant_key"], r["business_type"])] = r
    return out


def upsert(description: str, business_type: str, category: str,
           confidence: float, reasoning: str = "") -> None:
    """Cache a fresh AI classification. Only writes high-confidence results."""
    if confidence < _MIN_CACHE_CONFIDENCE:
        return
    key = _merchant_key(description)
    if not key:
        return
    from database import _execute
    _execute(
        """
        INSERT INTO hmrc_merchant_cache
          (merchant_key, business_type, category, confidence, reasoning, hits, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(merchant_key, business_type) DO UPDATE SET
          category = excluded.category,
          confidence = excluded.confidence,
          reasoning = excluded.reasoning,
          hits = hmrc_merchant_cache.hits + 1,
          updated_at = excluded.updated_at
        """,
        (key, business_type, category, confidence, reasoning, time.time()),
    )


def size() -> int:
    from database import _fetchone_dict
    row = _fetchone_dict("SELECT COUNT(*) AS n FROM hmrc_merchant_cache")
    return int(row["n"]) if row else 0
