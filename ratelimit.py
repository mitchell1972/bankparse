"""
BankParse — Database-backed Rate Limiter
Uses Turso (or local SQLite) to track request counts per key per time window.
Stateless across serverless instances since state lives in the database.
"""

import time
import logging
from database import _execute, _fetchone_dict

logger = logging.getLogger("bankparse.ratelimit")


def check_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """
    Check whether a request identified by `key` is within the rate limit.

    Args:
        key: Identifier string, typically f"{ip}:{endpoint}"
        limit: Maximum number of requests allowed in the window.
        window_seconds: Length of the sliding window in seconds.

    Returns:
        True if the request is allowed, False if rate-limited.
    """
    now = time.time()
    row = _fetchone_dict(
        "SELECT window_start, count FROM rate_limits WHERE key = ?",
        (key,),
    )

    if row is None:
        # First request for this key — insert a new record
        _execute(
            "INSERT INTO rate_limits (key, window_start, count) VALUES (?, ?, 1)",
            (key, now),
        )
        return True

    window_start = row["window_start"]
    count = row["count"]

    if now - window_start > window_seconds:
        # Window expired — reset
        _execute(
            "UPDATE rate_limits SET window_start = ?, count = 1 WHERE key = ?",
            (now, key),
        )
        return True

    if count >= limit:
        # Over the limit
        return False

    # Within window and under limit — increment
    _execute(
        "UPDATE rate_limits SET count = count + 1 WHERE key = ?",
        (key,),
    )
    return True


def cleanup_rate_limits():
    """Delete all expired rate-limit windows (older than 5 minutes)."""
    cutoff = time.time() - 300
    _execute("DELETE FROM rate_limits WHERE window_start < ?", (cutoff,))
