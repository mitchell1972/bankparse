"""
Receipt → bank-transaction matching engine.

Four strategies, in order. The first that fires for a given receipt wins.
The output is always a ``MatchResult`` dataclass.

  1. ``exact``   — amount ±£0.01, date ±3 days, merchant overlap ≥ 70%
                   → auto-link, no user action
  2. ``strong``  — amount ±£1 OR ±2%, date ±5 days, OR merchant ≥ 40%
                   → user confirms via inbox card
  3. ``ai``      — ask Claude to pick from the 10 closest unmatched bank
                   lines. Returns reason. User confirms in inbox.
  4. ``orphan``  — no plausible match. Receipt becomes a "cash payment"
                   sub-ledger entry.

The matcher is deliberately pure-Python and side-effect-free. Callers
decide whether to persist the link via ``database.insert_ledger_link``.
That keeps the engine testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchResult:
    strategy: str           # 'exact' | 'strong' | 'ai' | 'orphan'
    transaction_id: int | None
    confidence: int         # 0-100
    reason: str
    auto_link: bool         # True only for 'exact' — link without asking the user
    needs_confirmation: bool = False  # 'strong' and 'ai' need user confirmation


# ---------------------------------------------------------------------------
# Tunable thresholds (kept here so tests can monkey-patch if needed)
# ---------------------------------------------------------------------------

EXACT_AMOUNT_TOLERANCE_GBP = 0.01
EXACT_DATE_TOLERANCE_DAYS = 3
EXACT_MERCHANT_OVERLAP = 0.70

STRONG_AMOUNT_TOLERANCE_GBP = 1.00
STRONG_AMOUNT_TOLERANCE_PCT = 0.02
STRONG_DATE_TOLERANCE_DAYS = 5
STRONG_MERCHANT_OVERLAP = 0.40

AI_TOPN_CANDIDATES = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    return None


def _days_between(a: str | None, b: str | None) -> int | None:
    da, db = _parse_iso(a), _parse_iso(b)
    if da is None or db is None:
        return None
    return abs((da - db).days)


def _amount_close(a: float, b: float, tol_gbp: float, tol_pct: float = 0.0) -> bool:
    """True if the two amounts match within either tolerance.
    Comparison is on absolute values — bank lines are signed (negative for
    debits), receipt totals are positive."""
    aa, ab = abs(float(a)), abs(float(b))
    if abs(aa - ab) <= tol_gbp:
        return True
    if ab > 0 and abs(aa - ab) / ab <= tol_pct:
        return True
    return False


def _normalise(text: str | None) -> str:
    """Lowercase, drop everything non-alphanumeric, collapse whitespace."""
    if not text:
        return ""
    import re
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


# Common bank-statement merchant prefixes/noise to strip.
_BANK_NOISE = {
    "apl", "amzn", "amazonuk", "amzpaymen", "googlecom", "googleplay",
    "paypalpaypal", "stripepaypal", "card", "purchase", "payment",
    "directdebit", "directdeposit", "transfer", "online",
    "marketplace", "uk", "london", "ldn", "ie",
}


def merchant_overlap(receipt_store: str | None, bank_desc: str | None) -> float:
    """Compute a 0..1 similarity score between a receipt's store name and a
    bank statement description. Token-set Jaccard on normalised tokens,
    stripping common bank-noise words.

    Returns 1.0 if either side starts with the other after normalisation —
    handles "APL*PURCHASE" vs "Apple Store"."""
    a = _normalise(receipt_store).split()
    b = _normalise(bank_desc).split()
    if not a or not b:
        return 0.0
    sa = {t for t in a if t not in _BANK_NOISE and len(t) >= 2}
    sb = {t for t in b if t not in _BANK_NOISE and len(t) >= 2}
    if not sa or not sb:
        return 0.0
    # Substring match on any token — catches "AMAZON" in "AMAZON UK MKTPLACE"
    for ta in sa:
        for tb in sb:
            if ta in tb or tb in ta:
                # Penalise very-short matches but still credit them
                shortest = min(len(ta), len(tb))
                if shortest >= 4:
                    inter = sa & sb
                    return max(0.7, len(inter) / max(len(sa | sb), 1))
    inter = sa & sb
    if not inter:
        return 0.0
    return len(inter) / len(sa | sb)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _try_exact(receipt: dict, transactions: list[dict]) -> MatchResult | None:
    """Strategy 1 — exact match. Returns None if no candidate qualifies."""
    rec_total = receipt.get("total_amount")
    rec_date = receipt.get("date_iso")
    rec_store = receipt.get("store_name")
    if rec_total is None:
        return None
    for tx in transactions:
        if tx.get("amount") is None:
            continue
        if not _amount_close(tx["amount"], rec_total, EXACT_AMOUNT_TOLERANCE_GBP):
            continue
        days = _days_between(tx.get("date_iso"), rec_date)
        if days is None or days > EXACT_DATE_TOLERANCE_DAYS:
            continue
        overlap = merchant_overlap(rec_store, tx.get("description"))
        if overlap < EXACT_MERCHANT_OVERLAP:
            continue
        confidence = int(95 + 5 * overlap)  # 95-100
        return MatchResult(
            strategy="exact",
            transaction_id=tx["id"],
            confidence=min(confidence, 100),
            reason=(
                f"Amount £{abs(tx['amount']):.2f} matches receipt total "
                f"£{rec_total:.2f} within £0.01; dates {days}d apart; "
                f"merchant overlap {int(overlap*100)}%."
            ),
            auto_link=True,
        )
    return None


def _try_strong(receipt: dict, transactions: list[dict]) -> MatchResult | None:
    """Strategy 2 — strong candidate. Two-of-three signals."""
    rec_total = receipt.get("total_amount")
    rec_date = receipt.get("date_iso")
    rec_store = receipt.get("store_name")
    if rec_total is None:
        return None

    best: tuple[int, MatchResult] | None = None
    for tx in transactions:
        if tx.get("amount") is None:
            continue
        amt_ok = _amount_close(
            tx["amount"], rec_total,
            STRONG_AMOUNT_TOLERANCE_GBP, STRONG_AMOUNT_TOLERANCE_PCT,
        )
        days = _days_between(tx.get("date_iso"), rec_date)
        date_ok = days is not None and days <= STRONG_DATE_TOLERANCE_DAYS
        overlap = merchant_overlap(rec_store, tx.get("description"))
        merchant_ok = overlap >= STRONG_MERCHANT_OVERLAP

        signals = sum([amt_ok, date_ok, merchant_ok])
        if signals < 2:
            continue
        # Safety rail: without a date signal, we refuse to suggest a strong
        # link. Dates are the single most reliable proof a transaction
        # actually happened in this period — without them we can't tell a
        # current-month £42.99 Amazon from a year-old one. The AI strategy
        # can still fire if the user actually wants the suggestion.
        if not date_ok:
            continue
        confidence = 50 + 10 * signals + int(20 * overlap)
        # Prefer the candidate with the most signals; tie-break by amount-closeness
        score = signals * 100 + int(100 - min(
            abs(abs(tx["amount"]) - rec_total) * 10, 100
        ))
        result = MatchResult(
            strategy="strong",
            transaction_id=tx["id"],
            confidence=min(confidence, 94),  # cap below 95 so exact wins ties
            reason=(
                f"Probable match: amount £{abs(tx['amount']):.2f} vs receipt "
                f"£{rec_total:.2f}, dates {days if days is not None else '?'}d apart, "
                f"merchant overlap {int(overlap*100)}%."
            ),
            auto_link=False,
            needs_confirmation=True,
        )
        if best is None or score > best[0]:
            best = (score, result)
    return best[1] if best else None


def _try_ai(
    receipt: dict,
    transactions: list[dict],
    *,
    ai_call: callable | None = None,
) -> MatchResult | None:
    """Strategy 3 — AI-mediated. Send the receipt + top-N closest unmatched
    bank lines to Claude. ``ai_call`` is injectable so tests can stub it."""
    if ai_call is None:
        return None
    rec_total = receipt.get("total_amount") or 0.0
    rec_date = receipt.get("date_iso")

    # Build a candidate list ranked by closeness — pick the top N.
    candidates = sorted(
        (tx for tx in transactions if tx.get("amount") is not None),
        key=lambda tx: (
            abs(abs(tx["amount"]) - abs(rec_total)),
            _days_between(tx.get("date_iso"), rec_date) or 999,
        ),
    )[:AI_TOPN_CANDIDATES]
    if not candidates:
        return None

    try:
        decision = ai_call(receipt, candidates)
    except Exception:  # noqa: BLE001 — AI fallback should never crash matching
        return None
    if not decision:
        return None
    tx_id = decision.get("match_id")
    if not tx_id:
        return None
    return MatchResult(
        strategy="ai",
        transaction_id=int(tx_id),
        confidence=int(decision.get("confidence", 70)),
        reason=decision.get("reason", "AI-suggested match"),
        auto_link=False,
        needs_confirmation=True,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def match_receipt(
    receipt: dict,
    candidate_transactions: list[dict],
    *,
    enable_ai: bool = True,
    ai_call: callable | None = None,
) -> MatchResult:
    """Match a single receipt against a list of (preferably unmatched) bank
    transactions. Returns a MatchResult — strategy='orphan' if nothing
    plausible was found.

    ``receipt`` must have at least: ``total_amount``, ``date_iso``, ``store_name``.
    Each transaction must have: ``id``, ``amount``, ``date_iso``, ``description``.

    The result is pure data — the caller persists the link if desired."""
    if not candidate_transactions:
        return MatchResult(
            strategy="orphan",
            transaction_id=None,
            confidence=0,
            reason="No unmatched bank transactions to consider.",
            auto_link=False,
        )

    result = _try_exact(receipt, candidate_transactions)
    if result:
        return result

    result = _try_strong(receipt, candidate_transactions)
    if result:
        return result

    if enable_ai:
        result = _try_ai(receipt, candidate_transactions, ai_call=ai_call)
        if result:
            return result

    return MatchResult(
        strategy="orphan",
        transaction_id=None,
        confidence=0,
        reason=(
            "No bank transaction within £1 and 5 days of this receipt — "
            "marking as cash/orphan. You can attach it manually."
        ),
        auto_link=False,
    )


def match_batch(
    receipts: Iterable[dict],
    transactions: list[dict],
    *,
    enable_ai: bool = True,
    ai_call: callable | None = None,
) -> list[tuple[dict, MatchResult]]:
    """Match a batch of receipts. Each receipt is matched against the
    remaining unmatched-this-round transactions, so one bank line can't be
    claimed by two receipts in the same batch."""
    consumed: set[int] = set()
    out: list[tuple[dict, MatchResult]] = []
    for receipt in receipts:
        candidates = [
            tx for tx in transactions
            if tx.get("id") not in consumed
        ]
        result = match_receipt(
            receipt, candidates, enable_ai=enable_ai, ai_call=ai_call,
        )
        if result.transaction_id is not None and result.strategy == "exact":
            consumed.add(result.transaction_id)
        out.append((receipt, result))
    return out
