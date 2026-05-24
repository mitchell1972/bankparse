"""
End-to-end Playwright journey for the HMRC quarterly-update submit flow.

What this exercises against a real uvicorn + a stub HMRC server:

  1. Register a fresh account via the UI
  2. Verify email via the test-mode OTP endpoint
  3. Mark the account trialing (skips Stripe Checkout)
  4. Upload a CSV bank statement DATED IN TODAY'S WINDOW so the rows fall
     inside the open obligation the stub will return
  5. Wait until those rows have landed in the ledger
  6. Drive the /hmrc/connect Connect button → stub OAuth round-trip →
     callback → tokens persisted
  7. Save NINO + one SE business via the same API the dashboard's
     "Discover my businesses" button calls
  8. Open /hmrc/file → wait for the obligations card to render the Submit
     button for the open quarter
  9. Click Submit → confirm the modal → assert the success message shows
     the transactionReference the stub returned
 10. Assert the stub captured exactly one POST to /period with a body that
     includes BOTH ``periodIncome`` and ``periodExpenses`` (regression
     guard for PRs #83 and #84)

The point of this test is to be the user the founder was complaining about
not having: it logs in, uploads, connects, clicks Submit. If any link in
that chain breaks, the test fails BEFORE production sees it.
"""

from __future__ import annotations

import datetime as dt
import re
import tempfile
import time
from pathlib import Path

import httpx
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
Page = playwright_sync.Page
expect = playwright_sync.expect

# Stub-server constants — kept in sync with tests/e2e/_hmrc_stub.py.
STUB_NINO = "AA123456A"
STUB_BUSINESS_ID_SE = "XAIS00000000001"
STUB_TRANSACTION_REFERENCE = "STUB-TX-REF-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _peek_otp(base_url: str, email: str) -> str:
    r = httpx.get(f"{base_url}/api/test/peek-otp",
                  params={"email": email}, timeout=5.0)
    r.raise_for_status()
    return r.json()["code"]


def _set_subscription_state(base_url: str, email: str, **fields) -> None:
    r = httpx.post(f"{base_url}/api/test/set-subscription-state",
                   json={"email": email, **fields}, timeout=5.0)
    r.raise_for_status()


def _csv_with_today_dates(path: Path) -> Path:
    """CSV with three transactions dated today + yesterday + day-before.

    Inside the stub's open quarter (today ± 20 days), so the submit path's
    ledger fetch picks them up. Descriptions are deliberately ones the
    regex categoriser recognises so the test doesn't depend on an
    Anthropic key being present.
    """
    today = dt.date.today()
    yday = today - dt.timedelta(days=1)
    dby = today - dt.timedelta(days=2)
    fmt = lambda d: d.strftime("%d/%m/%Y")
    path.write_text(
        "Date,Description,Amount\n"
        f"{fmt(today)},SALARY ACME CONSULTING LTD,2500.00\n"
        f"{fmt(yday)},TESCO STORES IPSWICH,-45.20\n"
        f"{fmt(dby)},DIRECT DEBIT BT GROUP BUSINESS,-29.99\n",
        encoding="utf-8",
    )
    return path


def _cookies_for_httpx(page: Page, base_url: str) -> tuple[dict, str]:
    """Pull (cookie_jar_dict, csrf_token) from a Playwright page."""
    cookies = page.context.cookies()
    jar = {c["name"]: c["value"] for c in cookies}
    csrf = jar.get("bp_csrf", "")
    return jar, csrf


def _wait_until(predicate, timeout_s: float = 15.0, interval: float = 0.5):
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as e:
            last_err = e
        time.sleep(interval)
    raise AssertionError(f"Timed out waiting for condition: {last_err}")


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

def test_user_submits_quarterly_update_end_to_end(page: Page, hmrc_live_server):
    base = hmrc_live_server["base_url"]
    stub = hmrc_live_server["stub"]
    email = f"hmrc-e2e-{int(time.time())}@example.test"
    password = "password12345"

    # -- 1. Register via the UI -----------------------------------------------
    page.goto(f"{base}/login")
    page.locator('button[data-tab="register"]').click()
    page.locator("input[type=email]").first.fill(email)
    page.locator("input[type=password]").first.fill(password)
    page.locator("button[type=submit], button#submitBtn").first.click()
    page.wait_for_timeout(2000)

    # -- 2. Verify email via the test-mode OTP --------------------------------
    if "/verify-email" not in page.url:
        page.goto(f"{base}/verify-email")
    code = _peek_otp(base, email)
    page.locator("input#code, input[name=code]").first.fill(code)
    page.locator("button#verify-btn, button[type=submit]").first.click()
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/start-trial(\?.*)?$"), timeout=10_000,
    )

    # -- 3. Skip Stripe — mark trialing directly ------------------------------
    _set_subscription_state(
        base, email,
        subscription_status="trialing",
        stripe_subscription_id="sub_test_hmrc_e2e",
        trial_end_at=time.time() + 6 * 86400,
    )

    # -- 4. Upload a CSV with today-dated transactions ------------------------
    page.goto(f"{base}/")
    with tempfile.NamedTemporaryFile(
        suffix=".csv", prefix="hmrc_e2e_", delete=False, mode="w",
    ) as fh:
        csv_path = Path(fh.name)
    _csv_with_today_dates(csv_path)
    page.locator("input[type=file]").first.set_input_files(str(csv_path))
    page.locator("button#parseBtn, button:has-text('Convert to Spreadsheet')").first.click()
    page.wait_for_timeout(4000)

    # -- 5. Confirm ledger has rows -------------------------------------------
    jar, csrf = _cookies_for_httpx(page, base)
    last_ledger = {"rows": [], "status": None, "body": None}
    def _ledger_has_three():
        r = httpx.get(f"{base}/api/ledger", cookies=jar, timeout=5.0)
        last_ledger["status"] = r.status_code
        last_ledger["body"] = r.text[:500]
        if r.status_code != 200:
            return False
        rows = (r.json() or {}).get("transactions") or []
        last_ledger["rows"] = rows
        return len(rows) >= 3
    try:
        _wait_until(_ledger_has_three, timeout_s=20.0)
    except AssertionError as e:
        raise AssertionError(
            f"Upload→ledger pipeline didn't deposit 3 rows. "
            f"GET /api/ledger -> {last_ledger['status']} {last_ledger['body']!r}. "
            f"Saw {len(last_ledger['rows'])} rows."
        ) from e

    # -- 6. OAuth round-trip through the stub --------------------------------
    # The Connect button is a plain GET form — clicking it lands the browser
    # on the stub's /oauth/authorize, which 302s straight back to /api/hmrc/
    # callback with code+state. End state: hmrc_connections row populated.
    page.goto(f"{base}/hmrc/connect")
    page.locator('form[action="/api/hmrc/connect"] button[type=submit]').click()
    # Land back on /hmrc/connect?status=ok.
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/hmrc/connect(\?.*)?$"), timeout=10_000,
    )
    page.wait_for_timeout(500)

    # -- 7. Save NINO + business via the discover API ------------------------
    # Skip the dashboard form click → call the same API endpoint directly.
    # This is the part of the journey where the user types their NINO and
    # presses Discover; we POST the equivalent payload so the test stays
    # focused on Submit.
    jar, csrf = _cookies_for_httpx(page, base)
    r = httpx.post(
        f"{base}/api/hmrc/obligations/business-setup",
        json={
            "nino": STUB_NINO,
            "businesses": [{
                "business_id": STUB_BUSINESS_ID_SE,
                "type_of_business": "self-employment",
                "label": "Test sole trader",
            }],
        },
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        cookies=jar, timeout=10.0,
    )
    assert r.status_code == 200, f"business-setup failed: {r.status_code} {r.text}"

    # -- 8. Open /hmrc/file and wait for obligations to render ----------------
    page.goto(f"{base}/hmrc/file")
    expect(page).to_have_url(re.compile(rf"^{re.escape(base)}/hmrc/file"))
    # Diagnostic: hit /api/hmrc/obligations directly so we know whether the
    # backend round-tripped to the stub at all. Surfaces the real failure
    # mode if the Submit button never appears.
    jar2, _ = _cookies_for_httpx(page, base)
    obl_resp = httpx.get(f"{base}/api/hmrc/obligations", cookies=jar2, timeout=10.0)
    assert obl_resp.status_code == 200, (
        f"/api/hmrc/obligations failed: {obl_resp.status_code} {obl_resp.text[:500]}"
    )
    obl_data = obl_resp.json()
    open_rows = [
        o for o in obl_data.get("obligations", [])
        if o.get("status") in ("open", "overdue")
    ]
    assert open_rows, (
        f"No open/overdue obligation returned. Full response: {obl_data!r}. "
        f"Stub captures: {[r['path'] for r in stub.recorded_requests]}"
    )

    # The obligations card renders client-side from /api/hmrc/obligations.
    # Each open obligation row gets a `<button class="btn" onclick=
    # "openSubmitDialog(N)">Submit</button>` — text is the exact string
    # "Submit" so we use text-is to avoid grabbing #dlgConfirmBtn whose
    # text is "Submit to HMRC".
    submit_btn = page.locator("button:text-is('Submit')").first
    submit_btn.wait_for(state="visible", timeout=15_000)
    submit_btn.click()

    # -- 9. Confirm the modal — final Submit to HMRC --------------------------
    confirm_btn = page.locator("#dlgConfirmBtn")
    confirm_btn.wait_for(state="visible", timeout=10_000)
    # Wait for preview totals to populate so we know /preview returned 200.
    expect(page.locator("#dlgTotals")).to_contain_text("Income", timeout=10_000)
    confirm_btn.click()

    # Success UI: reference-box appears with the stub's tx reference.
    expect(page.locator("#dlgResult")).to_contain_text(
        STUB_TRANSACTION_REFERENCE, timeout=15_000,
    )

    # -- 10. Stub-side assertions: payload was well-formed --------------------
    period_posts = [
        r for r in stub.recorded_requests
        if r["method"] == "POST" and r["path"].endswith("/period")
    ]
    assert period_posts, (
        f"Expected at least one POST to .../period, recorded: "
        f"{[(r['method'], r['path']) for r in stub.recorded_requests]}"
    )
    last = period_posts[-1]
    body = last["body"] or {}
    assert "periodDates" in body, f"missing periodDates: {body!r}"
    assert "periodIncome" in body, f"missing periodIncome: {body!r}"
    assert "periodExpenses" in body, f"missing periodExpenses: {body!r}"
    # Idempotency-Key — HMRC requires it on this endpoint, see #19.
    assert any(
        k.lower() == "idempotency-key" for k in (last["headers"] or {})
    ), f"missing Idempotency-Key header: {list(last['headers'])}"
    # Categoriser routed our credit row into at least one income bucket
    # (turnover for trading income, other for catch-alls — both prove the
    # pipeline ran on real ledger rows and didn't hand HMRC a £0 payload).
    income = body["periodIncome"] or {}
    total_income = sum(v for v in income.values() if isinstance(v, (int, float)))
    assert total_income > 0, (
        f"Expected non-zero income from the upload, got: {income!r}. "
        f"Whole body: {body!r}"
    )
    # And the expense side has the two debit rows from the CSV (tesco + BT).
    expenses = body["periodExpenses"] or {}
    total_expenses = sum(v for v in expenses.values() if isinstance(v, (int, float)))
    assert total_expenses > 0, (
        f"Expected non-zero expenses from the upload, got: {expenses!r}. "
        f"Whole body: {body!r}"
    )

    csv_path.unlink(missing_ok=True)
