"""
HMRC categorisation endpoints.

  POST /api/hmrc/categorise            classify a batch of rows
  POST /api/hmrc/categorise/override   save user's manual correction
  GET  /api/hmrc/categorise/summary    aggregate current session rows
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..services import ai_classifier as _ai
from ..services import mapping as _mapping
from ..repositories import overrides as _overrides

logger = logging.getLogger("bankparse.hmrc.routes.categorise")
router = APIRouter()


def _user(request: Request) -> dict | None:
    try:
        from app import get_current_user  # type: ignore
        return get_current_user(request)
    except Exception:
        return None


def _business_type(raw: str | None) -> str:
    """Normalise to 'se' or 'property'."""
    if (raw or "").lower() in ("property", "uk-property", "ukproperty", "landlord"):
        return "property"
    return "se"


def _classify_one(description: str, amount: float, business_type: str, user_full_name: str | None) -> _mapping.Classification:
    if business_type == "property":
        return _mapping.classify_property(description, amount, user_full_name=user_full_name)
    return _mapping.classify_self_employment(description, amount, user_full_name=user_full_name)


@router.post("/api/hmrc/categorise")
async def categorise(request: Request):
    """Take a list of rows; return them annotated with HMRC category data.

    Body:
        {
          "business_type": "se" | "property",
          "rows": [{"description": "...", "amount": -12.50, "date": "..."}, ...]
        }

    Response:
        {
          "rows": [
            { ...input row..., "hmrc": {
                "category": "travelCosts",
                "confidence": 0.9,
                "is_income": false,
                "reasoning": "Parking merchant",
                "source": "rule" | "override" | "ai"
            }},
            ...
          ]
        }
    """
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    business_type = _business_type(body.get("business_type"))
    rows_in = body.get("rows") or []
    if not isinstance(rows_in, list):
        raise HTTPException(status_code=400, detail="`rows` must be a list.")

    user_full_name = user.get("email", "").split("@")[0]  # placeholder until we collect real names
    out_rows: list[dict] = []
    low_conf_rows: list[tuple[int, dict]] = []

    for i, r in enumerate(rows_in):
        desc = (r.get("description") or "").strip()
        try:
            amount = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        # 1. User override wins
        ov_category = _overrides.lookup(user["id"], desc, business_type)
        if ov_category:
            cl = _mapping.Classification(
                category=ov_category, confidence=1.0,
                is_income=amount > 0,
                reasoning="Your saved category for this merchant",
            )
            source = "override"
        else:
            # 2. Static rules
            cl = _classify_one(desc, amount, business_type, user_full_name=user_full_name)
            source = "rule"
            if cl.confidence < 0.5:
                # 3. (Optional) AI fallback for low-confidence rows.
                low_conf_rows.append((i, r))
                # Tentative placeholder; will be overwritten below if AI runs.

        out_rows.append({**r, "hmrc": {
            "category": cl.category,
            "confidence": cl.confidence,
            "is_income": cl.is_income,
            "reasoning": cl.reasoning,
            "source": source,
        }})

    # AI fallback batch (only fires if feature flag is on)
    if low_conf_rows and _ai.is_enabled():
        try:
            ai_results = _ai.reclassify_batch([r for _, r in low_conf_rows], business_type)
            for (idx, _), ai_cl in zip(low_conf_rows, ai_results):
                if ai_cl.confidence > 0:
                    out_rows[idx]["hmrc"] = {
                        "category": ai_cl.category,
                        "confidence": ai_cl.confidence,
                        "is_income": ai_cl.is_income,
                        "reasoning": ai_cl.reasoning,
                        "source": "ai",
                    }
        except Exception:
            logger.exception("AI re-classify batch failed; keeping rule outputs")

    return JSONResponse({"rows": out_rows, "business_type": business_type})


@router.post("/api/hmrc/categorise/override")
async def save_override(request: Request):
    """Save a manual category correction. The next time the same merchant
    appears for this user + business type, we'll use this category.

    Body: { "description": "...", "business_type": "se"|"property", "category": "..." }
    """
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
    """Aggregate a list of rows into income + expenses by HMRC category."""
    user = _user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    body = await request.json()
    business_type = _business_type(body.get("business_type"))
    rows_in = body.get("rows") or []
    user_full_name = user.get("email", "").split("@")[0]

    # Apply user overrides on the fly so the summary uses their preferred mapping.
    rows_for_agg = []
    for r in rows_in:
        desc = (r.get("description") or "").strip()
        ov = _overrides.lookup(user["id"], desc, business_type)
        if ov:
            # Synthesise a deterministic classification by overriding the description
            # with a sentinel — quickest path is to pass through and let aggregate
            # re-classify; we instead pre-bucket directly here for accuracy.
            rows_for_agg.append({**r, "__forced_category__": ov})
        else:
            rows_for_agg.append(r)

    if business_type == "property":
        result = _mapping.aggregate_property(rows_for_agg, user_full_name=user_full_name)
    else:
        result = _mapping.aggregate_self_employment(rows_for_agg, user_full_name=user_full_name)

    return JSONResponse({"summary": result, "business_type": business_type})
