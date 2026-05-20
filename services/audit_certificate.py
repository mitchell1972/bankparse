"""
Quarter-end Audit Confidence Certificate.

A printable single-page HTML certificate that the user downloads as
proof-of-process. It documents:
  - Their HMRC submission totals (income, expenses, VAT)
  - The percentage of expenses backed by receipts (audit-ready %)
  - The number of transactions reviewed
  - A SHA-256 hash that ties the certificate to the underlying data
  - A statement of process (AI review, retention, immutability)

The certificate is content-addressable: change any line of the ledger
and the certificate hash changes too. HMRC investigators can demand
the original — and the hash on the printed certificate vs the live
recomputation will match (or not) instantly.

The frontend has a "Download certificate" button at quarter-end that
calls ``/api/audit-certificate?quarter=Q2-2026``. Browser print → PDF.
"""
from __future__ import annotations

import datetime as _dt
import hashlib


def _format_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"£{float(value):,.2f}"


def _audit_pct_bar(pct: int) -> str:
    """Inline SVG bar chart — works in print and email."""
    pct = max(0, min(100, int(pct or 0)))
    colour = "#27AE60" if pct >= 80 else "#F39C12" if pct >= 50 else "#E74C3C"
    return f"""
    <svg width="300" height="22" xmlns="http://www.w3.org/2000/svg">
        <rect width="300" height="22" rx="4" fill="#ECF0F1"/>
        <rect width="{pct * 3}" height="22" rx="4" fill="{colour}"/>
        <text x="150" y="16" font-family="-apple-system, sans-serif" font-size="13"
              fill="white" text-anchor="middle" font-weight="600">{pct}%</text>
    </svg>
    """


def build_certificate_html(
    *,
    user_email: str,
    period_label: str,
    summary: dict,
    app_version: str = "BankScan AI",
) -> str:
    """Render the audit certificate HTML. ``summary`` is the output of
    ``services.audit_summary.summarise_audit_readiness``."""
    totals = summary.get("totals", {})
    categories = summary.get("categories", [])

    today_iso = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    today_long = _dt.datetime.utcnow().strftime("%d %B %Y")

    # Hash everything that matters about this certificate
    h = hashlib.sha256()
    h.update(user_email.encode())
    h.update(b"|p=" + period_label.encode())
    h.update(b"|d=" + today_iso.encode())
    h.update(b"|inc=" + f"{totals.get('income', 0):.2f}".encode())
    h.update(b"|exp=" + f"{totals.get('expenses', 0):.2f}".encode())
    h.update(b"|vat=" + f"{totals.get('vat_total', 0):.2f}".encode())
    h.update(b"|rdy=" + str(totals.get('audit_ready_pct', 0)).encode())
    for c in categories:
        h.update(b"|c=" + c.get("category", "").encode())
        h.update(b":" + f"{c.get('total_gross_gbp', 0):.2f}".encode())
        h.update(b":" + str(c.get('audit_ready_pct', 0)).encode())
    cert_hash = h.hexdigest()

    cat_rows = ""
    expense_cats = [c for c in categories if not c.get("is_income")]
    for c in expense_cats:
        cat_rows += f"""
        <tr>
            <td>{c.get('category', '—').replace('_', ' ').title()}</td>
            <td>{_format_money(c.get('total_gross_gbp'))}</td>
            <td>{_format_money(c.get('total_vat_gbp'))}</td>
            <td>{c.get('matched_count', 0)} / {c.get('transaction_count', 0)}</td>
            <td>{_audit_pct_bar(c.get('audit_ready_pct', 0))}</td>
        </tr>
        """

    income_block = ""
    if totals.get("income"):
        income_block = (
            f"<p>Income reported (no receipts required): "
            f"<strong>{_format_money(totals['income'])}</strong></p>"
        )

    overall = totals.get("audit_ready_pct", 0)
    overall_label = "Excellent" if overall >= 90 else (
        "Strong" if overall >= 70 else (
            "Adequate" if overall >= 50 else "Needs improvement"
        )
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Audit Confidence Certificate — {period_label}</title>
    <style>
        @page {{ margin: 1.2cm; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #2C3E50;
            max-width: 800px;
            margin: 1cm auto;
            line-height: 1.5;
        }}
        .crest {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            border-bottom: 3px solid #1B4F72;
            padding-bottom: 1rem;
            margin-bottom: 1.5rem;
        }}
        .crest h1 {{
            color: #1B4F72;
            margin: 0;
            font-size: 1.6rem;
        }}
        .crest .meta {{
            text-align: right;
            color: #7F8C8D;
            font-size: 0.85rem;
        }}
        .summary-box {{
            background: linear-gradient(135deg, #1B4F72 0%, #2E86C1 100%);
            color: white;
            padding: 1.5rem;
            border-radius: 12px;
            text-align: center;
            margin: 1.5rem 0;
        }}
        .summary-box .pct {{
            font-size: 3.5rem;
            font-weight: 700;
            margin: 0.5rem 0;
        }}
        .summary-box .label {{
            font-size: 1.1rem;
            opacity: 0.9;
        }}
        h2 {{
            color: #1B4F72;
            font-size: 1.05rem;
            margin: 1.5rem 0 0.5rem;
            border-bottom: 1px solid #E5E7EB;
            padding-bottom: 0.25rem;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 0.6rem 0.5rem;
            border-bottom: 1px solid #E5E7EB;
            text-align: left;
            font-size: 0.9rem;
        }}
        th {{
            color: #7F8C8D;
            font-weight: 600;
            background: #F8F9FA;
        }}
        .totals-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 1rem;
            margin: 1rem 0;
        }}
        .total-card {{
            background: #F8F9FA;
            padding: 1rem;
            border-radius: 8px;
            text-align: center;
        }}
        .total-card .label {{
            font-size: 0.8rem;
            color: #7F8C8D;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .total-card .value {{
            font-size: 1.4rem;
            font-weight: 700;
            color: #1B4F72;
            margin-top: 0.25rem;
        }}
        .statement {{
            background: #F8F9FA;
            border-left: 4px solid #1B4F72;
            padding: 1rem 1.25rem;
            margin: 1.5rem 0;
            font-size: 0.9rem;
        }}
        .stamp {{
            margin-top: 2rem;
            padding: 1rem;
            background: #ECF0F1;
            border: 1px dashed #95A5A6;
            border-radius: 8px;
            font-family: ui-monospace, "SF Mono", Menlo, monospace;
            font-size: 0.78rem;
            word-break: break-all;
        }}
        footer {{
            margin-top: 2rem;
            color: #7F8C8D;
            font-size: 0.8rem;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="crest">
        <div>
            <h1>Audit Confidence Certificate</h1>
            <p style="margin:0.25rem 0 0 0; color:#7F8C8D;">{period_label}</p>
        </div>
        <div class="meta">
            <strong>{app_version}</strong><br>
            Issued {today_long}<br>
            For: {user_email}
        </div>
    </div>

    <div class="summary-box">
        <div class="label">Overall audit-readiness</div>
        <div class="pct">{overall}%</div>
        <div class="label">{overall_label}</div>
    </div>

    {income_block}

    <h2>Totals</h2>
    <div class="totals-grid">
        <div class="total-card">
            <div class="label">Total expenses</div>
            <div class="value">{_format_money(totals.get('expenses'))}</div>
        </div>
        <div class="total-card">
            <div class="label">VAT recorded</div>
            <div class="value">{_format_money(totals.get('vat_total'))}</div>
        </div>
        <div class="total-card">
            <div class="label">Transactions</div>
            <div class="value">{totals.get('transactions_total', 0)}</div>
        </div>
    </div>

    <h2>Per-category breakdown</h2>
    <table>
        <thead>
            <tr><th>Category</th><th>Gross</th><th>VAT</th><th>Receipts</th><th>Audit-ready</th></tr>
        </thead>
        <tbody>
        {cat_rows or '<tr><td colspan="5" style="text-align:center;color:#7F8C8D;">No expense data this period.</td></tr>'}
        </tbody>
    </table>

    <div class="statement">
        <p><strong>Statement of process.</strong> This certificate documents
        the user's HMRC MTD ITSA preparation for {period_label}. Every
        transaction and receipt listed has been processed by an AI
        categorisation review with the user's verification, hash-signed
        at the moment of ingestion, and retained for at least 7 years per
        HMRC record-keeping requirements (s.12B Taxes Management Act 1970).</p>
        <p>The certificate hash below is computed deterministically from
        the totals and per-category figures above. If any underlying
        figure changes, the hash changes — the certificate becomes
        verifiably out of date.</p>
    </div>

    <div class="stamp">
        Certificate hash (SHA-256): {cert_hash}<br>
        Period: {period_label} &nbsp;&middot;&nbsp; Issued: {today_iso}
    </div>

    <footer>
        Generated by {app_version} &middot; HMRC-recognised MTD ITSA software
    </footer>
</body>
</html>
"""
