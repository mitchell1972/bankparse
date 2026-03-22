"""Tests for CSV bank statement parser."""

import os
import tempfile

import pytest

from parsers.csv_parser import find_column, parse_csv, COLUMN_ALIASES


class TestUtf8BomHandling:
    """Bug fix: UTF-8 BOM bytes should not corrupt the first column name."""

    def test_utf8_bom_handling(self):
        csv_content = "Date,Description,Amount,Balance\n01/03/2025,TESCO STORES,−25.50,1234.50\n02/03/2025,SALARY PAYMENT,2500.00,3734.50\n"
        bom = b"\xef\xbb\xbf"
        raw = bom + csv_content.encode("utf-8")

        with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as f:
            f.write(raw)
            tmp_path = f.name

        try:
            result = parse_csv(tmp_path)
            assert result["summary"]["total_transactions"] >= 1, (
                "Parser should find transactions in a BOM-prefixed CSV"
            )
            # The date column should be detected correctly (not '\ufeffDate')
            assert result["metadata"]["columns_detected"]["date"] == "Date"
        finally:
            os.unlink(tmp_path)


class TestColumnDetectionNoFalsePositives:
    """Bug fix: short aliases 'in' and 'out' should not substring-match
    column names like 'Transaction Information' or 'Outstanding Balance'."""

    def test_transaction_information_not_matched_as_credit(self):
        columns = ["Date", "Transaction Information", "Outstanding Balance", "Amount"]
        credit_col = find_column(columns, COLUMN_ALIASES["credit"])
        assert credit_col is None, (
            f"'Transaction Information' should not match as credit column, got: {credit_col}"
        )

    def test_outstanding_balance_not_matched_as_debit(self):
        columns = ["Date", "Transaction Information", "Outstanding Balance", "Amount"]
        debit_col = find_column(columns, COLUMN_ALIASES["debit"])
        assert debit_col is None, (
            f"'Outstanding Balance' should not match as debit column, got: {debit_col}"
        )

    def test_legitimate_credit_debit_columns_still_match(self):
        columns = ["Date", "Description", "Paid In", "Paid Out", "Balance"]
        credit_col = find_column(columns, COLUMN_ALIASES["credit"])
        debit_col = find_column(columns, COLUMN_ALIASES["debit"])
        assert credit_col == "Paid In"
        assert debit_col == "Paid Out"


class TestBarclaysFormat:
    """Barclays-style CSV: Date, Description, Amount, Balance."""

    def test_barclays_format(self):
        csv_content = (
            "Date,Description,Amount,Balance\n"
            "01/03/2025,DIRECT DEBIT - SKY,-45.00,1200.00\n"
            "02/03/2025,BANK TRANSFER,500.00,1700.00\n"
            "03/03/2025,CARD PAYMENT - AMAZON,-29.99,1670.01\n"
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(csv_content)
            tmp_path = f.name

        try:
            result = parse_csv(tmp_path)
            txns = result["transactions"]
            assert len(txns) == 3
            assert result["metadata"]["columns_detected"]["amount"] == "Amount"
            assert result["metadata"]["columns_detected"]["balance"] == "Balance"

            # Check amounts parsed correctly
            amounts = [t["amount"] for t in txns]
            assert -45.00 in amounts
            assert 500.00 in amounts
            assert -29.99 in amounts
        finally:
            os.unlink(tmp_path)


class TestLloydsFormat:
    """Lloyds-style CSV: Date, Type, Description, Paid In, Paid Out, Balance."""

    def test_lloyds_format(self):
        csv_content = (
            "Date,Type,Description,Paid In,Paid Out,Balance\n"
            "01/03/2025,DD,SKY UK LIMITED,,45.00,1200.00\n"
            "02/03/2025,TFR,BANK TRANSFER,500.00,,1700.00\n"
            "03/03/2025,DEB,AMAZON MARKETPLACE,,29.99,1670.01\n"
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(csv_content)
            tmp_path = f.name

        try:
            result = parse_csv(tmp_path)
            txns = result["transactions"]
            assert len(txns) == 3

            cols = result["metadata"]["columns_detected"]
            assert cols["credit"] == "Paid In"
            assert cols["debit"] == "Paid Out"

            # Debits should be negative, credits positive
            sky_tx = next(t for t in txns if "SKY" in t["description"])
            assert sky_tx["amount"] < 0

            transfer_tx = next(t for t in txns if "TRANSFER" in t["description"])
            assert transfer_tx["amount"] > 0
        finally:
            os.unlink(tmp_path)
