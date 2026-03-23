"""
Excel Exporter
Generates clean, formatted XLSX spreadsheets from parsed bank statement data.
"""

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
from openpyxl.utils import get_column_letter


# Styling constants
HEADER_FILL = PatternFill(start_color="1B4F72", end_color="1B4F72", fill_type="solid")
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
CREDIT_FONT = Font(name="Calibri", color="1A7A2E", size=10)
DEBIT_FONT = Font(name="Calibri", color="C0392B", size=10)
NORMAL_FONT = Font(name="Calibri", size=10)
TITLE_FONT = Font(name="Calibri", bold=True, size=14, color="1B4F72")
SUMMARY_FONT = Font(name="Calibri", bold=True, size=11)
THIN_BORDER = Border(
    bottom=Side(style="thin", color="D5D8DC"),
)


def export_to_xlsx(data: dict, output_path: str) -> str:
    """
    Export parsed bank statement data to a formatted XLSX file.

    Args:
        data: Dict with 'transactions', 'summary', and 'metadata' keys
        output_path: Path for the output XLSX file

    Returns:
        Path to the created file
    """
    wb = Workbook()

    # ── Transactions Sheet ──
    ws = wb.active
    ws.title = "Transactions"

    # Title row
    ws.merge_cells("A1:G1")
    ws["A1"] = "Bank Statement - Transaction Report"
    ws["A1"].font = TITLE_FONT
    ws.row_dimensions[1].height = 30

    # Summary section
    summary = data.get("summary", {})
    ws["A3"] = "Total Transactions:"
    ws["B3"] = summary.get("total_transactions", 0)
    ws["A3"].font = SUMMARY_FONT

    ws["A4"] = "Total Credits:"
    ws["B4"] = summary.get("total_credits", 0)
    ws["B4"].number_format = '£#,##0.00'
    ws["A4"].font = SUMMARY_FONT
    ws["B4"].font = CREDIT_FONT

    ws["A5"] = "Total Debits:"
    ws["B5"] = summary.get("total_debits", 0)
    ws["B5"].number_format = '£#,##0.00'
    ws["A5"].font = SUMMARY_FONT
    ws["B5"].font = DEBIT_FONT

    ws["A6"] = "Net:"
    ws["B6"] = summary.get("net", 0)
    ws["B6"].number_format = '£#,##0.00'
    ws["A6"].font = SUMMARY_FONT

    # Headers
    headers = ["Date", "Description", "Type", "Debit", "Credit", "Amount", "Balance"]
    header_row = 8

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.row_dimensions[header_row].height = 25

    # Data rows
    transactions = data.get("transactions", [])
    for i, tx in enumerate(transactions):
        row = header_row + 1 + i

        # Date
        ws.cell(row=row, column=1, value=tx["date"]).font = NORMAL_FONT

        # Description
        ws.cell(row=row, column=2, value=tx["description"]).font = NORMAL_FONT

        # Type
        ws.cell(row=row, column=3, value=tx.get("type", "")).font = NORMAL_FONT

        # Debit
        debit_cell = ws.cell(row=row, column=4)
        if tx.get("debit"):
            debit_cell.value = tx["debit"]
            debit_cell.number_format = '£#,##0.00'
            debit_cell.font = DEBIT_FONT
        else:
            debit_cell.font = NORMAL_FONT

        # Credit
        credit_cell = ws.cell(row=row, column=5)
        if tx.get("credit"):
            credit_cell.value = tx["credit"]
            credit_cell.number_format = '£#,##0.00'
            credit_cell.font = CREDIT_FONT
        else:
            credit_cell.font = NORMAL_FONT

        # Amount
        amount_cell = ws.cell(row=row, column=6, value=tx["amount"])
        amount_cell.number_format = '£#,##0.00'
        amount_cell.font = CREDIT_FONT if tx["amount"] >= 0 else DEBIT_FONT

        # Balance
        balance_cell = ws.cell(row=row, column=7)
        if tx.get("balance") is not None:
            balance_cell.value = tx["balance"]
            balance_cell.number_format = '£#,##0.00'
        balance_cell.font = NORMAL_FONT

        # Alternating row border
        for col in range(1, 8):
            ws.cell(row=row, column=col).border = THIN_BORDER

    # Column widths
    col_widths = [12, 45, 12, 14, 14, 14, 14]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    # Freeze panes (freeze header row)
    ws.freeze_panes = f"A{header_row + 1}"

    # Auto-filter
    if transactions:
        last_row = header_row + len(transactions)
        ws.auto_filter.ref = f"A{header_row}:G{last_row}"

    # ── Summary Sheet ──
    ws2 = wb.create_sheet("Summary")

    ws2.merge_cells("A1:D1")
    ws2["A1"] = "Statement Summary"
    ws2["A1"].font = TITLE_FONT

    summary_data = [
        ("Total Transactions", summary.get("total_transactions", 0)),
        ("Total Money In (Credits)", summary.get("total_credits", 0)),
        ("Total Money Out (Debits)", summary.get("total_debits", 0)),
        ("Net Change", summary.get("net", 0)),
    ]

    for i, (label, value) in enumerate(summary_data, 3):
        ws2.cell(row=i, column=1, value=label).font = SUMMARY_FONT
        val_cell = ws2.cell(row=i, column=2, value=value)
        if isinstance(value, (int, float)) and i > 3:
            val_cell.number_format = '£#,##0.00'
        val_cell.font = SUMMARY_FONT

    # Monthly breakdown if enough data
    if transactions:
        monthly = {}
        for tx in transactions:
            month_key = tx["date"][:7]  # YYYY-MM
            if month_key not in monthly:
                monthly[month_key] = {"credits": 0, "debits": 0, "count": 0}
            if tx["amount"] > 0:
                monthly[month_key]["credits"] += tx["amount"]
            else:
                monthly[month_key]["debits"] += tx["amount"]
            monthly[month_key]["count"] += 1

        if monthly:
            row_offset = len(summary_data) + 5
            ws2.cell(row=row_offset, column=1, value="Monthly Breakdown").font = TITLE_FONT

            month_headers = ["Month", "Transactions", "Credits", "Debits", "Net"]
            for col, h in enumerate(month_headers, 1):
                cell = ws2.cell(row=row_offset + 1, column=col, value=h)
                cell.font = HEADER_FONT
                cell.fill = HEADER_FILL

            for i, (month, vals) in enumerate(sorted(monthly.items())):
                r = row_offset + 2 + i
                ws2.cell(row=r, column=1, value=month)
                ws2.cell(row=r, column=2, value=vals["count"])
                ws2.cell(row=r, column=3, value=round(vals["credits"], 2)).number_format = '£#,##0.00'
                ws2.cell(row=r, column=4, value=round(vals["debits"], 2)).number_format = '£#,##0.00'
                net = round(vals["credits"] + vals["debits"], 2)
                ws2.cell(row=r, column=5, value=net).number_format = '£#,##0.00'

    ws2.column_dimensions["A"].width = 25
    ws2.column_dimensions["B"].width = 18
    ws2.column_dimensions["C"].width = 18
    ws2.column_dimensions["D"].width = 18
    ws2.column_dimensions["E"].width = 18

    # Save
    wb.save(output_path)
    return output_path


def export_receipt_to_xlsx(data: dict, output_path: str) -> str:
    """
    Export parsed receipt data to a formatted XLSX file.

    Args:
        data: Dict with 'items', 'totals', and 'metadata' keys
        output_path: Path for the output XLSX file

    Returns:
        Path to the created file
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Receipt Items"

    metadata = data.get("metadata", {})
    totals = data.get("totals", {})
    items = data.get("items", [])

    # Title
    ws.merge_cells("A1:E1")
    ws["A1"] = f"Receipt — {metadata.get('store_name', 'Unknown Store')}"
    ws["A1"].font = TITLE_FONT
    ws.row_dimensions[1].height = 30

    # Receipt info
    ws["A3"] = "Store:"
    ws["B3"] = metadata.get("store_name", "")
    ws["A3"].font = SUMMARY_FONT
    ws["B3"].font = NORMAL_FONT

    ws["A4"] = "Date:"
    ws["B4"] = metadata.get("date", "")
    ws["A4"].font = SUMMARY_FONT
    ws["B4"].font = NORMAL_FONT

    ws["A5"] = "Items:"
    ws["B5"] = metadata.get("item_count", len(items))
    ws["A5"].font = SUMMARY_FONT
    ws["B5"].font = NORMAL_FONT

    # Item headers
    headers = ["#", "Item", "Qty", "Unit Price", "Total"]
    header_row = 7

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=header_row, column=col, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    ws.row_dimensions[header_row].height = 25

    # Item rows
    for i, item in enumerate(items):
        row = header_row + 1 + i

        ws.cell(row=row, column=1, value=i + 1).font = NORMAL_FONT
        ws.cell(row=row, column=1).alignment = Alignment(horizontal="center")

        ws.cell(row=row, column=2, value=item["description"]).font = NORMAL_FONT

        ws.cell(row=row, column=3, value=item["quantity"]).font = NORMAL_FONT
        ws.cell(row=row, column=3).alignment = Alignment(horizontal="center")

        unit_cell = ws.cell(row=row, column=4, value=item["unit_price"])
        unit_cell.number_format = '£#,##0.00'
        unit_cell.font = NORMAL_FONT

        total_cell = ws.cell(row=row, column=5, value=item["total_price"])
        total_cell.number_format = '£#,##0.00'
        total_cell.font = NORMAL_FONT

        for col in range(1, 6):
            ws.cell(row=row, column=col).border = THIN_BORDER

    # Totals section
    totals_start = header_row + len(items) + 2

    TOTALS_FILL = PatternFill(start_color="EBF5FB", end_color="EBF5FB", fill_type="solid")

    total_rows = []
    if "subtotal" in totals:
        total_rows.append(("Subtotal", totals["subtotal"]))
    if "discount" in totals:
        total_rows.append(("Discount", totals["discount"]))
    if "tax" in totals:
        total_rows.append(("VAT / Tax", totals["tax"]))
    if "total" in totals:
        total_rows.append(("TOTAL", totals["total"]))
    if "payment" in totals:
        total_rows.append(("Payment", totals["payment"]))
    if "change" in totals:
        total_rows.append(("Change", totals["change"]))

    for i, (label, value) in enumerate(total_rows):
        r = totals_start + i
        label_cell = ws.cell(row=r, column=4, value=label)
        label_cell.font = SUMMARY_FONT
        label_cell.alignment = Alignment(horizontal="right")
        label_cell.fill = TOTALS_FILL

        val_cell = ws.cell(row=r, column=5, value=value)
        val_cell.number_format = '£#,##0.00'
        val_cell.fill = TOTALS_FILL
        if label == "TOTAL":
            val_cell.font = Font(name="Calibri", bold=True, size=12, color="1B4F72")
        else:
            val_cell.font = SUMMARY_FONT

    # Column widths
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 8
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 14

    # Freeze header
    ws.freeze_panes = f"A{header_row + 1}"

    # Auto-filter
    if items:
        last_row = header_row + len(items)
        ws.auto_filter.ref = f"A{header_row}:E{last_row}"

    wb.save(output_path)
    return output_path


def export_bulk_receipts_to_xlsx(bulk_result: dict, output_path: str) -> str:
    """
    Export combined bulk receipt data to a formatted XLSX file with two sheets.

    Sheet 1 — "All Expenses": All items from all receipts grouped by store.
    Sheet 2 — "Summary": One row per receipt with totals.

    Args:
        bulk_result: Dict from parse_receipts_bulk with 'receipts', 'combined_items',
                     'grand_total', 'receipt_count', 'total_items'
        output_path: Path for the output XLSX file

    Returns:
        Path to the created file
    """
    wb = Workbook()

    # Styling
    PURPLE_FILL = PatternFill(start_color="8E44AD", end_color="8E44AD", fill_type="solid")
    PURPLE_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    ALT_ROW_FILL = PatternFill(start_color="F5EEF8", end_color="F5EEF8", fill_type="solid")
    GRAND_TOTAL_FILL = PatternFill(start_color="D7BDE2", end_color="D7BDE2", fill_type="solid")
    GRAND_TOTAL_FONT = Font(name="Calibri", bold=True, size=12, color="4A235A")

    # -- Sheet 1: All Expenses --
    ws1 = wb.active
    ws1.title = "All Expenses"

    # Title
    ws1.merge_cells("A1:F1")
    ws1["A1"] = f"Combined Receipt Expenses — {bulk_result['receipt_count']} receipts"
    ws1["A1"].font = Font(name="Calibri", bold=True, size=14, color="8E44AD")
    ws1.row_dimensions[1].height = 30

    # Headers
    headers = ["Store", "Date", "Item", "Qty", "Unit Price", "Total"]
    header_row = 3
    for col, header in enumerate(headers, 1):
        cell = ws1.cell(row=header_row, column=col, value=header)
        cell.font = PURPLE_FONT
        cell.fill = PURPLE_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[header_row].height = 25

    # Data rows — items grouped by store then date
    combined_items = bulk_result.get("combined_items", [])
    sorted_items = sorted(combined_items, key=lambda x: (x.get("store", ""), x.get("date") or ""))

    for i, item in enumerate(sorted_items):
        row = header_row + 1 + i
        use_alt = i % 2 == 1

        store_cell = ws1.cell(row=row, column=1, value=item.get("store", ""))
        store_cell.font = NORMAL_FONT
        if use_alt:
            store_cell.fill = ALT_ROW_FILL

        date_cell = ws1.cell(row=row, column=2, value=item.get("date", ""))
        date_cell.font = NORMAL_FONT
        if use_alt:
            date_cell.fill = ALT_ROW_FILL

        desc_cell = ws1.cell(row=row, column=3, value=item.get("description", ""))
        desc_cell.font = NORMAL_FONT
        if use_alt:
            desc_cell.fill = ALT_ROW_FILL

        qty_cell = ws1.cell(row=row, column=4, value=item.get("quantity", 1))
        qty_cell.font = NORMAL_FONT
        qty_cell.alignment = Alignment(horizontal="center")
        if use_alt:
            qty_cell.fill = ALT_ROW_FILL

        unit_cell = ws1.cell(row=row, column=5, value=item.get("unit_price", 0))
        unit_cell.number_format = '£#,##0.00'
        unit_cell.font = NORMAL_FONT
        if use_alt:
            unit_cell.fill = ALT_ROW_FILL

        total_cell = ws1.cell(row=row, column=6, value=item.get("total_price", 0))
        total_cell.number_format = '£#,##0.00'
        total_cell.font = NORMAL_FONT
        if use_alt:
            total_cell.fill = ALT_ROW_FILL

        for c in range(1, 7):
            ws1.cell(row=row, column=c).border = THIN_BORDER

    # Grand total row
    if sorted_items:
        grand_row = header_row + len(sorted_items) + 2
        label_cell = ws1.cell(row=grand_row, column=5, value="GRAND TOTAL")
        label_cell.font = GRAND_TOTAL_FONT
        label_cell.fill = GRAND_TOTAL_FILL
        label_cell.alignment = Alignment(horizontal="right")

        total_val_cell = ws1.cell(row=grand_row, column=6, value=bulk_result.get("grand_total", 0))
        total_val_cell.number_format = '£#,##0.00'
        total_val_cell.font = GRAND_TOTAL_FONT
        total_val_cell.fill = GRAND_TOTAL_FILL

    # Column widths
    ws1.column_dimensions["A"].width = 22
    ws1.column_dimensions["B"].width = 14
    ws1.column_dimensions["C"].width = 40
    ws1.column_dimensions["D"].width = 8
    ws1.column_dimensions["E"].width = 14
    ws1.column_dimensions["F"].width = 14

    # Freeze panes
    ws1.freeze_panes = f"A{header_row + 1}"

    # Auto-filter
    if sorted_items:
        last_row = header_row + len(sorted_items)
        ws1.auto_filter.ref = f"A{header_row}:F{last_row}"

    # -- Sheet 2: Summary --
    ws2 = wb.create_sheet("Summary")

    ws2.merge_cells("A1:D1")
    ws2["A1"] = "Receipt Summary"
    ws2["A1"].font = Font(name="Calibri", bold=True, size=14, color="8E44AD")
    ws2.row_dimensions[1].height = 30

    # Summary headers
    summary_headers = ["Store", "Date", "Items Count", "Receipt Total"]
    summary_header_row = 3
    for col, header in enumerate(summary_headers, 1):
        cell = ws2.cell(row=summary_header_row, column=col, value=header)
        cell.font = PURPLE_FONT
        cell.fill = PURPLE_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[summary_header_row].height = 25

    # One row per receipt
    receipts = bulk_result.get("receipts", [])
    for i, receipt in enumerate(receipts):
        row = summary_header_row + 1 + i
        use_alt = i % 2 == 1

        store_cell = ws2.cell(row=row, column=1, value=receipt.get("store_name", "Unknown"))
        store_cell.font = NORMAL_FONT
        if use_alt:
            store_cell.fill = ALT_ROW_FILL

        date_cell = ws2.cell(row=row, column=2, value=receipt.get("date", ""))
        date_cell.font = NORMAL_FONT
        if use_alt:
            date_cell.fill = ALT_ROW_FILL

        count_cell = ws2.cell(row=row, column=3, value=len(receipt.get("items", [])))
        count_cell.font = NORMAL_FONT
        count_cell.alignment = Alignment(horizontal="center")
        if use_alt:
            count_cell.fill = ALT_ROW_FILL

        total_cell = ws2.cell(row=row, column=4, value=receipt.get("total", 0))
        total_cell.number_format = '£#,##0.00'
        total_cell.font = NORMAL_FONT
        if use_alt:
            total_cell.fill = ALT_ROW_FILL

        for c in range(1, 5):
            ws2.cell(row=row, column=c).border = THIN_BORDER

    # Grand total at bottom of summary
    if receipts:
        grand_row = summary_header_row + len(receipts) + 2
        label_cell = ws2.cell(row=grand_row, column=3, value="GRAND TOTAL")
        label_cell.font = GRAND_TOTAL_FONT
        label_cell.fill = GRAND_TOTAL_FILL
        label_cell.alignment = Alignment(horizontal="right")

        total_val_cell = ws2.cell(row=grand_row, column=4, value=bulk_result.get("grand_total", 0))
        total_val_cell.number_format = '£#,##0.00'
        total_val_cell.font = GRAND_TOTAL_FONT
        total_val_cell.fill = GRAND_TOTAL_FILL

    ws2.column_dimensions["A"].width = 25
    ws2.column_dimensions["B"].width = 14
    ws2.column_dimensions["C"].width = 14
    ws2.column_dimensions["D"].width = 16

    wb.save(output_path)
    return output_path


def export_bulk_statements_to_xlsx(bulk_result: dict, output_path: str) -> str:
    """
    Export combined bulk statement data to a formatted XLSX file.

    Sheet 1 — "All Transactions": All transactions from all statements.
    Sheet 2 — "Summary": One row per statement with totals.
    """
    wb = Workbook()

    PRIMARY_FILL = PatternFill(start_color="1B4F72", end_color="1B4F72", fill_type="solid")
    PRIMARY_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    CREDIT_FONT = Font(name="Calibri", color="27AE60", size=11)
    DEBIT_FONT = Font(name="Calibri", color="E74C3C", size=11)
    ALT_FILL = PatternFill(start_color="EBF5FB", end_color="EBF5FB", fill_type="solid")
    TOTAL_FILL = PatternFill(start_color="D4E6F1", end_color="D4E6F1", fill_type="solid")
    TOTAL_FONT_STYLE = Font(name="Calibri", bold=True, size=12, color="1B4F72")

    ws1 = wb.active
    ws1.title = "All Transactions"

    count = bulk_result.get("summary", {}).get("total_transactions", 0)
    ws1.merge_cells("A1:G1")
    ws1["A1"] = f"Combined Bank Statements — {bulk_result.get('statement_count', 0)} statements, {count} transactions"
    ws1["A1"].font = Font(name="Calibri", bold=True, size=14, color="1B4F72")
    ws1.row_dimensions[1].height = 30

    headers = ["Source", "Date", "Description", "Type", "Amount", "Balance", "Bank"]
    header_row = 3
    for col, header in enumerate(headers, 1):
        cell = ws1.cell(row=header_row, column=col, value=header)
        cell.font = PRIMARY_FONT
        cell.fill = PRIMARY_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[header_row].height = 25

    txs = bulk_result.get("all_transactions", [])
    for i, tx in enumerate(txs):
        row = header_row + 1 + i
        use_alt = i % 2 == 1

        ws1.cell(row=row, column=1, value=tx.get("source", "")).font = NORMAL_FONT
        ws1.cell(row=row, column=2, value=tx.get("date", "")).font = NORMAL_FONT
        ws1.cell(row=row, column=3, value=tx.get("description", "")).font = NORMAL_FONT
        ws1.cell(row=row, column=4, value=tx.get("type", "")).font = NORMAL_FONT

        amt = tx.get("amount", 0)
        amt_cell = ws1.cell(row=row, column=5, value=amt)
        amt_cell.number_format = '£#,##0.00'
        amt_cell.font = CREDIT_FONT if amt >= 0 else DEBIT_FONT

        bal = tx.get("balance")
        bal_cell = ws1.cell(row=row, column=6, value=bal if bal is not None else "")
        if bal is not None:
            bal_cell.number_format = '£#,##0.00'
        bal_cell.font = NORMAL_FONT

        ws1.cell(row=row, column=7, value=tx.get("bank", "")).font = NORMAL_FONT

        if use_alt:
            for c in range(1, 8):
                ws1.cell(row=row, column=c).fill = ALT_FILL
        for c in range(1, 8):
            ws1.cell(row=row, column=c).border = THIN_BORDER

    if txs:
        summary = bulk_result.get("summary", {})
        sr = header_row + len(txs) + 2
        for label, key, color in [("CREDITS", "total_credits", "27AE60"), ("DEBITS", "total_debits", "E74C3C"), ("NET", "net", "1B4F72")]:
            ws1.cell(row=sr, column=3, value=label).font = TOTAL_FONT_STYLE
            ws1.cell(row=sr, column=3).fill = TOTAL_FILL
            v_cell = ws1.cell(row=sr, column=5, value=summary.get(key, 0))
            v_cell.number_format = '£#,##0.00'
            v_cell.font = Font(name="Calibri", bold=True, size=12, color=color)
            v_cell.fill = TOTAL_FILL
            sr += 1

    ws1.column_dimensions["A"].width = 25
    ws1.column_dimensions["B"].width = 14
    ws1.column_dimensions["C"].width = 45
    ws1.column_dimensions["D"].width = 10
    ws1.column_dimensions["E"].width = 14
    ws1.column_dimensions["F"].width = 14
    ws1.column_dimensions["G"].width = 18
    ws1.freeze_panes = f"A{header_row + 1}"
    if txs:
        ws1.auto_filter.ref = f"A{header_row}:G{header_row + len(txs)}"

    ws2 = wb.create_sheet("Summary")
    ws2.merge_cells("A1:E1")
    ws2["A1"] = "Statement Summary"
    ws2["A1"].font = Font(name="Calibri", bold=True, size=14, color="1B4F72")
    ws2.row_dimensions[1].height = 30

    s_headers = ["File", "Bank", "Transactions", "Credits", "Debits"]
    s_row = 3
    for col, h in enumerate(s_headers, 1):
        cell = ws2.cell(row=s_row, column=col, value=h)
        cell.font = PRIMARY_FONT
        cell.fill = PRIMARY_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for i, stmt in enumerate(bulk_result.get("statements", [])):
        row = s_row + 1 + i
        s = stmt.get("summary", {})
        ws2.cell(row=row, column=1, value=stmt.get("source", "")).font = NORMAL_FONT
        ws2.cell(row=row, column=2, value=stmt.get("bank_name", "")).font = NORMAL_FONT
        ws2.cell(row=row, column=3, value=stmt.get("transaction_count", 0)).font = NORMAL_FONT
        cr = ws2.cell(row=row, column=4, value=s.get("total_credits", 0))
        cr.number_format = '£#,##0.00'
        cr.font = CREDIT_FONT
        dr = ws2.cell(row=row, column=5, value=s.get("total_debits", 0))
        dr.number_format = '£#,##0.00'
        dr.font = DEBIT_FONT
        if i % 2 == 1:
            for c in range(1, 6):
                ws2.cell(row=row, column=c).fill = ALT_FILL
        for c in range(1, 6):
            ws2.cell(row=row, column=c).border = THIN_BORDER

    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 20
    ws2.column_dimensions["C"].width = 14
    ws2.column_dimensions["D"].width = 16
    ws2.column_dimensions["E"].width = 16

    wb.save(output_path)
    return output_path
