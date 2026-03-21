"""
CSV/TSV Bank Statement Parser
Handles various CSV export formats from UK banks.
Auto-detects delimiters, date formats, and column mappings.
"""

import csv
import io
import re
from datetime import datetime
from typing import Optional

import pandas as pd


# Column name mappings for common UK banks
COLUMN_ALIASES = {
    "date": ["date", "transaction date", "trans date", "posting date", "value date", "posted", "booked"],
    "description": ["description", "details", "narrative", "reference", "particulars", "memo", "payee", "name", "transaction description"],
    "amount": ["amount", "value", "transaction amount"],
    "debit": ["debit", "money out", "withdrawal", "paid out", "dr", "debits", "out"],
    "credit": ["credit", "money in", "deposit", "paid in", "cr", "credits", "in"],
    "balance": ["balance", "running balance", "closing balance", "available balance"],
    "type": ["type", "transaction type", "trans type", "category"],
}


def find_column(columns: list[str], aliases: list[str], exclude: list[str] = None) -> Optional[str]:
    """Find a column name that matches any of the given aliases (exact or partial)."""
    exclude = [e.lower().strip() for e in (exclude or [])]

    # First try exact match
    for col in columns:
        cleaned = col.lower().strip()
        if cleaned in exclude:
            continue
        if cleaned in aliases:
            return col

    # Then try: any alias word appears in the column name
    for col in columns:
        cleaned = col.lower().strip()
        if cleaned in exclude:
            continue
        for alias in aliases:
            # Check if the alias is a substring of the column name
            if alias in cleaned:
                return col

    return None


def parse_date_flexible(date_str: str) -> Optional[str]:
    """Try multiple date formats to parse a date string."""
    if not date_str or not isinstance(date_str, str):
        return None

    date_str = date_str.strip()
    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y",
        "%d %b %Y", "%d %b %y", "%d %B %Y", "%Y/%m/%d",
        "%m/%d/%Y", "%m-%d-%Y",  # US format fallback
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            # Sanity check: year should be reasonable
            if 2000 <= dt.year <= 2030:
                return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def clean_amount(val) -> Optional[float]:
    """Convert various amount representations to float."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    text = str(val).strip().replace("£", "").replace(",", "").replace(" ", "")
    text = text.replace("(", "-").replace(")", "")  # Accountant notation for negatives
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_csv(file_path: str) -> dict:
    """
    Parse a CSV/TSV bank statement file.
    Auto-detects delimiter, encoding, and column mappings.
    """
    # Try to read the file and detect format
    encodings = ["utf-8", "latin-1", "cp1252"]
    df = None

    for encoding in encodings:
        try:
            # Read first few lines to detect delimiter
            with open(file_path, "r", encoding=encoding) as f:
                sample = f.read(4096)

            sniffer = csv.Sniffer()
            try:
                dialect = sniffer.sniff(sample)
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","

            # Check if there's a header row (some banks put metadata at top)
            lines = sample.split("\n")
            skip_rows = 0
            for i, line in enumerate(lines):
                if any(alias in line.lower() for aliases in COLUMN_ALIASES.values() for alias in aliases):
                    skip_rows = i
                    break

            df = pd.read_csv(
                file_path,
                delimiter=delimiter,
                encoding=encoding,
                skiprows=skip_rows,
                skipinitialspace=True,
                on_bad_lines="skip",
            )
            if len(df.columns) >= 2:
                break
        except Exception:
            continue

    if df is None or df.empty:
        return {
            "transactions": [],
            "summary": {"total_transactions": 0, "total_credits": 0, "total_debits": 0, "net": 0},
            "metadata": {"error": "Could not parse CSV file"},
        }

    # Strip whitespace from column names
    df.columns = [str(c).strip() for c in df.columns]

    # Map columns (order matters — match specific columns first, exclude already-matched)
    matched = []
    date_col = find_column(df.columns, COLUMN_ALIASES["date"], exclude=matched)
    if date_col: matched.append(date_col)

    debit_col = find_column(df.columns, COLUMN_ALIASES["debit"], exclude=matched)
    if debit_col: matched.append(debit_col)

    credit_col = find_column(df.columns, COLUMN_ALIASES["credit"], exclude=matched)
    if credit_col: matched.append(credit_col)

    balance_col = find_column(df.columns, COLUMN_ALIASES["balance"], exclude=matched)
    if balance_col: matched.append(balance_col)

    amount_col = find_column(df.columns, COLUMN_ALIASES["amount"], exclude=matched)
    if amount_col: matched.append(amount_col)

    type_col = find_column(df.columns, COLUMN_ALIASES["type"], exclude=matched)
    if type_col: matched.append(type_col)

    desc_col = find_column(df.columns, COLUMN_ALIASES["description"], exclude=matched)
    if desc_col: matched.append(desc_col)

    # If no date column found, try first column
    if date_col is None:
        for col in df.columns:
            sample_vals = df[col].dropna().head(5).astype(str)
            if any(parse_date_flexible(v) for v in sample_vals):
                date_col = col
                break

    if date_col is None:
        date_col = df.columns[0]

    if desc_col is None:
        # Use the column with the longest average string length
        text_cols = [c for c in df.columns if c not in [date_col, amount_col, debit_col, credit_col, balance_col]]
        if text_cols:
            desc_col = max(text_cols, key=lambda c: df[c].astype(str).str.len().mean())
        else:
            desc_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

    # Parse transactions
    transactions = []

    for _, row in df.iterrows():
        date_val = parse_date_flexible(str(row[date_col])) if date_col else None
        if not date_val:
            continue

        description = str(row[desc_col]).strip() if desc_col and pd.notna(row.get(desc_col)) else ""

        debit = clean_amount(row.get(debit_col)) if debit_col else None
        credit = clean_amount(row.get(credit_col)) if credit_col else None
        amount = clean_amount(row.get(amount_col)) if amount_col else None
        balance = clean_amount(row.get(balance_col)) if balance_col else None
        tx_type = str(row.get(type_col, "")).strip() if type_col else ""

        # Determine final amount
        if amount is not None:
            final_amount = amount
        elif debit is not None and credit is not None:
            final_amount = credit - debit if credit else -debit
        elif debit is not None:
            final_amount = -abs(debit)
        elif credit is not None:
            final_amount = abs(credit)
        else:
            continue  # Skip rows with no amount

        tx = {
            "date": date_val,
            "description": description,
            "amount": round(final_amount, 2),
            "debit": round(debit, 2) if debit else None,
            "credit": round(credit, 2) if credit else None,
            "balance": round(balance, 2) if balance else None,
            "type": tx_type or ("credit" if final_amount >= 0 else "debit"),
        }
        transactions.append(tx)

    # Sort by date
    transactions.sort(key=lambda x: x["date"])

    # Summary
    total_credits = sum(t["amount"] for t in transactions if t["amount"] > 0)
    total_debits = sum(t["amount"] for t in transactions if t["amount"] < 0)

    return {
        "transactions": transactions,
        "summary": {
            "total_transactions": len(transactions),
            "total_credits": round(total_credits, 2),
            "total_debits": round(total_debits, 2),
            "net": round(total_credits + total_debits, 2),
        },
        "metadata": {
            "columns_detected": {
                "date": date_col,
                "description": desc_col,
                "amount": amount_col,
                "debit": debit_col,
                "credit": credit_col,
                "balance": balance_col,
            },
            "rows_processed": len(df),
            "rows_parsed": len(transactions),
        },
    }
