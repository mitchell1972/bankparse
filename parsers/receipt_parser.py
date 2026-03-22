"""
Receipt Parser
Extracts line items, totals, and metadata from store receipts.
Supports PDF receipts (via pdfplumber) and image receipts (via pytesseract OCR).
"""

import re
from datetime import datetime
from typing import Optional

import pdfplumber

# Try importing image/OCR libraries (optional)
try:
    from PIL import Image, ImageFilter, ImageEnhance
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

HAS_OCR = HAS_PIL and HAS_TESSERACT


# Common date formats on receipts
DATE_PATTERNS = [
    (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
    (r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    (r"\d{2}/\d{2}/\d{2}", "%d/%m/%y"),
    (r"\d{2}\.\d{2}\.\d{4}", "%d.%m.%Y"),
    (r"\d{2}\.\d{2}\.\d{2}", "%d.%m.%y"),
    (r"\d{2}\s\w{3}\s\d{4}", "%d %b %Y"),
    # US-style fallbacks
    (r"\d{1,2}/\d{1,2}/\d{4}", "%m/%d/%Y"),
]

# Money pattern — matches prices like 1.99, £1.99, $1.99, 12,345.67
PRICE_PATTERN = re.compile(
    r"[£$€]?\s*(\d{1,6}(?:[,]\d{3})*\.\d{2})"
)

# Patterns that indicate summary/total lines (not line items)
# Uses word-boundary regex to avoid false positives (e.g. "tip" matching "Tips" in "PG Tips")
TOTAL_PATTERNS = [
    re.compile(r"\b" + kw + r"\b", re.IGNORECASE)
    for kw in [
        "total", "subtotal", "sub total", "sub-total",
        "vat", "tax", "gst", "service charge", "gratuity",
        "change due", "cash tendered", "card payment", "paid by",
        "visa", "mastercard", "amex", "debit card", "credit card",
        "payment method", "amount tendered", "balance due",
        "discount", "savings", "rounding",
    ]
]

# These use substring matching since they're distinctive enough
SKIP_KEYWORDS = [
    "receipt", "thank you", "thanks", "visit us",
    "tel:", "phone:", "www.", "http", ".com", ".co.uk",
    "vat reg", "vat no", "company reg", "registered",
    "opening hours", "customer copy", "merchant copy",
    "served by", "cashier", "till no",
    "auth code", "ref:", "order no",
    "clubcard points", "nectar points", "loyalty points",
]


def extract_date(text: str) -> Optional[str]:
    """Extract the first date found in text."""
    for pattern, fmt in DATE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            try:
                dt = datetime.strptime(match.group(), fmt)
                if 2000 <= dt.year <= 2030:
                    return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def extract_store_name(lines: list[str]) -> str:
    """Guess the store name from the first few non-empty lines."""
    for line in lines[:5]:
        line = line.strip()
        # Skip very short lines, lines that are all digits/symbols, or date-like lines
        if len(line) < 3:
            continue
        if re.match(r"^[\d\s\-/:.]+$", line):
            continue
        # Skip lines that look like addresses (contain postcodes)
        if re.search(r"[A-Z]{1,2}\d{1,2}\s?\d[A-Z]{2}", line, re.IGNORECASE):
            continue
        # Skip phone numbers
        if re.search(r"\d{5,}", line.replace(" ", "")):
            continue
        return line
    return "Unknown Store"


def clean_price(text: str) -> Optional[float]:
    """Extract a price value from text."""
    text = text.replace(",", "").replace("£", "").replace("$", "").replace("€", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def is_total_line(line: str) -> bool:
    """Check if a line is a total/summary line using word-boundary matching."""
    return any(p.search(line) for p in TOTAL_PATTERNS)


def is_skip_line(line: str) -> bool:
    """Check if a line should be skipped entirely."""
    lower = line.lower()
    return any(kw in lower for kw in SKIP_KEYWORDS)


def parse_receipt_text(text: str) -> dict:
    """
    Parse receipt text and extract structured data.
    Returns dict with 'items', 'totals', and 'metadata'.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    store_name = extract_store_name(lines)
    receipt_date = None
    items = []
    totals = {}

    for line in lines:
        # Try to find the date
        if not receipt_date:
            d = extract_date(line)
            if d:
                receipt_date = d

        # Find prices in this line
        price_matches = list(PRICE_PATTERN.finditer(line))
        if not price_matches:
            continue

        # Get the last price on the line (usually the line total)
        last_match = price_matches[-1]
        price_str = last_match.group(1)
        price = clean_price(price_str)
        if price is None:
            continue

        # Get the description (everything before the price)
        desc = line[:last_match.start()].strip()
        # Clean up description — remove leading quantity markers like "2x", "3 x"
        qty = 1
        qty_match = re.match(r"^(\d+)\s*[xX×]\s*", desc)
        if qty_match:
            qty = int(qty_match.group(1))
            desc = desc[qty_match.end():].strip()

        # Also check for quantity at the end or a separate qty column
        if not qty_match:
            qty_end = re.search(r"\s+(\d+)\s*$", desc)
            # Only treat as qty if it's a small number and not part of the item name
            if qty_end and int(qty_end.group(1)) <= 20:
                potential_qty = int(qty_end.group(1))
                # Only use if there are multiple prices (unit price + line total)
                if len(price_matches) > 1:
                    qty = potential_qty
                    desc = desc[:qty_end.start()].strip()

        # Remove trailing price-like artifacts from description
        desc = re.sub(r"[£$€]\s*$", "", desc).strip()
        desc = re.sub(r"\s+@\s+.*$", "", desc).strip()  # Remove "@ unit price" patterns

        if not desc:
            continue

        # Classify: total/summary line vs line item
        if is_total_line(line):
            lower = line.lower()
            if re.search(r"\bsub\s*-?\s*total\b", lower):
                totals["subtotal"] = price
            elif re.search(r"\b(vat|tax|gst)\b", lower):
                totals["tax"] = price
            elif re.search(r"\btotal\b", lower):
                totals["total"] = price
            elif re.search(r"\b(discount|savings)\b", lower):
                totals["discount"] = price
            elif re.search(r"\b(change due|amount tendered)\b", lower):
                totals["change"] = price
            elif re.search(r"\b(card payment|paid by|visa|mastercard|amex|debit card|credit card|payment method)\b", lower):
                totals["payment"] = price
            continue

        if is_skip_line(line):
            continue

        items.append({
            "description": desc,
            "quantity": qty,
            "unit_price": round(price / qty, 2) if qty > 1 else price,
            "total_price": price,
        })

    # If no total found, sum up items
    if "total" not in totals and items:
        totals["total"] = round(sum(i["total_price"] for i in items), 2)

    return {
        "items": items,
        "totals": totals,
        "metadata": {
            "store_name": store_name,
            "date": receipt_date,  # None if no date found; callers should handle gracefully
            "item_count": len(items),
            "currency": "GBP",
        },
    }


def parse_receipt_pdf(file_path: str) -> dict:
    """Parse a PDF receipt using pdfplumber."""
    full_text = ""

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            # Try table extraction first
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        if row:
                            cells = [str(c).strip() for c in row if c]
                            full_text += "  ".join(cells) + "\n"
            else:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

    result = parse_receipt_text(full_text)
    result["metadata"]["source"] = "pdf"
    result["metadata"]["method"] = "pdfplumber"
    return result


def _preprocess_image(image: "Image.Image") -> "Image.Image":
    """
    Preprocess a receipt image for better OCR accuracy.
    Applies grayscale conversion, resizing, sharpening, and contrast
    enhancement using only Pillow (no OpenCV).
    Note: Binary thresholding is intentionally NOT applied — it destroys
    detail on photos of receipts (vs scanned receipts).
    """
    # Convert to grayscale
    if image.mode != "L":
        image = image.convert("L")

    # Resize if too small — ensure minimum width of 1000px for OCR accuracy
    MIN_WIDTH = 1000
    w, h = image.size
    if w < MIN_WIDTH:
        scale = MIN_WIDTH / w
        image = image.resize((MIN_WIDTH, int(h * scale)), Image.LANCZOS)

    # Resize down if very large (phone cameras: 3000-4000px wide) to speed up OCR
    MAX_WIDTH = 2000
    w, h = image.size
    if w > MAX_WIDTH:
        scale = MAX_WIDTH / w
        image = image.resize((MAX_WIDTH, int(h * scale)), Image.LANCZOS)

    # Sharpen
    image = image.filter(ImageFilter.SHARPEN)

    # Moderate contrast boost (1.5x, not 2x — preserves more detail)
    enhancer = ImageEnhance.Contrast(image)
    image = enhancer.enhance(1.5)

    return image


def parse_receipt_image(file_path: str) -> dict:
    """Parse a receipt image using OCR (pytesseract)."""
    if not HAS_PIL:
        raise ImportError(
            "Receipt image parsing requires Pillow. "
            "Install with: pip install Pillow"
        )
    if not HAS_TESSERACT:
        raise ImportError(
            "Receipt image parsing requires pytesseract. "
            "Install with: pip install pytesseract. "
            "Also requires Tesseract OCR engine."
        )

    # Handle HEIC/HEIF format — convert to JPEG via pillow-heif if available
    file_lower = file_path.lower()
    if file_lower.endswith((".heic", ".heif")):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise ImportError(
                "HEIC/HEIF image support requires pillow-heif. "
                "Install with: pip install pillow-heif"
            )

    image = Image.open(file_path)

    # Convert HEIC (which may be RGBA/P mode) to RGB
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")

    # Apply preprocessing pipeline for better OCR results
    image = _preprocess_image(image)

    # PSM 3: fully automatic page segmentation (works best for phone photos of receipts)
    text = pytesseract.image_to_string(image, config="--psm 3")

    # Confidence check — reject garbage OCR output
    if len(text.strip()) < 20:
        return {
            "items": [],
            "totals": {},
            "metadata": {
                "store_name": "Unknown Store",
                "date": None,
                "item_count": 0,
                "currency": "GBP",
                "source": "image",
                "method": "tesseract_ocr",
                "error": "OCR produced too little text (< 20 chars). Image may be unreadable.",
            },
        }

    # Check for mostly non-ASCII garbage
    ascii_chars = sum(1 for c in text if c.isascii())
    if len(text) > 0 and ascii_chars / len(text) < 0.5:
        return {
            "items": [],
            "totals": {},
            "metadata": {
                "store_name": "Unknown Store",
                "date": None,
                "item_count": 0,
                "currency": "GBP",
                "source": "image",
                "method": "tesseract_ocr",
                "error": "OCR output contains mostly non-ASCII characters. Image may be unreadable.",
            },
        }

    result = parse_receipt_text(text)
    result["metadata"]["source"] = "image"
    result["metadata"]["method"] = "tesseract_ocr"
    return result


def parse_receipt(file_path: str) -> dict:
    """
    Main entry point: parse a receipt file (PDF or image).
    Auto-detects format based on file extension.
    """
    lower = file_path.lower()

    if lower.endswith(".pdf"):
        return parse_receipt_pdf(file_path)
    elif lower.endswith((".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp", ".heic", ".heif")):
        return parse_receipt_image(file_path)
    else:
        raise ValueError(f"Unsupported receipt format: {file_path}")
