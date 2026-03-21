"""
BankParse — Vercel Serverless Entry Point
Adapts the FastAPI app for Vercel's serverless Python runtime.
"""

import os
import sys
import uuid
import tempfile
import base64
from pathlib import Path

# Add parent directory to path so parsers can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.requests import Request

from parsers.pdf_parser import parse_pdf
from parsers.csv_parser import parse_csv
from parsers.xlsx_exporter import export_to_xlsx, export_receipt_to_xlsx
from parsers.receipt_parser import parse_receipt

# Use /tmp on Vercel (only writable directory in serverless)
TMP_DIR = Path(tempfile.gettempdir()) / "bankparse"
TMP_DIR.mkdir(exist_ok=True)

# Read the template once at cold start
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "index.html"
TEMPLATE_HTML = TEMPLATE_PATH.read_text()

# App
app = FastAPI(title="BankParse", version="1.1.0")


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(TEMPLATE_HTML)


@app.post("/api/parse")
async def parse_statement(file: UploadFile = File(...)):
    """Upload and parse a bank statement, returning structured data + XLSX as base64."""

    filename = file.filename.lower()
    if not any(filename.endswith(ext) for ext in [".pdf", ".csv", ".tsv", ".txt"]):
        raise HTTPException(status_code=400, detail="Unsupported file type. Please upload a PDF or CSV file.")

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    job_id = str(uuid.uuid4())[:8]
    upload_path = TMP_DIR / f"{job_id}_{file.filename}"

    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        if filename.endswith(".pdf"):
            result = parse_pdf(str(upload_path))
        else:
            result = parse_csv(str(upload_path))

        if not result["transactions"]:
            raise HTTPException(
                status_code=422,
                detail="No transactions found. The file format may not be supported yet, or the statement may be empty."
            )

        # Export to XLSX in /tmp
        output_path = TMP_DIR / f"bankparse_{job_id}.xlsx"
        export_to_xlsx(result, str(output_path))

        # Read XLSX and encode as base64 for client-side download
        xlsx_bytes = output_path.read_bytes()
        xlsx_b64 = base64.b64encode(xlsx_bytes).decode("utf-8")

        return JSONResponse({
            "transactions": result["transactions"],
            "summary": result["summary"],
            "metadata": result["metadata"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"bankparse_{job_id}.xlsx",
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")
    finally:
        if upload_path.exists():
            upload_path.unlink()
        output_file = TMP_DIR / f"bankparse_{job_id}.xlsx"
        if output_file.exists():
            output_file.unlink()


@app.post("/api/parse-receipt")
async def parse_receipt_endpoint(file: UploadFile = File(...)):
    """Upload and parse a store receipt, returning itemised data + XLSX as base64."""

    filename = file.filename.lower()
    IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"]
    RECEIPT_EXTENSIONS = [".pdf"] + IMAGE_EXTENSIONS
    if not any(filename.endswith(ext) for ext in RECEIPT_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Please upload a PDF or image (PNG, JPG, TIFF) of your receipt."
        )

    contents = await file.read()
    if len(contents) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

    job_id = str(uuid.uuid4())[:8]
    upload_path = TMP_DIR / f"{job_id}_{file.filename}"

    with open(upload_path, "wb") as f:
        f.write(contents)

    try:
        result = parse_receipt(str(upload_path))

        if not result["items"]:
            raise HTTPException(
                status_code=422,
                detail="No items found on the receipt. The format may not be supported, or the image may be unclear."
            )

        output_path = TMP_DIR / f"receipt_{job_id}.xlsx"
        export_receipt_to_xlsx(result, str(output_path))

        xlsx_bytes = output_path.read_bytes()
        xlsx_b64 = base64.b64encode(xlsx_bytes).decode("utf-8")

        return JSONResponse({
            "items": result["items"],
            "totals": result["totals"],
            "metadata": result["metadata"],
            "xlsx_base64": xlsx_b64,
            "xlsx_filename": f"receipt_{job_id}.xlsx",
        })

    except HTTPException:
        raise
    except ImportError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Receipt parsing error: {str(e)}")
    finally:
        if upload_path.exists():
            upload_path.unlink()
        output_file = TMP_DIR / f"receipt_{job_id}.xlsx"
        if output_file.exists():
            output_file.unlink()


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.1.0", "runtime": "vercel"}
