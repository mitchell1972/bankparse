"""
Claude-driven HMRC category classifier (feature-flagged).

When enabled (env `HMRC_AI_CATEGORISE=1`), low-confidence regex output is
re-classified by Claude Haiku in batches. Cheaper + more accurate on UK
merchants the static rules don't recognise.

Default OFF in this PR so we can ship the UI without spending AI budget;
flipping the flag in Railway turns it on instantly.

Cost notes (Claude Haiku 4.5 @ rough rates):
  ~ £0.0001 per 25-row batch (input ~600 tokens, output ~200 tokens)
  At 100 statements/month × ~80 rows = ~£0.03/month per active user.
  Cached per merchant_key via the overrides repo, so repeat rows are free.

Output contract: returns the SAME shape as `Classification` from mapping.py
"""

from __future__ import annotations

import json
import logging
import os
from typing import Iterable

from . import mapping as _mapping

logger = logging.getLogger("bankparse.hmrc.ai_classifier")


def is_enabled() -> bool:
    return os.environ.get("HMRC_AI_CATEGORISE", "").lower() in ("1", "true", "yes")


_SE_CATEGORIES = [
    _mapping.SE_INCOME, _mapping.SE_OTHER_INCOME,
    _mapping.SE_EXPENSE_COST_OF_GOODS, _mapping.SE_EXPENSE_CIS,
    _mapping.SE_EXPENSE_STAFF, _mapping.SE_EXPENSE_TRAVEL,
    _mapping.SE_EXPENSE_PREMISES, _mapping.SE_EXPENSE_REPAIRS,
    _mapping.SE_EXPENSE_ADMIN, _mapping.SE_EXPENSE_ADVERTISING,
    _mapping.SE_EXPENSE_ENTERTAINMENT, _mapping.SE_EXPENSE_INTEREST,
    _mapping.SE_EXPENSE_FINANCIAL, _mapping.SE_EXPENSE_BAD_DEBT,
    _mapping.SE_EXPENSE_PROFESSIONAL, _mapping.SE_EXPENSE_DEPRECIATION,
    _mapping.SE_EXPENSE_OTHER,
]

_PROP_CATEGORIES = [
    _mapping.PROP_INCOME_RENT, _mapping.PROP_INCOME_PREMIUMS,
    _mapping.PROP_INCOME_OTHER,
    _mapping.PROP_EXPENSE_PREMISES, _mapping.PROP_EXPENSE_REPAIRS,
    _mapping.PROP_EXPENSE_FINANCIAL, _mapping.PROP_EXPENSE_PROFESSIONAL,
    _mapping.PROP_EXPENSE_SERVICES, _mapping.PROP_EXPENSE_TRAVEL,
    _mapping.PROP_EXPENSE_OTHER, _mapping.PROP_EXPENSE_RESIDENTIAL_FINANCIAL,
]


def _categories_for(business_type: str) -> list[str]:
    return _PROP_CATEGORIES if business_type == "property" else _SE_CATEGORIES


def classify_batch(rows: list[dict], business_type: str = "se") -> list[_mapping.Classification]:
    """Classify a batch of (description, amount) rows with Claude Haiku.

    Returns a list of Classification, same length as `rows`, same order.
    On any error, returns 'other' with confidence 0 (caller falls back).

    Synchronous — for parallel use, call from `asyncio.to_thread()`.
    """
    if not rows:
        return []
    if not is_enabled():
        return [_mapping.Classification(
            category=_mapping.SE_EXPENSE_OTHER if business_type != "property" else _mapping.PROP_EXPENSE_OTHER,
            confidence=0.0, is_income=float(r.get("amount") or 0) > 0,
            reasoning="AI classifier disabled",
        ) for r in rows]

    try:
        import anthropic  # type: ignore
    except ImportError:
        logger.warning("anthropic SDK not installed — AI classifier disabled")
        return [_mapping.Classification(category=_mapping.SE_EXPENSE_OTHER, confidence=0.0,
                                        is_income=False, reasoning="anthropic SDK missing")
                for _ in rows]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return [_mapping.Classification(category=_mapping.SE_EXPENSE_OTHER, confidence=0.0,
                                        is_income=False, reasoning="ANTHROPIC_API_KEY missing")
                for _ in rows]

    valid = _categories_for(business_type)
    prompt = _build_prompt(rows, business_type, valid)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text if msg.content else "[]"
        parsed = _parse_response(text, expected=len(rows))
    except Exception:
        logger.exception("AI classifier call failed; falling back to 'other'")
        return [_mapping.Classification(category=_mapping.SE_EXPENSE_OTHER, confidence=0.0,
                                        is_income=False, reasoning="AI call failed")
                for _ in rows]

    out: list[_mapping.Classification] = []
    for row, item in zip(rows, parsed):
        cat = item.get("category", "")
        if cat not in valid:
            cat = _mapping.SE_EXPENSE_OTHER if business_type != "property" else _mapping.PROP_EXPENSE_OTHER
        is_credit = float(row.get("amount") or 0) > 0
        out.append(_mapping.Classification(
            category=cat,
            confidence=float(item.get("confidence", 0.6)),
            is_income=bool(item.get("is_income", is_credit)),
            reasoning=str(item.get("reasoning", "Classified by AI"))[:200],
        ))
    return out


def _build_prompt(rows: list[dict], business_type: str, categories: list[str]) -> str:
    rows_text = "\n".join(
        f"  {i+1}. \"{(r.get('description') or '').strip()[:80]}\" amount={r.get('amount')}"
        for i, r in enumerate(rows)
    )
    biz_label = "UK property income" if business_type == "property" else "self-employment (sole trader)"
    return (
        f"You are classifying UK bank transactions for {biz_label} HMRC MTD ITSA "
        "quarterly submission. For each transaction, return one of these "
        "categories EXACTLY (HMRC API field names):\n"
        + "\n".join(f"  - {c}" for c in categories)
        + "\n\nRules:\n"
        "  - Positive amounts are money INTO the account (likely income).\n"
        "  - Negative amounts are money OUT (likely expense).\n"
        "  - UK parking merchants (NCP, MiPermit, RingGo, JustPark) → travelCosts.\n"
        "  - Restaurants/cafes → businessEntertainmentCosts (HMRC restricts this).\n"
        "  - Software subs (AWS, OpenAI, Notion) → adminCosts.\n"
        "  - Be conservative with confidence; 0.3-0.5 means user should review.\n\n"
        "Transactions:\n"
        + rows_text
        + "\n\nReturn a JSON array, one object per transaction, same order. Each:\n"
        '  {"category": "...", "confidence": 0.0-1.0, "is_income": true|false, '
        '"reasoning": "one short sentence"}\n'
        "Return ONLY the JSON array, nothing else."
    )


def _parse_response(text: str, expected: int) -> list[dict]:
    """Robust-ish parse of Claude's JSON array reply."""
    text = text.strip()
    # Strip markdown fences if Claude added them
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    try:
        arr = json.loads(text)
    except json.JSONDecodeError:
        # Last-ditch: find the outermost [...] block
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            arr = json.loads(text[start:end + 1])
        else:
            raise
    if not isinstance(arr, list):
        raise ValueError("AI response was not a JSON array")
    # Pad with empties if Claude returned fewer items
    while len(arr) < expected:
        arr.append({})
    return arr[:expected]
