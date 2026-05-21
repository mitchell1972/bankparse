"""
Ingestion bridge: takes the AI parser's per-batch output and writes
structured rows into the ledger tables, then runs the auto-matcher.

Wired into the existing upload endpoints (statement + receipt) so that
every parse populates BOTH the legacy JSON blob (user_extracted_data)
AND the new structured tables. No migration required for the rollout.
"""
from __future__ import annotations

import logging
from typing import Iterable

import database
from services.receipt_matcher import match_receipt

logger = logging.getLogger(__name__)


def ingest_statement_rows(
    user_id: int,
    extracted_data_id: int,
    rows: list[dict],
) -> list[int]:
    """Take the per-row output of parse_statement_ai and write structured
    ledger_transactions. Returns the list of new transaction ids.

    Picks up the parser's new ``reference`` field if present — that's the
    customer-supplied memo / invoice line we now extract separately. Both
    ``reference`` and ``ref`` are accepted as aliases for back-compat.
    """
    new_ids: list[int] = []
    for row in rows or []:
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        ref_raw = row.get("reference") or row.get("ref")
        ref = (str(ref_raw).strip() or None) if ref_raw else None
        tx_id = database.insert_ledger_transaction(
            user_id,
            extracted_data_id=extracted_data_id,
            date_iso=row.get("date") or row.get("date_iso"),
            description=row.get("description") or row.get("desc"),
            reference=ref,
            amount=amount,
            currency=row.get("currency") or "GBP",
            balance=row.get("balance"),
            transaction_type=row.get("type"),
            hmrc_category=row.get("hmrc_category"),
            hmrc_category_confidence=row.get("hmrc_confidence"),
            hmrc_category_reason=row.get("hmrc_reason"),
        )
        new_ids.append(tx_id)
    return new_ids


async def auto_categorise_user_transactions(
    user_id: int,
    *,
    business_type: str = "se",
    only_uncategorised: bool = True,
    limit: int = 500,
) -> int:
    """Run the HMRC categoriser on the user's transactions and persist the
    result on each row.

    By default only runs on rows where ``hmrc_category`` is NULL so we don't
    keep re-categorising things the user has already confirmed. Returns the
    number of transactions that got a category attached this run.
    """
    # Local imports — keep ledger_ingest importable without the full hmrc stack
    from hmrc.schemas.categorise import CategoriseRequest, TransactionIn
    from hmrc.services.categorisation import resolve

    txs = database.get_user_ledger_transactions(user_id, limit=limit)
    if only_uncategorised:
        targets = [tx for tx in txs if not tx.get("hmrc_category")]
    else:
        targets = txs
    if not targets:
        return 0

    # Build the request payload — preserve the tx id so we can match the
    # response back to the ledger row. ``reference`` flows through so
    # the categoriser can lean on the customer-supplied memo.
    rows_in = [
        TransactionIn(
            description=tx.get("description") or "",
            reference=tx.get("reference") or None,
            amount=float(tx.get("amount") or 0),
            date=tx.get("date_iso"),
            id=tx["id"],
        )
        for tx in targets
    ]
    req = CategoriseRequest(business_type=business_type, rows=rows_in)

    try:
        resp, _metrics = await resolve(req, user_id=user_id)
    except Exception:  # noqa: BLE001 — auto-categorisation is best-effort
        logger.exception("Auto-categorisation failed for user %s", user_id)
        return 0

    # The response preserves order, so zip back to the input ids.
    updated = 0
    for tx, out_row in zip(targets, resp.rows):
        cls = out_row.hmrc
        if not cls or not cls.category:
            continue
        confidence = int(round(float(cls.confidence) * 100))
        database.update_transaction_status(
            tx["id"],
            hmrc_category=cls.category,
            hmrc_category_confidence=confidence,
            hmrc_category_reason=cls.reasoning or None,
        )
        updated += 1
    return updated


def ingest_receipt_and_match(
    user_id: int,
    extracted_data_id: int,
    receipt_parsed: dict,
    *,
    file_path: str | None = None,
    source_filename: str | None = None,
    enable_ai: bool = True,
    ai_call=None,
) -> dict:
    """Insert a receipt row, then run the matcher against the user's
    unmatched bank transactions. If the matcher returns ``strategy='exact'``
    AND ``auto_link=True``, the link is persisted immediately.

    Returns ``{"receipt_id": ..., "match": MatchResult dict}``.
    The caller can decide what to show the user (the inbox card for
    'strong'/'ai' matches, the green tick for 'exact'.)
    """
    totals = receipt_parsed.get("totals") or {}
    # parse_receipt_ai puts store_name/date/currency under `metadata`. Older
    # callers used `summary`. Accept both so we don't silently lose data —
    # which we did between 2026-04 and 2026-05-20, when every uploaded
    # receipt landed in the ledger with NULL store/date and was unmatchable.
    summary = receipt_parsed.get("metadata") or receipt_parsed.get("summary") or {}
    items = receipt_parsed.get("items") or []

    rc_id = database.insert_ledger_receipt(
        user_id,
        extracted_data_id=extracted_data_id,
        file_path=file_path,
        source_filename=source_filename or summary.get("source_filename"),
        store_name=summary.get("store_name"),
        date_iso=summary.get("date") or summary.get("date_iso"),
        total_amount=totals.get("total"),
        currency=summary.get("currency") or "GBP",
        subtotal=totals.get("subtotal"),
        tax_amount=totals.get("tax"),
        payment_method=totals.get("payment_method") or summary.get("payment_method"),
        items=items,
    )

    # Pull unmatched transactions to feed the matcher
    candidates = [
        tx for tx in database.get_user_ledger_transactions(user_id, limit=2000)
        if tx.get("receipt_status") != "matched"
    ]

    receipt_for_match = {
        "store_name": summary.get("store_name"),
        "date_iso": summary.get("date") or summary.get("date_iso"),
        "total_amount": totals.get("total"),
    }

    result = match_receipt(
        receipt_for_match,
        candidates,
        enable_ai=enable_ai,
        ai_call=ai_call,
    )

    # Auto-link only if exact + the matcher told us to
    if result.auto_link and result.transaction_id is not None:
        try:
            database.insert_ledger_link(
                transaction_id=result.transaction_id,
                receipt_id=rc_id,
                match_strategy=result.strategy,
                confidence=result.confidence,
                user_confirmed=False,
                reason=result.reason,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist auto-link for rc=%s", rc_id)

    return {
        "receipt_id": rc_id,
        "match": {
            "strategy": result.strategy,
            "transaction_id": result.transaction_id,
            "confidence": result.confidence,
            "reason": result.reason,
            "auto_link": result.auto_link,
            "needs_confirmation": result.needs_confirmation,
        },
    }


def rematch_user_unmatched_receipts(
    user_id: int,
    *,
    enable_ai: bool = False,
) -> list[dict]:
    """Re-run the matcher across every unmatched receipt for the user.
    Useful after the user uploads a new bank statement (now-matched
    receipts can be auto-resolved). AI is off by default — runs are
    silent, no UI prompts.
    """
    unmatched_receipts = database.get_user_ledger_receipts(
        user_id, only_unmatched=True,
    )
    candidates = [
        tx for tx in database.get_user_ledger_transactions(user_id, limit=5000)
        if tx.get("receipt_status") != "matched"
    ]

    consumed: set[int] = set()
    results: list[dict] = []
    for r in unmatched_receipts:
        receipt_for_match = {
            "store_name": r.get("store_name"),
            "date_iso": r.get("date_iso"),
            "total_amount": r.get("total_amount"),
        }
        active_candidates = [c for c in candidates if c["id"] not in consumed]
        result = match_receipt(receipt_for_match, active_candidates,
                               enable_ai=enable_ai)
        if result.auto_link and result.transaction_id is not None:
            consumed.add(result.transaction_id)
            try:
                database.insert_ledger_link(
                    transaction_id=result.transaction_id,
                    receipt_id=r["id"],
                    match_strategy=result.strategy,
                    confidence=result.confidence,
                    user_confirmed=False,
                    reason=result.reason,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Re-match failed for rc=%s", r["id"])
        results.append({
            "receipt_id": r["id"],
            "strategy": result.strategy,
            "transaction_id": result.transaction_id,
            "confidence": result.confidence,
        })
    return results


def build_unified_ledger(user_id: int) -> dict:
    """Build the unified-ledger view that powers the dashboard:
    every transaction with its linked receipts inlined, summarised
    counts, and HMRC category totals.
    """
    txs = database.get_user_ledger_transactions(user_id, limit=5000)
    receipts = database.get_user_ledger_receipts(user_id, limit=5000)
    receipts_by_id = {r["id"]: r for r in receipts}

    # Pull links once for the whole user
    links_rows = database._fetchall_dicts(
        "SELECT l.* FROM ledger_links l "
        "JOIN ledger_transactions t ON l.transaction_id = t.id "
        "WHERE t.user_id = ?",
        (user_id,),
    )
    links_by_tx: dict[int, list[dict]] = {}
    for link in links_rows:
        links_by_tx.setdefault(link["transaction_id"], []).append(link)

    out_transactions: list[dict] = []
    for tx in txs:
        linked = []
        for link in links_by_tx.get(tx["id"], []):
            r = receipts_by_id.get(link["receipt_id"])
            if r is None:
                continue
            linked.append({
                "receipt_id": r["id"],
                "store_name": r["store_name"],
                "date_iso": r["date_iso"],
                "total_amount": r["total_amount"],
                "tax_amount": r["tax_amount"],
                "match_strategy": link["match_strategy"],
                "confidence": link["confidence"],
                "user_confirmed": bool(link["user_confirmed"]),
                "reason": link.get("reason"),
            })
        out_transactions.append({
            "id": tx["id"],
            "date_iso": tx["date_iso"],
            "description": tx["description"],
            "reference": tx.get("reference"),
            "amount": tx["amount"],
            "currency": tx["currency"],
            "hmrc_category": tx["hmrc_category"],
            "hmrc_category_confidence": tx["hmrc_category_confidence"],
            "hmrc_category_reason": tx["hmrc_category_reason"],
            "receipt_status": tx["receipt_status"],
            "exclusion_reason": tx["exclusion_reason"],
            "vat_amount": tx["vat_amount"],
            "is_capital": bool(tx["is_capital"]),
            "business_pct": tx["business_pct"],
            "content_hash": tx["content_hash"],
            "linked_receipts": linked,
        })

    orphan_receipts = [
        {
            "id": r["id"],
            "store_name": r["store_name"],
            "date_iso": r["date_iso"],
            "total_amount": r["total_amount"],
            "tax_amount": r["tax_amount"],
            "match_status": r["match_status"],
        }
        for r in receipts
        if r.get("match_status") == "unmatched"
    ]

    return {
        "transactions": out_transactions,
        "orphan_receipts": orphan_receipts,
        "counts": {
            "transactions": len(out_transactions),
            "with_receipt": sum(
                1 for t in out_transactions if t["receipt_status"] == "matched"
            ),
            "missing_receipt": sum(
                1 for t in out_transactions if t["receipt_status"] == "missing"
            ),
            "orphan_receipts": len(orphan_receipts),
        },
    }
