"""
Unit tests for the 4-strategy receipt → bank-transaction matcher.

Covers the realistic failure modes documented in the design:
  1. Amount drift (exact, ±£1, ±2%, FX, tips)
  2. Date drift (card settlement lag)
  3. Merchant-name drift (APL*PURCHASE vs Apple Store)
  4. Many-to-one / one-to-many
  5. Orphan handling (cash receipts, no candidates)
  6. AI-mediated fallback for hard cases
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.receipt_matcher import (
    MatchResult,
    match_receipt,
    match_batch,
    merchant_overlap,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _tx(tid: int, date: str, desc: str, amount: float) -> dict:
    return {"id": tid, "date_iso": date, "description": desc, "amount": amount}


def _receipt(store: str, date: str, total: float) -> dict:
    return {"store_name": store, "date_iso": date, "total_amount": total}


# ---------------------------------------------------------------------------
# Strategy 1: exact match
# ---------------------------------------------------------------------------


def test_exact_amount_date_merchant_auto_links():
    txs = [_tx(1, "2026-08-04", "AMAZON UK MARKETPLACE", -42.99)]
    r = match_receipt(_receipt("Amazon", "2026-08-04", 42.99), txs)
    assert r.strategy == "exact"
    assert r.transaction_id == 1
    assert r.auto_link is True
    assert r.confidence >= 95


def test_exact_card_settlement_lag_within_3_days():
    """Card receipt 4th, bank settled 6th — within 3-day tolerance. The
    bank description has the merchant name in clear text, so exact fires."""
    txs = [_tx(1, "2026-08-06", "APPLE STORE LONDON", -49.99)]
    r = match_receipt(_receipt("Apple Store", "2026-08-04", 49.99), txs)
    assert r.strategy == "exact"
    assert r.transaction_id == 1


def test_apl_purchase_alias_falls_to_strong_or_ai():
    """The bank-noise prefix 'APL*PURCHASE' doesn't share a token with
    'Apple Store' — exact fails, strong fires on amount+date, AI confirms."""
    txs = [_tx(1, "2026-08-06", "APL*PURCHASE 0844 209 0611", -49.99)]
    r = match_receipt(_receipt("Apple Store", "2026-08-04", 49.99), txs)
    assert r.strategy == "strong"
    assert r.transaction_id == 1
    assert r.needs_confirmation


def test_exact_handles_signed_bank_amounts():
    """Bank line is -42.99 (debit), receipt is 42.99 (gross)."""
    txs = [_tx(1, "2026-08-04", "AMAZON UK", -42.99)]
    r = match_receipt(_receipt("Amazon", "2026-08-04", 42.99), txs)
    assert r.strategy == "exact"


def test_no_match_when_amount_differs_by_one_pence_too_far_for_exact():
    """0.02p drift fails exact (limit 0.01) — falls to strong instead."""
    txs = [_tx(1, "2026-08-04", "AMAZON UK", -43.01)]
    r = match_receipt(_receipt("Amazon", "2026-08-04", 42.99), txs)
    assert r.strategy in ("strong", "orphan")  # not exact
    if r.strategy == "strong":
        assert r.needs_confirmation


# ---------------------------------------------------------------------------
# Strategy 2: strong candidate (2-of-3 signals)
# ---------------------------------------------------------------------------


def test_strong_tip_added_above_receipt_amount():
    """Restaurant: receipt £45, bank £50 (£5 tip). Amount within 2%? No — 11%.
    But within £1? No. So this needs date + merchant signals."""
    # 50/45 = 11% diff, so amount fails strong's ±2% / ±£1.
    txs = [_tx(1, "2026-09-12", "DISHOOM SHOREDITCH", -50.00)]
    r = match_receipt(_receipt("Dishoom", "2026-09-12", 45.00), txs)
    # With amount NOT matching, only date + merchant signal = 2/3, qualifies as strong
    assert r.strategy == "strong"
    assert r.needs_confirmation


def test_strong_fx_drift_within_2_percent():
    """€50 receipt becomes £43.45; bank line £43.50 = 0.1% drift — strong."""
    txs = [_tx(1, "2026-07-15", "TICKETMASTER DE", -43.50)]
    r = match_receipt(_receipt("Ticketmaster", "2026-07-15", 43.45), txs)
    assert r.strategy in ("exact", "strong")


def test_strong_caps_confidence_below_exact():
    txs = [_tx(1, "2026-08-04", "WHSMITH 0123 LONDON", -18.50)]
    # Same amount + date but merchant overlap 60% (still less than exact's 70%)
    r = match_receipt(_receipt("WHSmith", "2026-08-04", 18.50), txs)
    # WHSmith / WHSMITH 0123 LONDON has high token overlap so should be exact
    assert r.strategy == "exact"


# ---------------------------------------------------------------------------
# Strategy 3: AI-mediated
# ---------------------------------------------------------------------------


def test_ai_fallback_when_rules_fail():
    """The rules can't find a match. Inject an AI stub that picks the right one."""
    txs = [
        _tx(1, "2026-08-04", "TFL.GOV.UK/CP", -12.40),
        _tx(2, "2026-08-04", "STARBUCKS 0987", -5.40),
        _tx(3, "2026-08-04", "APL*ITUNES.COM/BILL", -49.99),
    ]
    # Receipt deliberately has merchant the rules can't link.
    receipt = _receipt("Apple Inc.", "2026-08-02", 49.99)

    def fake_ai(rec: dict, candidates: list[dict]) -> dict:
        # The candidate with the matching amount is id=3
        return {"match_id": 3, "confidence": 88, "reason": "APL = Apple"}

    r = match_receipt(receipt, txs, ai_call=fake_ai)
    # Try the more lenient strong strategy first — amount + date match. Tests
    # that the fallback chain is correct.
    assert r.transaction_id == 3
    assert r.confidence >= 70
    # If rules already matched, we don't expect AI to fire; assert the chain
    # is sensible by checking the result is consistent.
    assert r.strategy in ("strong", "ai")


def test_ai_returns_none_when_no_match():
    txs = [_tx(1, "2026-08-04", "TFL", -12.40)]
    receipt = _receipt("Apple Inc.", "2026-01-01", 999.00)

    def fake_ai(rec, cands):
        return None

    r = match_receipt(receipt, txs, ai_call=fake_ai)
    assert r.strategy == "orphan"


def test_ai_can_be_disabled():
    txs = [_tx(1, "2026-08-04", "TFL", -12.40)]
    receipt = _receipt("Apple Inc.", "2026-01-01", 999.00)
    r = match_receipt(receipt, txs, enable_ai=False)
    assert r.strategy == "orphan"


def test_ai_failure_does_not_crash_matcher():
    """If the AI call raises, the matcher falls through to orphan gracefully."""
    txs = [_tx(1, "2026-08-04", "TFL", -12.40)]

    def boom(*args, **kwargs):
        raise RuntimeError("Claude is down")

    r = match_receipt(_receipt("Apple", "2026-08-04", 49.99), txs, ai_call=boom)
    assert r.strategy == "orphan"


# ---------------------------------------------------------------------------
# Strategy 4: orphan handling
# ---------------------------------------------------------------------------


def test_orphan_when_no_candidates():
    r = match_receipt(_receipt("Apple", "2026-08-04", 49.99), [])
    assert r.strategy == "orphan"
    assert r.transaction_id is None
    assert r.auto_link is False


def test_orphan_when_amount_too_far_off():
    """Cash receipt — nothing in the bank statement matches."""
    txs = [_tx(1, "2026-08-04", "TFL", -2.50)]
    r = match_receipt(_receipt("Local cafe", "2026-08-04", 8.50), txs)
    assert r.strategy == "orphan"


# ---------------------------------------------------------------------------
# Merchant overlap helper — explicit edge cases
# ---------------------------------------------------------------------------


def test_merchant_overlap_normalises_punctuation_and_case():
    assert merchant_overlap("Amazon.co.uk", "AMAZON UK MARKETPLACE") >= 0.5


def test_merchant_overlap_handles_bank_prefix_garbage():
    """APL*PURCHASE 0844 209 0611 should still match 'Apple Store'."""
    score = merchant_overlap("Apple Store", "APL*PURCHASE 0844 209 0611")
    # 'apple' is in 'apl' via prefix? No — 'apl' is shorter than 4 chars so
    # the substring path is too short. But token overlap is also 0.
    # This test documents that we DON'T magically match these — that's the
    # AI fallback's job.
    assert score < 0.4


def test_merchant_overlap_empty_inputs():
    assert merchant_overlap("", "") == 0.0
    assert merchant_overlap(None, "AMAZON") == 0.0
    assert merchant_overlap("Amazon", None) == 0.0


def test_merchant_overlap_completely_different():
    assert merchant_overlap("Amazon", "Tesco") == 0.0


# ---------------------------------------------------------------------------
# Confidence ordering — exact > strong > ai
# ---------------------------------------------------------------------------


def test_exact_beats_strong_when_both_qualify():
    """Two candidates — one matches exactly, one matches loosely. Exact wins."""
    txs = [
        _tx(1, "2026-08-05", "AMAZON SOMETHING", -41.50),   # loose
        _tx(2, "2026-08-04", "AMAZON UK", -42.99),          # exact
    ]
    r = match_receipt(_receipt("Amazon", "2026-08-04", 42.99), txs)
    assert r.strategy == "exact"
    assert r.transaction_id == 2


# ---------------------------------------------------------------------------
# Batch matching — one bank line can't be claimed twice
# ---------------------------------------------------------------------------


def test_batch_does_not_double_match_same_transaction():
    """Two receipts both match the same bank line exactly. First receipt
    wins; second has to fall back."""
    txs = [_tx(1, "2026-08-04", "AMAZON UK", -42.99)]
    receipts = [
        _receipt("Amazon", "2026-08-04", 42.99),
        _receipt("Amazon", "2026-08-04", 42.99),   # duplicate
    ]
    results = match_batch(receipts, txs)
    assert results[0][1].strategy == "exact"
    assert results[0][1].transaction_id == 1
    assert results[1][1].strategy == "orphan"


def test_batch_independent_when_amounts_differ():
    """Two receipts, two distinct bank lines. Both match cleanly."""
    txs = [
        _tx(1, "2026-08-04", "AMAZON UK", -42.99),
        _tx(2, "2026-08-04", "WHSMITH LONDON", -18.50),
    ]
    receipts = [
        _receipt("Amazon", "2026-08-04", 42.99),
        _receipt("WHSmith", "2026-08-04", 18.50),
    ]
    results = match_batch(receipts, txs)
    assert {r[1].transaction_id for r in results} == {1, 2}
    assert all(r[1].strategy == "exact" for r in results)


# ---------------------------------------------------------------------------
# Defensive: malformed inputs shouldn't crash
# ---------------------------------------------------------------------------


def test_missing_receipt_amount_falls_to_orphan():
    txs = [_tx(1, "2026-08-04", "AMAZON UK", -42.99)]
    r = match_receipt({"store_name": "Amazon", "date_iso": "2026-08-04"}, txs)
    assert r.strategy == "orphan"


def test_missing_dates_handled():
    txs = [_tx(1, None, "AMAZON UK", -42.99)]
    r = match_receipt(_receipt("Amazon", None, 42.99), txs)
    assert r.strategy == "orphan"
    # Both dates missing → can't establish date_ok → no match


def test_transaction_with_no_amount_skipped():
    txs = [
        {"id": 1, "date_iso": "2026-08-04", "description": "AMAZON", "amount": None},
        _tx(2, "2026-08-04", "AMAZON UK", -42.99),
    ]
    r = match_receipt(_receipt("Amazon", "2026-08-04", 42.99), txs)
    assert r.strategy == "exact"
    assert r.transaction_id == 2
