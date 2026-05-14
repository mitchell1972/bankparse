"""
E2E Playwright + API test for 7-day free trial with cumulative data persistence.
Tests: landing page, registration + email verification, upload statements,
cumulative data accumulation, 25MB session size cap, data persists across logout,
Clear & Upload New wipes data.

Requires TEST_MODE_ENABLED=1 on the server for /api/test/peek-otp.
Requires ANTHROPIC_API_KEY for receipt parsing (skipped if unavailable).

Usage:
    pip install playwright requests
    python -m playwright install chromium
    BASE_URL=http://localhost:8000 python tests/e2e_trial_cumulative.py
"""

from playwright.sync_api import sync_playwright
import requests
import time
import tempfile
import os

BASE_URL = os.environ.get("BASE_URL", "https://bankscanai.com")
TS = str(int(time.time()))
TEST_EMAIL = f"e2e_trial_{TS}@test.com"
TEST_PASSWORD = "TestPass123!"

results = []

def report(step, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    msg = f"[{status}] Step {step}"
    if detail:
        msg += f" — {detail}"
    results.append((step, passed, detail))
    print(msg)

def create_test_csv():
    content = b"Date,Description,Amount\n01/01/2025,Salary,2500.00\n02/01/2025,Tesco,-45.67\n"
    tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, prefix="e2e_stmt_")
    tmp.write(content)
    tmp.close()
    return tmp.name, len(content)

def verify_and_login(session, email, password, headers):
    """Register + verify email via test mode API, then login."""
    r = session.post(f"{BASE_URL}/api/register", json={"email": email, "password": password}, headers=headers)
    if r.status_code not in (200, 409):
        return False
    if r.status_code == 409:
        r = session.post(f"{BASE_URL}/api/login", json={"email": email, "password": password}, headers=headers)
        return r.status_code == 200
    # Fetch OTP via test endpoint and verify
    r = session.get(f"{BASE_URL}/api/test/peek-otp?email={email}")
    if r.status_code == 200:
        otp = r.json().get("code", "")
        r = session.post(f"{BASE_URL}/api/verify-email-code", json={"email": email, "code": otp}, headers=headers)
    r = session.post(f"{BASE_URL}/api/login", json={"email": email, "password": password}, headers=headers)
    return r.status_code == 200

def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_default_timeout(15000)
        
        # STEP 1: Landing page loads correctly
        try:
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
            ok = "/landing" in page.url and "BankScan" in page.title()
            report(1, ok, f"URL={page.url[:60]}, title={page.title()[:50]}")
        except Exception as e:
            report(1, False, str(e))
        
        browser.close()
    
    # API-based tests (more reliable than UI automation)
    s = requests.Session()
    s.get(f"{BASE_URL}/login")
    csrf = s.cookies.get("bp_csrf", "")
    h = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
    
    # STEP 2: Register + verify + login
    ok = verify_and_login(s, TEST_EMAIL, TEST_PASSWORD, h)
    report(2, ok, f"Register/verify/login, email={TEST_EMAIL}")
    
    # STEP 3: Usage API shows trial active with 7 days, no file caps
    r = s.get(f"{BASE_URL}/api/usage", headers=h)
    usage = r.json()
    ok = (usage.get("trial_active") == True
          and usage.get("trial_days_remaining") == 7
          and usage.get("statements_limit") is None
          and usage.get("receipts_limit") is None)
    report(3, ok, f"trial={usage.get('trial_active')}, days={usage.get('trial_days_remaining')}, "
                  f"stmt_limit={usage.get('statements_limit')}, rcpt_limit={usage.get('receipts_limit')}")
    
    # STEP 4: Upload first statement — should succeed
    stmt1, sz1 = create_test_csv()
    try:
        with open(stmt1, 'rb') as f:
            r = s.post(f"{BASE_URL}/api/parse", files={"file": ("stmt1.csv", f, "text/csv")},
                      headers={"X-CSRF-Token": csrf})
        ok = r.status_code == 200
        report(4, ok, f"Upload 1: status={r.status_code}")
    except Exception as e:
        report(4, False, str(e))
    finally:
        os.unlink(stmt1)
    
    # STEP 5: Upload second statement — cumulative, should also succeed
    stmt2, sz2 = create_test_csv()
    try:
        with open(stmt2, 'rb') as f:
            r = s.post(f"{BASE_URL}/api/parse", files={"file": ("stmt2.csv", f, "text/csv")},
                      headers={"X-CSRF-Token": csrf})
        ok = r.status_code == 200
        report(5, ok, f"Upload 2: status={r.status_code}")
    except Exception as e:
        report(5, False, str(e))
    finally:
        os.unlink(stmt2)
    
    # STEP 6: Extracted data shows cumulative totals, 25MB cap
    r = s.get(f"{BASE_URL}/api/extracted-data", headers=h)
    data = r.json()
    stmt_files = data.get("statements", {}).get("files", [])
    stmt_rows = data.get("statements", {}).get("rows", [])
    session_max = data.get("session_max_bytes", 0)
    
    ok = (len(stmt_files) == 2  # both uploads tracked
          and len(stmt_rows) == 4  # 2 rows per file = 4 total cumulative
          and session_max == 25 * 1024 * 1024)  # 25MB cap
    report(6, ok, f"files={len(stmt_files)}, cumulative_rows={len(stmt_rows)}, max={session_max}")
    
    # STEP 7: Logout → login → data PERSISTS
    s.post(f"{BASE_URL}/api/logout", headers=h)
    ok_login = verify_and_login(s, TEST_EMAIL, TEST_PASSWORD, h)
    
    r = s.get(f"{BASE_URL}/api/extracted-data", headers=h)
    data2 = r.json()
    stmt_files2 = data2.get("statements", {}).get("files", [])
    ok = ok_login and len(stmt_files2) == 2
    report(7, ok, f"After re-login: files={len(stmt_files2)} (expected 2)")
    
    # STEP 8: Clear data → data GONE
    r = s.post(f"{BASE_URL}/api/extracted-data/clear", headers=h)
    r = s.get(f"{BASE_URL}/api/extracted-data", headers=h)
    data3 = r.json()
    cleared = len(data3.get("statements", {}).get("files", [])) == 0
    report(8, cleared, f"After clear: files={len(data3.get('statements', {}).get('files', []))}")
    
    print("\n" + "=" * 60)
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    print(f"TOTAL: {passed}/{total} passed ({100*passed//total}%)")
    print("=" * 60)
    
    for step, ok, detail in results:
        if not ok:
            print(f"  [FAIL] Step {step}: {detail}")
    
    return passed == total

if __name__ == "__main__":
    exit(0 if run_tests() else 1)
