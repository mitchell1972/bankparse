"""
Claude-driven HMRC category classifier (feature-flagged).

When `HMRC_AI_CATEGORISE=1`, the categorisation pipeline calls Claude with
the canonical HMRC category list for the row's business type. Output shape
matches `mapping.Classification` so the caller doesn't care which path
produced it.

Configuration (all env-overridable so we can tune in production without a
deploy):

  HMRC_AI_CATEGORISE        # "1" to enable
  HMRC_AI_MODEL             # default "claude-haiku-4-5-20251001"
  HMRC_AI_MAX_TOKENS        # default 1500
  ANTHROPIC_API_KEY         # required when enabled

Cost note: ~£0.0001 per 25-row batch on Haiku 4.5. The shared merchant
cache (see `repositories/classifier_cache`) amortises this across all users,
so a 100-row statement typically resolves 90%+ from cache after the first
few users.
"""

from __future__ import annotations

import json
import logging
import os

from . import mapping as _mapping
from ..schemas import categories as _cats

logger = logging.getLogger("bankparse.hmrc.ai_classifier")


# ---------------------------------------------------------------------------
# Configuration — read at call time, not import time, so tests + Railway
# config changes apply without a process restart.
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_MAX_TOKENS = 1500


def is_enabled() -> bool:
    return os.environ.get("HMRC_AI_CATEGORISE", "").lower() in ("1", "true", "yes")


def _model_name() -> str:
    return os.environ.get("HMRC_AI_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _max_tokens() -> int:
    try:
        return int(os.environ.get("HMRC_AI_MAX_TOKENS", "") or _DEFAULT_MAX_TOKENS)
    except ValueError:
        return _DEFAULT_MAX_TOKENS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_batch(rows: list[dict], business_type: str = "se") -> list[_mapping.Classification]:
    """Classify a batch of (description, amount) rows with Claude.

    Returns a list of `Classification`, same length and order as `rows`.
    On any failure the function NEVER raises — it returns 'other' with
    confidence 0.0 so the caller can fall back cleanly.

    Synchronous. For parallel use, wrap with `asyncio.to_thread()`.
    """
    if not rows:
        return []
    if not is_enabled():
        return _all_other(rows, business_type, "AI classifier disabled")

    try:
        import anthropic  # type: ignore
    except ImportError:
        logger.warning("anthropic SDK not installed — AI classifier returning 'other'")
        return _all_other(rows, business_type, "anthropic SDK missing")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _all_other(rows, business_type, "ANTHROPIC_API_KEY missing")

    valid = _cats.categories_for(business_type)
    prompt = _build_prompt(rows, business_type, valid)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_model_name(),
            max_tokens=_max_tokens(),
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text if msg.content else "[]"
        parsed = _parse_response(text, expected=len(rows))
    except Exception:
        logger.exception("AI classifier call failed; falling back to 'other'")
        return _all_other(rows, business_type, "AI call failed")

    out: list[_mapping.Classification] = []
    fallback_cat = _cats.fallback_other(business_type)
    for row, item in zip(rows, parsed):
        cat = item.get("category", "")
        if not _cats.is_valid_category(cat, business_type):
            cat = fallback_cat
        is_credit = float(row.get("amount") or 0) > 0
        out.append(_mapping.Classification(
            category=cat,
            confidence=float(item.get("confidence", 0.6)),
            is_income=bool(item.get("is_income", is_credit)),
            reasoning=str(item.get("reasoning", "Classified by AI"))[:200],
        ))
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _all_other(rows: list[dict], business_type: str, reason: str) -> list[_mapping.Classification]:
    """Build a list of 'other' classifications — used for every failure mode
    so the caller can fall back to rules / cache without special-casing."""
    cat = _cats.fallback_other(business_type)
    return [_mapping.Classification(
        category=cat,
        confidence=0.0,
        is_income=float(r.get("amount") or 0) > 0,
        reasoning=reason,
    ) for r in rows]


def _build_prompt(rows: list[dict], business_type: str, categories: tuple[str, ...]) -> str:
    # Each transaction line carries up to two signals:
    #   - description: the merchant / payee
    #   - reference:   the customer-supplied memo / invoice / "RENT FEB" line
    # The reference is often FAR more diagnostic than the merchant —
    # "FPI Acme Ltd" + reference "INV-2026-001" is unambiguous turnover.
    rows_text_lines: list[str] = []
    for i, r in enumerate(rows):
        desc = (r.get("description") or "").strip()[:80]
        ref = (r.get("reference") or "").strip()[:80] if r.get("reference") else ""
        line = f"  {i+1}. \"{desc}\" amount={r.get('amount')}"
        if ref:
            line += f" reference=\"{ref}\""
        rows_text_lines.append(line)
    rows_text = "\n".join(rows_text_lines)
    biz_label = "UK property income" if business_type == "property" else "self-employment (sole trader)"
    return (
        f"You are classifying UK bank transactions for {biz_label} HMRC MTD ITSA "
        "quarterly submission. For each transaction, return one of these "
        "categories EXACTLY (HMRC API field names):\n"
        + "\n".join(f"  - {c}" for c in categories)
        + "\n\nRules:\n"
        "  - Positive amounts are money INTO the account (likely income).\n"
        "  - Negative amounts are money OUT (likely expense).\n"
        "  - The `reference` field (when present) is a STRONG signal. It's\n"
        "    the customer-supplied memo: invoice numbers, rent periods,\n"
        "    'WAGES JOHN SMITH', 'HMRC SELF ASSESSMENT', etc. Use it to\n"
        "    disambiguate vague merchants. e.g. payee 'Acme Ltd' + reference\n"
        "    'INV-2026-001' -> turnover (definite); payee 'Acme Ltd' alone\n"
        "    -> low-confidence guess.\n"
        "  - 'HMRC' references (Self Assessment, PAYE, VAT, Corporation Tax)\n"
        "    are TAX PAYMENTS, not deductible expenses — use 'other' for\n"
        "    self-employment with reasoning explaining it's a tax liability\n"
        "    settlement, not a business expense.\n"
        "  - UK parking merchants (NCP, MiPermit, RingGo, JustPark) -> travelCosts.\n"
        "  - Restaurants/cafes -> businessEntertainmentCosts (HMRC restricts this).\n"
        "  - Software subs (AWS, OpenAI, Notion) -> adminCosts.\n"
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
