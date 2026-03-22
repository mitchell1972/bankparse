"""
Tests for parsers.ai_parser — AI-powered receipt and statement parsing.
All tests use mocks so no Anthropic API key is needed.
"""

import json
import os
import tempfile

import pytest
from unittest.mock import patch, MagicMock

from parsers.ai_parser import (
    _image_to_base64,
    RECEIPT_PROMPT,
    STATEMENT_PROMPT,
    parse_receipt_ai,
    parse_receipts_bulk,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_ai_response(json_str):
    """Build a mock Anthropic API response whose .content[0].text is *json_str*."""
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text=json_str)]
    mock_resp.usage = MagicMock(input_tokens=100, output_tokens=50)
    return mock_resp


def _make_test_image(path, width=100, height=80, colour=(255, 0, 0)):
    """Create a small solid-colour JPEG via Pillow and save to *path*."""
    from PIL import Image

    img = Image.new("RGB", (width, height), colour)
    img.save(path, format="JPEG")


SAMPLE_RECEIPT_JSON = json.dumps({
    "store_name": "Test Shop",
    "date": "2025-06-01",
    "currency": "GBP",
    "items": [
        {"description": "Widget", "quantity": 2, "unit_price": 3.50, "total_price": 7.00},
        {"description": "Gadget", "quantity": 1, "unit_price": 12.99, "total_price": 12.99},
    ],
    "subtotal": 19.99,
    "tax": 0.00,
    "total": 19.99,
    "payment_method": "card",
})


# ---------------------------------------------------------------------------
# Test _image_to_base64
# ---------------------------------------------------------------------------

class TestImageToBase64:
    """Test that _image_to_base64 converts a test image correctly."""

    def test_returns_base64_and_media_type(self, tmp_path):
        img_path = str(tmp_path / "test.jpg")
        _make_test_image(img_path)

        b64_data, media_type = _image_to_base64(img_path)

        assert isinstance(b64_data, str)
        assert len(b64_data) > 0
        assert media_type == "image/jpeg"

    def test_base64_is_valid(self, tmp_path):
        import base64

        img_path = str(tmp_path / "test.png")
        from PIL import Image
        img = Image.new("RGB", (50, 50), (0, 128, 255))
        img.save(img_path, format="PNG")

        b64_data, _ = _image_to_base64(img_path)

        # Should decode without error
        raw_bytes = base64.standard_b64decode(b64_data)
        assert len(raw_bytes) > 0

    def test_large_image_resized(self, tmp_path):
        """Images wider than max_width should be scaled down."""
        import base64
        from PIL import Image
        import io

        img_path = str(tmp_path / "wide.jpg")
        _make_test_image(img_path, width=3000, height=2000)

        b64_data, _ = _image_to_base64(img_path, max_width=800)

        raw_bytes = base64.standard_b64decode(b64_data)
        result_img = Image.open(io.BytesIO(raw_bytes))
        assert result_img.width == 800

    def test_rgba_image_converted(self, tmp_path):
        """RGBA images should be converted to RGB without error."""
        from PIL import Image

        img_path = str(tmp_path / "rgba.png")
        img = Image.new("RGBA", (50, 50), (255, 0, 0, 128))
        img.save(img_path, format="PNG")

        b64_data, media_type = _image_to_base64(img_path)
        assert media_type == "image/jpeg"
        assert len(b64_data) > 0


# ---------------------------------------------------------------------------
# Test prompt formats
# ---------------------------------------------------------------------------

class TestReceiptPromptFormat:
    """Verify RECEIPT_PROMPT contains required JSON fields."""

    def test_contains_store_name(self):
        assert "store_name" in RECEIPT_PROMPT

    def test_contains_items(self):
        assert "items" in RECEIPT_PROMPT

    def test_contains_total(self):
        assert "total" in RECEIPT_PROMPT

    def test_contains_description(self):
        assert "description" in RECEIPT_PROMPT

    def test_contains_quantity(self):
        assert "quantity" in RECEIPT_PROMPT

    def test_contains_unit_price(self):
        assert "unit_price" in RECEIPT_PROMPT

    def test_contains_total_price(self):
        assert "total_price" in RECEIPT_PROMPT


class TestStatementPromptFormat:
    """Verify STATEMENT_PROMPT contains required JSON fields."""

    def test_contains_transactions(self):
        assert "transactions" in STATEMENT_PROMPT

    def test_contains_date(self):
        assert "date" in STATEMENT_PROMPT

    def test_contains_amount(self):
        assert "amount" in STATEMENT_PROMPT

    def test_contains_description(self):
        assert "description" in STATEMENT_PROMPT

    def test_contains_balance(self):
        assert "balance" in STATEMENT_PROMPT

    def test_contains_type(self):
        assert "type" in STATEMENT_PROMPT

    def test_contains_bank_name(self):
        assert "bank_name" in STATEMENT_PROMPT


# ---------------------------------------------------------------------------
# Test parse_receipt_ai with no API key
# ---------------------------------------------------------------------------

class TestParseReceiptAiNoApiKey:
    """When ANTHROPIC_API_KEY is empty, parse_receipt_ai should raise ValueError."""

    def test_raises_value_error(self, tmp_path):
        img_path = str(tmp_path / "receipt.jpg")
        _make_test_image(img_path)

        with patch("parsers.ai_parser.ANTHROPIC_API_KEY", ""), \
             patch("parsers.ai_parser._client", None):
            # parse_receipt_ai calls _get_client which checks the key
            # The ValueError is raised inside _get_client and caught by
            # parse_receipt_ai's broad except, returning an error result.
            result = parse_receipt_ai(img_path)
            assert result["items"] == []
            assert "error" in result["metadata"]
            assert "ANTHROPIC_API_KEY" in result["metadata"]["error"]


# ---------------------------------------------------------------------------
# Test parse_receipts_bulk with empty list
# ---------------------------------------------------------------------------

class TestParseReceiptsBulkEmptyList:
    """Passing empty list to parse_receipts_bulk should return empty result."""

    def test_empty_list_returns_empty(self):
        result = parse_receipts_bulk([])

        assert result["receipts"] == []
        assert result["combined_items"] == []
        assert result["grand_total"] == 0
        assert result["receipt_count"] == 0
        assert result["total_items"] == 0


# ---------------------------------------------------------------------------
# Test bulk result structure with mocked AI
# ---------------------------------------------------------------------------

class TestBulkResultStructure:
    """Mock the AI response and verify the bulk result has correct structure."""

    def test_result_keys(self, tmp_path):
        img1 = str(tmp_path / "r1.jpg")
        img2 = str(tmp_path / "r2.jpg")
        _make_test_image(img1)
        _make_test_image(img2)

        receipt_json_1 = json.dumps({
            "store_name": "Shop A",
            "date": "2025-01-10",
            "currency": "GBP",
            "items": [
                {"description": "Apple", "quantity": 3, "unit_price": 0.50, "total_price": 1.50},
            ],
            "subtotal": 1.50,
            "tax": 0.00,
            "total": 1.50,
            "payment_method": "card",
        })
        receipt_json_2 = json.dumps({
            "store_name": "Shop B",
            "date": "2025-01-11",
            "currency": "GBP",
            "items": [
                {"description": "Banana", "quantity": 1, "unit_price": 0.30, "total_price": 0.30},
                {"description": "Milk", "quantity": 1, "unit_price": 1.20, "total_price": 1.20},
            ],
            "subtotal": 1.50,
            "tax": 0.00,
            "total": 1.50,
            "payment_method": "cash",
        })

        mock_client = MagicMock()
        # Two calls to messages.create (one per receipt)
        mock_client.messages.create.side_effect = [
            _mock_ai_response(receipt_json_1),
            _mock_ai_response(receipt_json_2),
        ]

        with patch("parsers.ai_parser.ANTHROPIC_API_KEY", "test-key-123"), \
             patch("parsers.ai_parser._client", mock_client):
            result = parse_receipts_bulk([img1, img2])

        # Top-level keys
        assert "receipts" in result
        assert "combined_items" in result
        assert "grand_total" in result
        assert "receipt_count" in result
        assert "total_items" in result

        # Correct counts
        assert result["receipt_count"] == 2
        assert result["total_items"] == 3  # 1 + 2 items
        assert result["grand_total"] == 3.00  # 1.50 + 1.50

        # Receipts list
        assert len(result["receipts"]) == 2
        assert result["receipts"][0]["store_name"] == "Shop A"
        assert result["receipts"][1]["store_name"] == "Shop B"

        # Combined items carry store tag
        stores_in_combined = {item["store"] for item in result["combined_items"]}
        assert "Shop A" in stores_in_combined
        assert "Shop B" in stores_in_combined

    def test_combined_items_have_required_fields(self, tmp_path):
        img = str(tmp_path / "r.jpg")
        _make_test_image(img)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_ai_response(SAMPLE_RECEIPT_JSON)

        with patch("parsers.ai_parser.ANTHROPIC_API_KEY", "test-key-123"), \
             patch("parsers.ai_parser._client", mock_client):
            result = parse_receipts_bulk([img])

        for item in result["combined_items"]:
            assert "store" in item
            assert "date" in item
            assert "description" in item
            assert "quantity" in item
            assert "unit_price" in item
            assert "total_price" in item

    def test_grand_total_is_float(self, tmp_path):
        img = str(tmp_path / "r.jpg")
        _make_test_image(img)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_ai_response(SAMPLE_RECEIPT_JSON)

        with patch("parsers.ai_parser.ANTHROPIC_API_KEY", "test-key-123"), \
             patch("parsers.ai_parser._client", mock_client):
            result = parse_receipts_bulk([img])

        assert isinstance(result["grand_total"], float)
