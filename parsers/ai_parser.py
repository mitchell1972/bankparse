"""
BankScan AI — AI-powered document parser using Claude Sonnet 4 vision.
Replaces Tesseract OCR with Claude API for near-perfect extraction.
Cost: ~3p per receipt, ~10p per bank statement page.
"""

import os
import io
import json
import base64
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bankparse.ai_parser")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-4-20250514")

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


RECEIPT_PROMPT = """Extract ALL items from this receipt/invoice image as JSON. Be thorough — include every line item.

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "store_name": "Store or restaurant name",
  "date": "YYYY-MM-DD or null if not visible",
  "currency": "GBP",
  "items": [
    {"description": "Item name", "quantity": 1, "unit_price": 0.00, "total_price": 0.00}
  ],
  "subtotal": 0.00,
  "tax": 0.00,
  "total": 0.00,
  "payment_method": "card/cash/null"
}

Rules:
- Include ALL items, even extras or add-ons
- If quantity is shown (e.g. "2x"), set quantity accordingly
- Prices should be numbers, not strings
- If you can't read a value clearly, use your best estimate
- date must be YYYY-MM-DD format or null"""


RECEIPT_PROMPT_STRICT = """You MUST return ONLY valid JSON. No markdown, no backticks, no explanation.

Extract ALL items from this receipt/invoice image. Return this exact JSON structure:
{"store_name":"...","date":"YYYY-MM-DD or null","currency":"GBP","items":[{"description":"...","quantity":1,"unit_price":0.00,"total_price":0.00}],"subtotal":0.00,"tax":0.00,"total":0.00,"payment_method":"card/cash/null"}"""


STATEMENT_PROMPT = """Extract ALL transactions from this bank statement page as JSON. Be thorough.

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "bank_name": "Bank name",
  "account_holder": "Name on statement",
  "statement_period": "Date range or null",
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
- Negative amounts for money OUT (debits), positive for money IN (credits)
- type is "debit" for money out, "credit" for money in
- Include ALL transactions, even if amounts are partially obscured
- Dates must be YYYY-MM-DD format
- Merge multi-line descriptions into one string
- balance can be null if not shown"""


STATEMENT_PROMPT_STRICT = """You MUST return ONLY valid JSON. No markdown, no backticks, no explanation.

Extract ALL transactions from this bank statement page. Return this exact JSON structure:
{"bank_name":"...","account_holder":"...","statement_period":"...","transactions":[{"date":"YYYY-MM-DD","description":"...","amount":-10.00,"balance":null,"type":"debit"}]}"""


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


def _call_vision(images: list[tuple[str, str]], prompt: str) -> str:
    """Send one or more images to Claude Haiku and return the text response.

    Args:
        images: list of (base64_data, media_type) tuples
        prompt: the user prompt to send

    Returns:
        The assistant's text response.
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
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


def _parse_json_response(raw: str, strict_prompt: str, images: list[tuple[str, str]]) -> dict:
    """Attempt to parse a JSON response.  If it fails, retry once with a
    stricter prompt.  Returns the parsed dict or a dict containing an
    ``error`` key."""
    cleaned = _clean_json_response(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_err:
        logger.warning("First JSON parse failed (%s), retrying with strict prompt", first_err)

    # Retry with stricter prompt
    try:
        raw_retry = _call_vision(images, strict_prompt)
        cleaned_retry = _clean_json_response(raw_retry)
        return json.loads(cleaned_retry)
    except (json.JSONDecodeError, Exception) as retry_err:
        logger.error("Strict-prompt retry also failed: %s", retry_err)
        return {"error": f"AI returned non-JSON after two attempts: {retry_err}"}


# ---------------------------------------------------------------------------
# Public API — receipt parsing
# ---------------------------------------------------------------------------

def parse_receipt_ai(file_path: str) -> dict:
    """Parse a receipt image or PDF using Claude Haiku vision.

    Accepts any image format supported by Pillow (including HEIC) or a
    single-page PDF.  Returns a dict compatible with the existing
    ``receipt_parser`` output format::

        {
            "items": [{"description", "quantity", "unit_price", "total_price"}, ...],
            "totals": {"subtotal", "tax", "total", ...},
            "metadata": {"store_name", "date", "item_count", "currency", "source", "method"},
        }
    """
    file_lower = file_path.lower()

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

        # Call the AI
        raw = _call_vision(images, RECEIPT_PROMPT)
        parsed = _parse_json_response(raw, RECEIPT_PROMPT_STRICT, images)

        if "error" in parsed:
            logger.error("AI parse error for %s: %s", file_path, parsed["error"])
            return _empty_receipt_result(parsed["error"], source="pdf" if file_lower.endswith(".pdf") else "image")

        return _normalise_receipt_result(parsed, file_path)

    except Exception as exc:
        logger.exception("Unexpected error parsing receipt %s", file_path)
        source = "pdf" if file_lower.endswith(".pdf") else "image"
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

def parse_statement_ai(file_path: str) -> dict:
    """Parse a PDF bank statement using Claude Haiku vision.

    Converts each page to an image, sends to Haiku, and combines the
    results.  Returns a dict compatible with the existing ``pdf_parser``
    output format::

        {
            "transactions": [{"date", "description", "amount", "balance", "type",
                              "debit", "credit"}, ...],
            "summary": {"total_transactions", "total_credits", "total_debits", "net"},
            "metadata": {"bank_name", "account_holder", "statement_period",
                         "source", "method", "pages_processed"},
        }
    """
    try:
        pages = _pdf_pages_to_base64(file_path)
        if not pages:
            return _empty_statement_result("PDF has no pages")

        all_transactions: list[dict] = []
        bank_name: Optional[str] = None
        account_holder: Optional[str] = None
        statement_period: Optional[str] = None

        for page_idx, page_image in enumerate(pages):
            logger.info("Processing statement page %d/%d", page_idx + 1, len(pages))
            try:
                raw = _call_vision([page_image], STATEMENT_PROMPT)
                parsed = _parse_json_response(raw, STATEMENT_PROMPT_STRICT, [page_image])

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

                for tx in parsed.get("transactions", []):
                    all_transactions.append(_normalise_transaction(tx))

            except Exception as page_exc:
                logger.exception("Error processing statement page %d", page_idx + 1)
                continue

        return _build_statement_result(
            all_transactions,
            bank_name=bank_name,
            account_holder=account_holder,
            statement_period=statement_period,
            pages_processed=len(pages),
        )

    except Exception as exc:
        logger.exception("Unexpected error parsing statement %s", file_path)
        return _empty_statement_result(str(exc))


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
    pages_processed: int = 0,
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
            "source": "pdf",
            "method": "claude_haiku_vision",
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

    Each file is processed independently with ``parse_receipt_ai``.
    Falls back to the traditional parser when the AI key is not set
    or when AI parsing returns no items.

    Returns a combined result::

        {
            "receipts": [<individual receipt results>],
            "combined_items": [{"store", "date", "description", "quantity",
                                "unit_price", "total_price"}, ...],
            "grand_total": 0.00,
            "receipt_count": N,
            "total_items": M,
        }
    """
    from parsers.receipt_parser import parse_receipt as parse_receipt_traditional

    receipts: list[dict] = []
    combined_items: list[dict] = []
    grand_total = 0.0

    for fp in file_paths:
        logger.info("Bulk parsing receipt: %s", fp)

        # Try AI first, fall back to traditional parser
        result = None
        if ANTHROPIC_API_KEY:
            try:
                result = parse_receipt_ai(fp)
            except Exception as exc:
                logger.warning("AI parse failed for %s, falling back: %s", fp, exc)

        if result is None or not result.get("items"):
            try:
                result = parse_receipt_traditional(fp)
            except Exception as exc:
                logger.warning("Traditional parse also failed for %s: %s", fp, exc)
                result = _empty_receipt_result(str(exc), source="image")

        store_name = result.get("metadata", {}).get("store_name", "Unknown Store")
        receipt_date = result.get("metadata", {}).get("date")

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

    return {
        "receipts": receipts,
        "combined_items": combined_items,
        "grand_total": round(grand_total, 2),
        "receipt_count": len(receipts),
        "total_items": len(combined_items),
    }


def parse_statements_bulk(file_paths: list[str]) -> dict:
    """Parse multiple bank statement files and combine all transactions.

    Each file is processed independently. Returns a combined result with
    all transactions merged, sorted by date, and a summary across all
    statements.

    Returns::

        {
            "statements": [<individual statement results>],
            "all_transactions": [<merged transaction list>],
            "summary": {"total_transactions", "total_credits", "total_debits", "net"},
            "statement_count": N,
        }
    """
    from parsers.pdf_parser import parse_pdf
    from parsers.csv_parser import parse_csv

    statements: list[dict] = []
    all_transactions: list[dict] = []

    for fp in file_paths:
        logger.info("Bulk parsing statement: %s", fp)
        lower = fp.lower()

        result = None

        # Try standard parsers first
        try:
            if lower.endswith(".pdf"):
                result = parse_pdf(fp)
            elif lower.endswith((".csv", ".tsv", ".txt")):
                result = parse_csv(fp)
        except Exception as exc:
            logger.warning("Standard parse failed for %s: %s", fp, exc)

        # If no transactions found, try AI fallback
        if (result is None or not result.get("transactions")) and ANTHROPIC_API_KEY and lower.endswith(".pdf"):
            try:
                result = parse_statement_ai(fp)
            except Exception as exc:
                logger.warning("AI parse also failed for %s: %s", fp, exc)

        if result is None:
            result = {"transactions": [], "summary": {}, "metadata": {"source": fp, "error": "Could not parse"}}

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
    }
