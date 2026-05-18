"""
Per-user merchant → HMRC category overrides.

When the auto-classifier gets a row wrong and the user fixes it, we store
the (merchant_key, business_type) → category mapping here. Next time the
same merchant turns up for the same user, we use their override instead of
the auto-classification — that's the basis of the per-user learning loop.

`merchant_key` is a normalised form of the transaction description: lower-
cased, stripped of trailing reference codes / dates / amounts, collapsed
whitespace. Two different transactions from the same merchant should
collapse to the same key.
"""

from __future__ import annotations

import re
import time


_REF_TAIL = re.compile(r"\s*(?:ref|reference|inv|trx|txn)\s*[#:]?\s*\w+\s*$", re.I)
_NUMERIC_TAIL = re.compile(r"\s+\d[\d\s,./-]*$")
_DATE_LIKE = re.compile(r"\s+\d{2,4}[-/]\d{2}[-/]\d{2,4}\b")
# Short DDMMM / MMMDD date suffix like '22DEC', '14MAR', '5JUL'
_DDMMM_TAIL = re.compile(
    r"\s+\d{1,2}\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s*$", re.I
)
_EXTRA_WS = re.compile(r"\s+")
_LEADING_PREFIX = re.compile(r"^\s*(?:dd|so|cr|dr|bp|vis|atm|chq|pos|fpi|fpo|tfr|trf|\)+|\(+)\s+", re.I)


def merchant_key(description: str) -> str:
    """Normalise a bank description into a stable merchant identifier.

    Example:
        '))) MIPERMIT LTD CHIPPENHAM 14/03'   →  'mipermit ltd chippenham'
        'BP Augusta k Chukwuma Mitchell 22DEC' →  'augusta k chukwuma mitchell'
        'DD TV LICENCE MBP'                    →  'tv licence mbp'
    """
    s = (description or "").strip()
    s = _LEADING_PREFIX.sub("", s)
    s = _REF_TAIL.sub("", s)
    s = _DDMMM_TAIL.sub("", s)
    s = _NUMERIC_TAIL.sub("", s)
    s = _DATE_LIKE.sub("", s)
    s = _EXTRA_WS.sub(" ", s)
    return s.strip().lower()


def save(user_id: int, description: str, business_type: str, category: str) -> None:
    """Upsert one merchant override. `business_type` is 'se' or 'property'."""
    from database import _execute

    key = merchant_key(description)
    if not key:
        return
    _execute(
        """
        INSERT INTO hmrc_merchant_overrides (user_id, merchant_key, business_type, category, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, merchant_key, business_type) DO UPDATE SET
          category = excluded.category,
          updated_at = excluded.updated_at
        """,
        (user_id, key, business_type, category, time.time()),
    )


def lookup(user_id: int, description: str, business_type: str) -> str | None:
    """Find the user's override for this merchant + business type, if any."""
    from database import _fetchone_dict

    key = merchant_key(description)
    if not key:
        return None
    row = _fetchone_dict(
        "SELECT category FROM hmrc_merchant_overrides "
        "WHERE user_id = ? AND merchant_key = ? AND business_type = ?",
        (user_id, key, business_type),
    )
    return row["category"] if row else None


def all_for_user(user_id: int) -> list[dict]:
    from database import _fetchall_dicts
    return _fetchall_dicts(
        "SELECT merchant_key, business_type, category, updated_at "
        "FROM hmrc_merchant_overrides WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    )
