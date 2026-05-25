"""
End-to-end Playwright journey for a realistic BankScan MTD ITSA user.

This is what an actual customer does in one session:

  1. Sign up + verify email + start their trial
  2. Upload a CSV bank statement (sole-trader fees + rent receipts +
     business + property expenses — one ledger, two income streams)
  3. Connect to HMRC sandbox; register BOTH a self-employment business
     and a UK property business against their NINO
  4. Open /hmrc/file; expect open obligations for both businesses
  5. **Trust check** — open the SE Submit modal, capture preview totals,
     submit, then assert the wire payload sums match the modal numbers.
     If those diverge, the user can't trust the app and won't file with it.
  6. Submit the UK property quarterly update
  7. **Correction loop** — override one transaction's category via the
     dashboard's correct-category endpoint, request another preview, and
     assert the corrected category bucket changed. Without this, the
     "your AI got it wrong, fix it" pitch is hollow.
  8. **Returning user** — log out, log back in, navigate to /hmrc/file,
     confirm both submissions are still on file. This is the morning-
     after-filing trust moment.

Failure at any step fails the journey loudly with a step-tagged message.

Stub: ``tests/e2e/_hmrc_stub.py`` (canonical HMRC MTD wire shapes).
Fixture: ``hmrc_live_server`` in ``tests/e2e/conftest.py`` (uvicorn + stub).
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

from tests.e2e._hmrc_stub import (
    STUB_BUSINESS_ID_PROP,
    STUB_BUSINESS_ID_SE,
    STUB_CALCULATION_ID,
    STUB_EOPS_REFERENCE,
    STUB_FINAL_DECL_REFERENCE,
    STUB_NINO,
    STUB_TOTAL_TAX_AMOUNT,
    STUB_TRANSACTION_REFERENCE,
    STUB_TRANSACTION_REFERENCE_PROP,
)


# ---------------------------------------------------------------------------
# Test fixtures (data, not pytest fixtures)
# ---------------------------------------------------------------------------

def _make_csv(path: Path) -> Path:
    """A realistic CSV — sole trader consulting + landlord rent + expenses
    on both sides. Dates are today/yesterday/etc so every row falls inside
    the stub's open quarter (today - 30 → today).

    Descriptions are deliberately ones the regex categoriser routes
    correctly so the test doesn't depend on an Anthropic API key being
    present in the local environment.
    """
    today = dt.date.today()
    rows = [
        # SE income
        (today - dt.timedelta(days=2),  "INVOICE 1042 ACME CONSULTING LTD", 2400.00),
        (today - dt.timedelta(days=8),  "FEE FROM HELMSLEY PARTNERS",        1500.00),
        # Property income — rent
        (today - dt.timedelta(days=1),  "RENT RECEIVED TOWER MILL LANE",      950.00),
        (today - dt.timedelta(days=15), "RENT RECEIVED 14 FOUNDRY STREET",   1300.00),
        # SE expenses
        (today - dt.timedelta(days=3),  "TRAIN LONDON LIVERPOOL ST IPSWICH",  -42.30),
        (today - dt.timedelta(days=4),  "HOTEL PREMIER INN MANCHESTER",       -89.00),
        # Property expenses
        (today - dt.timedelta(days=5),  "PLUMBER CALLOUT FLAT 1 LEAK",       -185.00),
        (today - dt.timedelta(days=6),  "LETTING AGENT FEE FOUNDRY ST",      -156.00),
    ]
    body = "Date,Description,Amount\n" + "".join(
        f"{d.strftime('%d/%m/%Y')},{desc},{amt:.2f}\n" for d, desc, amt in rows
    )
    path.write_text(body, encoding="utf-8")
    return path


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


def _cookies_for_httpx(page: Page) -> tuple[dict, str]:
    cookies = page.context.cookies()
    jar = {c["name"]: c["value"] for c in cookies}
    return jar, jar.get("bp_csrf", "")


def _wait_until(predicate, timeout_s: float = 20.0, interval: float = 0.5):
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as e:
            last_err = e
        time.sleep(interval)
    raise AssertionError(f"Timed out waiting for condition: {last_err}")


def _ui_register(page: Page, base: str, email: str, password: str) -> None:
    page.goto(f"{base}/login")
    page.locator('button[data-tab="register"]').click()
    page.locator("input[type=email]").first.fill(email)
    page.locator("input[type=password]").first.fill(password)
    page.locator("button[type=submit], button#submitBtn").first.click()
    page.wait_for_timeout(2000)


def _ui_login(page: Page, base: str, email: str, password: str) -> None:
    """Sign-in via the /login page's Sign-In tab (the 'returning user' flow)."""
    page.goto(f"{base}/login")
    page.locator('button[data-tab="signin"]').click()
    page.locator("input[type=email]").first.fill(email)
    page.locator("input[type=password]").first.fill(password)
    page.locator("button[type=submit], button#submitBtn").first.click()
    page.wait_for_timeout(2000)


def _parse_gbp(text: str) -> float:
    """Pull a GBP amount out of a modal label like '£2,500.00'.

    Permissive — strips £, commas, whitespace. Returns 0.0 if no digit
    found so the assert that follows surfaces the actual modal text in
    the diff."""
    m = re.search(r"([\d,]+\.\d{2})", text)
    if not m:
        return 0.0
    return float(m.group(1).replace(",", ""))


def _sum_amounts(obj: dict) -> float:
    return round(sum(v for v in (obj or {}).values()
                     if isinstance(v, (int, float))), 2)


# ---------------------------------------------------------------------------
# The full journey
# ---------------------------------------------------------------------------

def test_mtd_user_full_journey(page: Page, hmrc_live_server):
    """One mega-test on purpose: a real customer doesn't do `pytest -x`,
    they go through this sequence in one sitting. If anything breaks, the
    user can't file."""
    base = hmrc_live_server["base_url"]
    stub = hmrc_live_server["stub"]
    email = f"mtd-user-{int(time.time())}@example.test"
    password = "password12345"

    # ====================================================================
    # ACT 1 — Sign up, verify email, mark trialing
    # ====================================================================
    _ui_register(page, base, email, password)

    if "/verify-email" not in page.url:
        page.goto(f"{base}/verify-email")
    code = _peek_otp(base, email)
    page.locator("input#code, input[name=code]").first.fill(code)
    page.locator("button#verify-btn, button[type=submit]").first.click()
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/start-trial(\?.*)?$"), timeout=10_000,
    )

    _set_subscription_state(
        base, email,
        subscription_status="trialing",
        stripe_subscription_id="sub_test_full_journey",
        trial_end_at=time.time() + 6 * 86400,
    )

    # ====================================================================
    # ACT 2 — Upload a realistic CSV; wait for ledger ingest
    # ====================================================================
    page.goto(f"{base}/")
    with tempfile.NamedTemporaryFile(
        suffix=".csv", prefix="mtd_e2e_", delete=False, mode="w",
    ) as fh:
        csv_path = Path(fh.name)
    _make_csv(csv_path)
    page.locator("input[type=file]").first.set_input_files(str(csv_path))
    page.locator(
        "button#parseBtn, button:has-text('Convert to Spreadsheet')"
    ).first.click()
    page.wait_for_timeout(4000)

    jar, csrf = _cookies_for_httpx(page)
    last_ledger = {"rows": [], "status": None, "body": None}
    def _ledger_has_eight():
        r = httpx.get(f"{base}/api/ledger", cookies=jar, timeout=5.0)
        last_ledger["status"] = r.status_code
        last_ledger["body"] = r.text[:500]
        if r.status_code != 200:
            return False
        rows = (r.json() or {}).get("transactions") or []
        last_ledger["rows"] = rows
        return len(rows) >= 8
    try:
        _wait_until(_ledger_has_eight, timeout_s=25.0)
    except AssertionError as e:
        raise AssertionError(
            f"[ACT 2] CSV→ledger pipeline only deposited "
            f"{len(last_ledger['rows'])} rows. "
            f"/api/ledger -> {last_ledger['status']} {last_ledger['body']!r}"
        ) from e

    # ====================================================================
    # ACT 3 — Connect to HMRC sandbox (OAuth round-trip via stub) + save
    #         BOTH an SE and a property business against the NINO
    # ====================================================================
    page.goto(f"{base}/hmrc/connect")
    page.locator(
        'form[action="/api/hmrc/connect"] button[type=submit]'
    ).click()
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/hmrc/connect(\?.*)?$"), timeout=10_000,
    )
    page.wait_for_timeout(500)

    jar, csrf = _cookies_for_httpx(page)
    setup = httpx.post(
        f"{base}/api/hmrc/obligations/business-setup",
        json={
            "nino": STUB_NINO,
            "businesses": [
                {"business_id": STUB_BUSINESS_ID_SE,
                 "type_of_business": "self-employment",
                 "label": "Mitoba sole trader"},
                {"business_id": STUB_BUSINESS_ID_PROP,
                 "type_of_business": "property",
                 "label": "Ipswich SA portfolio"},
            ],
        },
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        cookies=jar, timeout=10.0,
    )
    assert setup.status_code == 200, (
        f"[ACT 3] business-setup failed: {setup.status_code} {setup.text}"
    )

    # ====================================================================
    # ACT 4 — /hmrc/file renders open obligations for both businesses
    # ====================================================================
    page.goto(f"{base}/hmrc/file")
    expect(page).to_have_url(re.compile(rf"^{re.escape(base)}/hmrc/file"))

    obl = httpx.get(f"{base}/api/hmrc/obligations",
                    cookies=jar, timeout=10.0).json()
    open_rows = [o for o in obl.get("obligations", [])
                 if o.get("status") in ("open", "overdue")]
    assert len(open_rows) >= 2, (
        f"[ACT 4] Expected open obligations for SE and property, got: "
        f"{[(o.get('business_type'), o.get('status')) for o in obl.get('obligations', [])]}"
    )
    types_open = {o["business_type"] for o in open_rows}
    assert {"self-employment", "property"}.issubset(types_open), (
        f"[ACT 4] Missing one of SE/property in open rows: {types_open}"
    )

    # ====================================================================
    # ACT 5 — Trust check: SE preview totals must match the wire payload
    # ====================================================================
    # Click the first Submit button (the obligation rows render in sorted
    # order; SE row appears first because both are 'open' and ordered by
    # due date which is identical → stable insertion order).
    submit_btns = page.locator("button:text-is('Submit')")
    submit_btns.first.wait_for(state="visible", timeout=15_000)
    submit_btns.first.click()

    # Modal renders; #dlgTotals shows "Income (gross) £X" / "Expenses (gross) £Y"
    expect(page.locator("#dlgTotals")).to_contain_text("Income", timeout=10_000)
    modal_text = page.locator("#dlgTotals").inner_text()
    # Extract every "£N.NN" — the modal shows three: income, expenses, net.
    money_matches = re.findall(r"£[\d,]+\.\d{2}", modal_text)
    assert len(money_matches) >= 3, (
        f"[ACT 5] Expected at least 3 GBP figures in the modal totals, got: "
        f"{money_matches!r} (text={modal_text!r})"
    )
    modal_income = _parse_gbp(money_matches[0])
    modal_expenses = _parse_gbp(money_matches[1])
    assert modal_income > 0 and modal_expenses > 0, (
        f"[ACT 5] Preview must show non-zero income AND expenses. Got "
        f"income={modal_income} expenses={modal_expenses}. "
        f"Modal text: {modal_text!r}"
    )

    # Now click the modal's Submit-to-HMRC and assert wire payload sums
    # match the modal display exactly. If those diverge, the customer is
    # being lied to about what we're sending in their name.
    requests_before = len(stub.recorded_requests)
    page.locator("#dlgConfirmBtn").click()
    expect(page.locator("#dlgResult")).to_contain_text(
        STUB_TRANSACTION_REFERENCE, timeout=15_000,
    )

    new_posts = [
        r for r in stub.recorded_requests[requests_before:]
        if r["method"] == "POST" and r["path"].endswith("/period")
    ]
    assert new_posts, (
        f"[ACT 5] No /period POST captured after SE submit. "
        f"Captured: {[(r['method'], r['path']) for r in stub.recorded_requests[requests_before:]]}"
    )
    se_post = new_posts[-1]
    body = se_post["body"] or {}
    for required in ("periodDates", "periodIncome", "periodExpenses"):
        assert required in body, f"[ACT 5] SE wire body missing {required}: {body!r}"
    assert any(k.lower() == "idempotency-key" for k in (se_post["headers"] or {})), (
        f"[ACT 5] SE submit missing Idempotency-Key header: {list(se_post['headers'])}"
    )
    wire_income = _sum_amounts(body["periodIncome"])
    wire_expenses = _sum_amounts(body["periodExpenses"])
    assert wire_income == modal_income, (
        f"[ACT 5] Trust break — modal showed Income £{modal_income} but "
        f"we POSTed £{wire_income} to HMRC. Body: {body['periodIncome']!r}"
    )
    assert wire_expenses == modal_expenses, (
        f"[ACT 5] Trust break — modal showed Expenses £{modal_expenses} "
        f"but we POSTed £{wire_expenses} to HMRC. Body: {body['periodExpenses']!r}"
    )

    # Close the success modal so the next Submit click works.
    page.evaluate("document.getElementById('submitDialog').close();")
    page.wait_for_timeout(500)

    # ====================================================================
    # ACT 6 — Submit the property quarterly update too
    # ====================================================================
    # Refresh obligations so the row that was just submitted (SE) has
    # status-rebuilt; the property row should still be Open.
    page.reload()
    submit_btns = page.locator("button:text-is('Submit')")
    submit_btns.first.wait_for(state="visible", timeout=15_000)

    # Walk every visible Submit button and click the one whose row mentions
    # property. file.html renders each row as a `.obligation` div whose
    # text includes the business label ("Property — UK" or "Ipswich SA
    # portfolio") and the period pill.
    n = submit_btns.count()
    seen_rows: list[str] = []
    clicked = False
    for i in range(n):
        btn = submit_btns.nth(i)
        row_text = btn.evaluate("el => el.closest('.obligation')?.innerText || ''")
        seen_rows.append(row_text)
        if (
            "Ipswich" in row_text
            or "Property" in row_text
            or "property" in row_text
        ):
            btn.click()
            clicked = True
            break
    assert clicked, (
        f"[ACT 6] No property obligation row found among {n} Submit "
        f"button(s). Row texts: {seen_rows!r}"
    )

    expect(page.locator("#dlgTotals")).to_contain_text("Income", timeout=10_000)
    requests_before = len(stub.recorded_requests)
    page.locator("#dlgConfirmBtn").click()
    expect(page.locator("#dlgResult")).to_contain_text(
        STUB_TRANSACTION_REFERENCE_PROP, timeout=15_000,
    )

    prop_posts = [
        r for r in stub.recorded_requests[requests_before:]
        if r["method"] == "POST" and "/property/" in r["path"]
        and r["path"].endswith("/period-summaries")
    ]
    assert prop_posts, (
        f"[ACT 6] No property /period-summaries POST captured. "
        f"Captured: {[(r['method'], r['path']) for r in stub.recorded_requests[requests_before:]]}"
    )
    prop_body = prop_posts[-1]["body"] or {}
    for required in ("periodDates", "periodIncome", "periodExpenses"):
        assert required in prop_body, (
            f"[ACT 6] Property wire body missing {required}: {prop_body!r}"
        )
    rent_value = (prop_body["periodIncome"] or {}).get("rentIncome") or 0.0
    assert rent_value >= 950.0, (
        f"[ACT 6] Expected rent income ≥ £950 from the two RENT RECEIVED "
        f"rows; got £{rent_value} in body {prop_body!r}"
    )

    page.evaluate("document.getElementById('submitDialog').close();")
    page.wait_for_timeout(500)

    # ====================================================================
    # ACT 7 — Categorisation correction loop
    # ====================================================================
    # User decides the train fare should be 'staffCosts' instead of
    # 'travelCosts' — exercise the override → preview pipeline. Real users
    # do this when the regex (or AI) gets a category wrong.
    jar, csrf = _cookies_for_httpx(page)
    ov = httpx.post(
        f"{base}/api/hmrc/categorise/override",
        json={
            "description": "TRAIN LONDON LIVERPOOL ST IPSWICH",
            "business_type": "se",
            "category": "staffCosts",
        },
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        cookies=jar, timeout=10.0,
    )
    assert ov.status_code == 200, (
        f"[ACT 7] override save failed: {ov.status_code} {ov.text}"
    )

    today = dt.date.today().isoformat()
    period_start = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    preview = httpx.post(
        f"{base}/api/hmrc/quarterly-updates/se/preview",
        json={
            "business_id": STUB_BUSINESS_ID_SE,
            "period_start": period_start,
            "period_end": today,
            "rows": [],
        },
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        cookies=jar, timeout=15.0,
    )
    assert preview.status_code == 200, (
        f"[ACT 7] preview after override failed: "
        f"{preview.status_code} {preview.text}"
    )
    preview_body = preview.json() or {}
    payload_after = preview_body.get("payload") or {}
    expenses_after = (payload_after.get("periodExpenses") or {})
    # The £42.30 train fare should now be in staffCosts.
    assert (expenses_after.get("staffCosts") or 0) >= 42.30, (
        f"[ACT 7] Override didn't move the train fare into staffCosts. "
        f"Expenses now: {expenses_after!r}"
    )
    assert (expenses_after.get("travelCosts") or 0) < 42.30, (
        f"[ACT 7] Old travelCosts still contains the train fare. "
        f"Expenses now: {expenses_after!r}"
    )

    # ====================================================================
    # ACT 8 — Returning user: log out, log back in, history is intact
    # ====================================================================
    # Use the API logout (the menu logout button is anchored differently
    # across templates and isn't core to this test).
    httpx.post(f"{base}/api/logout", cookies=jar,
               headers={"X-CSRF-Token": csrf}, timeout=5.0)
    # Wipe Playwright's cookies so the next nav is truly anonymous.
    page.context.clear_cookies()

    _ui_login(page, base, email, password)
    # Should land on the dashboard (not /login or /verify-email).
    assert "/login" not in page.url and "/verify-email" not in page.url, (
        f"[ACT 8] Re-login dropped the user back on {page.url}"
    )

    # Both submissions still on file.
    jar2, _ = _cookies_for_httpx(page)
    subs = httpx.get(f"{base}/api/hmrc/submissions",
                     cookies=jar2, timeout=10.0)
    assert subs.status_code == 200, (
        f"[ACT 8] /api/hmrc/submissions failed: {subs.status_code} {subs.text}"
    )
    submissions = subs.json().get("submissions") or []
    se_subs = [s for s in submissions
               if "/self-employment/" in (s.get("endpoint") or "")
               and (s.get("response_status") or 0) == 200]
    prop_subs = [s for s in submissions
                 if "/property/" in (s.get("endpoint") or "")
                 and (s.get("response_status") or 0) == 200]
    assert se_subs, (
        f"[ACT 8] No successful SE submission in the audit log. "
        f"All: {[(s.get('endpoint'), s.get('response_status')) for s in submissions]}"
    )
    assert prop_subs, (
        f"[ACT 8] No successful property submission in the audit log. "
        f"All: {[(s.get('endpoint'), s.get('response_status')) for s in submissions]}"
    )

    # /hmrc/file should render with the existing tokens (no re-OAuth).
    page.goto(f"{base}/hmrc/file")
    expect(page.locator("body")).to_contain_text(
        "File with HMRC", timeout=10_000,
    )

    # ====================================================================
    # ACT 9 — Tax estimate ("how much do I owe?") tile shows real numbers
    # ====================================================================
    # Every dashboard render hits this endpoint. If it returns £0 against
    # a properly-categorised ledger, the user can't trust any number on
    # the page — and that's what we saw with the legacy `se_`/`property_`
    # prefix bug, which would have made the tile show £0 against any
    # categorised ledger. Assert real numbers come back.
    jar3, csrf3 = _cookies_for_httpx(page)
    fc = httpx.get(f"{base}/api/tax-forecast", cookies=jar3, timeout=10.0)
    assert fc.status_code == 200, (
        f"[ACT 9] /api/tax-forecast failed: {fc.status_code} {fc.text}"
    )
    forecast = fc.json() or {}
    se_income = (forecast.get("self_employment") or {}).get("income") or 0.0
    prop_income = (forecast.get("property") or {}).get("income") or 0.0
    assert se_income > 0, (
        f"[ACT 9] SE income on the tax tile is £{se_income} — should be "
        f"~£3,900 from the consulting+fee credits we uploaded. Full forecast: {forecast!r}"
    )
    assert prop_income > 0, (
        f"[ACT 9] Property income on the tax tile is £{prop_income} — should "
        f"be ~£2,250 from the two RENT RECEIVED rows. Full forecast: {forecast!r}"
    )

    # ====================================================================
    # ACT 10 — End of Period Statement: finalise SE + property for the year
    # ====================================================================
    # Once a year per business. We drive the API directly since the
    # dashboard UI for EOPS submit isn't wired up on /hmrc/file yet — but
    # the endpoint MUST work so when the UI ships there's no surprise.
    today_iso = dt.date.today().isoformat()
    year_start = (dt.date.today() - dt.timedelta(days=350)).isoformat()
    for biz_id, biz_type, label in (
        (STUB_BUSINESS_ID_SE, "self-employment", "SE"),
        (STUB_BUSINESS_ID_PROP, "uk-property", "property"),
    ):
        eops_req_start = len(stub.recorded_requests)
        r = httpx.post(
            f"{base}/api/hmrc/eops/submit",
            json={
                "business_id": biz_id,
                "type_of_business": biz_type,
                "period_start": year_start,
                "period_end": today_iso,
            },
            headers={"X-CSRF-Token": csrf3, "Content-Type": "application/json"},
            cookies=jar3, timeout=15.0,
        )
        assert r.status_code == 200, (
            f"[ACT 10/{label}] EOPS submit failed: {r.status_code} {r.text}"
        )
        eops_posts = [
            x for x in stub.recorded_requests[eops_req_start:]
            if x["method"] == "POST" and x["path"].endswith("/end-of-period-statements")
        ]
        assert eops_posts, (
            f"[ACT 10/{label}] No EOPS POST hit the stub. Captured: "
            f"{[x['path'] for x in stub.recorded_requests[eops_req_start:]]}"
        )
        eops_body = eops_posts[-1]["body"] or {}
        assert eops_body.get("finalised") is True, (
            f"[ACT 10/{label}] HMRC requires finalised=true on EOPS body, "
            f"got: {eops_body!r}"
        )

    # ====================================================================
    # ACT 11 — Trigger + fetch tax calculation
    # ====================================================================
    # Tax year format HMRC expects: "2026-27" (the year started on
    # 6 April that year). Build it from today rather than hard-coding so
    # the test stays correct across tax-year boundaries.
    today = dt.date.today()
    ty_start_year = today.year if today >= dt.date(today.year, 4, 6) else today.year - 1
    tax_year = f"{ty_start_year}-{str(ty_start_year + 1)[-2:]}"

    trig = httpx.post(
        f"{base}/api/hmrc/calculation/trigger",
        json={"tax_year": tax_year},
        headers={"X-CSRF-Token": csrf3, "Content-Type": "application/json"},
        cookies=jar3, timeout=15.0,
    )
    assert trig.status_code == 200, (
        f"[ACT 11] calculation trigger failed: {trig.status_code} {trig.text}"
    )
    calc_id = trig.json().get("calculation_id")
    assert calc_id == STUB_CALCULATION_ID, (
        f"[ACT 11] Expected calculationId {STUB_CALCULATION_ID}, got {calc_id!r}"
    )

    got = httpx.post(
        f"{base}/api/hmrc/calculation/get",
        json={"tax_year": tax_year, "calculation_id": calc_id},
        headers={"X-CSRF-Token": csrf3, "Content-Type": "application/json"},
        cookies=jar3, timeout=15.0,
    )
    assert got.status_code == 200, (
        f"[ACT 11] calculation get failed: {got.status_code} {got.text}"
    )
    summary = got.json()
    assert summary.get("total_amount_payable") == STUB_TOTAL_TAX_AMOUNT, (
        f"[ACT 11] Calculation summary didn't surface totalTaxAmount. "
        f"Got: {summary!r}"
    )

    # ====================================================================
    # ACT 12 — Submit the Final Declaration (= file the tax return)
    # ====================================================================
    fd_req_start = len(stub.recorded_requests)
    fd = httpx.post(
        f"{base}/api/hmrc/final-declaration/submit",
        json={
            "tax_year": tax_year,
            "calculation_id": calc_id,
            "finalised": True,
        },
        headers={"X-CSRF-Token": csrf3, "Content-Type": "application/json"},
        cookies=jar3, timeout=15.0,
    )
    assert fd.status_code == 200, (
        f"[ACT 12] final declaration failed: {fd.status_code} {fd.text}"
    )
    fd_posts = [
        x for x in stub.recorded_requests[fd_req_start:]
        if x["method"] == "POST" and x["path"].endswith("/final-declaration")
    ]
    assert fd_posts, (
        f"[ACT 12] No final-declaration POST hit the stub. Captured: "
        f"{[x['path'] for x in stub.recorded_requests[fd_req_start:]]}"
    )
    assert any(
        k.lower() == "idempotency-key" for k in (fd_posts[-1]["headers"] or {})
    ), (
        f"[ACT 12] final declaration missing Idempotency-Key: "
        f"{list(fd_posts[-1]['headers'])}"
    )

    csv_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# PDF upload — gated on a real Anthropic API key
# ---------------------------------------------------------------------------

import os as _os
_PDF_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "us_statements" / "bofa_sample.pdf"
)


@pytest.mark.skipif(
    not _os.environ.get("ANTHROPIC_API_KEY"),
    reason="PDF parsing needs ANTHROPIC_API_KEY; set it to exercise the AI path",
)
@pytest.mark.skipif(
    not _PDF_FIXTURE.exists(),
    reason=f"PDF fixture missing at {_PDF_FIXTURE}",
)
def test_pdf_upload_lands_rows_in_ledger(page: Page, hmrc_live_server):
    """Most real BankScan users upload PDF statements from their bank, not
    handcrafted CSVs. Exercise that path so we know:

      1. The /api/parse PDF code path actually runs end-to-end with AI
      2. Parsed rows hit the ledger (so they'd feed the HMRC submit)

    Skipped when no ANTHROPIC_API_KEY is in the env so CI without secrets
    doesn't fail; if you set the key, this runs and burns ~$0.01 of Claude
    tokens to read the fixture statement.
    """
    base = hmrc_live_server["base_url"]
    email = f"pdf-user-{int(time.time())}@example.test"
    password = "password12345"

    # Same bootstrap as the main journey, in helper form.
    _ui_register(page, base, email, password)
    if "/verify-email" not in page.url:
        page.goto(f"{base}/verify-email")
    page.locator("input#code, input[name=code]").first.fill(
        _peek_otp(base, email)
    )
    page.locator("button#verify-btn, button[type=submit]").first.click()
    expect(page).to_have_url(
        re.compile(rf"^{re.escape(base)}/start-trial(\?.*)?$"), timeout=10_000,
    )
    _set_subscription_state(
        base, email, subscription_status="trialing",
        stripe_subscription_id="sub_pdf_e2e",
        trial_end_at=time.time() + 6 * 86400,
    )

    page.goto(f"{base}/")
    page.locator("input[type=file]").first.set_input_files(str(_PDF_FIXTURE))
    page.locator(
        "button#parseBtn, button:has-text('Convert to Spreadsheet')"
    ).first.click()
    # PDFs go through Claude vision; allow up to 60 s for parse.
    page.wait_for_timeout(8000)

    jar, _ = _cookies_for_httpx(page)
    last = {"rows": [], "status": None}
    def _has_rows():
        r = httpx.get(f"{base}/api/ledger", cookies=jar, timeout=10.0)
        last["status"] = r.status_code
        if r.status_code != 200:
            return False
        rows = (r.json() or {}).get("transactions") or []
        last["rows"] = rows
        return len(rows) >= 1
    try:
        _wait_until(_has_rows, timeout_s=75.0, interval=2.0)
    except AssertionError as e:
        raise AssertionError(
            f"PDF→ledger pipeline didn't deposit any rows. "
            f"/api/ledger -> {last['status']}, rows={len(last['rows'])}"
        ) from e
