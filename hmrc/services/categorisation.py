"""
Categorisation orchestration — the service layer for `/api/hmrc/categorise*`.

This is where the resolution-order policy lives, NOT the router. The router
is a thin HTTP adapter that validates the request, calls this service, and
serialises the result.

Resolution order per row (top wins):
  1. User's saved override            → instant         (source="override")
  2. Global merchant cache hit        → instant         (source="ai_cached")
  3. Fresh Claude classification      → parallel batch  (source="ai")
  4. Regex rule fallback              → only when AI is disabled
                                                          (source="rule")

The service exposes two methods that map 1:1 to the public endpoints:

    resolve(req, *, user_id)    -> CategoriseResponse
    summarise(req, *, user_id)  -> SummaryResponse

`summarise` composes `resolve` and aggregates — no Request faking, no
recursive HTTP calls.

Config (all env-overridable so we can tune live):
    HMRC_AI_BATCH_SIZE        # rows per Claude call           (default 25)
    HMRC_AI_PARALLEL          # concurrent Claude calls        (default 8)
    HMRC_CACHE_MIN_CONFIDENCE # AI confidence to cache         (default 0.7)
                              # (see classifier_cache._MIN_CACHE_CONFIDENCE)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Iterable

from . import ai_classifier as _ai
from . import mapping as _mapping
from ..repositories import classifier_cache as _cache
from ..repositories import overrides as _overrides
from ..schemas import categories as _cats
from ..schemas.categorise import (
    CategoriseRequest,
    CategoriseResponse,
    CategorySummary,
    HmrcClassification,
    SummaryResponse,
    TransactionIn,
    TransactionOut,
)

logger = logging.getLogger("bankparse.hmrc.categorisation")


# ---------------------------------------------------------------------------
# Configuration helpers — read at call time so Railway env changes apply
# without a restart and tests can monkeypatch via env.
# ---------------------------------------------------------------------------

def _batch_size() -> int:
    try:
        return max(1, int(os.environ.get("HMRC_AI_BATCH_SIZE", "") or 25))
    except ValueError:
        return 25


def _max_parallel() -> int:
    try:
        return max(1, int(os.environ.get("HMRC_AI_PARALLEL", "") or 8))
    except ValueError:
        return 8


# ---------------------------------------------------------------------------
# Internal value objects (no FastAPI/pydantic dependency — pure Python so the
# service can be called from a CLI, a cron job, or a unit test).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Resolved:
    """One row's classification + provenance."""
    index: int                  # row index in the original request
    category: str
    confidence: float
    is_income: bool
    reasoning: str
    source: str                 # "override" | "ai_cached" | "ai" | "rule"


@dataclass(frozen=True)
class CategorisationMetrics:
    """What happened during a single resolve() call. Returned so the router
    (or a background metrics writer) can log / persist it."""
    total_rows: int
    overrides: int
    cache_hits: int
    ai_calls: int               # rows sent to Claude (not batches)
    rule_fallbacks: int
    elapsed_ms: int


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------

async def resolve(
    req: CategoriseRequest, *, user_id: int,
) -> tuple[CategoriseResponse, CategorisationMetrics]:
    """Run the full resolution pipeline and return the API response shape
    plus structured metrics about which paths fired.

    Pure orchestration — no HTTP, no FastAPI Request. Callable from a test
    or a background job.
    """
    started = time.monotonic()
    business_type = req.business_type
    rows_in: list[TransactionIn] = req.rows or []

    if not rows_in:
        return (
            CategoriseResponse(business_type=business_type, rows=[]),
            CategorisationMetrics(0, 0, 0, 0, 0, 0),
        )

    resolved: list[_Resolved | None] = [None] * len(rows_in)

    # ---- Step 1: user overrides (per-user, instant) -----------------------
    pending_after_overrides: list[int] = []
    override_count = 0
    for i, r in enumerate(rows_in):
        desc = (r.description or "").strip()
        amount = r.amount
        ov = _overrides.lookup(user_id, desc, business_type)
        if ov:
            resolved[i] = _Resolved(
                index=i, category=ov, confidence=1.0, is_income=amount > 0,
                reasoning="Your saved category for this merchant",
                source="override",
            )
            override_count += 1
        else:
            pending_after_overrides.append(i)

    # ---- Step 2: bulk cache lookup ---------------------------------------
    cache_keys = [
        (_overrides.merchant_key((rows_in[i].description or "").strip()), business_type)
        for i in pending_after_overrides
    ]
    cache_hits_by_key = _cache.lookup_many(cache_keys) if cache_keys else {}
    pending_after_cache: list[int] = []
    cache_hit_count = 0
    for list_idx, i in enumerate(pending_after_overrides):
        key = cache_keys[list_idx]
        hit = cache_hits_by_key.get(key)
        if hit:
            amount = rows_in[i].amount
            resolved[i] = _Resolved(
                index=i,
                category=hit["category"],
                confidence=float(hit["confidence"]),
                is_income=amount > 0,
                reasoning=hit.get("reasoning") or "Cached AI classification",
                source="ai_cached",
            )
            cache_hit_count += 1
        else:
            pending_after_cache.append(i)

    # ---- Step 3: classify the remainder ----------------------------------
    ai_call_count = 0
    rule_fallback_count = 0
    if pending_after_cache:
        if _ai.is_enabled():
            ai_rows = [rows_in[i].model_dump() for i in pending_after_cache]
            classifications = await _run_ai_batches(ai_rows, business_type)
            for i, cl in zip(pending_after_cache, classifications):
                amount = rows_in[i].amount
                is_real_ai = cl.confidence > 0
                resolved[i] = _Resolved(
                    index=i,
                    category=cl.category,
                    confidence=cl.confidence,
                    is_income=cl.is_income if cl.is_income is not None else (amount > 0),
                    reasoning=cl.reasoning,
                    source="ai" if is_real_ai else "rule",
                )
                if is_real_ai:
                    ai_call_count += 1
                    # Persist high-confidence wins to the shared cache so the
                    # next user gets it instantly. Failure here MUST NOT
                    # break the response.
                    try:
                        _cache.upsert(
                            (rows_in[i].description or "").strip(),
                            business_type, cl.category, cl.confidence, cl.reasoning,
                        )
                    except Exception:
                        logger.exception("Failed to cache AI classification for row %d", i)
                else:
                    rule_fallback_count += 1
        else:
            # AI off: fall through to regex rules.
            for i in pending_after_cache:
                r = rows_in[i]
                cl = _classify_with_rules(
                    (r.description or "").strip(), r.amount, business_type,
                )
                resolved[i] = _Resolved(
                    index=i,
                    category=cl.category,
                    confidence=cl.confidence,
                    is_income=cl.is_income,
                    reasoning=cl.reasoning,
                    source="rule",
                )
                rule_fallback_count += 1

    # ---- Assemble the response in original row order ---------------------
    out_rows: list[TransactionOut] = []
    for i, src_row in enumerate(rows_in):
        r = resolved[i]
        # `r` is guaranteed non-None at this point — every branch assigns it.
        assert r is not None, "every row must be resolved"
        out_rows.append(TransactionOut(
            **src_row.model_dump(),
            hmrc=HmrcClassification(
                category=r.category,
                confidence=r.confidence,
                is_income=r.is_income,
                reasoning=r.reasoning,
                source=r.source,
            ),
        ))

    elapsed_ms = int((time.monotonic() - started) * 1000)
    metrics = CategorisationMetrics(
        total_rows=len(rows_in),
        overrides=override_count,
        cache_hits=cache_hit_count,
        ai_calls=ai_call_count,
        rule_fallbacks=rule_fallback_count,
        elapsed_ms=elapsed_ms,
    )
    return CategoriseResponse(business_type=business_type, rows=out_rows), metrics


async def summarise(
    req: CategoriseRequest, *, user_id: int,
) -> tuple[SummaryResponse, CategorisationMetrics]:
    """Resolve + aggregate. Re-uses `resolve()` so the totals always match
    what the user sees in the table — no two different category paths."""
    cat_resp, metrics = await resolve(req, user_id=user_id)

    income: dict[str, float] = {}
    expenses: dict[str, float] = {}
    flagged: list[dict] = []
    excluded: list[dict] = []

    for row_out in cat_resp.rows:
        h = row_out.hmrc
        amount = abs(row_out.amount)
        if h.category == _cats.EXCLUDE_OWNER_TRANSFER:
            excluded.append({"row": row_out.model_dump(), "classification": h.model_dump()})
            continue
        # Route by the category's INTRINSIC HMRC meaning, not by the upstream
        # `is_income` flag. The classifier (rules or AI) occasionally tags a
        # credit as `other` with `is_income=True` — under the old logic that
        # made "Other expense" show up under Income, which is nonsense.
        # `other`, `adminCosts`, etc. are expense categories no matter what
        # sign the transaction has; if the user actually has unmatched
        # income they should be in `otherIncome`/`turnover`.
        bucket = income if _cats.is_income_category(h.category, req.business_type) else expenses
        bucket[h.category] = round(bucket.get(h.category, 0.0) + amount, 2)
        if h.confidence < 0.5:
            flagged.append({"row": row_out.model_dump(), "classification": h.model_dump()})

    summary = CategorySummary(
        income=income, expenses=expenses,
        flagged_for_review=flagged, excluded=excluded,
    )
    return (
        SummaryResponse(summary=summary, business_type=req.business_type),
        metrics,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _run_ai_batches(
    rows: list[dict], business_type: str,
) -> list[_mapping.Classification]:
    """Split rows into batches, call Claude in parallel under a semaphore.

    The semaphore bounds how many in-flight Anthropic requests we make on a
    huge statement (otherwise a 500-row upload would fire 20+ batches at
    once and hit Anthropic's rate limits).
    """
    if not rows:
        return []
    batch_size = _batch_size()
    parallel = _max_parallel()

    batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]
    sem = asyncio.Semaphore(parallel)

    async def _run_one(batch: list[dict]) -> list[_mapping.Classification]:
        async with sem:
            return await asyncio.to_thread(_ai.classify_batch, batch, business_type)

    results = await asyncio.gather(*[_run_one(b) for b in batches], return_exceptions=True)

    out: list[_mapping.Classification] = []
    for batch, result in zip(batches, results):
        if isinstance(result, BaseException):
            logger.exception("AI batch failed; falling back to rules", exc_info=result)
            for r in batch:
                out.append(_classify_with_rules(
                    (r.get("description") or "").strip(),
                    float(r.get("amount") or 0),
                    business_type,
                ))
        else:
            out.extend(result)
    return out


def _classify_with_rules(
    desc: str, amount: float, business_type: str,
) -> _mapping.Classification:
    """Regex-rule classifier. Used when AI is off OR an AI batch raised."""
    if business_type == "property":
        return _mapping.classify_property(desc, amount)
    return _mapping.classify_self_employment(desc, amount)
