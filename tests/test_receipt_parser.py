"""
Tests for parsers.receipt_parser — text-based receipt parsing.
No OCR / pytesseract required; we test parse_receipt_text directly.
"""

import pytest
from parsers.receipt_parser import parse_receipt_text


# ---------------------------------------------------------------------------
# Fixture receipts
# ---------------------------------------------------------------------------

TESCO_RECEIPT = """\
TESCO
Express
123 High Street
London W1A 1AA
Tel: 020 1234 5678

22/03/2025

Hovis Wholemeal 800g          1.35
Lurpak Spreadable 500g       3.65
2x Heinz Beans               1.80
Semi-skimmed Milk 2L          1.15
Chicken Breast 500g           3.99
PG Tips 80s                   3.15

SUBTOTAL                     15.09
TOTAL                        15.09
CARD PAYMENT                 15.09

Clubcard points earned: 15
Thank you for shopping at Tesco
"""

SAINSBURYS_RECEIPT = """\
Sainsbury's
42 Broad Lane, Birmingham B1 2HQ

15.06.2025

Taste the Diff Sourdough      2.00
British Whole Milk 4pt        1.55
Free Range Eggs x6            1.90
TTD Cheddar 350g              3.50
Nectar points this visit: 12

SUB-TOTAL                     8.95
VAT                           0.00
TOTAL                         8.95
Visa Debit                    8.95
Thank you
"""


class TestParseReceiptTextTesco:
    """Parse Tesco receipt text and verify items."""

    def test_items_extracted(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        descriptions = [i["description"] for i in result["items"]]
        assert "Hovis Wholemeal 800g" in descriptions
        assert "Lurpak Spreadable 500g" in descriptions
        assert "Chicken Breast 500g" in descriptions
        assert "PG Tips 80s" in descriptions

    def test_prices(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        by_desc = {i["description"]: i for i in result["items"]}
        assert by_desc["Hovis Wholemeal 800g"]["total_price"] == 1.35
        assert by_desc["Lurpak Spreadable 500g"]["total_price"] == 3.65

    def test_store_name(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        assert result["metadata"]["store_name"] == "TESCO"

    def test_date(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        assert result["metadata"]["date"] == "2025-03-22"

    def test_totals(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        assert result["totals"].get("total") == 15.09


class TestParseReceiptTextSainsburys:
    """Parse Sainsbury's receipt text."""

    def test_items_extracted(self):
        result = parse_receipt_text(SAINSBURYS_RECEIPT)
        descriptions = [i["description"] for i in result["items"]]
        assert "Taste the Diff Sourdough" in descriptions
        assert "British Whole Milk 4pt" in descriptions
        assert "Free Range Eggs x6" in descriptions
        assert "TTD Cheddar 350g" in descriptions

    def test_total(self):
        result = parse_receipt_text(SAINSBURYS_RECEIPT)
        assert result["totals"]["total"] == 8.95

    def test_date(self):
        result = parse_receipt_text(SAINSBURYS_RECEIPT)
        assert result["metadata"]["date"] == "2025-06-15"


class TestQuantityDetection:
    """'2x Heinz Beans' should give qty=2."""

    def test_2x_prefix(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        beans = [i for i in result["items"] if "Heinz Beans" in i["description"]]
        assert len(beans) == 1
        assert beans[0]["quantity"] == 2
        assert beans[0]["total_price"] == 1.80
        assert beans[0]["unit_price"] == 0.90


class TestTotalNotInItems:
    """TOTAL line should not appear in items list."""

    def test_total_excluded(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        descriptions = [i["description"].upper() for i in result["items"]]
        for d in descriptions:
            assert "TOTAL" not in d
            assert "SUBTOTAL" not in d

    def test_card_payment_excluded(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        descriptions = [i["description"].upper() for i in result["items"]]
        for d in descriptions:
            assert "CARD PAYMENT" not in d


class TestEmptyText:
    """Empty string returns empty items list."""

    def test_empty_string(self):
        result = parse_receipt_text("")
        assert result["items"] == []
        assert result["metadata"]["item_count"] == 0

    def test_whitespace_only(self):
        result = parse_receipt_text("   \n\n  \n  ")
        assert result["items"] == []

    def test_date_is_none(self):
        result = parse_receipt_text("")
        assert result["metadata"]["date"] is None


class TestTipNotMisclassified:
    """'PG Tips 80s 3.15' should be an item, not a total."""

    def test_pg_tips_is_item(self):
        result = parse_receipt_text(TESCO_RECEIPT)
        tips_items = [i for i in result["items"] if "PG Tips" in i["description"]]
        assert len(tips_items) == 1
        assert tips_items[0]["total_price"] == 3.15

    def test_pg_tips_not_in_totals(self):
        """PG Tips should not end up classified as a total/summary line."""
        result = parse_receipt_text(TESCO_RECEIPT)
        # If it were misclassified, it might appear under a totals key
        # and be absent from items.
        descriptions = [i["description"] for i in result["items"]]
        assert any("PG Tips" in d for d in descriptions)

    def test_standalone_tip_line(self):
        """A standalone 'PG Tips 80s 3.15' should be an item."""
        text = "Some Store\n\nPG Tips 80s  3.15\n\nTOTAL 3.15\n"
        result = parse_receipt_text(text)
        descriptions = [i["description"] for i in result["items"]]
        assert any("PG Tips" in d for d in descriptions)


class TestDateFallback:
    """If no date is found, metadata date should be None (not today's date)."""

    def test_no_date_returns_none(self):
        text = "SHOP\nApples  1.00\nTOTAL  1.00\n"
        result = parse_receipt_text(text)
        assert result["metadata"]["date"] is None
