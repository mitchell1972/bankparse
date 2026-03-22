"""
Test suite for BankParse — tests parsers, XLSX export, database, OTP, and API endpoints.
"""
import os
import csv
import time
import pytest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

# Set test database path before importing app
os.environ["BANKPARSE_DB_PATH"] = "/tmp/bankparse_test.db"

from app import app
from database import (
    get_usage, save_usage, increment_usage,
    store_otp, verify_otp, cleanup_expired_otps,
    track_output_file, get_stale_output_files, remove_output_file_record,
    init_db, get_connection,
)
from otp import generate_otp
from parsers.csv_parser import parse_csv
from parsers.xlsx_exporter import export_to_xlsx, export_receipt_to_xlsx
from parsers.receipt_parser import parse_receipt_text


@pytest.fixture(autouse=True)
def clean_db():
    """Reset the test database before each test."""
    conn = get_connection()
    conn.execute("DELETE FROM sessions")
    conn.execute("DELETE FROM otp_codes")
    conn.execute("DELETE FROM output_files")
    conn.commit()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def create_sample_csv(path: str):
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
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Transaction Date", "Transaction Type", "Transaction Description", "Debit Amount", "Credit Amount", "Balance"])
        writer.writerow(["01/02/2025", "DD", "NETFLIX.COM", "15.99", "", "3200.01"])
        writer.writerow(["02/02/2025", "FPO", "HMRC TAX REFUND", "", "350.00", "3550.01"])
        writer.writerow(["03/02/2025", "CPT", "PRET A MANGER", "7.45", "", "3542.56"])
        writer.writerow(["04/02/2025", "BGC", "SALARY", "", "3100.00", "6642.56"])
        writer.writerow(["05/02/2025", "DD", "SCOTTISH POWER", "89.00", "", "6553.56"])


# --- Parser Tests ---

def test_csv_parser(tmp_path):
    path = str(tmp_path / "barclays.csv")
    create_sample_csv(path)
    result = parse_csv(path)

    assert len(result["transactions"]) == 12
    assert result["summary"]["total_transactions"] == 12

    salary = [t for t in result["transactions"] if "ACME" in t["description"]]
    assert len(salary) == 1
    assert salary[0]["amount"] == 2850.00


def test_csv_parser_lloyds(tmp_path):
    path = str(tmp_path / "lloyds.csv")
    create_sample_csv_lloyds(path)
    result = parse_csv(path)

    assert len(result["transactions"]) == 5
    assert result["summary"]["total_transactions"] == 5


def test_xlsx_export(tmp_path):
    csv_path = str(tmp_path / "barclays.csv")
    create_sample_csv(csv_path)
    data = parse_csv(csv_path)

    xlsx_path = str(tmp_path / "output.xlsx")
    export_to_xlsx(data, xlsx_path)

    assert os.path.getsize(xlsx_path) > 0

    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path)
    assert "Transactions" in wb.sheetnames
    assert "Summary" in wb.sheetnames


def test_full_pipeline(tmp_path):
    csv_path = str(tmp_path / "statement.csv")
    create_sample_csv(csv_path)
    result = parse_csv(csv_path)

    xlsx_path = str(tmp_path / "output.xlsx")
    export_to_xlsx(result, xlsx_path)

    assert os.path.exists(xlsx_path)
    assert os.path.getsize(xlsx_path) > 0
    assert len(result["transactions"]) == 12


def test_receipt_parser():
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

    assert len(result["items"]) == 10
    assert result["metadata"]["store_name"] == "TESCO STORES LTD"
    assert result["metadata"]["date"] == "2025-03-15"
    assert result["totals"].get("total") == 20.38


def test_receipt_parser_sainsburys():
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

    assert len(result["items"]) == 9
    assert result["totals"].get("total") == 18.55
    assert result["metadata"]["date"] == "2025-03-22"


def test_receipt_xlsx_export(tmp_path):
    data = {
        "items": [
            {"description": "Milk", "quantity": 1, "unit_price": 1.55, "total_price": 1.55},
            {"description": "Bread", "quantity": 2, "unit_price": 1.20, "total_price": 2.40},
            {"description": "Eggs", "quantity": 1, "unit_price": 1.95, "total_price": 1.95},
        ],
        "totals": {"subtotal": 5.90, "total": 5.90},
        "metadata": {"store_name": "Test Store", "date": "2025-03-15", "item_count": 3, "currency": "GBP"},
    }

    xlsx_path = str(tmp_path / "receipt.xlsx")
    export_receipt_to_xlsx(data, xlsx_path)

    assert os.path.getsize(xlsx_path) > 0

    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path)
    assert "Receipt Items" in wb.sheetnames
    ws = wb["Receipt Items"]
    assert "Test Store" in str(ws["A1"].value)


# --- Database Tests ---

def test_database_usage_crud():
    usage = get_usage("test_session_1")
    assert usage["statements"] == 0
    assert usage["receipts"] == 0

    usage["statements"] = 3
    usage["email"] = "test@example.com"
    save_usage("test_session_1", usage)

    loaded = get_usage("test_session_1")
    assert loaded["statements"] == 3
    assert loaded["email"] == "test@example.com"


def test_database_increment_usage():
    increment_usage("test_session_2", "statement")
    increment_usage("test_session_2", "statement")
    increment_usage("test_session_2", "receipt")

    usage = get_usage("test_session_2")
    assert usage["statements"] == 2
    assert usage["receipts"] == 1


def test_database_empty_session():
    usage = get_usage("")
    assert usage["statements"] == 0
    assert usage["stripe_customer_id"] is None


# --- OTP Tests ---

def test_otp_generation():
    code = generate_otp()
    assert len(code) == 6
    assert code.isdigit()


def test_otp_store_and_verify():
    store_otp("user@test.com", "123456", "session_abc")
    result = verify_otp("user@test.com", "123456")
    assert result == "session_abc"


def test_otp_single_use():
    store_otp("user2@test.com", "654321", "session_xyz")
    assert verify_otp("user2@test.com", "654321") == "session_xyz"
    assert verify_otp("user2@test.com", "654321") is None


def test_otp_wrong_code():
    store_otp("user3@test.com", "111111", "session_123")
    assert verify_otp("user3@test.com", "999999") is None


def test_otp_invalidates_previous():
    store_otp("user4@test.com", "111111", "session_a")
    store_otp("user4@test.com", "222222", "session_b")
    assert verify_otp("user4@test.com", "111111") is None
    assert verify_otp("user4@test.com", "222222") == "session_b"


# --- Output File Tracking Tests ---

def test_output_file_tracking():
    track_output_file("test_file.xlsx")
    stale = get_stale_output_files(max_age_seconds=0)
    assert "test_file.xlsx" in stale

    remove_output_file_record("test_file.xlsx")
    stale = get_stale_output_files(max_age_seconds=0)
    assert "test_file.xlsx" not in stale


# --- API Tests ---

def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "2.2.0"


def test_home_page(client):
    response = client.get("/")
    assert response.status_code == 200


def test_parse_invalid_file(client):
    response = client.post("/api/parse", files={"file": ("test.exe", b"data", "application/octet-stream")})
    assert response.status_code == 400


def test_usage_endpoint(client):
    response = client.get("/api/usage")
    assert response.status_code == 200
    data = response.json()
    assert "statements_used" in data
    assert "receipts_used" in data
    assert "has_subscription" in data


def test_config_endpoint(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "free_statement_limit" in data
    assert "plans" in data
