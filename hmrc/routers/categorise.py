"""
HMRC categorisation endpoints — AI-first.

Resolution order per row:
  1. User's saved override → instant (source='override', ★ saved)
  2. Global merchant cache → instant (source='ai_cached', ✨ AI cached)
  3. Fresh Claude Haiku call → parallel batches of 25 (source='ai', ✨ AI)
  4. Fallback to legacy regex rules ONLY when AI is disabled
     (HMRC_AI_CATEGORISE not set)

The AI prompt always includes the full canonical HMRC category list so
Claude is constrained to pick valid values. Results that come back with
confidence >= 0.7 are written into the global cache for every future user.

Performance: a 172-row statement with a cold cache fires ~7 batches of 25
in parallel via asyncio — wall-clock dominated by one round-trip (~2 s)
instead of one giant batch (~12 s). With a warm cache, common UK merchants
(Costa, Tesco, NCP, etc.) are near-instant.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..services import ai_classifier as _ai
from ..services import mapping as _mapping
from ..repositories import overrides as _overrides
from ..repositories import classifier_cache as _cache

logger = logging.getLogger("bankparse.hmrc.routes.categorise")
router = APIRouter()

# How many rows per Claude call. Higher = fewer round-trips but slower per
# batch; lower = more parallelism but more overhead per batch.
_BATCH_SIZE = 25
# How many batches to run concurrently. Bounded so we don't hammer the
# Anthropic API on a huge statement.
_MAX_PARALLEL_BATCHES = 8


def _user(request: Request) -> dict | None:
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


def _business_type(raw: str | None) -> str:
    if (raw or "").lower() in ("property", "uk-property", "ukproperty", "landlord"):
        return "property"
    return "se"


def _fallback_classify(desc: str, amount: float, business_type: str, user_full_name: str | None) -> _mapping.Classification:
    """Static-rule classification — used when AI is disabled or fails."""
    if business_type == "property":
        return _mapping.classify_property(desc, amount, user_full_name=user_full_name)
    return _mapping.classify_self_employment(desc, amount, user_full_name=user_full_name)


async def _run_batches_concurrently(rows: list[dict], business_type: str) -> list[_mapping.Classification]:
    """Split rows into chunks and run AI batches in parallel.

    Returns one Classification per input row, same order.
    """
    if not rows:
        return []

    # Build batches
    batches: list[list[dict]] = [
        rows[i:i + _BATCH_SIZE] for i in range(0, len(rows), _BATCH_SIZE)
    ]
    sem = asyncio.Semaphore(_MAX_PARALLEL_BATCHES)

    async def _run_one(batch: list[dict]) -> list[_mapping.Classification]:
        async with sem:
            return await asyncio.to_thread(_ai.classify_batch, batch, business_type)

    results_per_batch = await asyncio.gather(*[_run_one(b) for b in batches], return_exceptions=True)

    # Flatten; on exception use rule fallback for that batch
    out: list[_mapping.Classification] = []
    for batch, result in zip(batches, results_per_batch):
        if isinstance(result, BaseException):
            logger.exception("AI batch failed; falling back to rules", exc_info=result)
            for r in batch:
                out.append(_fallback_classify(
                    r.get("description", ""), float(r.get("amount") or 0),
                    business_type, None,
                ))
        else:
            out.extend(result)
    return out


@router.post("/api/hmrc/categorise")
async def categorise(request: Request):
    """AI-first categorisation. See module docstring for resolution order.

    Body:
        {"business_type": "se" | "property",
         "rows": [{"description": "...", "amount": -12.50, "date": "..."}]}

    Response (same shape as before):
        {"rows": [{ ...input row..., "hmrc": {
            "category": "travelCosts",
            "confidence": 0.92,
            "is_income": false,
            "reasoning": "Parking app payment",
            "source": "override" | "ai_cached" | "ai" | "rule"
        }}]}
    """
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    business_type = _business_type(body.get("business_type"))
    rows_in = body.get("rows") or []
    if not isinstance(rows_in, list):
        raise HTTPException(status_code=400, detail="`rows` must be a list.")

    out_rows: list[dict] = [None] * len(rows_in)  # type: ignore
    to_classify: list[tuple[int, dict]] = []

    # Step 1: overrides + cache (both instant, no AI call)
    cache_keys: list[tuple[str, str]] = []
    cache_idx_map: list[int] = []
    for i, r in enumerate(rows_in):
        desc = (r.get("description") or "").strip()
        try:
            amount = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        # 1a. User override wins.
        ov = _overrides.lookup(user["id"], desc, business_type)
        if ov:
            out_rows[i] = {**r, "hmrc": {
                "category": ov, "confidence": 1.0, "is_income": amount > 0,
                "reasoning": "Your saved category for this merchant",
                "source": "override",
            }}
            continue

        # 1b. Defer cache lookup to a bulk fetch — collect keys.
        from ..repositories.overrides import merchant_key as _mk
        cache_keys.append((_mk(desc), business_type))
        cache_idx_map.append(i)

    # Step 2: bulk-fetch the cache for everything not overridden
    cache_hits = _cache.lookup_many(cache_keys) if cache_keys else {}

    for list_idx, i in enumerate(cache_idx_map):
        r = rows_in[i]
        amount = float(r.get("amount") or 0)
        key = cache_keys[list_idx]
        hit = cache_hits.get(key)
        if hit:
            out_rows[i] = {**r, "hmrc": {
                "category": hit["category"],
                "confidence": float(hit["confidence"]),
                "is_income": amount > 0,
                "reasoning": hit.get("reasoning") or "Cached AI classification",
                "source": "ai_cached",
            }}
        else:
            to_classify.append((i, r))

    # Step 3: classify the remainder via AI (or rules if AI disabled)
    if to_classify:
        if _ai.is_enabled():
            classifications = await _run_batches_concurrently(
                [r for _, r in to_classify], business_type,
            )
            for (i, r), cl in zip(to_classify, classifications):
                amount = float(r.get("amount") or 0)
                source = "ai" if cl.confidence > 0 else "rule"
                out_rows[i] = {**r, "hmrc": {
                    "category": cl.category,
                    "confidence": cl.confidence,
                    "is_income": cl.is_income if cl.is_income is not None else (amount > 0),
                    "reasoning": cl.reasoning,
                    "source": source,
                }}
                # Cache fresh AI wins so the next user gets them free.
                if source == "ai" and cl.confidence >= 0.7:
                    try:
                        _cache.upsert(
                            (r.get("description") or "").strip(),
                            business_type,
                            cl.category,
                            cl.confidence,
                            cl.reasoning,
                        )
                    except Exception:
                        logger.exception("Failed to cache AI classification")
        else:
            # AI flag off — fall back to regex rules for the remainder.
            for i, r in to_classify:
                desc = (r.get("description") or "").strip()
                amount = float(r.get("amount") or 0)
                cl = _fallback_classify(desc, amount, business_type, None)
                out_rows[i] = {**r, "hmrc": {
                    "category": cl.category,
                    "confidence": cl.confidence,
                    "is_income": cl.is_income,
                    "reasoning": cl.reasoning,
                    "source": "rule",
                }}

    return JSONResponse({"rows": out_rows, "business_type": business_type})


@router.post("/api/hmrc/categorise/override")
async def save_override(request: Request):
    """Save a manual category correction (per-user, NOT written into the
    global cache — one user's preference isn't necessarily right for everyone)."""
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    body = await request.json()
    desc = (body.get("description") or "").strip()
    category = (body.get("category") or "").strip()
    business_type = _business_type(body.get("business_type"))
    if not desc or not category:
        raise HTTPException(status_code=400, detail="`description` and `category` are required.")
    _overrides.save(user["id"], desc, business_type, category)
    return JSONResponse({"status": "ok", "merchant_key": _overrides.merchant_key(desc)})


@router.post("/api/hmrc/categorise/summary")
async def summary(request: Request):
    """Aggregate rows by HMRC category (used by the summary panel + XLSX export).

    Routes everything through `/api/hmrc/categorise` first to get the
    AI-corrected categories, then sums them.
    """
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    business_type = _business_type(body.get("business_type"))
    rows_in = body.get("rows") or []
    if not isinstance(rows_in, list) or not rows_in:
        return JSONResponse({"summary": {"income": {}, "expenses": {},
                                          "flagged_for_review": [], "excluded": []},
                             "business_type": business_type})

    # Re-use the categorise pipeline so totals match what the user sees in the table.
    sub_request_body = {"business_type": business_type, "rows": rows_in}

    class _BodyReplay:
        async def json(self):
            return sub_request_body

    class _ProxiedRequest:
        """Tiny wrapper so we can call categorise() with body but reuse the
        original request's auth (cookies/headers)."""
        def __init__(self, original, body):
            self._orig = original
            self._body = body
        def __getattr__(self, item):
            return getattr(self._orig, item)
        async def json(self):
            return self._body

    proxied = _ProxiedRequest(request, sub_request_body)
    cat_resp = await categorise(proxied)
    cat_data = cat_resp.body if hasattr(cat_resp, "body") else None
    if cat_data:
        import json as _json
        out = _json.loads(cat_data)
        rows = out.get("rows") or []
    else:
        rows = []

    income: dict[str, float] = {}
    expenses: dict[str, float] = {}
    flagged: list[dict] = []
    excluded: list[dict] = []
    for r in rows:
        h = r.get("hmrc") or {}
        cat = h.get("category", "")
        conf = float(h.get("confidence") or 0)
        is_income = bool(h.get("is_income"))
        amount = abs(float(r.get("amount") or 0))
        if cat == _mapping.EXCLUDE_OWNER_TRANSFER:
            excluded.append({"row": r, "classification": h})
            continue
        bucket = income if is_income else expenses
        bucket[cat] = round(bucket.get(cat, 0.0) + amount, 2)
        if conf < 0.5:
            flagged.append({"row": r, "classification": h})

    return JSONResponse({
        "summary": {
            "income": income,
            "expenses": expenses,
            "flagged_for_review": flagged,
            "excluded": excluded,
        },
        "business_type": business_type,
    })
