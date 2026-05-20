"""
Accountant export pack.

Builds a single ZIP that contains EVERYTHING the user's accountant needs
to rubber-stamp an HMRC submission. The accountant's job becomes
review, not categorise. That's what makes them recommend us.

Contents of the ZIP:

  summary.html        ← Audit Confidence Certificate
  transactions.csv    ← One row per transaction with all HMRC columns
  receipts/           ← Folder, one file per receipt, named
                        {date}_{merchant}_{amount}.{ext}
  mismatch_report.csv ← Transactions without a receipt + user's stated reason
  reasoning_log.csv   ← One row per (transaction, category) decision
                        with the AI's confidence + reasoning
  manifest.json       ← File hashes — accountant verifies integrity

Returns a ``bytes`` blob the endpoint streams as
``application/zip``.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import zipfile

import database
from services.audit_summary import summarise_audit_readiness
from services.audit_certificate import build_certificate_html


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(text: str | None, max_len: int = 40) -> str:
    if not text:
        return "unknown"
    cleaned = _FILENAME_SAFE.sub("_", text).strip("_")
    return cleaned[:max_len] or "unknown"


def build_export_zip(
    user_id: int,
    user_email: str,
    *,
    period_label: str | None = None,
) -> bytes:
    """Compose the ZIP. Returns raw bytes — caller wraps in StreamingResponse."""
    if period_label is None:
        now = _dt.datetime.utcnow()
        q = (now.month - 1) // 3 + 1
        period_label = f"Q{q}-{now.year}"

    txs = database.get_user_ledger_transactions(user_id, limit=20000)
    receipts = database.get_user_ledger_receipts(user_id, limit=20000)
    receipts_by_id = {r["id"]: r for r in receipts}

    links_rows = database._fetchall_dicts(
        "SELECT l.* FROM ledger_links l "
        "JOIN ledger_transactions t ON l.transaction_id = t.id "
        "WHERE t.user_id = ?",
        (user_id,),
    )
    links_by_tx: dict[int, list[dict]] = {}
    for link in links_rows:
        links_by_tx.setdefault(link["transaction_id"], []).append(link)

    summary = summarise_audit_readiness(user_id)

    buf = io.BytesIO()
    file_hashes: dict[str, str] = {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Audit Confidence Certificate as summary.html
        cert_html = build_certificate_html(
            user_email=user_email,
            period_label=period_label,
            summary=summary,
        )
        zf.writestr("summary.html", cert_html)
        file_hashes["summary.html"] = hashlib.sha256(cert_html.encode()).hexdigest()

        # 2. transactions.csv — every transaction with all HMRC columns
        tx_csv = io.StringIO()
        writer = csv.DictWriter(tx_csv, fieldnames=[
            "id", "date_iso", "description", "amount", "currency",
            "hmrc_category", "hmrc_category_confidence",
            "business_pct", "is_capital",
            "receipt_status", "exclusion_reason",
            "vat_amount", "linked_receipt_ids",
            "content_hash",
        ])
        writer.writeheader()
        for tx in txs:
            linked_ids = [
                l["receipt_id"] for l in links_by_tx.get(tx["id"], [])
            ]
            writer.writerow({
                "id": tx["id"],
                "date_iso": tx.get("date_iso") or "",
                "description": tx.get("description") or "",
                "amount": f"{tx['amount']:.2f}",
                "currency": tx.get("currency") or "GBP",
                "hmrc_category": tx.get("hmrc_category") or "",
                "hmrc_category_confidence": tx.get("hmrc_category_confidence") or "",
                "business_pct": tx.get("business_pct") or 100,
                "is_capital": tx.get("is_capital") or 0,
                "receipt_status": tx.get("receipt_status") or "",
                "exclusion_reason": tx.get("exclusion_reason") or "",
                "vat_amount": f"{tx['vat_amount']:.2f}" if tx.get("vat_amount") is not None else "",
                "linked_receipt_ids": ",".join(str(x) for x in linked_ids),
                "content_hash": tx.get("content_hash") or "",
            })
        tx_csv_bytes = tx_csv.getvalue().encode()
        zf.writestr("transactions.csv", tx_csv_bytes)
        file_hashes["transactions.csv"] = hashlib.sha256(tx_csv_bytes).hexdigest()

        # 3. receipts/ folder — one file per receipt named for the accountant
        for r in receipts:
            file_path = r.get("file_path")
            if not file_path or not os.path.exists(file_path):
                # Receipt parsed but original file missing — still include a
                # JSON stub so the accountant knows the receipt was processed
                stub = json.dumps({
                    "receipt_id": r["id"],
                    "store_name": r.get("store_name"),
                    "date_iso": r.get("date_iso"),
                    "total_amount": r.get("total_amount"),
                    "tax_amount": r.get("tax_amount"),
                    "content_hash": r.get("content_hash"),
                    "items": json.loads(r.get("items_json") or "[]"),
                    "note": "Original file not available on disk; parsed data only.",
                }, indent=2)
                stub_path = f"receipts/missing_rc{r['id']}.json"
                zf.writestr(stub_path, stub)
                file_hashes[stub_path] = hashlib.sha256(stub.encode()).hexdigest()
                continue
            ext = os.path.splitext(file_path)[1] or ".bin"
            archive_name = (
                f"receipts/"
                f"{_safe_name(r.get('date_iso') or 'undated', 12)}_"
                f"{_safe_name(r.get('store_name'), 30)}_"
                f"{r.get('total_amount') or 0:.2f}_rc{r['id']}{ext}"
            )
            try:
                with open(file_path, "rb") as fh:
                    data = fh.read()
                zf.writestr(archive_name, data)
                file_hashes[archive_name] = hashlib.sha256(data).hexdigest()
            except OSError:
                continue

        # 4. mismatch_report.csv — transactions without a receipt
        mismatch_csv = io.StringIO()
        mw = csv.DictWriter(mismatch_csv, fieldnames=[
            "id", "date_iso", "description", "amount",
            "hmrc_category", "receipt_status", "exclusion_reason",
        ])
        mw.writeheader()
        for tx in txs:
            if tx.get("receipt_status") in ("matched", "excluded"):
                continue
            mw.writerow({
                "id": tx["id"],
                "date_iso": tx.get("date_iso") or "",
                "description": tx.get("description") or "",
                "amount": f"{tx['amount']:.2f}",
                "hmrc_category": tx.get("hmrc_category") or "",
                "receipt_status": tx.get("receipt_status") or "",
                "exclusion_reason": tx.get("exclusion_reason") or "",
            })
        mm_bytes = mismatch_csv.getvalue().encode()
        zf.writestr("mismatch_report.csv", mm_bytes)
        file_hashes["mismatch_report.csv"] = hashlib.sha256(mm_bytes).hexdigest()

        # 5. reasoning_log.csv — one row per category decision
        reasoning_csv = io.StringIO()
        rw = csv.DictWriter(reasoning_csv, fieldnames=[
            "transaction_id", "date_iso", "description",
            "hmrc_category", "hmrc_category_confidence", "hmrc_category_reason",
        ])
        rw.writeheader()
        for tx in txs:
            rw.writerow({
                "transaction_id": tx["id"],
                "date_iso": tx.get("date_iso") or "",
                "description": tx.get("description") or "",
                "hmrc_category": tx.get("hmrc_category") or "",
                "hmrc_category_confidence": tx.get("hmrc_category_confidence") or "",
                "hmrc_category_reason": tx.get("hmrc_category_reason") or "",
            })
        rl_bytes = reasoning_csv.getvalue().encode()
        zf.writestr("reasoning_log.csv", rl_bytes)
        file_hashes["reasoning_log.csv"] = hashlib.sha256(rl_bytes).hexdigest()

        # 6. manifest.json — file hashes for tamper-evidence
        manifest = {
            "generated_at": _dt.datetime.utcnow().isoformat() + "Z",
            "user_email": user_email,
            "period_label": period_label,
            "counts": {
                "transactions": len(txs),
                "receipts": len(receipts),
                "with_receipt": sum(
                    1 for tx in txs if tx.get("receipt_status") == "matched"
                ),
            },
            "totals": summary.get("totals", {}),
            "file_hashes": file_hashes,
        }
        manifest_bytes = json.dumps(manifest, indent=2).encode()
        zf.writestr("manifest.json", manifest_bytes)

    return buf.getvalue()
