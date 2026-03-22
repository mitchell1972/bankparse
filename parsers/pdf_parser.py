"""
PDF Bank Statement Parser
Extracts transaction data from PDF bank statements using pdfplumber.
Handles multiple common UK bank statement formats including HSBC.
"""

import re
from datetime import datetime
from typing import Optional
import pdfplumber


# Common date formats found in UK bank statements
DATE_PATTERNS = [
    (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
    (r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
    (r"\d{2}\s\w{3}\s\d{4}", "%d %b %Y"),
    (r"\d{2}\s\w{3}\s\d{2}", "%d %b %y"),
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    (r"\d{2}/\d{2}/\d{2}", "%d/%m/%y"),
]

# Regex for money amounts (UK format)
MONEY_PATTERN = re.compile(r"[-]?\£?\s?[\d,]+\.\d{2}")

# HSBC payment type codes
HSBC_PAYMENT_TYPES = {
    "DD": "Direct Debit",
    "CR": "Credit",
    "BP": "Bill Payment",
    "OBP": "Online Bill Payment",
    "VIS": "Visa",
    "ATM": "ATM Withdrawal",
    "DR": "Debit",
    ")))": "Contactless",
    "SO": "Standing Order",
    "CHQ": "Cheque",
    "TFR": "Transfer",
    "FPO": "Faster Payment Out",
    "FPI": "Faster Payment In",
    "BGC": "Bank Giro Credit",
    "CPT": "Card Payment",
    "DEB": "Debit Card",
}

# Lines to skip in HSBC statements
HSBC_SKIP_PATTERNS = [
    re.compile(r"BALANCE\s*(BROUGHT|CARRIED)\s*FORWARD", re.IGNORECASE),
    re.compile(r"^(Date|Payment type|£\s*Paid|£\s*Balance)", re.IGNORECASE),
    re.compile(r"Your HSBC", re.IGNORECASE),
    re.compile(r"Account (Name|Summary)", re.IGNORECASE),
    re.compile(r"(Opening|Closing)\s*Balance", re.IGNORECASE),
    re.compile(r"Payments\s*(In|Out)", re.IGNORECASE),
    re.compile(r"Arranged\s*Overdraft", re.IGNORECASE),
    re.compile(r"(Sortcode|Sheet Number|Account Number)", re.IGNORECASE),
    re.compile(r"International Bank", re.IGNORECASE),
    re.compile(r"Bank Identifier", re.IGNORECASE),
    re.compile(r"Customer Service", re.IGNORECASE),
    re.compile(r"(Contact tel|Text phone|www\.hsbc|www\.FSCS)", re.IGNORECASE),
    re.compile(r"see reverse", re.IGNORECASE),
    re.compile(r"(deaf or speech|impaired)", re.IGNORECASE),
    re.compile(r"^\d+\s+\w+\s+to\s+\d+\s+\w+\s+\d{4}", re.IGNORECASE),
    re.compile(r"^(Mr|Mrs|Ms|Miss)\s+\w+", re.IGNORECASE),
    re.compile(r"Your Statement", re.IGNORECASE),
    re.compile(r"Information about th?e?", re.IGNORECASE),
    re.compile(r"Financial Services", re.IGNORECASE),
    re.compile(r"Account Fee", re.IGNORECASE),
    re.compile(r"Fee for maintaining", re.IGNORECASE),
    re.compile(r"charge frequency", re.IGNORECASE),
    re.compile(r"Credit\s*Interest\s*Rate", re.IGNORECASE),
    re.compile(r"Overdraft\s*Interest\s*Rate", re.IGNORECASE),
    re.compile(r"Credit\s*interest\s+\d", re.IGNORECASE),
    re.compile(r"^\d+\s*Monthly$", re.IGNORECASE),
    re.compile(r"^\d+\s+\w+\s+(Lane|Road|Street|Avenue|Drive|Close)", re.IGNORECASE),
    re.compile(r"^[A-Z]{1,2}\d{1,2}\s?\d[A-Z]{2}$", re.IGNORECASE),
    re.compile(r"^(GB|HBUK)\w+$", re.IGNORECASE),
    re.compile(r"^\.$"),
    re.compile(r"^A$"),
    re.compile(r"^(Ipswich|London|Manchester|Birmingham|Leeds)$", re.IGNORECASE),
]


def parse_date(text: str) -> Optional[str]:
    """Try to extract a date from text using common bank statement formats."""
    text = text.strip()
    for pattern, fmt in DATE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            try:
                dt = datetime.strptime(match.group(), fmt)
                # Sanity check: bank statements should be recent
                if dt.year < 2000 or dt.year > 2040:
                    continue
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def clean_amount(text: str) -> Optional[float]:
    """Convert a money string to a float. Handles parenthetical negatives."""
    text = text.strip().replace("£", "").replace(",", "").replace(" ", "")
    # Handle parenthetical negatives: (100.00) → -100.00
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def is_hsbc_statement(full_text: str) -> bool:
    """Detect if the text is from an HSBC bank statement."""
    lower = full_text.lower()
    return "hsbc" in lower and ("paid out" in lower or "paid in" in lower)


def extract_hsbc_transactions(all_text: str) -> list[dict]:
    """
    Parse HSBC bank statement text, handling multi-line transactions
    and redacted/missing amounts.
    """
    transactions = []
    lines = all_text.split("\n")

    current_date = None
    current_type = None
    current_desc_parts = []
    current_amounts = []
    current_balance = None
    last_known_balance = None

    def flush_transaction():
        """Save the current transaction if valid."""
        nonlocal current_date, current_type, current_desc_parts, current_amounts
        nonlocal current_balance, last_known_balance
        if current_date and current_desc_parts:
            desc = " ".join(current_desc_parts).strip()
            # Skip empty or very short descriptions
            if len(desc) < 2:
                current_desc_parts = []
                current_amounts = []
                current_balance = None
                return

            # Try to determine amount
            debit = None
            credit = None
            amount = 0.0
            balance = current_balance

            if current_amounts:
                # Use the first amount found as the transaction amount
                amount = current_amounts[0]

            # If we have a balance, update the running balance
            if balance is not None:
                last_known_balance = balance

            # Determine type from payment code
            tx_type = current_type or ""
            type_label = HSBC_PAYMENT_TYPES.get(tx_type, tx_type)

            # CR = credit (money in), most others = debit (money out)
            if tx_type == "CR":
                credit = abs(amount) if amount else None
                amount = abs(amount) if amount else 0.0
                direction = "credit"
            else:
                debit = abs(amount) if amount else None
                amount = -abs(amount) if amount else 0.0
                direction = "debit"

            # Build description with type prefix
            full_desc = f"{type_label}: {desc}" if type_label and type_label != desc else desc

            tx = {
                "date": current_date,
                "description": full_desc,
                "amount": amount,
                "debit": debit,
                "credit": credit,
                "balance": balance,
                "type": direction,
            }
            transactions.append(tx)

        current_desc_parts = []
        current_amounts = []
        current_balance = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Stop parsing at terms & conditions / info / rates sections
        if re.search(
            r"^(Interest and Charges|Business Banking Customers|Personal Banking Customers|"
            r"Credit Interest Rates|Overdraft Interest Rates|AER\s)",
            line,
        ):
            flush_transaction()
            break

        # Skip header/footer/metadata lines
        if any(p.search(line) for p in HSBC_SKIP_PATTERNS):
            continue

        # Check if line starts with a date (new transaction group)
        date_match = re.match(r"^(\d{2}\s\w{3}\s\d{2})\s+(.*)", line)
        if date_match:
            # Flush previous transaction
            flush_transaction()

            current_date = parse_date(date_match.group(1))
            raw_remainder = date_match.group(2).strip()
            line_had_d = bool(re.search(r"\s+D$", raw_remainder))
            remainder = re.sub(r"\s+D$", "", raw_remainder)

            # Check if remainder starts with a payment type code
            type_match = re.match(
                r"^(DD|CR|BP|OBP|VIS|ATM|DR|\)\)\)|SO|CHQ|TFR|FPO|FPI|BGC|CPT|DEB)\s+(.*)",
                remainder,
            )
            if type_match:
                current_type = type_match.group(1)
                desc_part = type_match.group(2).strip()
            else:
                current_type = None
                desc_part = remainder

            # Extract any amounts from the line
            amounts = MONEY_PATTERN.findall(desc_part)
            parsed_amounts = []
            for a in amounts:
                val = clean_amount(a)
                if val is not None:
                    parsed_amounts.append(val)
                desc_part = desc_part.replace(a, "").strip()

            # If line ended with D and has 2+ amounts, last is balance
            if line_had_d and len(parsed_amounts) >= 2:
                current_amounts = parsed_amounts[:-1]
                current_balance = parsed_amounts[-1]
            elif line_had_d and len(parsed_amounts) == 1 and not desc_part.strip():
                # Standalone balance line (e.g., date + balance only)
                current_balance = parsed_amounts[0]
            else:
                current_amounts = parsed_amounts

            if desc_part:
                current_desc_parts = [desc_part]
            continue

        # Check if line starts with a payment type code (same date, new transaction)
        type_match = re.match(
            r"^(DD|CR|BP|OBP|VIS|ATM|DR|\)\)\)|SO|CHQ|TFR|FPO|FPI|BGC|CPT|DEB)\s+(.*)",
            line,
        )
        if type_match and current_date:
            # Flush previous transaction
            flush_transaction()

            current_type = type_match.group(1)
            raw_desc = type_match.group(2).strip()
            line_had_d = bool(re.search(r"\s+D$", raw_desc))
            desc_part = re.sub(r"\s+D$", "", raw_desc)

            # Extract amounts
            amounts = MONEY_PATTERN.findall(desc_part)
            parsed_amounts = []
            for a in amounts:
                val = clean_amount(a)
                if val is not None:
                    parsed_amounts.append(val)
                desc_part = desc_part.replace(a, "").strip()

            if line_had_d and len(parsed_amounts) >= 2:
                current_amounts = parsed_amounts[:-1]
                current_balance = parsed_amounts[-1]
            else:
                current_amounts = parsed_amounts

            if desc_part:
                current_desc_parts = [desc_part]
            continue

        # Continuation line (part of current transaction description)
        if current_date and current_desc_parts is not None:
            # Detect trailing "D" (overdrawn indicator) before stripping
            line_had_d = bool(re.search(r"\s+D$", line))
            stripped_line = re.sub(r"\s+D$", "", line)

            # Check for amounts on this line
            amounts = MONEY_PATTERN.findall(stripped_line)
            clean_line = stripped_line
            parsed_amounts = []
            for a in amounts:
                val = clean_amount(a)
                if val is not None:
                    parsed_amounts.append(val)
                clean_line = clean_line.replace(a, "").strip()

            # If line ended with D and has 2+ amounts, last is balance
            if line_had_d and len(parsed_amounts) >= 2:
                current_amounts.extend(parsed_amounts[:-1])
                current_balance = parsed_amounts[-1]
            elif not line_had_d and len(parsed_amounts) == 1 and re.match(r"^\d[\d,.]+$", stripped_line.strip()):
                # Standalone balance number (no D, no text)
                pass  # Skip standalone balance numbers without context
            else:
                current_amounts.extend(parsed_amounts)

            # Skip lines that are just card numbers
            if re.match(r"^\d{6}\*+\d{4}$", clean_line):
                continue
            # Skip lines that are just reference numbers
            if re.match(r"^PB\d{4}\*+\d{5}$", clean_line):
                continue
            # Skip "NO REF" standalone
            if clean_line.upper() == "NO REF":
                continue
            # Skip "FIRST PAYMENT" type annotations
            if clean_line.upper() in ("FIRST PAYMENT", "TRANSFER", "GIFT"):
                # Add as context to description
                current_desc_parts.append(f"({clean_line})")
                continue
            # Skip lines that are just "Visa Rate" or foreign currency info
            if re.match(r"^(Visa Rate|USD\s|Non-Sterling|Transaction Fee)$", clean_line, re.IGNORECASE):
                continue

            # Skip leaked header/footer text
            if re.search(
                r"Your Statement|Information about|Financial Services|Account Fee|"
                r"or call your|telephone banking|compensation|FSCS|"
                r"eligible for protection|www\.hsbc|hsbc\.co\.uk|"
                r"Lost and Stolen|Dispute Resolution|Accessibility|"
                r"HSBC UK Bank plc|Prudential Regulation|"
                r"Business Banking|Personal Banking|"
                r"upto \d+|over \d+|balance variable|"
                r"Monthly$|charge$|frequency$",
                clean_line, re.IGNORECASE
            ):
                continue

            # Skip date period text that leaks across page boundaries
            if re.search(r"\d+\s+(December|January|February|March|April|May|June|July|August|September|October|November)\s+\d{4}\s+to", clean_line, re.IGNORECASE):
                continue

            # Skip standalone "WWW.NATIONAL-" or similar URL fragments after amount extraction
            if re.match(r"^WWW\.\w+-?$", clean_line, re.IGNORECASE):
                continue

            if clean_line and len(clean_line) > 1:
                current_desc_parts.append(clean_line)

    # Flush last transaction
    flush_transaction()

    return transactions


def extract_transactions_from_table(table: list[list]) -> list[dict]:
    """Extract transactions from a pdfplumber table."""
    transactions = []

    if not table or len(table) < 2:
        return transactions

    # Try to identify columns from the header row
    header = [str(cell).lower().strip() if cell else "" for cell in table[0]]

    date_col = None
    desc_col = None
    debit_col = None
    credit_col = None
    amount_col = None
    balance_col = None

    for i, h in enumerate(header):
        if any(kw in h for kw in ["date", "posted", "trans"]):
            date_col = i
        elif any(kw in h for kw in ["description", "details", "particulars", "narrative", "reference"]):
            desc_col = i
        elif any(kw in h for kw in ["debit", "money out", "withdrawal", "paid out"]):
            debit_col = i
        elif any(kw in h for kw in ["credit", "money in", "deposit", "paid in"]):
            credit_col = i
        elif any(kw in h for kw in ["amount", "value"]):
            amount_col = i
        elif any(kw in h for kw in ["balance", "running"]):
            balance_col = i

    # If no header detected, try positional guessing
    if date_col is None:
        # Assume first column with dates is date, next is description
        for i, cell in enumerate(table[1] if len(table) > 1 else []):
            if cell and parse_date(str(cell)):
                date_col = i
                break

    if date_col is None:
        date_col = 0
    if desc_col is None:
        desc_col = 1 if len(header) > 1 else 0

    # Process data rows (skip header)
    for row in table[1:]:
        if not row or all(cell is None or str(cell).strip() == "" for cell in row):
            continue

        date_val = parse_date(str(row[date_col])) if date_col < len(row) and row[date_col] else None
        if not date_val:
            continue  # Skip rows without a valid date

        description = str(row[desc_col]).strip() if desc_col < len(row) and row[desc_col] else ""

        debit = None
        credit = None
        amount = None
        balance = None

        if debit_col is not None and debit_col < len(row) and row[debit_col]:
            debit = clean_amount(str(row[debit_col]))
        if credit_col is not None and credit_col < len(row) and row[credit_col]:
            credit = clean_amount(str(row[credit_col]))
        if amount_col is not None and amount_col < len(row) and row[amount_col]:
            amount = clean_amount(str(row[amount_col]))
        if balance_col is not None and balance_col < len(row) and row[balance_col]:
            balance = clean_amount(str(row[balance_col]))

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
            # Try to find any money amount in the row
            for cell in row:
                if cell:
                    amt = clean_amount(str(cell))
                    if amt is not None:
                        final_amount = amt
                        break
            else:
                final_amount = 0.0

        tx = {
            "date": date_val,
            "description": description,
            "amount": final_amount,
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "type": "credit" if final_amount >= 0 else "debit",
        }
        transactions.append(tx)

    return transactions


def extract_transactions_from_text(text: str) -> list[dict]:
    """Fallback: extract transactions from raw text when tables aren't detected."""
    transactions = []
    lines = text.split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        date_val = parse_date(line)
        if not date_val:
            continue

        amounts = MONEY_PATTERN.findall(line)
        if not amounts:
            continue

        # Remove date and amounts from line to get description
        description = line
        for pattern, _ in DATE_PATTERNS:
            description = re.sub(pattern, "", description)
        for amt in amounts:
            description = description.replace(amt, "")
        description = re.sub(r"\s+", " ", description).strip()

        amount_val = clean_amount(amounts[0])
        balance_val = clean_amount(amounts[-1]) if len(amounts) > 1 else None

        if amount_val is not None:
            tx = {
                "date": date_val,
                "description": description,
                "amount": amount_val,
                "debit": abs(amount_val) if amount_val < 0 else None,
                "credit": amount_val if amount_val >= 0 else None,
                "balance": balance_val,
                "type": "credit" if amount_val >= 0 else "debit",
            }
            transactions.append(tx)

    return transactions


def parse_pdf(file_path: str) -> dict:
    """
    Main entry point: parse a PDF bank statement and return structured data.
    Returns dict with 'transactions' list, 'summary', and 'metadata'.
    """
    all_transactions = []
    metadata = {
        "pages": 0,
        "method": "unknown",
    }

    with pdfplumber.open(file_path) as pdf:
        metadata["pages"] = len(pdf.pages)

        # Collect all text first to detect format
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

        # Check for HSBC format
        if is_hsbc_statement(full_text):
            metadata["method"] = "hsbc_text"
            all_transactions = extract_hsbc_transactions(full_text)
        else:
            # Try table extraction first (most reliable)
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    metadata["method"] = "table"
                    for table in tables:
                        txs = extract_transactions_from_table(table)
                        all_transactions.extend(txs)

            # Fallback to text extraction
            if not all_transactions and full_text:
                metadata["method"] = "text"
                all_transactions = extract_transactions_from_text(full_text)

    # Sort by date
    all_transactions.sort(key=lambda x: x["date"])

    # Build summary
    total_credits = sum(t["amount"] for t in all_transactions if t["amount"] > 0)
    total_debits = sum(t["amount"] for t in all_transactions if t["amount"] < 0)

    summary = {
        "total_transactions": len(all_transactions),
        "total_credits": round(total_credits, 2),
        "total_debits": round(total_debits, 2),
        "net": round(total_credits + total_debits, 2),
    }

    return {
        "transactions": all_transactions,
        "summary": summary,
        "metadata": metadata,
    }
