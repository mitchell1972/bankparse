"""
Tests for the chat-context formatter currency handling.

Regression test for the bug where the "Ask about your data" panel was
showing UK statements with `$` signs and claiming "The statement is
primarily in USD" — because the formatter hardcoded `$` on every amount
and Claude believed the data over the system-prompt instruction.

The fix: every formatted amount now uses the actual currency symbol from
the statement metadata (`metadata.currency`), and the formatter returns
the currency code so the system prompt can reference it explicitly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Currency symbol lookup
# ---------------------------------------------------------------------------

def test_currency_symbol_known_codes():
    from app import _currency_symbol
    assert _currency_symbol("GBP") == "£"
    assert _currency_symbol("USD") == "$"
    assert _currency_symbol("EUR") == "€"
    assert _currency_symbol("JPY") == "¥"
    assert _currency_symbol("INR") == "₹"


def test_currency_symbol_unknown_code_passes_through_as_label():
    """A currency we haven't mapped (e.g. SEK) should still be visible — fall
    through to the 3-letter label so the AI sees SOMETHING, not nothing."""
    from app import _currency_symbol
    assert _currency_symbol("SEK") == "SEK "
    assert _currency_symbol("NOK") == "NOK "


def test_currency_symbol_defaults_to_gbp_for_blank():
    """The vast majority of users are UK — when metadata.currency is missing
    we assume GBP rather than guessing."""
    from app import _currency_symbol
    assert _currency_symbol("") == "£"
    assert _currency_symbol(None) == "£"


def test_currency_symbol_strips_and_uppers():
    from app import _currency_symbol
    assert _currency_symbol(" gbp ") == "£"
    assert _currency_symbol("eur") == "€"


# ---------------------------------------------------------------------------
# The formatter — the regression we're guarding against
# ---------------------------------------------------------------------------

_GBP_STATEMENT = {
    "summary": {"total_transactions": 2, "total_credits": 0.0, "total_debits": 5.10, "net": -5.10},
    "transactions": [
        {"date": "2025-12-22", "description": "NCP IPSWICH TOWER IPSWICH", "amount": -2.50, "type": "debit"},
        {"date": "2025-12-22", "description": "VIS MIPERMIT LTD CHIPPENHAM", "amount": -2.60, "type": "debit"},
    ],
    "metadata": {"currency": "GBP", "bank_name": "Test Bank"},
}


def test_statement_formatter_uses_pound_sign_for_gbp():
    """The original bug: amounts came out with `$` for GBP statements."""
    from app import _format_chat_context
    text, currency = _format_chat_context("statement", _GBP_STATEMENT)
    assert currency == "GBP"
    # Every amount must use £, not $.
    assert "£-2.50" in text
    assert "£-2.60" in text
    assert "£5.10" in text  # total_debits
    # And NO dollar signs anywhere.
    assert "$" not in text


def test_statement_formatter_announces_currency_in_header():
    """The header explicitly states the currency so a downstream consumer
    (the system prompt + the AI) can't miss it."""
    from app import _format_chat_context
    text, _ = _format_chat_context("statement", _GBP_STATEMENT)
    assert "(GBP)" in text


def test_statement_formatter_uses_dollar_for_usd():
    """A real USD statement keeps the $ — the bug was hardcoding $ even
    when the statement was GBP, not removing $ altogether."""
    from app import _format_chat_context
    usd_payload = {
        "summary": {"total_credits": 100.0, "total_debits": 50.0, "net": 50.0,
                    "total_transactions": 2},
        "transactions": [
            {"date": "2025-12-22", "description": "STARBUCKS NYC", "amount": -5.20, "type": "debit"},
        ],
        "metadata": {"currency": "USD"},
    }
    text, currency = _format_chat_context("statement", usd_payload)
    assert currency == "USD"
    assert "$-5.20" in text
    assert "(USD)" in text
    # £ symbol should NOT appear for a USD statement.
    assert "£" not in text


def test_statement_formatter_eur_statement():
    from app import _format_chat_context
    eur_payload = {
        "summary": {"total_credits": 0, "total_debits": 12.5, "net": -12.5,
                    "total_transactions": 1},
        "transactions": [
            {"date": "2025-12-22", "description": "CARREFOUR PARIS", "amount": -12.50, "type": "debit"},
        ],
        "metadata": {"currency": "EUR"},
    }
    text, currency = _format_chat_context("statement", eur_payload)
    assert currency == "EUR"
    assert "€-12.50" in text


def test_statement_formatter_defaults_to_gbp_when_metadata_missing():
    """Older payloads without metadata.currency should still render in GBP
    rather than crashing or guessing dollars."""
    from app import _format_chat_context
    payload = {
        "summary": {"total_credits": 0, "total_debits": 2.5, "net": -2.5,
                    "total_transactions": 1},
        "transactions": [
            {"date": "2025-12-22", "description": "TEST", "amount": -2.50, "type": "debit"},
        ],
        # No metadata at all.
    }
    text, currency = _format_chat_context("statement", payload)
    assert currency == "GBP"
    assert "£-2.50" in text
    assert "$" not in text


def test_receipt_formatter_uses_currency_from_metadata():
    from app import _format_chat_context
    payload = {
        "metadata": {"store_name": "Tesco", "date": "2025-12-22", "currency": "GBP"},
        "totals": {"total": 14.95, "tax": 2.49},
        "items": [
            {"description": "Milk 2L", "quantity": 1, "unit_price": 2.20, "total_price": 2.20},
        ],
    }
    text, currency = _format_chat_context("receipt", payload)
    assert currency == "GBP"
    assert "£14.95" in text
    assert "£2.49" in text
    assert "£2.20" in text
    assert "$" not in text


def test_bulk_statement_formatter_uses_currency_from_metadata():
    from app import _format_chat_context
    payload = {
        "statement_count": 3,
        "summary": {"total_transactions": 4, "total_credits": 100.0, "total_debits": 50.0, "net": 50.0},
        "all_transactions": [
            {"source": "stmt1.pdf", "date": "2025-12-22", "description": "X", "amount": -5.0, "type": "debit"},
        ],
        "metadata": {"currency": "GBP"},
    }
    text, currency = _format_chat_context("bulk_statement", payload)
    assert currency == "GBP"
    assert "£100.00" in text
    assert "£50.00" in text
    assert "£-5.00" in text
    assert "$" not in text


def test_amount_zero_does_not_crash():
    """Defensive — a missing amount or None shouldn't blow up the formatter."""
    from app import _format_chat_context
    payload = {
        "summary": {"total_credits": None, "total_debits": None, "net": None,
                    "total_transactions": 1},
        "transactions": [
            {"date": "2025-12-22", "description": "TEST", "amount": None, "type": "debit"},
        ],
        "metadata": {"currency": "GBP"},
    }
    text, _ = _format_chat_context("statement", payload)
    assert "£0.00" in text  # None amounts render as 0.00


def test_currency_passes_through_to_system_prompt_caller():
    """The function's contract is (text, currency_code). The chat endpoint
    relies on this so it can put the right currency into the system prompt.
    If this contract changes we want to know."""
    from app import _format_chat_context
    result = _format_chat_context("statement", _GBP_STATEMENT)
    assert isinstance(result, tuple)
    assert len(result) == 2
    text, currency = result
    assert isinstance(text, str)
    assert isinstance(currency, str)
    assert currency == "GBP"
