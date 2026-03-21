"""
Test suite for BankParse — tests parsers and XLSX export with sample data.
"""
import os
import csv
import json
import tempfile
from pathlib import Path

# Test CSV parser
from parsers.csv_parser import parse_csv
from parsers.xlsx_exporter import export_to_xlsx, export_receipt_to_xlsx
from parsers.receipt_parser import parse_receipt_text


def create_sample_csv(path: str):
    """Create a sample bank statement CSV (Barclays-style)."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Description", "Money Out", "Money In", "Balance"])
        writer.writerow(["15/01/2025", "TESCO STORES 3217", "45.67", "", "1954.33"])
        writer.writerow(["15/01/2025", "SALARY - ACME LTD", "", "2850.00", "4804.33"])
        writer.writerow(["16/01/2025", "DIRECT DEBIT - VODAFONE", "32.00", "", "4772.33"])
        writer.writerow(["17/01/2025", "TRANSFER - J SMITH", "", "150.00", "4922.33"])
        writer.writerow(["18/01/2025", "AMAZON UK MARKETPLACE", "89.99", "", "4832.34"])
        writer.writerow(["19/01/2025", "STANDING ORDER - RENT", "750.00", "", "4082.34"])
        writer.writerow(["20/01/2025", "CONTACTLESS - COSTA COFFEE", "4.50", "", "4077.84"])
        writer.writerow(["21/01/2025", "FASTER PAYMENT - FREELANCE", "", "500.00", "4577.84"])
        writer.writerow(["22/01/2025", "DIRECT DEBIT - COUNCIL TAX", "156.00", "", "4421.84"])
        writer.writerow(["23/01/2025", "CARD PAYMENT - SAINSBURYS", "62.30", "", "4359.54"])
        writer.writerow(["24/01/2025", "INTEREST PAID", "", "1.23", "4360.77"])
        writer.writerow(["25/01/2025", "ATM WITHDRAWAL - HSBC", "200.00", "", "4160.77"])


def create_sample_csv_lloyds(path: str):
    """Create a sample Lloyds-format CSV."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Transaction Date", "Transaction Type", "Transaction Description", "Debit Amount", "Credit Amount", "Balance"])
        writer.writerow(["01/02/2025", "DD", "NETFLIX.COM", "15.99", "", "3200.01"])
        writer.writerow(["02/02/2025", "FPO", "HMRC TAX REFUND", "", "350.00", "3550.01"])
        writer.writerow(["03/02/2025", "CPT", "PRET A MANGER", "7.45", "", "3542.56"])
        writer.writerow(["04/02/2025", "BGC", "SALARY", "", "3100.00", "6642.56"])
        writer.writerow(["05/02/2025", "DD", "SCOTTISH POWER", "89.00", "", "6553.56"])


def test_csv_parser():
    """Test CSV parsing with sample Barclays-style statement."""
    print("=" * 60)
    print("TEST 1: CSV Parser (Barclays format)")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        path = f.name
    create_sample_csv(path)

    result = parse_csv(path)
    os.unlink(path)

    assert len(result["transactions"]) == 12, f"Expected 12 transactions, got {len(result['transactions'])}"
    assert result["summary"]["total_transactions"] == 12

    print(f"  Transactions found: {result['summary']['total_transactions']}")
    print(f"  Total credits: £{result['summary']['total_credits']:.2f}")
    print(f"  Total debits: £{result['summary']['total_debits']:.2f}")
    print(f"  Net: £{result['summary']['net']:.2f}")
    print(f"  Columns detected: {result['metadata']['columns_detected']}")

    # Check a specific transaction
    salary = [t for t in result["transactions"] if "ACME" in t["description"]]
    assert len(salary) == 1, "Should find salary transaction"
    assert salary[0]["amount"] == 2850.00, f"Salary should be 2850, got {salary[0]['amount']}"

    print("  ✓ All assertions passed")
    return result


def test_csv_parser_lloyds():
    """Test CSV parsing with Lloyds-style statement."""
    print("\n" + "=" * 60)
    print("TEST 2: CSV Parser (Lloyds format)")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        path = f.name
    create_sample_csv_lloyds(path)

    result = parse_csv(path)
    os.unlink(path)

    assert len(result["transactions"]) == 5, f"Expected 5 transactions, got {len(result['transactions'])}"
    print(f"  Transactions found: {result['summary']['total_transactions']}")
    print(f"  Total credits: £{result['summary']['total_credits']:.2f}")
    print(f"  Total debits: £{result['summary']['total_debits']:.2f}")
    print("  ✓ All assertions passed")
    return result


def test_xlsx_export(data: dict):
    """Test XLSX export."""
    print("\n" + "=" * 60)
    print("TEST 3: XLSX Export")
    print("=" * 60)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        path = f.name

    export_to_xlsx(data, path)

    file_size = os.path.getsize(path)
    assert file_size > 0, "XLSX file should not be empty"
    print(f"  File created: {path}")
    print(f"  File size: {file_size:,} bytes")

    # Verify it can be read back
    import openpyxl
    wb = openpyxl.load_workbook(path)
    assert "Transactions" in wb.sheetnames
    assert "Summary" in wb.sheetnames
    print(f"  Sheets: {wb.sheetnames}")

    ws = wb["Transactions"]
    print(f"  Rows in Transactions sheet: {ws.max_row}")
    print("  ✓ All assertions passed")

    os.unlink(path)


def test_full_pipeline():
    """Test the full pipeline: CSV → parse → XLSX."""
    print("\n" + "=" * 60)
    print("TEST 4: Full Pipeline (CSV → Parse → XLSX)")
    print("=" * 60)

    # Create CSV
    csv_path = "/tmp/test_statement.csv"
    create_sample_csv(csv_path)

    # Parse
    result = parse_csv(csv_path)
    os.unlink(csv_path)

    # Export
    xlsx_path = "/tmp/test_output.xlsx"
    export_to_xlsx(result, xlsx_path)

    assert os.path.exists(xlsx_path)
    print(f"  Pipeline complete: 12 transactions → {os.path.getsize(xlsx_path):,} byte XLSX")
    print("  ✓ Full pipeline working")

    os.unlink(xlsx_path)


def test_receipt_parser():
    """Test receipt text parsing with a sample Tesco receipt."""
    print("\n" + "=" * 60)
    print("TEST 5: Receipt Parser (Tesco-style)")
    print("=" * 60)

    sample_receipt = """
TESCO STORES LTD
Express Manchester
14 Deansgate M3 2BA
Tel: 0345 6779012

15/03/2025  14:32

Whole Milk 2L          1.55
Hovis Bread 800g       1.35
Chicken Breast 500g    3.80
Basmati Rice 1kg       1.89
2x Heinz Beans 415g    2.00
Lurpak Butter 250g     2.25
6x Eggs Free Range     1.95
Red Onions 3pk         0.79
Fairy Liquid 500ml     1.65
PG Tips 80s            3.15

SUBTOTAL              20.38
VAT                    0.00
TOTAL                 20.38

CARD PAYMENT          20.38
Visa Debit ****7742

Clubcard Points: 20
THANK YOU FOR SHOPPING AT TESCO
"""

    result = parse_receipt_text(sample_receipt)

    assert len(result["items"]) == 10, f"Expected 10 items, got {len(result['items'])}"
    assert result["metadata"]["store_name"] == "TESCO STORES LTD"
    assert result["metadata"]["date"] == "2025-03-15"
    assert result["totals"].get("total") == 20.38

    print(f"  Store: {result['metadata']['store_name']}")
    print(f"  Date: {result['metadata']['date']}")
    print(f"  Items found: {len(result['items'])}")
    for item in result["items"]:
        print(f"    - {item['description']}: £{item['total_price']:.2f} (qty: {item['quantity']})")
    print(f"  Subtotal: £{result['totals'].get('subtotal', 0):.2f}")
    print(f"  Total: £{result['totals'].get('total', 0):.2f}")
    print("  ✓ All assertions passed")
    return result


def test_receipt_parser_sainsburys():
    """Test receipt parsing with a Sainsbury's-style receipt."""
    print("\n" + "=" * 60)
    print("TEST 6: Receipt Parser (Sainsbury's-style)")
    print("=" * 60)

    sample_receipt = """
Sainsbury's
Local Wandsworth
22/03/2025

Bananas Loose          0.65
Semi Skimmed Milk 1L   1.10
Warburtons Toastie     1.30
Cathedral Cheddar      3.00
Chicken Thighs 1kg     4.50
Broccoli               0.75
Cherry Tomatoes 250g   1.25
Muller Corner x4       2.50
Persil Liquid 1.5L     5.00

Subtotal              20.05
Savings               -1.50
TOTAL                 18.55

Paid by Card          18.55
Nectar Points: 18
"""

    result = parse_receipt_text(sample_receipt)

    assert len(result["items"]) == 9, f"Expected 9 items, got {len(result['items'])}"
    assert result["totals"].get("total") == 18.55
    assert result["metadata"]["date"] == "2025-03-22"

    print(f"  Store: {result['metadata']['store_name']}")
    print(f"  Items found: {len(result['items'])}")
    print(f"  Total: £{result['totals'].get('total', 0):.2f}")
    print("  ✓ All assertions passed")
    return result


def test_receipt_xlsx_export():
    """Test XLSX export of receipt data."""
    print("\n" + "=" * 60)
    print("TEST 7: Receipt XLSX Export")
    print("=" * 60)

    data = {
        "items": [
            {"description": "Milk", "quantity": 1, "unit_price": 1.55, "total_price": 1.55},
            {"description": "Bread", "quantity": 2, "unit_price": 1.20, "total_price": 2.40},
            {"description": "Eggs", "quantity": 1, "unit_price": 1.95, "total_price": 1.95},
        ],
        "totals": {"subtotal": 5.90, "total": 5.90},
        "metadata": {"store_name": "Test Store", "date": "2025-03-15", "item_count": 3, "currency": "GBP"},
    }

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        path = f.name

    export_receipt_to_xlsx(data, path)

    file_size = os.path.getsize(path)
    assert file_size > 0, "XLSX file should not be empty"

    import openpyxl
    wb = openpyxl.load_workbook(path)
    assert "Receipt Items" in wb.sheetnames
    ws = wb["Receipt Items"]

    # Check store name in title
    assert "Test Store" in str(ws["A1"].value)

    print(f"  File size: {file_size:,} bytes")
    print(f"  Sheets: {wb.sheetnames}")
    print(f"  Rows: {ws.max_row}")
    print("  ✓ All assertions passed")

    os.unlink(path)


if __name__ == "__main__":
    result1 = test_csv_parser()
    result2 = test_csv_parser_lloyds()
    test_xlsx_export(result1)
    test_full_pipeline()
    test_receipt_parser()
    test_receipt_parser_sainsburys()
    test_receipt_xlsx_export()

    print("\n" + "=" * 60)
    print("ALL 7 TESTS PASSED ✓")
    print("=" * 60)
