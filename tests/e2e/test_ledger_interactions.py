"""
Full-coverage Playwright E2E for every interactive widget on /ledger.

Drives a real browser through every UI flow the previous test_ledger_journey
skipped:

  1. Snap-and-send camera capture button (setInputFiles on hidden input)
  2. Bulk gallery upload (setInputFiles on hidden input with multiple)
  3. Drag-and-drop orphan receipt → bank-line row
  4. Mileage form submit + assert table row appears
  5. Mileage delete (with browser confirm() dialog)
  6. Anomaly card visibility when baseline + drop exists
  7. Bulk-approve API exercised via fetch from the live browser context
  8. Download accountant ZIP (verify response in same browser session)
  9. Forwarding address displayed in the snap-and-send card
 10. Explain link opens a new tab with HMRC citation

The seeding is done by /api/test/seed-ledger-rich which sets up exactly
the shape this test needs.
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path

import httpx
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
Page = playwright_sync.Page
expect = playwright_sync.expect


TEST_EMAIL = f"ledger-full-{int(time.time())}@example.test"
TEST_PASSWORD = "password12345"


def _peek_otp(base_url: str, email: str) -> str:
    r = httpx.get(f"{base_url}/api/test/peek-otp", params={"email": email}, timeout=5.0)
    r.raise_for_status()
    return r.json()["code"]


def _seed_rich(base_url: str, email: str) -> dict:
    r = httpx.post(
        f"{base_url}/api/test/seed-ledger-rich",
        json={"email": email}, timeout=10.0,
    )
    r.raise_for_status()
    return r.json()


def _register_and_verify(page: Page, base_url: str) -> None:
    """Register a fresh user, click through verify-email, end on /start-trial."""
    page.goto(f"{base_url}/login")
    page.locator('button[data-tab="register"]').click()
    page.locator("input[type=email], input[name=email]").first.fill(TEST_EMAIL)
    page.locator("input[type=password], input[name=password]").first.fill(TEST_PASSWORD)
    page.locator("button[type=submit], button#submitBtn").first.click()
    page.wait_for_timeout(2000)
    if "/verify-email" not in page.url:
        page.goto(f"{base_url}/verify-email")
    code = _peek_otp(base_url, TEST_EMAIL)
    page.locator("input#code, input[name=code]").first.fill(code)
    page.locator("button#verify-btn, button[type=submit]").first.click()
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base_url)}/start-trial(\?.*)?$"),
        timeout=10_000,
    )


def test_full_ledger_interactions(live_server: str, page: Page):
    base_url = live_server

    # ---- Setup ----
    _register_and_verify(page, base_url)
    seeded = _seed_rich(base_url, TEST_EMAIL)
    # tx_matched = matched bank line with receipt attached
    # tx_unmatched = bank line that will be drag-drop target
    # rc_orphan = orphan receipt that will be dragged

    # Land on /ledger — wait for loadAll() to finish
    page.goto(f"{base_url}/ledger")
    # Wait for the hero to render — proves /api/audit-summary returned
    expect(page.locator("#auditPct")).not_to_contain_text("—", timeout=10_000)

    # ----------------------------------------------------------------------
    # 1. Forwarding address visible in snap-and-send card. The local-part
    # is a per-user random token (8 alphanumeric chars), NOT the integer
    # user id — see test_auto_categorise_and_receipt_token.py.
    # ----------------------------------------------------------------------
    expect(page.locator("#forwardingAddress")).to_contain_text(
        "@receipts.bankscanai.com"
    )

    # ----------------------------------------------------------------------
    # 2. Snap-and-send camera button — setInputFiles drives the hidden input
    # ----------------------------------------------------------------------
    fake_receipt = b"%PDF-1.4 fake camera capture"
    page.locator("#cameraInput").set_input_files(
        files=[{
            "name": "snap_camera.jpg",
            "mimeType": "image/jpeg",
            "buffer": fake_receipt,
        }],
    )
    # The status text updates with "Saved X of Y"
    expect(page.locator("#snapStatus")).to_contain_text(
        re.compile(r"Saved \d+ of \d+"), timeout=8_000,
    )

    # ----------------------------------------------------------------------
    # 3. Bulk gallery upload — setInputFiles with multiple files
    # ----------------------------------------------------------------------
    page.locator("#bulkPhotoInput").set_input_files(files=[
        {"name": "bulk_1.jpg", "mimeType": "image/jpeg", "buffer": b"file1"},
        {"name": "bulk_2.jpg", "mimeType": "image/jpeg", "buffer": b"file2"},
        {"name": "bulk_3.pdf", "mimeType": "application/pdf", "buffer": b"file3"},
    ])
    expect(page.locator("#snapStatus")).to_contain_text(
        re.compile(r"Saved 3 of 3"), timeout=10_000,
    )

    # Wait for the auto-reload to finish (loadAll runs 2.5s after upload)
    page.wait_for_timeout(3500)

    # ----------------------------------------------------------------------
    # 4. Mileage form submit + assert row appears
    # ----------------------------------------------------------------------
    import datetime as _dt
    today = _dt.date.today().isoformat()
    page.locator("#mileageDate").fill(today)
    page.locator("#mileageMiles").fill("42")
    page.locator("#mileageFrom").fill("Home")
    page.locator("#mileageTo").fill("Client site")
    page.locator("#mileageVehicle").select_option("car")
    page.locator("#mileageForm button[type=submit]").click()
    # The table now has 1 row (no prior journeys in this fresh user)
    # We don't search by exact text because the table renders dates in YYYY-MM-DD
    expect(page.locator("#mileageLogs table tbody tr")).to_have_count(1, timeout=8_000)
    # The claim column should show £18.90 (42 * 0.45)
    expect(page.locator("#mileageLogs")).to_contain_text("£18.90")

    # ----------------------------------------------------------------------
    # 5. Mileage delete via the ✕ link — handle confirm() dialog
    # ----------------------------------------------------------------------
    page.once("dialog", lambda d: d.accept())
    page.locator('#mileageLogs a[onclick^="deleteMileage"]').first.click()
    expect(page.locator("#mileageLogs")).to_contain_text("No journeys logged yet.")

    # ----------------------------------------------------------------------
    # 6. Anomaly card visibility — seeded baseline + drop should fire
    # ----------------------------------------------------------------------
    # Reload to pick up the seeded baseline
    page.reload()
    expect(page.locator("#auditPct")).not_to_contain_text("—", timeout=10_000)
    # The anomalies card displays when at least one motor-expense anomaly is detected
    # (baseline has 3 quarters of motor expense; current has 0 → flagged).
    expect(page.locator("#anomaliesCard")).to_be_visible(timeout=8_000)
    expect(page.locator("#anomaliesList")).to_contain_text(re.compile(r"motor", re.I))

    # ----------------------------------------------------------------------
    # 7. Drag-and-drop orphan receipt onto the unmatched bank-line row
    # ----------------------------------------------------------------------
    # The orphan card is the draggable; find it by its store name "LonelyStore"
    orphan = page.locator(f'.orphan-card[data-receipt-id="{seeded["rc_orphan"]}"]')
    expect(orphan).to_be_visible()
    target_row = page.locator(f'.tx-row[data-tx-id="{seeded["tx_unmatched"]}"]')
    expect(target_row).to_be_visible()

    orphan.drag_to(target_row)
    # After drop, /api/ledger/link is called and the page reloads via loadAll()
    # The row should now show "matched" status pill
    expect(target_row).to_contain_text(re.compile(r"matched", re.I), timeout=8_000)

    # ----------------------------------------------------------------------
    # 8. Bulk-approve via fetch from inside the browser context
    #    (uses the page's cookies + CSRF — true in-browser API call)
    # ----------------------------------------------------------------------
    approve_result = page.evaluate("""
        async () => {
            const m = document.cookie.match(/(^|;\\s*)bp_csrf=([^;]+)/);
            const csrf = m ? m[2] : '';
            const r = await fetch('/api/ledger/bulk-approve', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify({ hmrc_category: 'se_office_expenses' }),
            });
            return await r.json();
        }
    """)
    assert approve_result.get("status") == "ok"
    assert approve_result.get("approved") >= 3  # the 3 seeded uncategorised

    # ----------------------------------------------------------------------
    # 9. Download accountant ZIP from the browser session
    # ----------------------------------------------------------------------
    zip_resp = page.request.get(f"{base_url}/api/accountant-export?period=Q2-2026")
    assert zip_resp.status == 200
    assert "application/zip" in zip_resp.headers["content-type"]
    assert zip_resp.body()[:2] == b"PK"  # ZIP magic

    # ----------------------------------------------------------------------
    # 10. Explain link opens a defence sheet with the HMRC citation.
    #     Target the MATCHED transaction's row specifically — that's the
    #     one with a known HMRC manual ref (se_general_admin_costs → BIM47800).
    # ----------------------------------------------------------------------
    matched_row = page.locator(f'.tx-row[data-tx-id="{seeded["tx_matched"]}"]')
    with page.context.expect_page() as new_page_info:
        matched_row.locator(".explain-link").click()
    explain = new_page_info.value
    explain.wait_for_load_state()
    body = explain.content()
    assert "HMRC defence sheet" in body
    assert "BIM" in body or "PIM" in body  # at least one HMRC manual ref
    explain.close()
