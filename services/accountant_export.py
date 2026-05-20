"""
Accountant export pack.

Builds a single ZIP that contains EVERYTHING the user's accountant needs
to rubber-stamp an HMRC submission. The accountant's job becomes
review, not categorise. That's what makes them recommend us.

Layout (2026-05-20 redesign):

  README.txt                          ← Start here. Table of contents.
  Accountant_Pack.xlsx                ← Multi-sheet workbook (Cover, Tax
                                        Return Boxes, Transactions, Missing
                                        Receipts, Receipt Inventory, VAT,
                                        Reasoning Log).
  summary.html                        ← Audit Confidence Certificate.
                                        Open in browser → Print to PDF.
  receipts/
    {hmrc_category}/                  ← One folder per HMRC category,
      {date}_{store}_{amount}_rc{id}  ← human-readable filenames.
    _orphan/                          ← Receipts not yet matched.
    _missing-file/                    ← Parsed receipts with no original
                                        (JSON stub with what we know).
  data/                               ← Raw CSVs for import into other tools.
    transactions.csv
    mismatch_report.csv
    reasoning_log.csv
  manifest.json                       ← File hashes — accountant verifies
                                        the ZIP wasn't tampered with.

Returns a ``bytes`` blob the endpoint streams as ``application/zip``.

If you need to change what the accountant sees, the rule is: every change
must reduce the accountant's review time. That's the only metric.
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
from services.accountant_xlsx import build_accountant_workbook, category_meta
from services.audit_summary import summarise_audit_readiness
from services.audit_certificate import build_certificate_html


_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(text: str | None, max_len: int = 40) -> str:
    if not text:
        return "unknown"
    cleaned = _FILENAME_SAFE.sub("_", text).strip("_")
    return cleaned[:max_len] or "unknown"


def _category_folder(tx_category: str | None) -> str:
    """Map an HMRC category code to a receipt sub-folder name. Returns
    "_uncategorised" for missing categories so the accountant can still
    find them."""
    if not tx_category:
        return "_uncategorised"
    return _safe_name(tx_category, 60)


def _build_readme(
    *, period_label: str, user_email: str, client_name: str | None,
    summary: dict, generated_at: str,
) -> str:
    """Plain-text table of contents for the ZIP — first file the
    accountant should open."""
    totals = summary.get("totals", {})
    lines = [
        "================================================================",
        "BankScan AI — Accountant Pack",
        "================================================================",
        "",
        f"Client:        {client_name or user_email}",
        f"Account:       {user_email}",
        f"Period:        {period_label}",
        f"Generated:     {generated_at}",
        "",
        "What this is:",
        "  AI-assisted ledger + receipt pack for professional review.",
        "  NOT a tax return. Categorisation requires sign-off.",
        "",
        "Where to start:",
        "  1. Accountant_Pack.xlsx → 'Cover' tab → 'Tax Return Boxes' tab.",
        "     Every figure is mapped to its SA103S / SA103F / SA105 box.",
        "  2. summary.html → open in browser → Print to PDF for the file.",
        "  3. receipts/ folder → grouped by HMRC category for easy review.",
        "  4. data/*.csv → raw exports if you want to import into your",
        "     practice tooling.",
        "",
        "At a glance:",
        f"  Income (gross):       £{totals.get('income', 0):,.2f}",
        f"  Expenses (gross):     £{totals.get('expenses', 0):,.2f}",
        f"  VAT recorded:         £{totals.get('vat_total', 0):,.2f}",
        f"  Transactions:         {totals.get('transactions_total', 0)}",
        f"  Backed by a receipt:  {totals.get('transactions_matched', 0)}"
        f" ({totals.get('audit_ready_pct', 0)}%)",
        f"  Missing a receipt:    {totals.get('transactions_missing', 0)}",
        f"  Excluded:             {totals.get('transactions_excluded', 0)}",
        "",
        "Integrity:",
        "  manifest.json lists SHA-256 hashes of every file. If the figures",
        "  on the client's tax return differ from this pack, the manifest",
        "  lets you prove what was generated and when.",
        "",
        "Questions: mitchellagoma@gmail.com",
        "================================================================",
        "",
    ]
    return "\n".join(lines)


def build_export_zip(
    user_id: int,
    user_email: str,
    *,
    period_label: str | None = None,
    client_name: str | None = None,
) -> bytes:
    """Compose the ZIP. Returns raw bytes — caller wraps in StreamingResponse.

    ``client_name`` is shown on the Cover sheet + README; defaults to email."""
    if period_label is None:
        now = _dt.datetime.utcnow()
        q = (now.month - 1) // 3 + 1
        period_label = f"Q{q}-{now.year}"

    generated_at = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    txs = database.get_user_ledger_transactions(user_id, limit=20000)
    receipts = database.get_user_ledger_receipts(user_id, limit=20000)

    links_rows = database._fetchall_dicts(
        "SELECT l.* FROM ledger_links l "
        "JOIN ledger_transactions t ON l.transaction_id = t.id "
        "WHERE t.user_id = ?",
        (user_id,),
    )
    links_by_tx: dict[int, list[dict]] = {}
    links_by_rc: dict[int, list[dict]] = {}
    for link in links_rows:
        links_by_tx.setdefault(link["transaction_id"], []).append(link)
        links_by_rc.setdefault(link["receipt_id"], []).append(link)

    summary = summarise_audit_readiness(user_id)

    buf = io.BytesIO()
    file_hashes: dict[str, str] = {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. README.txt — first thing the accountant opens
        readme = _build_readme(
            period_label=period_label, user_email=user_email,
            client_name=client_name, summary=summary, generated_at=generated_at,
        )
        zf.writestr("README.txt", readme)
        file_hashes["README.txt"] = hashlib.sha256(readme.encode()).hexdigest()

        # 2. Accountant_Pack.xlsx — the headline workbook
        xlsx_bytes = build_accountant_workbook(
            user_email=user_email,
            period_label=period_label,
            client_name=client_name,
            summary=summary,
            txs=txs,
            receipts=receipts,
            links_by_tx=links_by_tx,
            links_by_rc=links_by_rc,
            generated_at=generated_at,
        )
        zf.writestr("Accountant_Pack.xlsx", xlsx_bytes)
        file_hashes["Accountant_Pack.xlsx"] = hashlib.sha256(xlsx_bytes).hexdigest()

        # 3. summary.html — Audit Confidence Certificate
        cert_html = build_certificate_html(
            user_email=user_email,
            period_label=period_label,
            summary=summary,
        )
        zf.writestr("summary.html", cert_html)
        file_hashes["summary.html"] = hashlib.sha256(cert_html.encode()).hexdigest()

        # 4. receipts/ — grouped by HMRC category. Each receipt lands in
        # the folder for the category of the transaction it's linked to.
        # Orphan receipts (no link) → receipts/_orphan/.
        # Receipts whose original file is missing → receipts/_missing-file/.
        for r in receipts:
            tx_links = links_by_rc.get(r["id"], [])
            linked_tx_categories = set()
            for link in tx_links:
                tx_row = next((t for t in txs if t["id"] == link["transaction_id"]), None)
                if tx_row:
                    linked_tx_categories.add(tx_row.get("hmrc_category"))
            folder = (
                "_orphan"
                if not linked_tx_categories
                else _category_folder(next(iter(linked_tx_categories)))
            )

            file_path = r.get("file_path")
            base = (
                f"{_safe_name(r.get('date_iso') or 'undated', 12)}_"
                f"{_safe_name(r.get('store_name'), 30)}_"
                f"{r.get('total_amount') or 0:.2f}_rc{r['id']}"
            )

            if not file_path or not os.path.exists(file_path):
                stub = json.dumps({
                    "receipt_id": r["id"],
                    "store_name": r.get("store_name"),
                    "date_iso": r.get("date_iso"),
                    "total_amount": r.get("total_amount"),
                    "tax_amount": r.get("tax_amount"),
                    "content_hash": r.get("content_hash"),
                    "items": json.loads(r.get("items_json") or "[]"),
                    "linked_to_transactions": [
                        l["transaction_id"] for l in tx_links
                    ],
                    "note": "Original file not available on disk; parsed data only.",
                }, indent=2)
                archive_name = f"receipts/_missing-file/{base}.json"
                zf.writestr(archive_name, stub)
                file_hashes[archive_name] = hashlib.sha256(stub.encode()).hexdigest()
                continue

            ext = os.path.splitext(file_path)[1] or ".bin"
            archive_name = f"receipts/{folder}/{base}{ext}"
            try:
                with open(file_path, "rb") as fh:
                    data = fh.read()
                zf.writestr(archive_name, data)
                file_hashes[archive_name] = hashlib.sha256(data).hexdigest()
            except OSError:
                # File on disk but unreadable — write a stub so the
                # accountant sees there should have been a file here.
                stub_name = f"receipts/_missing-file/{base}.txt"
                stub_content = (
                    f"Original file at {file_path} could not be read at export time."
                )
                zf.writestr(stub_name, stub_content)
                file_hashes[stub_name] = hashlib.sha256(stub_content.encode()).hexdigest()

        # 5. data/*.csv — raw CSVs for import into other tools. Kept
        # alongside the workbook for users whose accountant uses CCH /
        # TaxCalc / IRIS where machine-readable input is preferred.
        # 5a. transactions.csv
        tx_csv = io.StringIO()
        writer = csv.DictWriter(tx_csv, fieldnames=[
            "id", "date_iso", "description", "amount", "currency",
            "hmrc_category", "hmrc_category_friendly",
            "sa_box_short", "sa_box_full",
            "hmrc_category_confidence",
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
            meta = category_meta(tx.get("hmrc_category"))
            writer.writerow({
                "id": tx["id"],
                "date_iso": tx.get("date_iso") or "",
                "description": tx.get("description") or "",
                "amount": f"{tx['amount']:.2f}",
                "currency": tx.get("currency") or "GBP",
                "hmrc_category": tx.get("hmrc_category") or "",
                "hmrc_category_friendly": meta["label"],
                "sa_box_short": meta["box_short"],
                "sa_box_full": meta["box_full"],
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
        zf.writestr("data/transactions.csv", tx_csv_bytes)
        file_hashes["data/transactions.csv"] = hashlib.sha256(tx_csv_bytes).hexdigest()

        # 5b. mismatch_report.csv — expenses without a receipt
        mismatch_csv = io.StringIO()
        mw = csv.DictWriter(mismatch_csv, fieldnames=[
            "id", "date_iso", "description", "amount",
            "hmrc_category", "hmrc_category_friendly",
            "receipt_status", "exclusion_reason",
        ])
        mw.writeheader()
        for tx in txs:
            if tx.get("receipt_status") in ("matched", "excluded"):
                continue
            meta = category_meta(tx.get("hmrc_category"))
            mw.writerow({
                "id": tx["id"],
                "date_iso": tx.get("date_iso") or "",
                "description": tx.get("description") or "",
                "amount": f"{tx['amount']:.2f}",
                "hmrc_category": tx.get("hmrc_category") or "",
                "hmrc_category_friendly": meta["label"],
                "receipt_status": tx.get("receipt_status") or "",
                "exclusion_reason": tx.get("exclusion_reason") or "",
            })
        mm_bytes = mismatch_csv.getvalue().encode()
        zf.writestr("data/mismatch_report.csv", mm_bytes)
        file_hashes["data/mismatch_report.csv"] = hashlib.sha256(mm_bytes).hexdigest()

        # 5c. reasoning_log.csv
        reasoning_csv = io.StringIO()
        rw = csv.DictWriter(reasoning_csv, fieldnames=[
            "transaction_id", "date_iso", "description",
            "hmrc_category", "hmrc_category_friendly",
            "hmrc_category_confidence", "hmrc_category_reason",
        ])
        rw.writeheader()
        for tx in txs:
            meta = category_meta(tx.get("hmrc_category"))
            rw.writerow({
                "transaction_id": tx["id"],
                "date_iso": tx.get("date_iso") or "",
                "description": tx.get("description") or "",
                "hmrc_category": tx.get("hmrc_category") or "",
                "hmrc_category_friendly": meta["label"],
                "hmrc_category_confidence": tx.get("hmrc_category_confidence") or "",
                "hmrc_category_reason": tx.get("hmrc_category_reason") or "",
            })
        rl_bytes = reasoning_csv.getvalue().encode()
        zf.writestr("data/reasoning_log.csv", rl_bytes)
        file_hashes["data/reasoning_log.csv"] = hashlib.sha256(rl_bytes).hexdigest()

        # 6. manifest.json — tamper-evident file hashes
        manifest = {
            "generated_at": generated_at,
            "user_email": user_email,
            "client_name": client_name or user_email,
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
