"""
PDF Bank Statement Parser
Extracts transaction data from PDF bank statements using pdfplumber.
Handles multiple common UK and US bank statement formats including HSBC,
Chase, Bank of America, Wells Fargo, and Citi.
"""

import re
from datetime import datetime
from typing import Optional
import pdfplumber


# Date formats — UK patterns first, US patterns second.
# The detect_date_format() function auto-detects which locale to use.
UK_DATE_PATTERNS = [
    (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
    (r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
    (r"\d{2}\s\w{3}\s\d{4}", "%d %b %Y"),
    (r"\d{2}\s\w{3}\s\d{2}", "%d %b %y"),
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    (r"\d{2}/\d{2}/\d{2}", "%d/%m/%y"),
]

US_DATE_PATTERNS = [
    (r"\d{2}/\d{2}/\d{4}", "%m/%d/%Y"),
    (r"\d{2}-\d{2}-\d{4}", "%m-%d-%Y"),
    (r"\d{2}/\d{2}/\d{2}", "%m/%d/%y"),
    (r"\d{2}/\d{2}", None),  # MM/DD short form (needs year from context)
    (r"\w{3,9}\s+\d{1,2},?\s+\d{4}", None),  # "January 1, 2024" or "Jul 14, 2008"
    (r"\d{1,2}/\d{1,2}", None),  # M/DD short form
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    (r"\d{2}\s\w{3}\s\d{4}", "%d %b %Y"),
]

# Combined default (UK first for backward compat)
DATE_PATTERNS = UK_DATE_PATTERNS

# Regex for money amounts (UK and US formats — matches £, $, or bare amounts)
MONEY_PATTERN = re.compile(r"[-]?[£$]?\s?[\d,]+\.\d{2}")

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


def parse_date(text: str, locale: str = "uk", context_year: int = None) -> Optional[str]:
    """Try to extract a date from text using common bank statement formats.
    locale: 'uk' or 'us' — determines date parsing order.
    context_year: fallback year for short-form dates like '07/02'.
    """
    text = text.strip()
    patterns = US_DATE_PATTERNS if locale == "us" else UK_DATE_PATTERNS

    for pattern, fmt in patterns:
        match = re.search(pattern, text)
        if match:
            date_str = match.group()
            try:
                if fmt is None:
                    # Handle "January 1, 2024" or "Jul 14, 2008" style
                    for long_fmt in ["%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"]:
                        try:
                            dt = datetime.strptime(date_str.replace(",", ","), long_fmt)
                            if 2000 <= dt.year <= 2040:
                                return dt.strftime("%Y-%m-%d")
                        except ValueError:
                            continue
                    # Handle short MM/DD with context year
                    if context_year and re.match(r"^\d{1,2}/\d{1,2}$", date_str):
                        try:
                            dt = datetime.strptime(f"{date_str}/{context_year}", "%m/%d/%Y")
                            return dt.strftime("%Y-%m-%d")
                        except ValueError:
                            pass
                    continue
                dt = datetime.strptime(date_str, fmt)
                # Sanity check: bank statements should be recent
                if dt.year < 2000 or dt.year > 2040:
                    continue
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def detect_date_format(full_text: str) -> str:
    """Auto-detect whether a statement uses US or UK date format.
    Returns 'us' or 'uk'."""
    lower = full_text.lower()
    # Explicit US bank markers
    us_markers = [
        "chase", "jpmorgan", "bank of america", "wells fargo", "citibank",
        "citi ", "capital one", "us bank", "pnc bank", "td bank",
        "fifth third", "regions bank", "truist", "ally bank",
        "usaa", "navy federal", "baton rouge", "wilmington, de",
        "tampa, fl", "sioux falls", "1-800-", "1-888-", "1-877-",
        "checking summary", "savings summary", "routing number",
    ]
    if any(marker in lower for marker in us_markers):
        return "us"
    # Check for US-style dates like "January 1, 2024" or "07/02"
    if re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}", full_text):
        return "us"
    # Check for dollar signs
    if "$" in full_text and "£" not in full_text:
        return "us"
    return "uk"


def clean_amount(text: str) -> Optional[float]:
    """Convert a money string to a float. Handles parenthetical negatives."""
    text = text.strip().replace("£", "").replace("$", "").replace(",", "").replace(" ", "")
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


# ==========================================================================
# US Bank Statement Parsers
# ==========================================================================

# Skip patterns for US bank statements
US_SKIP_PATTERNS = [
    re.compile(r"(CHECKING|SAVINGS)\s*SUMMARY", re.IGNORECASE),
    re.compile(r"(Beginning|Ending)\s*Balance", re.IGNORECASE),
    re.compile(r"(Deposits|Withdrawals)\s*and\s*(Additions|Subtractions)", re.IGNORECASE),
    re.compile(r"^(Total|Page)\s+\d", re.IGNORECASE),
    re.compile(r"Customer Service|WebSite|www\.", re.IGNORECASE),
    re.compile(r"Account\s*(Number|Summary|number)", re.IGNORECASE),
    re.compile(r"^(INSTANCES|AMOUNT)$", re.IGNORECASE),
    re.compile(r"^Downloaded from", re.IGNORECASE),
    re.compile(r"^(DATE|DESCRIPTION|AMOUNT|BALANCE|DEPOSITS|CHECKS PAID)$", re.IGNORECASE),
    re.compile(r"(Hearing Impaired|Para Espanol|International Calls)", re.IGNORECASE),
    re.compile(r"(overdraft protection|minimum payment|late payment)", re.IGNORECASE),
    re.compile(r"Service Center|Baton Rouge|P\.?\s*O\.?\s*Box", re.IGNORECASE),
    re.compile(r"^\d+\s+of\s+\d+$", re.IGNORECASE),  # "1 of 4"
    re.compile(r"^(PULL|SPEC|CYCLE|DELIVERY|TYPE|IMAGE|BC):", re.IGNORECASE),
    re.compile(r"PULL:.*CYCLE:.*SPEC:", re.IGNORECASE),
    re.compile(r"(Daily Balance|DAILY ENDING BALANCE|Average Balance|Interest\s*Charged|Interest\s*Rate)", re.IGNORECASE),
    re.compile(r"SERVICE (CHARGE|FEE) (SUMMARY|CALCULATION)", re.IGNORECASE),
    re.compile(r"(NUMBER OF|TRANSACTIONS FOR|Transaction Total|Net Service Fee|Total Service Fee|Excessive Transaction)", re.IGNORECASE),
    re.compile(r"(Checks Paid.*Debits|Deposits.*Credits|Deposited Items)\s+\d", re.IGNORECASE),
    re.compile(r"(This Page Intentionally Left Blank)", re.IGNORECASE),
    re.compile(r"^(CHECK NUMBER|PAID AMOUNT)", re.IGNORECASE),
    re.compile(r"(If you see a description|not the original|not able to return)", re.IGNORECASE),
    re.compile(r"(An image of this check|may be available)", re.IGNORECASE),
    re.compile(r"(Manage your account|Mobile:|Download the)", re.IGNORECASE),
    re.compile(r"(REWARDS SUMMARY|Previous points|Points earned|Points available)", re.IGNORECASE),
    re.compile(r"(Payment Due Date|Credit Limit|Available Credit|Cash Access)", re.IGNORECASE),
    re.compile(r"(YOUR ACCOUNT MESSAGES|ACCOUNT SUMMARY)", re.IGNORECASE),
    re.compile(r"(Previous Balance|New Balance|Past Due Amount|Balance over)", re.IGNORECASE),
    re.compile(r"(Fees Charged|Interest Charged|Cash Advances|Balance Transfers)", re.IGNORECASE),
    re.compile(r"(Opening/Closing Date|Payment, Credits|Purchases \+)", re.IGNORECASE),
]


def is_us_statement(full_text: str) -> bool:
    """Detect if the text is from a US bank statement."""
    return detect_date_format(full_text) == "us"


def _extract_context_year(full_text: str) -> Optional[int]:
    """Extract a year from statement header text for context."""
    # Look for "July 1, 2008 through July 31, 2008" or "January 2019"
    m = re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{0,2},?\s*(\d{4})", full_text)
    if m:
        return int(m.group(2))
    # Look for year like "2016" or "2019" near the top
    m = re.search(r"\b(20\d{2})\b", full_text[:500])
    if m:
        return int(m.group(1))
    return None


def extract_us_transactions(all_text: str) -> list[dict]:
    """Parse US bank statement text (Chase, BofA, JPMorgan, Wells Fargo, etc.)."""
    transactions = []
    lines = all_text.split("\n")
    context_year = _extract_context_year(all_text)

    # Detect if this is a Chase credit card (has different format)
    is_credit_card = bool(re.search(r"(credit limit|previous balance|new balance.*\$)", all_text, re.IGNORECASE))

    # Track which section we're in (deposits, withdrawals, checks, etc.)
    current_section = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Skip header/metadata lines
        if any(p.search(line) for p in US_SKIP_PATTERNS):
            continue

        # Detect section headers — must NOT be followed by amounts (to avoid matching summary lines)
        # e.g., "DEPOSITS AND ADDITIONS" is a header, but "Deposits and Additions 10 125,883.63" is a summary
        section_match = re.match(
            r"^(DEPOSITS AND ADDITIONS|CHECKS PAID|OTHER WITHDRAWALS[\w\s,&]*|"
            r"ELECTRONIC WITHDRAWALS|ATM WITHDRAWALS|"
            r"Deposits and other additions|Withdrawals and other subtractions|"
            r"Service fees|TRANSACTION DETAIL|"
            r"PURCHASE|PAYMENT AND OTHER CREDITS|FEES|"
            r"DAILY ENDING BALANCE|SERVICE CHARGE SUMMARY|SERVICE FEE CALCULATION)\s*$",
            line, re.IGNORECASE
        )
        if section_match:
            section_text = section_match.group(1).lower()
            if any(kw in section_text for kw in ["daily", "service charge", "service fee", "balance summary"]):
                current_section = "skip"
            elif any(kw in section_text for kw in ["deposit", "addition", "credit"]):
                current_section = "credit"
            else:
                current_section = "debit"
            continue

        # Skip lines in non-transaction sections
        if current_section == "skip":
            continue

        # Skip "Total" summary lines
        if re.match(r"^Total\s+(Deposits|Withdrawals|Checks|Service)", line, re.IGNORECASE):
            continue

        # Skip daily balance lines: "07/02 $98,727.40 07/21 129,173.36"
        if re.match(r"^\d{1,2}/\d{1,2}\s+\$?[\d,]+\.\d{2}\s+\d{1,2}/\d{1,2}\s+[\d,]+\.\d{2}", line):
            continue
        # Skip single-line balance entries after section header
        if re.match(r"^\d{1,2}/\d{1,2}\s+[\d,]+\.\d{2}$", line):
            continue

        # Try to parse transaction lines
        # Format 1: "MM/DD Description $Amount" or "MM/DD Description Amount"
        tx_match = re.match(
            r"^(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(.+?)\s+\$?([\d,]+\.\d{2})\s*$",
            line
        )
        if tx_match:
            date_str = tx_match.group(1)
            desc = tx_match.group(2).strip()
            amount_str = tx_match.group(3)

            date_val = parse_date(date_str, locale="us", context_year=context_year)
            amount = clean_amount(amount_str)
            if date_val and amount is not None:
                # Determine direction from section or description
                is_debit = current_section == "debit"
                if not current_section:
                    # Guess from description keywords
                    desc_lower = desc.lower()
                    is_debit = any(kw in desc_lower for kw in [
                        "withdrawal", "withdrwl", "payment", "purchase",
                        "check", "fee", "debit", "atm",
                    ])

                final_amount = -abs(amount) if is_debit else abs(amount)
                tx = {
                    "date": date_val,
                    "description": desc,
                    "amount": final_amount,
                    "debit": abs(amount) if is_debit else None,
                    "credit": abs(amount) if not is_debit else None,
                    "balance": None,
                    "type": "debit" if is_debit else "credit",
                }
                transactions.append(tx)
                continue

        # Format 2: BofA-style "CHECKCARD MMDD Description STATE Amount"
        bofa_match = re.match(
            r"^(CHECKCARD|CHECK CARD|ACH|WIRE|ATM|PREAUTHORIZED)\s+(\d{4})\s+(.+?)\s+([\d,]+\.\d{2})\s*$",
            line, re.IGNORECASE
        )
        if bofa_match and context_year:
            tx_type = bofa_match.group(1)
            mmdd = bofa_match.group(2)
            desc = bofa_match.group(3).strip()
            amount_str = bofa_match.group(4)

            month = mmdd[:2]
            day = mmdd[2:]
            date_val = parse_date(f"{month}/{day}", locale="us", context_year=context_year)
            amount = clean_amount(amount_str)
            if date_val and amount is not None:
                is_debit = current_section == "debit" or tx_type.upper() in ("CHECKCARD", "CHECK CARD", "ATM", "WIRE")
                final_amount = -abs(amount) if is_debit else abs(amount)
                tx = {
                    "date": date_val,
                    "description": f"{tx_type}: {desc}",
                    "amount": final_amount,
                    "debit": abs(amount) if is_debit else None,
                    "credit": abs(amount) if not is_debit else None,
                    "balance": None,
                    "type": "debit" if is_debit else "credit",
                }
                transactions.append(tx)
                continue

        # Format 3: BofA detailed — lines starting with date description then amount on next area
        bofa_detail = re.match(
            r"^(\d{2}/\d{2}/\d{2,4})\s+(.+?)\s+(-?[\d,]+\.\d{2})\s*$",
            line
        )
        if bofa_detail:
            date_str = bofa_detail.group(1)
            desc = bofa_detail.group(2).strip()
            amount_str = bofa_detail.group(3)

            date_val = parse_date(date_str, locale="us", context_year=context_year)
            amount = clean_amount(amount_str)
            if date_val and amount is not None:
                is_debit = current_section == "debit" or amount < 0
                if not current_section and amount > 0:
                    desc_lower = desc.lower()
                    is_debit = any(kw in desc_lower for kw in [
                        "withdrawal", "withdrwl", "payment", "purchase",
                        "check", "fee", "debit", "atm", "checkcard",
                    ])
                final_amount = -abs(amount) if is_debit else abs(amount)
                tx = {
                    "date": date_val,
                    "description": desc,
                    "amount": final_amount,
                    "debit": abs(amount) if is_debit else None,
                    "credit": abs(amount) if not is_debit else None,
                    "balance": None,
                    "type": "debit" if is_debit else "credit",
                }
                transactions.append(tx)
                continue

        # Format 4: Check format "XXXX ^ MM/DD $Amount"
        check_match = re.match(
            r"^(XXXX|\d{4})\s*\^?\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+\$?([\d,]+\.\d{2})\s*$",
            line
        )
        if check_match and context_year:
            check_num = check_match.group(1)
            date_str = check_match.group(2)
            amount_str = check_match.group(3)

            date_val = parse_date(date_str, locale="us", context_year=context_year)
            amount = clean_amount(amount_str)
            if date_val and amount is not None:
                tx = {
                    "date": date_val,
                    "description": f"Check #{check_num}",
                    "amount": -abs(amount),
                    "debit": abs(amount),
                    "credit": None,
                    "balance": None,
                    "type": "debit",
                }
                transactions.append(tx)
                continue

        # Format 5: Simple "Date Description $Amount" with dollar sign
        dollar_match = re.match(
            r"^(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(.+?)\s+\$([\d,]+\.\d{2})\s*$",
            line
        )
        if dollar_match:
            date_str = dollar_match.group(1)
            desc = dollar_match.group(2).strip()
            amount_str = dollar_match.group(3)

            date_val = parse_date(date_str, locale="us", context_year=context_year)
            amount = clean_amount(amount_str)
            if date_val and amount is not None:
                is_debit = current_section == "debit"
                final_amount = -abs(amount) if is_debit else abs(amount)
                tx = {
                    "date": date_val,
                    "description": desc,
                    "amount": final_amount,
                    "debit": abs(amount) if is_debit else None,
                    "credit": abs(amount) if not is_debit else None,
                    "balance": None,
                    "type": "debit" if is_debit else "credit",
                }
                transactions.append(tx)
                continue

    return transactions


def extract_transactions_from_table(table: list[list], locale: str = "uk") -> list[dict]:
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
            if cell and parse_date(str(cell), locale=locale):
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

        date_val = parse_date(str(row[date_col]), locale=locale) if date_col < len(row) and row[date_col] else None
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


def extract_transactions_from_text(text: str, locale: str = "uk") -> list[dict]:
    """Fallback: extract transactions from raw text when tables aren't detected."""
    transactions = []
    lines = text.split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        date_val = parse_date(line, locale=locale)
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
    Auto-detects UK vs US format.
    """
    all_transactions = []
    metadata = {
        "pages": 0,
        "method": "unknown",
        "locale": "unknown",
    }

    with pdfplumber.open(file_path) as pdf:
        metadata["pages"] = len(pdf.pages)

        # Collect all text first to detect format
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

        # Auto-detect locale
        locale = detect_date_format(full_text)
        metadata["locale"] = locale

        # Check for HSBC format (UK)
        if is_hsbc_statement(full_text):
            metadata["method"] = "hsbc_text"
            all_transactions = extract_hsbc_transactions(full_text)

        # Check for US bank format
        elif is_us_statement(full_text):
            metadata["method"] = "us_text"
            all_transactions = extract_us_transactions(full_text)

            # If US text parser didn't find much, try table extraction
            if len(all_transactions) < 3:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    if tables:
                        for table in tables:
                            txs = extract_transactions_from_table(table, locale="us")
                            if txs:
                                all_transactions = txs
                                metadata["method"] = "us_table"
                                break
                    if metadata["method"] == "us_table":
                        break
        else:
            # UK/generic: try table extraction first (most reliable)
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    metadata["method"] = "table"
                    for table in tables:
                        txs = extract_transactions_from_table(table, locale=locale)
                        all_transactions.extend(txs)

            # Fallback to text extraction
            if not all_transactions and full_text:
                metadata["method"] = "text"
                all_transactions = extract_transactions_from_text(full_text, locale=locale)

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
