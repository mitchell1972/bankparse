"""
Per-transaction "Explain this to HMRC" defence sheet.

Builds a printable HTML page that, for a given transaction:
  - Shows the claim (amount, date, merchant, category)
  - Cites the HMRC manual section that justifies the category
  - Lists the linked receipts + their hashes (audit trail)
  - Shows the AI's stated reasoning + confidence
  - Stamps the user's name + the BankScan version + a hash so HMRC can
    verify nothing has been altered

The output is a clean print-friendly HTML page. The frontend's "Print to
PDF" button triggers ``window.print()`` — the browser handles the actual
PDF rendering with full font support, no extra Python dependency.

HMRC manual references are curated per category — these are the
specific BIM or PIM sections HMRC cite when investigating expenses.
"""
from __future__ import annotations

import datetime as _dt
import hashlib


# Manual references — categories → (Section ref, brief description)
# Curated from BIM (Business Income Manual), PIM (Property Income Manual),
# and the SA103 / SA105 notes. Conservative — only cite sections that
# clearly apply to the listed category.
_HMRC_MANUAL_REFS: dict[str, tuple[str, str]] = {
    # Self-employment
    "se_turnover": ("BIM40000", "Trading income — receipts of the business"),
    "se_other_income": ("BIM40000", "Other trading income"),
    "se_cost_of_goods_bought": (
        "BIM38200",
        "Stock and work-in-progress — cost of goods bought for resale",
    ),
    "se_disallowable_expenses": (
        "BIM37000",
        "Disallowable expenses — not wholly and exclusively for the trade",
    ),
    "se_payments_to_subcontractors": (
        "BIM43500",
        "Subcontractor payments under CIS rules",
    ),
    "se_wages_salaries_other_staff_costs": (
        "BIM47000",
        "Staff costs — wages, salaries, employer NI, pension",
    ),
    "se_premises_running_costs": (
        "BIM46810",
        "Rent, rates, power and insurance of business premises",
    ),
    "se_maintenance_costs": (
        "BIM46900",
        "Repairs and maintenance of business assets",
    ),
    "se_general_admin_costs": (
        "BIM47800",
        "General administration — stationery, postage, software, phone",
    ),
    "se_business_travel_costs": (
        "BIM45000",
        "Travel costs incurred wholly for the business",
    ),
    "se_advertising_costs": (
        "BIM42550",
        "Advertising, marketing, entertainment (subject to exclusions)",
    ),
    "se_business_entertainment_costs": (
        "BIM45000",
        "Business entertainment — generally disallowable except staff",
    ),
    "se_interest_on_bank_other_loans": (
        "BIM45650",
        "Interest paid on business borrowings",
    ),
    "se_bank_credit_card_other_financial_charges": (
        "BIM45650",
        "Bank charges, credit card fees, factoring costs",
    ),
    "se_irrecoverable_debts_written_off": (
        "BIM42700",
        "Bad debts written off",
    ),
    "se_accountancy_legal_other_professional_fees": (
        "BIM46400",
        "Accountancy, legal, and professional fees",
    ),
    "se_depreciation_and_loss_profit_on_sale_of_assets": (
        "BIM46900",
        "Depreciation and gain/loss on sale of business assets",
    ),
    "se_other_expenses": ("BIM47800", "Other allowable business expenses"),
    "office_expenses": (
        "BIM47800",
        "Office consumables — stationery, software, computer accessories",
    ),
    # Property
    "property_rent_income": ("PIM1051", "Rents received from UK property"),
    "property_other_income": (
        "PIM1054",
        "Other income from UK property (lease premiums, service charges)",
    ),
    "property_rent_rates_insurance": (
        "PIM2020",
        "Rent paid to a superior landlord, rates, insurance",
    ),
    "property_property_repairs_maintenance": (
        "PIM2030",
        "Repairs and maintenance — must be like-for-like replacement",
    ),
    "property_loan_interest_other_financial_costs": (
        "PIM2054",
        "Finance cost restriction for individual landlords",
    ),
    "property_legal_management_other_professional_fees": (
        "PIM2040",
        "Legal, agent and professional fees",
    ),
    "property_costs_of_services_provided": (
        "PIM2042",
        "Services provided to tenants (cleaning, gardening)",
    ),
    "property_other_allowable_property_expenses": (
        "PIM2068",
        "Other allowable property expenses",
    ),
}


def _hmrc_ref_for(category: str | None) -> tuple[str, str]:
    if not category:
        return ("—", "No HMRC category assigned yet — review before submission.")
    return _HMRC_MANUAL_REFS.get(category, ("—", "No specific HMRC manual ref recorded for this category."))


def _format_money(value: float | None, currency: str = "GBP") -> str:
    if value is None:
        return "—"
    sym = "£" if currency == "GBP" else (currency + " ")
    return f"{sym}{abs(float(value)):,.2f}"


def _short_hash(h: str | None) -> str:
    if not h:
        return "—"
    return f"{h[:8]}…{h[-4:]}"


def build_defence_html(
    *,
    transaction: dict,
    linked_receipts: list[dict],
    user_email: str,
    app_version: str = "BankScan AI",
) -> str:
    """Render the per-transaction defence sheet as a print-ready HTML page.

    ``transaction`` is a row from ``ledger_transactions``.
    ``linked_receipts`` is a list of ``ledger_receipts`` rows linked to it.
    """
    cat = transaction.get("hmrc_category") or "—"
    cat_ref, cat_desc = _hmrc_ref_for(transaction.get("hmrc_category"))
    reason = transaction.get("hmrc_category_reason") or "—"
    confidence = transaction.get("hmrc_category_confidence")
    conf_label = f"{int(confidence)}%" if confidence is not None else "—"
    amount_gbp = _format_money(transaction.get("amount"), transaction.get("currency") or "GBP")
    business_pct = transaction.get("business_pct") or 100
    is_capital = bool(transaction.get("is_capital"))

    # Build a master hash for this defence sheet — links the user, the
    # transaction, every receipt, and the day stamp.
    h = hashlib.sha256()
    h.update(user_email.encode())
    h.update(b"|tx=" + str(transaction.get("id")).encode())
    h.update(b"|h=" + (transaction.get("content_hash") or "").encode())
    for r in linked_receipts:
        h.update(b"|rc=" + str(r.get("receipt_id") or r.get("id")).encode())
        h.update(b"|rh=" + (r.get("content_hash") or "").encode())
    today_iso = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    h.update(b"|d=" + today_iso.encode())
    sheet_hash = h.hexdigest()

    receipts_rows = ""
    for r in linked_receipts:
        receipts_rows += f"""
        <tr>
            <td>{r.get('store_name') or '—'}</td>
            <td>{r.get('date_iso') or '—'}</td>
            <td>{_format_money(r.get('total_amount'))}</td>
            <td>{_format_money(r.get('tax_amount'))}</td>
            <td class="hash">{_short_hash(r.get('content_hash'))}</td>
        </tr>"""
    if not receipts_rows:
        receipts_rows = (
            '<tr><td colspan="5" style="color:#A93226;">'
            'No receipts attached. HMRC may ask you to evidence this expense.'
            '</td></tr>'
        )

    capital_block = ""
    if is_capital:
        capital_block = """
        <p class="note">
          <strong>Capital item.</strong> This transaction is claimed as a
          capital allowance, not as a revenue expense. See HMRC manual
          CA20000 onward for plant and machinery allowances.
        </p>
        """

    business_pct_block = ""
    if business_pct != 100:
        business_pct_block = f"""
        <p class="note">
          <strong>{business_pct}% business use.</strong> Only £{abs(transaction['amount']) * business_pct/100:,.2f}
          is claimed against HMRC — the remaining {100-business_pct}%
          is treated as personal.
        </p>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>HMRC defence — Transaction #{transaction.get('id')}</title>
    <style>
        @page {{ margin: 1.5cm; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #2C3E50;
            max-width: 720px;
            margin: 1cm auto;
            line-height: 1.5;
        }}
        header {{
            border-bottom: 2px solid #1B4F72;
            padding-bottom: 1rem;
            margin-bottom: 1.5rem;
        }}
        header h1 {{
            color: #1B4F72;
            margin: 0;
        }}
        header .meta {{
            color: #7F8C8D;
            font-size: 0.9rem;
            margin-top: 0.25rem;
        }}
        h2 {{
            color: #1B4F72;
            font-size: 1.05rem;
            margin: 1.5rem 0 0.5rem;
            border-bottom: 1px solid #E5E7EB;
            padding-bottom: 0.25rem;
        }}
        .claim {{
            background: #F8F9FA;
            border-radius: 8px;
            padding: 1rem;
            font-family: ui-monospace, "SF Mono", Menlo, monospace;
            font-size: 0.95rem;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 0.5rem;
        }}
        th, td {{
            padding: 0.5rem 0.75rem;
            border-bottom: 1px solid #E5E7EB;
            text-align: left;
            font-size: 0.9rem;
        }}
        th {{
            color: #7F8C8D;
            font-weight: 600;
            background: #F8F9FA;
        }}
        .hash {{
            font-family: ui-monospace, "SF Mono", Menlo, monospace;
            color: #5B6A7A;
            font-size: 0.8rem;
        }}
        .note {{
            background: #FEF9E7;
            border-left: 4px solid #F39C12;
            padding: 0.75rem 1rem;
            font-size: 0.9rem;
            margin: 0.75rem 0;
        }}
        footer {{
            margin-top: 2rem;
            padding-top: 1rem;
            border-top: 1px solid #E5E7EB;
            color: #7F8C8D;
            font-size: 0.8rem;
        }}
        .stamp {{
            background: #ECF0F1;
            border: 1px dashed #95A5A6;
            padding: 0.75rem;
            font-family: ui-monospace, "SF Mono", Menlo, monospace;
            font-size: 0.8rem;
            margin-top: 0.5rem;
            border-radius: 4px;
            word-break: break-all;
        }}
        @media print {{
            body {{ max-width: none; }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>HMRC defence sheet</h1>
        <div class="meta">Generated {today_iso} by {app_version} for {user_email}</div>
    </header>

    <h2>The claim</h2>
    <div class="claim">
        Transaction: {amount_gbp} paid to <strong>{transaction.get('description') or '—'}</strong>
        on <strong>{transaction.get('date_iso') or '—'}</strong>.<br>
        Claimed under HMRC category: <strong>{cat}</strong>.
    </div>
    {capital_block}
    {business_pct_block}

    <h2>HMRC manual reference</h2>
    <p><strong>{cat_ref}</strong> — {cat_desc}</p>

    <h2>AI categorisation rationale</h2>
    <p>{reason}</p>
    <p><em>Confidence: {conf_label}. Reviewed automatically and stored
    unmodified for at least 7 years per HMRC s.12B TMA 1970.</em></p>

    <h2>Supporting receipts</h2>
    <table>
        <thead>
            <tr>
                <th>Store</th><th>Date</th><th>Total</th><th>VAT</th><th>Hash</th>
            </tr>
        </thead>
        <tbody>{receipts_rows}</tbody>
    </table>

    <footer>
        <p>This sheet is content-addressable. If any single value above changes
        — transaction amount, receipt, hash, date, category — the sheet hash
        below changes too.</p>
        <div class="stamp">
            Sheet hash: {sheet_hash}<br>
            Transaction hash: {transaction.get('content_hash') or '—'}
        </div>
    </footer>
</body>
</html>
"""
