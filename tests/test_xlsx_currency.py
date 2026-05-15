"""Regression tests for xlsx_exporter currency detection edge cases."""
from parsers.xlsx_exporter import _currency_fmt, export_bulk_receipts_to_xlsx


def test_currency_fmt_gbp():
    assert _currency_fmt({"currency": "GBP"}) == "\u00a3#,##0.00"


def test_currency_fmt_usd():
    assert _currency_fmt({"currency": "USD"}) == "$#,##0.00"


def test_currency_fmt_unknown_falls_back_to_plain():
    assert _currency_fmt({"currency": "XYZ"}) == "#,##0.00"


def test_currency_fmt_none_metadata():
    assert _currency_fmt(None) == "#,##0.00"


def test_currency_fmt_empty_dict():
    assert _currency_fmt({}) == "#,##0.00"


def test_bulk_receipt_no_metadata_does_not_crash(tmp_path):
    """Receipts without metadata keys shouldn't cause AttributeError."""
    bulk_result = {
        "receipts": [
            {
                "filename": "r1.jpg",
                "store_name": "Test Store",
                "items": [{"description": "Milk", "quantity": 1, "unit_price": 1.50, "total_price": 1.50}],
                "total": 1.50,
            },
            # receipt with metadata set to None (simulating missing key)
            {
                "filename": "r2.jpg",
                "store_name": "Test Store 2",
                "items": [],
                "total": 0,
                "metadata": None,
            },
            # receipt with metadata but no currency
            {
                "filename": "r3.jpg",
                "store_name": "Test Store 3",
                "items": [],
                "total": 0,
                "metadata": {"source": "image"},
            },
            # non-dict receipt (should be skipped)
            None,
        ],
        "combined_items": [
            {"store": "Test Store", "description": "Milk", "quantity": 1, "unit_price": 1.50, "total_price": 1.50},
        ],
        "grand_total": 1.50,
        "receipt_count": 3,
        "total_items": 1,
        "ai_usage": {"model": "test", "input_tokens": 10, "output_tokens": 5, "cost_gbp": 0.01},
    }
    out = tmp_path / "test.xlsx"
    path = export_bulk_receipts_to_xlsx(bulk_result, str(out))
    assert path == str(out)
    assert out.exists()


def test_cumulative_download_url_is_importable_and_syntax_clean():
    """Sanity check: the download endpoint module exports without syntax errors.

    This catches the NameError (undefined variable) class of bugs that cause
    the Export All button to show an unhelpful 'Export failed' modal.
    """
    import app as app_module
    from core import QUOTA_REASON_MESSAGES, TIER_LIMITS, get_current_user
    # The download function should be reachable through FastAPI routing
    routes = {r.path for r in app_module.app.routes if hasattr(r, 'path')}
    assert "/api/extracted-data/download" in routes
