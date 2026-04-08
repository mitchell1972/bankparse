"""
BankScan AI — AI-powered document parser using Claude Haiku 4.5 vision.
Every document (statement or receipt) is sent to Claude; each call records
exact token usage so callers can bill the user the real Anthropic cost.

Typical cost at Haiku 4.5 pricing ($1/MTok in, $5/MTok out):
    receipt      ~0.2p GBP
    statement    ~0.7p GBP per page
"""

import os
import io
import json
import base64
import logging
from pathlib import Path
from typing import Optional

import ai_pricing

logger = logging.getLogger("bankparse.ai_parser")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "claude-haiku-4-5-20251001")

# Lazy import
_client = None


def _get_client():
    """Lazily initialise and return the Anthropic client."""
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY environment variable is required for AI parsing")
        import anthropic
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _image_to_base64(file_path: str, max_width: int = 1500) -> tuple[str, str]:
    """Convert any image (including HEIC) to base64 JPEG for the API.
    Returns (base64_data, media_type)."""
    from PIL import Image

    file_lower = file_path.lower()
    if file_lower.endswith((".heic", ".heif")):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise ImportError("HEIC support requires pillow-heif: pip install pillow-heif")

    img = Image.open(file_path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Resize if too large (saves tokens and speeds up API call)
    w, h = img.size
    if w > max_width:
        scale = max_width / w
        img = img.resize((max_width, int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"


def _pdf_pages_to_base64(file_path: str, max_pages: int = 20) -> list[tuple[str, str]]:
    """Convert PDF pages to base64 images for the API.
    Returns list of (base64_data, media_type) tuples."""
    import pdfplumber
    from PIL import Image

    pages = []
    with pdfplumber.open(file_path) as pdf:
        for i, page in enumerate(pdf.pages[:max_pages]):
            # Convert PDF page to image
            img = page.to_image(resolution=200).original
            if img.mode != "RGB":
                img = img.convert("RGB")
            # Resize
            w, h = img.size
            if w > 1500:
                scale = 1500 / w
                img = img.resize((1500, int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            pages.append((base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"))
    return pages


def _pdf_pages_to_text(file_path: str, max_pages: int = 20) -> list[str]:
    """Extract text from PDF pages using pdfplumber.

    Returns a list of per-page strings (empty strings for pages with no
    extractable text). Text-based PDFs (most modern UK bank statements)
    extract cleanly and let us skip vision entirely, which is both
    cheaper and dramatically more accurate on dense multi-column layouts.
    """
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages[:max_pages]:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                # A single page that fails to extract shouldn't kill the
                # whole parse; record it as empty so the caller can decide
                # whether to fall back to vision.
                pages.append("")
    return pages


RECEIPT_PROMPT = """You are a universal receipt / invoice parser. Extract EVERY line item as JSON. Works for receipts and invoices from ANY country in ANY currency: UK (Tesco, Sainsbury's, M&S, Waitrose, Boots, pubs, cafes, JD Wetherspoon...), USA (Walmart, Target, Costco, CVS, Starbucks, restaurants...), Europe (Carrefour, Lidl, Aldi, IKEA, Zara...), Asia (7-Eleven, FamilyMart, local markets...), and anything else — hotels, gas stations, ride-shares, takeout apps, freelance invoices, utility bills, etc.

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "store_name": "Store / restaurant / merchant / supplier name",
  "date": "YYYY-MM-DD or null if not visible",
  "currency": "3-letter ISO code (GBP, USD, EUR, CAD, AUD, JPY, INR, ...) inferred from the receipt",
  "items": [
    {"description": "Item name", "quantity": 1, "unit_price": 0.00, "total_price": 0.00}
  ],
  "subtotal": 0.00,
  "tax": 0.00,
  "total": 0.00,
  "payment_method": "card/cash/null"
}

Rules:
- CURRENCY: Determine from symbols / codes on the receipt (£, $, €, ¥, ₹, "GBP", "USD", "EUR", ...). Do NOT default to GBP. Do NOT include the symbol in any amount field; amounts are plain numbers in that currency.
- DATE: Output YYYY-MM-DD. Parse any format: "22 Dec 2025", "12/22/2025" (US), "22/12/2025" (UK/EU), "2025-12-22", "Dec 22 '25". For ambiguous all-numeric dates use the store country to decide MM/DD vs DD/MM. If the date is not visible at all, use null.
- ITEMS: Include ALL items, even extras, add-ons, substitutions, and discounts shown as line items. If the receipt uses a loyalty / discount / negative line (e.g. "-2.00 OFF"), include it as a line item with a negative total_price.
- QUANTITY: If quantity is shown (e.g. "2x", "x3", "QTY 2"), set quantity accordingly. Default to 1.
- TAX: Sum up all tax / VAT / GST / sales-tax lines into the single "tax" field. If no tax is shown, use 0.00.
- PAYMENT METHOD: Set to "card", "cash", "contactless", "mobile", "online" etc. if visible on the receipt, else null.
- Prices must be numbers (never strings), rounded to 2 decimals.
- If you can't read a value clearly, give your best estimate rather than skipping the line."""


RECEIPT_PROMPT_STRICT = """You MUST return ONLY valid JSON. No markdown, no backticks, no explanation.

Extract ALL items from this receipt / invoice (any country, any currency). Determine the currency from the receipt itself — do NOT default to GBP. Return this exact JSON structure:
{"store_name":"...","date":"YYYY-MM-DD or null","currency":"GBP|USD|EUR|...","items":[{"description":"...","quantity":1,"unit_price":0.00,"total_price":0.00}],"subtotal":0.00,"tax":0.00,"total":0.00,"payment_method":"card/cash/null"}"""


STATEMENT_PROMPT = """You are a universal bank statement parser. Extract EVERY transaction from this bank statement page image as JSON. Works for statements from ANY bank in ANY country (HSBC, Chase, BNP Paribas, CBA, RBC, N26, HDFC, Revolut, ...) — adapt to whatever layout is shown. Be thorough.

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "bank_name": "Bank name",
  "account_holder": "Name on statement",
  "statement_period": "Date range or null",
  "currency": "3-letter ISO code (GBP, USD, EUR, ...) inferred from the statement",
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "description": "Transaction description",
      "amount": -10.00,
      "balance": null,
      "type": "debit"
    }
  ]
}

Rules:
- DATE CARRYING: Many layouts show the date only on the first transaction of each day; subsequent rows have a blank date cell. Carry the most recent date forward until a new one appears.
- MULTI-LINE DESCRIPTIONS: A single transaction often spans 2-7 lines (merchant name, reference, international currency conversion + non-sterling fee, etc.). Merge them into ONE clean description, and combine any separate "FX fee" / "non-sterling fee" line into the parent transaction's amount.
- AMOUNTS: Negative for money OUT (debits / withdrawals / purchases / fees), positive for money IN (credits / deposits / payments received). type="debit" or "credit" correspondingly.
- Include ALL transactions, even small ones and those with partially obscured amounts.
- Dates MUST be YYYY-MM-DD. Parse any format ("22 Dec 25", "12/22/2025", "22/12/2025", "2025-12-22") using the statement period + bank country to disambiguate MM/DD vs DD/MM.
- CURRENCY: Determine from the account header (£, $, €, "USD", "GBP", ...) — do not default to GBP.
- Balance may be null if not shown for that row. "D" / "DR" / trailing "-" on a balance means overdrawn (negative).
- SKIP header / footer / summary rows: "Opening Balance", "Closing Balance", "Balance Brought Forward", "Balance Carried Forward", "Previous Balance", "New Balance", "Total Payments In", "Total Payments Out", "Account Summary", column header rows, contact info, page numbers, FSCS / FDIC / legal disclosures."""


STATEMENT_PROMPT_STRICT = """You MUST return ONLY valid JSON. No markdown, no backticks, no explanation.

Extract ALL transactions from this bank statement page (any bank, any country). Carry dates forward across blank rows. Merge multi-line descriptions. Skip opening/closing/brought-forward balance rows and header / footer text. Negative for money out, positive for money in. Dates YYYY-MM-DD. Determine currency from the statement.

Return this exact JSON structure:
{"bank_name":"...","account_holder":"...","statement_period":"...","currency":"GBP","transactions":[{"date":"YYYY-MM-DD","description":"...","amount":-10.00,"balance":null,"type":"debit"}]}"""


STATEMENT_TEXT_PROMPT = """You are a universal bank statement parser. Below is the TEXT extracted from every page of a bank statement PDF. Extract EVERY transaction as JSON.

You must handle statements from ANY bank in ANY country: UK (HSBC, Barclays, Lloyds, NatWest, Santander, Monzo, Starling, Nationwide, Halifax, Revolut, Wise...), USA (Chase, Bank of America, Wells Fargo, Citi, Capital One, US Bank, PNC, Ally, Discover, American Express, Charles Schwab...), Canada (RBC, TD, Scotiabank, BMO, CIBC...), Europe (Deutsche Bank, BNP Paribas, ING, N26, UniCredit, Santander, BBVA...), Australia (CBA, Westpac, ANZ, NAB...), India (HDFC, ICICI, SBI, Axis...), and any other regional or online bank. Do not assume a specific format — adapt to whatever is in the text.

CRITICAL RULES that apply to every statement format:

1. DATE CARRYING: Many statements only show the date on the FIRST transaction of a given day; subsequent same-day transactions have a blank date column. Carry the most recent date forward until a new date appears. Example:
     22 Dec 25 DD TV LICENCE       14.95
               CR PAYROLL        1559.88   <- also 22 Dec 25
               VIS TESCO           20.24   <- also 22 Dec 25
     23 Dec 25 DD ELECTRIC         40.00   <- new date starts here

2. MULTI-LINE DESCRIPTIONS: A single transaction often spans 2-7 text lines. Merge them into ONE clean description (single spaces, no line breaks). Common cases across all banks:
   - A merchant name wrapped to a second line
   - International / foreign-currency transactions that include the original amount, exchange rate, visa/mastercard rate, and a separate "non-sterling" / "foreign transaction" / "FX" fee. Combine them into ONE transaction whose amount is the TOTAL in the account currency (including the fee). Example:
       VIS INT'L 0014799223
       OPENROUTER, INC
       OPENROUTER.AI
       USD 15.83 @ 1.3358
       Visa Rate       11.85
       DR Non-Sterling
       Transaction Fee  0.32
     becomes ONE transaction: description "VIS INT'L OPENROUTER, INC OPENROUTER.AI USD 15.83 @ 1.3358 Non-Sterling Fee", amount -(11.85 + 0.32) = -12.17
   - Bill-pay / ACH / standing-order transactions where the payee name, reference, and amount are on separate lines
   - Check/cheque transactions that show the check number on one line and the amount on the next

3. DEBIT vs CREDIT (money OUT vs money IN): Figure this out from the column layout in front of you:
   - Columns labelled "Paid out" / "Debit" / "Withdrawals" / "Money Out" / "Debits" / "Charges" / "-" = NEGATIVE amount, type="debit"
   - Columns labelled "Paid in" / "Credit" / "Deposits" / "Money In" / "Credits" / "+" = POSITIVE amount, type="credit"
   - Single-column layouts: money out is usually shown as a positive number in the "Amount" column with a "DR" / "-" marker (or just "Withdrawal" in the type), and money in with "CR" / "+" / "Deposit". Output negative for money out regardless.
   - Credit-card statements invert this: purchases are money you owe (negative amount), payments received are money in (positive amount).

4. BALANCE COLUMN: Many layouts only show the running balance at the end of each day (not per transaction). Set balance=null unless that specific row clearly shows one. Markers like "D", "DR", or a trailing minus sign on the balance mean overdrawn / negative balance.

5. SKIP NON-TRANSACTION LINES. Do NOT include these as transactions:
   - "BALANCE BROUGHT FORWARD" / "BALANCE CARRIED FORWARD" / "Opening Balance" / "Closing Balance" / "Previous Balance" / "New Balance" / "Beginning Balance" / "Ending Balance"
   - Column headers ("Date", "Description", "Amount", "Debit", "Credit", "Balance", "Payment type and details", "Withdrawals", "Deposits", "Running Balance")
   - Bank header / footer text (contact phone numbers, addresses, "Customer Service Centre", website URLs, "Your Statement", "Page X of Y")
   - Sheet numbers, IBAN, BIC, SWIFT, sort code, account number, routing number lines in the header
   - Account summary blocks ("Account Summary", "Total Payments In", "Total Payments Out", "Total Withdrawals", "Total Deposits", "Finance Charges", "Minimum Payment Due", "Statement Balance")
   - Marketing / legal / rate-disclosure sections (FSCS, FDIC, APR/AER tables, "Information about...", "Important information")

6. TRANSACTION TYPE CODES (keep them as part of the description, don't strip them):
   UK: DD (Direct Debit), CR (Credit), BP (Bill Payment), VIS (Visa), DR (Debit), OBP (Online Bill Payment), ))) (Contactless), SO (Standing Order), FPI/FPO (Faster Payment), CHQ (Cheque), ATM
   USA: ACH, POS, CHECK, WIRE, DEBIT, CREDIT, EFT, ATM, PURCHASE, DEPOSIT, WITHDRAWAL, FEE, INT (Interest)
   Generic: INT'L (International), FX, PMT, TRANSFER, REFUND

7. DATE FORMAT: Output ALWAYS in YYYY-MM-DD (ISO 8601). Parse whatever format is in the source, using the statement period header for disambiguation:
   - "22 Dec 25" / "22 DEC 2025" / "Dec 22, 2025" / "12/22/2025" (US) / "22/12/2025" (UK, EU) / "2025-12-22" (ISO) all -> "2025-12-22"
   - If only day + month are shown (e.g. "22 Dec"), infer the year from the statement period
   - For ambiguous all-numeric dates like "03/04/2025", use the statement period and the bank's country to decide MM/DD vs DD/MM. A US bank (Chase, BofA, Wells Fargo, etc.) is almost always MM/DD. A UK / European / Australian bank is almost always DD/MM.

8. CURRENCY: Determine the account currency from the statement header (£, $, €, etc. or an explicit "USD"/"GBP"/"EUR" label). Amounts should be plain numbers in that currency — do NOT include currency symbols in the amount field, and do NOT convert to a different currency. If the statement is multi-currency, use the primary account currency.

Return ONLY valid JSON, no markdown, no explanation, no preamble:
{
  "bank_name": "Bank name (e.g. HSBC, Chase, BNP Paribas)",
  "account_holder": "Full name on statement",
  "statement_period": "Date range in natural language",
  "currency": "3-letter ISO code (GBP, USD, EUR, CAD, AUD, INR, ...) inferred from the statement",
  "transactions": [
    {"date": "YYYY-MM-DD", "description": "Merged clean description", "amount": -10.00, "balance": null, "type": "debit"}
  ]
}

Be THOROUGH. A typical monthly statement has 30-150 transactions — extract every single one. Do not stop early, do not summarise, do not skip small amounts."""


STATEMENT_TEXT_PROMPT_STRICT = """You MUST return ONLY valid JSON. No markdown, no backticks, no explanation, no preamble.

Extract EVERY transaction from this bank statement text (any bank, any country). Carry the date forward to rows that do not show a date. Merge multi-line descriptions into a single clean string. Skip header/footer/summary rows and "BALANCE BROUGHT FORWARD" / "CARRIED FORWARD" / opening/closing balance rows. Negative amount for money OUT, positive for money IN. Dates MUST be YYYY-MM-DD.

Return this exact JSON structure:
{"bank_name":"...","account_holder":"...","statement_period":"...","currency":"GBP","transactions":[{"date":"YYYY-MM-DD","description":"...","amount":-10.00,"balance":null,"type":"debit"}]}"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_json_response(text: str) -> str:
    """Strip markdown fences and surrounding whitespace from an API response
    so that ``json.loads`` can parse it."""
    text = text.strip()
    # Remove ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        # Drop the opening fence (optionally followed by a language tag)
        first_newline = text.index("\n") if "\n" in text else 3
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _call_vision(images: list[tuple[str, str]], prompt: str, max_tokens: int = 8192) -> tuple[str, dict]:
    """Send one or more images to Claude Haiku and return the text response
    along with usage metadata.

    Args:
        images: list of (base64_data, media_type) tuples
        prompt: the user prompt to send
        max_tokens: output token cap. Default 8192 is enough for a dense
            single-page statement (~100 transactions). Raise for receipts
            only if they genuinely have hundreds of line items.

    Returns:
        Tuple of (assistant_text, usage_dict). usage_dict has keys:
            - model: the model ID used
            - input_tokens: int
            - output_tokens: int
    """
    content: list[dict] = []
    for b64_data, media_type in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64_data,
            },
        })
    content.append({"type": "text", "text": prompt})

    client = _get_client()
    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
    )

    usage = {
        "model": AI_MODEL,
        "input_tokens": int(getattr(response.usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(response.usage, "output_tokens", 0) or 0),
    }
    return response.content[0].text, usage


def _call_text(text: str, prompt: str, max_tokens: int = 16384) -> tuple[str, dict]:
    """Send a text-only request to Claude Haiku and return the response.

    Used for statements where pdfplumber extracts clean text — this is
    much cheaper than vision (no image tokens) and way more accurate on
    dense multi-column layouts. max_tokens defaults to 16384 so a single
    call can emit a full monthly statement (100+ transactions) in one JSON
    object without truncation.
    """
    full_prompt = f"{prompt}\n\n--- BANK STATEMENT TEXT ---\n{text}\n--- END ---"

    client = _get_client()
    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=max_tokens,
        messages=[{
            "role": "user",
            "content": [{"type": "text", "text": full_prompt}],
        }],
    )

    usage = {
        "model": AI_MODEL,
        "input_tokens": int(getattr(response.usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(response.usage, "output_tokens", 0) or 0),
    }
    return response.content[0].text, usage


def _parse_json_response(raw: str, strict_prompt: str, images: list[tuple[str, str]]) -> tuple[dict, dict]:
    """Attempt to parse a JSON response.  If it fails, retry once with a
    stricter prompt.

    Returns a tuple of (parsed_dict_or_error, extra_usage_dict).
    extra_usage_dict contains any *additional* token usage incurred by
    the retry (zero if no retry happened). Keys: input_tokens, output_tokens.
    """
    cleaned = _clean_json_response(raw)
    empty_usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        return json.loads(cleaned), empty_usage
    except json.JSONDecodeError as first_err:
        logger.warning("First JSON parse failed (%s), retrying with strict prompt", first_err)

    # Retry with stricter prompt — this call costs additional tokens
    try:
        raw_retry, retry_usage = _call_vision(images, strict_prompt)
        cleaned_retry = _clean_json_response(raw_retry)
        return json.loads(cleaned_retry), {
            "input_tokens": retry_usage.get("input_tokens", 0),
            "output_tokens": retry_usage.get("output_tokens", 0),
        }
    except json.JSONDecodeError as retry_err:
        logger.error("Strict-prompt retry also failed: %s", retry_err)
        return {"error": f"AI returned non-JSON after two attempts: {retry_err}"}, empty_usage
    except Exception as retry_err:
        logger.error("Strict-prompt retry raised: %s", retry_err)
        return {"error": f"AI retry raised exception: {retry_err}"}, empty_usage


def _parse_json_response_text(raw: str, strict_prompt: str, text: str) -> tuple[dict, dict]:
    """Same as _parse_json_response but for text-only calls (no images).
    Retries once with a stricter prompt on JSON decode failure."""
    cleaned = _clean_json_response(raw)
    empty_usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        return json.loads(cleaned), empty_usage
    except json.JSONDecodeError as first_err:
        logger.warning("First JSON parse (text) failed (%s), retrying with strict prompt", first_err)

    try:
        raw_retry, retry_usage = _call_text(text, strict_prompt)
        cleaned_retry = _clean_json_response(raw_retry)
        return json.loads(cleaned_retry), {
            "input_tokens": retry_usage.get("input_tokens", 0),
            "output_tokens": retry_usage.get("output_tokens", 0),
        }
    except json.JSONDecodeError as retry_err:
        logger.error("Text strict-prompt retry also failed: %s", retry_err)
        return {"error": f"AI returned non-JSON after two attempts: {retry_err}"}, empty_usage
    except Exception as retry_err:
        logger.error("Text strict-prompt retry raised: %s", retry_err)
        return {"error": f"AI retry raised exception: {retry_err}"}, empty_usage


# ---------------------------------------------------------------------------
# Public API — receipt parsing
# ---------------------------------------------------------------------------

def parse_receipt_ai(file_path: str) -> dict:
    """Parse a receipt image or PDF using Claude Haiku vision.

    Accepts any image format supported by Pillow (including HEIC) or a
    single-page PDF.  Returns a dict compatible with the existing
    ``receipt_parser`` output format, plus a nested ``ai_usage`` dict
    inside ``metadata`` for billing::

        {
            "items": [{"description", "quantity", "unit_price", "total_price"}, ...],
            "totals": {"subtotal", "tax", "total", ...},
            "metadata": {
                "store_name", "date", "item_count", "currency", "source", "method",
                "ai_usage": {"model", "input_tokens", "output_tokens", "cost_gbp"},
            },
        }
    """
    file_lower = file_path.lower()
    source = "pdf" if file_lower.endswith(".pdf") else "image"

    try:
        # Convert file to base64 image(s)
        if file_lower.endswith(".pdf"):
            pages = _pdf_pages_to_base64(file_path, max_pages=3)
            if not pages:
                return _empty_receipt_result("PDF has no pages", source="pdf")
            images = pages
        else:
            b64, media = _image_to_base64(file_path)
            images = [(b64, media)]

        # Call the AI — capture token usage from every call
        raw, call_usage = _call_vision(images, RECEIPT_PROMPT)
        parsed, retry_usage = _parse_json_response(raw, RECEIPT_PROMPT_STRICT, images)

        total_in = int(call_usage.get("input_tokens", 0)) + int(retry_usage.get("input_tokens", 0))
        total_out = int(call_usage.get("output_tokens", 0)) + int(retry_usage.get("output_tokens", 0))
        cost_gbp = ai_pricing.calculate_cost_gbp(AI_MODEL, total_in, total_out)
        ai_usage = {
            "model": AI_MODEL,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost_gbp": cost_gbp,
        }

        if "error" in parsed:
            logger.error("AI parse error for %s: %s", file_path, parsed["error"])
            result = _empty_receipt_result(parsed["error"], source=source)
            result["metadata"]["ai_usage"] = ai_usage
            return result

        result = _normalise_receipt_result(parsed, file_path)
        result["metadata"]["ai_usage"] = ai_usage
        return result

    except Exception as exc:
        logger.exception("Unexpected error parsing receipt %s", file_path)
        return _empty_receipt_result(str(exc), source=source)


def _empty_receipt_result(error_msg: str, source: str = "image") -> dict:
    """Return a well-formed but empty receipt result with an error note."""
    return {
        "items": [],
        "totals": {},
        "metadata": {
            "store_name": "Unknown Store",
            "date": None,
            "item_count": 0,
            "currency": "GBP",
            "source": source,
            "method": "claude_haiku_vision",
            "error": error_msg,
        },
    }


def _normalise_receipt_result(parsed: dict, file_path: str) -> dict:
    """Convert the raw AI JSON into the standard receipt-parser output
    format consumed by ``export_receipt_to_xlsx``."""
    items = []
    for raw_item in parsed.get("items", []):
        items.append({
            "description": str(raw_item.get("description", "")),
            "quantity": int(raw_item.get("quantity", 1)),
            "unit_price": float(raw_item.get("unit_price", 0)),
            "total_price": float(raw_item.get("total_price", 0)),
        })

    totals: dict = {}
    if parsed.get("subtotal") is not None:
        totals["subtotal"] = float(parsed["subtotal"])
    if parsed.get("tax") is not None:
        totals["tax"] = float(parsed["tax"])
    if parsed.get("total") is not None:
        totals["total"] = float(parsed["total"])
    if parsed.get("payment_method"):
        totals["payment_method"] = parsed["payment_method"]

    # Fall back: compute total from items if the AI didn't provide one
    if "total" not in totals and items:
        totals["total"] = round(sum(i["total_price"] for i in items), 2)

    file_lower = file_path.lower()
    source = "pdf" if file_lower.endswith(".pdf") else "image"

    return {
        "items": items,
        "totals": totals,
        "metadata": {
            "store_name": parsed.get("store_name", "Unknown Store"),
            "date": parsed.get("date"),
            "item_count": len(items),
            "currency": parsed.get("currency", "GBP"),
            "source": source,
            "method": "claude_haiku_vision",
        },
    }


# ---------------------------------------------------------------------------
# Public API — bank statement parsing
# ---------------------------------------------------------------------------

# Minimum number of characters of extracted text across all pages before
# we trust the text path. Below this we assume the PDF is scanned / image-
# only and fall through to vision. 500 is low enough to catch even a
# statement with very few transactions but high enough to skip garbage
# from encrypted or image-only PDFs.
TEXT_PATH_MIN_CHARS = 500


def parse_statement_ai(file_path: str) -> dict:
    """Parse a PDF bank statement using Claude Haiku.

    Pipeline:
      1. Try to extract text from every page with pdfplumber. For
         text-based PDFs (the vast majority of modern bank statements:
         HSBC, Chase, BNP, RBC, N26, Revolut, Wise, etc.) this produces
         clean, complete text at zero API cost.
      2. If we got a meaningful amount of text, send it to Claude Haiku
         in a SINGLE text-only call with a universal international
         statement prompt. Text is much cheaper than vision *and*
         dramatically more accurate on dense multi-column layouts (narrow
         columns, multi-line descriptions, date-carrying, etc.).
      3. If the text path yields nothing (scanned PDF, encrypted, no
         extractable text, or Claude returns no transactions), fall back
         to the per-page vision pipeline.

    Returns a dict compatible with the existing ``pdf_parser`` output
    format, plus a nested ``ai_usage`` dict inside ``metadata`` summing
    token usage across the whole parse::

        {
            "transactions": [{"date", "description", "amount", "balance", "type",
                              "debit", "credit"}, ...],
            "summary": {"total_transactions", "total_credits", "total_debits", "net"},
            "metadata": {
                "bank_name", "account_holder", "statement_period", "currency",
                "source", "method", "pages_processed",
                "ai_usage": {"model", "input_tokens", "output_tokens", "cost_gbp"},
            },
        }
    """
    total_in = 0
    total_out = 0

    # -----------------------------------------------------------------
    # Step 1: attempt text extraction with pdfplumber
    # -----------------------------------------------------------------
    pages_text: list[str] = []
    try:
        pages_text = _pdf_pages_to_text(file_path)
    except Exception:
        logger.exception("pdfplumber text extraction raised for %s", file_path)
        pages_text = []

    total_text_len = sum(len(t) for t in pages_text)
    text_path_result: Optional[dict] = None

    if total_text_len >= TEXT_PATH_MIN_CHARS:
        logger.info(
            "Statement text extraction succeeded: %d chars across %d pages — using text path",
            total_text_len, len(pages_text),
        )
        try:
            combined_text = "\n\n--- PAGE BREAK ---\n\n".join(pages_text)
            raw, call_usage = _call_text(combined_text, STATEMENT_TEXT_PROMPT)
            parsed, retry_usage = _parse_json_response_text(
                raw, STATEMENT_TEXT_PROMPT_STRICT, combined_text,
            )

            total_in += int(call_usage.get("input_tokens", 0)) + int(retry_usage.get("input_tokens", 0))
            total_out += int(call_usage.get("output_tokens", 0)) + int(retry_usage.get("output_tokens", 0))

            if "error" not in parsed and parsed.get("transactions"):
                all_transactions = [
                    _normalise_transaction(tx) for tx in parsed.get("transactions", [])
                ]
                cost_gbp = ai_pricing.calculate_cost_gbp(AI_MODEL, total_in, total_out)
                text_path_result = _build_statement_result(
                    all_transactions,
                    bank_name=parsed.get("bank_name"),
                    account_holder=parsed.get("account_holder"),
                    statement_period=parsed.get("statement_period"),
                    currency=parsed.get("currency"),
                    pages_processed=len(pages_text),
                    method="claude_haiku_text",
                )
                text_path_result["metadata"]["ai_usage"] = {
                    "model": AI_MODEL,
                    "input_tokens": total_in,
                    "output_tokens": total_out,
                    "cost_gbp": cost_gbp,
                }
                logger.info(
                    "Text path extracted %d transactions from %s",
                    len(all_transactions), file_path,
                )
                return text_path_result

            # Parsed successfully but zero transactions — fall through to vision.
            logger.warning(
                "Text path returned no transactions for %s, falling back to vision",
                file_path,
            )
        except Exception:
            logger.exception(
                "Text path failed for %s, falling back to vision", file_path,
            )

    # -----------------------------------------------------------------
    # Step 2: vision fallback (scanned PDFs, or when text path fails)
    # -----------------------------------------------------------------
    try:
        pages = _pdf_pages_to_base64(file_path)
        if not pages:
            result = _empty_statement_result("PDF has no pages")
            result["metadata"]["ai_usage"] = {
                "model": AI_MODEL, "input_tokens": total_in, "output_tokens": total_out, "cost_gbp": 0.0,
            }
            return result

        all_transactions: list[dict] = []
        bank_name: Optional[str] = None
        account_holder: Optional[str] = None
        statement_period: Optional[str] = None
        currency: Optional[str] = None

        for page_idx, page_image in enumerate(pages):
            logger.info("Processing statement page %d/%d (vision)", page_idx + 1, len(pages))
            try:
                raw, call_usage = _call_vision([page_image], STATEMENT_PROMPT)
                parsed, retry_usage = _parse_json_response(raw, STATEMENT_PROMPT_STRICT, [page_image])

                total_in += int(call_usage.get("input_tokens", 0)) + int(retry_usage.get("input_tokens", 0))
                total_out += int(call_usage.get("output_tokens", 0)) + int(retry_usage.get("output_tokens", 0))

                if "error" in parsed:
                    logger.warning("AI parse error on page %d: %s", page_idx + 1, parsed["error"])
                    continue

                # Capture metadata from the first page that has it
                if not bank_name and parsed.get("bank_name"):
                    bank_name = parsed["bank_name"]
                if not account_holder and parsed.get("account_holder"):
                    account_holder = parsed["account_holder"]
                if not statement_period and parsed.get("statement_period"):
                    statement_period = parsed["statement_period"]
                if not currency and parsed.get("currency"):
                    currency = parsed["currency"]

                for tx in parsed.get("transactions", []):
                    all_transactions.append(_normalise_transaction(tx))

            except Exception:
                logger.exception("Error processing statement page %d", page_idx + 1)
                continue

        cost_gbp = ai_pricing.calculate_cost_gbp(AI_MODEL, total_in, total_out)
        result = _build_statement_result(
            all_transactions,
            bank_name=bank_name,
            account_holder=account_holder,
            statement_period=statement_period,
            currency=currency,
            pages_processed=len(pages),
            method="claude_haiku_vision",
        )
        result["metadata"]["ai_usage"] = {
            "model": AI_MODEL,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost_gbp": cost_gbp,
        }
        return result

    except Exception as exc:
        logger.exception("Unexpected error parsing statement %s", file_path)
        result = _empty_statement_result(str(exc))
        cost_gbp = ai_pricing.calculate_cost_gbp(AI_MODEL, total_in, total_out)
        result["metadata"]["ai_usage"] = {
            "model": AI_MODEL,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cost_gbp": cost_gbp,
        }
        return result


def _normalise_transaction(raw_tx: dict) -> dict:
    """Normalise a single transaction from the AI into the standard format
    expected by ``export_to_xlsx``."""
    amount = float(raw_tx.get("amount", 0))
    tx_type = raw_tx.get("type", "debit" if amount < 0 else "credit")

    # Ensure debit/credit fields match the xlsx exporter expectations
    debit = abs(amount) if amount < 0 else None
    credit = amount if amount > 0 else None

    balance = raw_tx.get("balance")
    if balance is not None:
        try:
            balance = float(balance)
        except (TypeError, ValueError):
            balance = None

    return {
        "date": str(raw_tx.get("date", "")),
        "description": str(raw_tx.get("description", "")),
        "amount": amount,
        "balance": balance,
        "type": tx_type,
        "debit": debit,
        "credit": credit,
    }


def _build_statement_result(
    transactions: list[dict],
    *,
    bank_name: Optional[str] = None,
    account_holder: Optional[str] = None,
    statement_period: Optional[str] = None,
    currency: Optional[str] = None,
    pages_processed: int = 0,
    method: str = "claude_haiku_vision",
) -> dict:
    """Assemble the final statement result dict with summary statistics."""
    total_credits = round(sum(tx["amount"] for tx in transactions if tx["amount"] > 0), 2)
    total_debits = round(sum(tx["amount"] for tx in transactions if tx["amount"] < 0), 2)

    return {
        "transactions": transactions,
        "summary": {
            "total_transactions": len(transactions),
            "total_credits": total_credits,
            "total_debits": total_debits,
            "net": round(total_credits + total_debits, 2),
        },
        "metadata": {
            "bank_name": bank_name or "Unknown Bank",
            "account_holder": account_holder,
            "statement_period": statement_period,
            "currency": currency,
            "source": "pdf",
            "method": method,
            "pages_processed": pages_processed,
        },
    }


def _empty_statement_result(error_msg: str) -> dict:
    """Return a well-formed but empty statement result with an error note."""
    return {
        "transactions": [],
        "summary": {
            "total_transactions": 0,
            "total_credits": 0,
            "total_debits": 0,
            "net": 0,
        },
        "metadata": {
            "bank_name": "Unknown Bank",
            "account_holder": None,
            "statement_period": None,
            "source": "pdf",
            "method": "claude_haiku_vision",
            "pages_processed": 0,
            "error": error_msg,
        },
    }


# ---------------------------------------------------------------------------
# Public API — bulk receipt parsing
# ---------------------------------------------------------------------------

def parse_receipts_bulk(file_paths: list[str]) -> dict:
    """Parse multiple receipt files and combine the results.

    Each file is processed independently with ``parse_receipt_ai`` — BankScan
    is AI-only and no longer falls back to the traditional receipt parser.

    Returns a combined result::

        {
            "receipts": [<individual receipt results>],
            "combined_items": [{"store", "date", "description", "quantity",
                                "unit_price", "total_price"}, ...],
            "grand_total": 0.00,
            "receipt_count": N,
            "total_items": M,
            "ai_usage": {"model", "input_tokens", "output_tokens", "cost_gbp"},
        }
    """
    receipts: list[dict] = []
    combined_items: list[dict] = []
    grand_total = 0.0
    bulk_in = 0
    bulk_out = 0

    for fp in file_paths:
        logger.info("Bulk parsing receipt: %s", fp)

        try:
            result = parse_receipt_ai(fp)
        except Exception as exc:
            logger.warning("AI receipt parse failed for %s: %s", fp, exc)
            result = _empty_receipt_result(str(exc), source="image")

        store_name = result.get("metadata", {}).get("store_name", "Unknown Store")
        receipt_date = result.get("metadata", {}).get("date")

        # Accumulate token usage across all files
        usage = result.get("metadata", {}).get("ai_usage") or {}
        bulk_in += int(usage.get("input_tokens", 0))
        bulk_out += int(usage.get("output_tokens", 0))

        # Build a slim receipt summary for the receipts list
        receipt_summary = {
            "store_name": store_name,
            "date": receipt_date,
            "items": result.get("items", []),
            "total": result.get("totals", {}).get("total", 0),
        }
        if "error" in result.get("metadata", {}):
            receipt_summary["error"] = result["metadata"]["error"]

        receipts.append(receipt_summary)

        # Flatten items into the combined list, tagging with the store name
        for item in result.get("items", []):
            combined_items.append({
                "store": store_name,
                "date": receipt_date,
                "description": item["description"],
                "quantity": item["quantity"],
                "unit_price": item["unit_price"],
                "total_price": item["total_price"],
            })

        grand_total += result.get("totals", {}).get("total", 0)

    bulk_cost = ai_pricing.calculate_cost_gbp(AI_MODEL, bulk_in, bulk_out)
    return {
        "receipts": receipts,
        "combined_items": combined_items,
        "grand_total": round(grand_total, 2),
        "receipt_count": len(receipts),
        "total_items": len(combined_items),
        "ai_usage": {
            "model": AI_MODEL,
            "input_tokens": bulk_in,
            "output_tokens": bulk_out,
            "cost_gbp": bulk_cost,
        },
    }


def parse_statements_bulk(file_paths: list[str]) -> dict:
    """Parse multiple bank statement files and combine all transactions.

    PDFs are always parsed via Claude vision (AI-only). CSV/TSV/TXT files
    are still parsed locally because they need no intelligence.

    Returns::

        {
            "statements": [<individual statement results>],
            "all_transactions": [<merged transaction list>],
            "summary": {"total_transactions", "total_credits", "total_debits", "net"},
            "statement_count": N,
            "ai_usage": {"model", "input_tokens", "output_tokens", "cost_gbp"},
        }
    """
    from parsers.csv_parser import parse_csv

    statements: list[dict] = []
    all_transactions: list[dict] = []
    bulk_in = 0
    bulk_out = 0

    for fp in file_paths:
        logger.info("Bulk parsing statement: %s", fp)
        lower = fp.lower()

        result = None
        try:
            if lower.endswith(".pdf"):
                result = parse_statement_ai(fp)
            elif lower.endswith((".csv", ".tsv", ".txt")):
                result = parse_csv(fp)
        except Exception as exc:
            logger.warning("Statement parse failed for %s: %s", fp, exc)

        if result is None:
            result = {"transactions": [], "summary": {}, "metadata": {"source": fp, "error": "Could not parse"}}

        # Accumulate token usage across all files
        usage = result.get("metadata", {}).get("ai_usage") or {}
        bulk_in += int(usage.get("input_tokens", 0))
        bulk_out += int(usage.get("output_tokens", 0))

        # Tag each transaction with the source file
        source_name = Path(fp).name
        bank_name = result.get("metadata", {}).get("bank_name", "Unknown Bank")
        for tx in result.get("transactions", []):
            tx["source"] = source_name
            tx["bank"] = bank_name

        statements.append({
            "source": source_name,
            "bank_name": bank_name,
            "transaction_count": len(result.get("transactions", [])),
            "summary": result.get("summary", {}),
            "metadata": result.get("metadata", {}),
        })

        all_transactions.extend(result.get("transactions", []))

    # Sort all transactions by date
    all_transactions.sort(key=lambda t: t.get("date", ""))

    # Build combined summary
    total_credits = sum(t.get("amount", 0) for t in all_transactions if t.get("amount", 0) > 0)
    total_debits = sum(t.get("amount", 0) for t in all_transactions if t.get("amount", 0) < 0)

    bulk_cost = ai_pricing.calculate_cost_gbp(AI_MODEL, bulk_in, bulk_out)
    return {
        "statements": statements,
        "all_transactions": all_transactions,
        "summary": {
            "total_transactions": len(all_transactions),
            "total_credits": round(total_credits, 2),
            "total_debits": round(total_debits, 2),
            "net": round(total_credits + total_debits, 2),
        },
        "statement_count": len(statements),
        "ai_usage": {
            "model": AI_MODEL,
            "input_tokens": bulk_in,
            "output_tokens": bulk_out,
            "cost_gbp": bulk_cost,
        },
    }
