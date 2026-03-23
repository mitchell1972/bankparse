"""
BankScan AI — Full E2E Test Suite (Headless Playwright + API)
Tests all user journeys across all tiers.
"""
import requests
import time
import json
from playwright.sync_api import sync_playwright

BASE = "https://bankscanai.com"
TS = str(int(time.time()))
RESULTS = []

def log(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append((name, passed, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

def get_session(email, password):
    """Register + login, return session with cookies."""
    s = requests.Session()
    s.get(f"{BASE}/login")
    csrf = s.cookies.get("bp_csrf", "")
    h = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
    s.post(f"{BASE}/api/register", json={"email": email, "password": password}, headers=h)
    r = s.post(f"{BASE}/api/login", json={"email": email, "password": password}, headers=h)
    return s, h, r.status_code == 200

print("=" * 70)
print(f"BankScan AI — Full E2E Test Suite")
print(f"Target: {BASE}")
print(f"Timestamp: {TS}")
print("=" * 70)

# =============================================
# SECTION 1: LANDING PAGE & NAVIGATION
# =============================================
print("\n--- SECTION 1: Landing Page & Navigation ---")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()

    # 1.1 Root redirects to landing
    page.goto(BASE)
    page.wait_for_load_state("networkidle")
    log("1.1 Root redirects to /landing", "/landing" in page.url, page.url)

    # 1.2 Landing page title
    title = page.title()
    log("1.2 Landing page has title", "BankScan" in title, title[:60])

    # 1.3 Nav links present
    nav_links = page.query_selector_all("header nav a")
    log("1.3 Nav links present", len(nav_links) >= 5, f"{len(nav_links)} links")

    # 1.4 Hero CTA present
    hero_btn = page.query_selector('a.btn-hero')
    log("1.4 Hero CTA button exists", hero_btn is not None)

    # 1.5 Pricing cards visible
    cards = page.query_selector_all(".price-card")
    log("1.5 Four pricing cards visible", len(cards) == 4, f"{len(cards)} cards")

    # 1.6 Prices correct
    content = page.content()
    log("1.6 Starter £7.99", "7.99" in content)
    log("1.7 Pro £24.99", "24.99" in content)
    log("1.8 Business £59.99", "59.99" in content)
    log("1.9 Enterprise £149", "149" in content)

    # 1.10 Try Free goes to /login (not loop)
    hero_btn.click()
    page.wait_for_load_state("networkidle")
    log("1.10 Try Free → /login", "/login" in page.url, page.url)

    # 1.11 Pricing buttons have ?plan= param
    page.goto(f"{BASE}/landing")
    page.wait_for_load_state("networkidle")
    starter_btn = page.query_selector('a[href*="plan=starter"]')
    pro_btn = page.query_selector('a[href*="plan=pro"]')
    biz_btn = page.query_selector('a[href*="plan=business"]')
    ent_btn = page.query_selector('a[href*="plan=enterprise"]')
    log("1.11 Starter has ?plan=starter", starter_btn is not None)
    log("1.12 Pro has ?plan=pro", pro_btn is not None)
    log("1.13 Business has ?plan=business", biz_btn is not None)
    log("1.14 Enterprise has ?plan=enterprise", ent_btn is not None)

    browser.close()

# =============================================
# SECTION 2: SEO & TECHNICAL
# =============================================
print("\n--- SECTION 2: SEO & Technical ---")

r = requests.get(f"{BASE}/robots.txt")
log("2.1 robots.txt returns 200", r.status_code == 200)
log("2.2 robots.txt has sitemap", "sitemap.xml" in r.text.lower())

r = requests.get(f"{BASE}/sitemap.xml")
log("2.3 sitemap.xml returns 200", r.status_code == 200)
log("2.4 sitemap has /landing", "/landing" in r.text)

r = requests.get(f"{BASE}/api/health")
data = r.json()
log("2.5 Health endpoint OK", data.get("status") == "ok")
log("2.6 Stripe configured", data.get("stripe_configured") == True)

r = requests.get(f"{BASE}/landing")
log("2.7 Meta description present", 'meta name="description"' in r.text)
log("2.8 OG tags present", 'og:title' in r.text)
log("2.9 Google verification present", "google-site-verification" in r.text)
log("2.10 JSON-LD present", "application/ld+json" in r.text)

# =============================================
# SECTION 3: AUTH FLOW
# =============================================
print("\n--- SECTION 3: Auth Flow ---")

email1 = f"e2e_auth_{TS}@test.com"
s = requests.Session()
s.get(f"{BASE}/login")
csrf = s.cookies.get("bp_csrf", "")
h = {"Content-Type": "application/json", "X-CSRF-Token": csrf}

# 3.1 Register
r = s.post(f"{BASE}/api/register", json={"email": email1, "password": "TestPass123!"}, headers=h)
log("3.1 Register succeeds", r.status_code == 200, r.json().get("email", ""))

# 3.2 Duplicate register
r = s.post(f"{BASE}/api/register", json={"email": email1, "password": "TestPass123!"}, headers=h)
log("3.2 Duplicate register → 409", r.status_code == 409)

# 3.3 Login
r = s.post(f"{BASE}/api/login", json={"email": email1, "password": "TestPass123!"}, headers=h)
log("3.3 Login succeeds", r.status_code == 200)
log("3.4 Auth cookie set", "bp_auth" in s.cookies.get_dict())

# 3.5 Wrong password
r = s.post(f"{BASE}/api/login", json={"email": email1, "password": "WrongPass!"}, headers=h)
log("3.5 Wrong password → 401", r.status_code == 401)

# 3.6 Short password register
email2 = f"e2e_short_{TS}@test.com"
r = s.post(f"{BASE}/api/register", json={"email": email2, "password": "123"}, headers=h)
log("3.6 Short password → 400", r.status_code == 400)

# 3.7 Invalid email register
r = s.post(f"{BASE}/api/register", json={"email": "notanemail", "password": "TestPass123!"}, headers=h)
log("3.7 Invalid email → 400", r.status_code == 400)

# =============================================
# SECTION 4: FREE TIER LIMITS
# =============================================
print("\n--- SECTION 4: Free Tier Limits ---")

email3 = f"e2e_free_{TS}@test.com"
s3, h3, ok = get_session(email3, "TestPass123!")
log("4.1 Free user created", ok)

# Usage
r = s3.get(f"{BASE}/api/usage", headers=h3)
usage = r.json()
log("4.2 Tier is free", usage.get("tier") == "free", f"tier={usage.get('tier')}")
log("4.3 Statement limit = 1", usage.get("statements_limit") == 1)
log("4.4 Receipt limit = 1", usage.get("receipts_limit") == 1)

# Upload CSV statement
csv_data = "Date,Description,Amount\n01/01/2025,Test Transaction,-50.00\n02/01/2025,Salary,1500.00"
files = {"file": ("test.csv", csv_data.encode(), "text/csv")}
r = s3.post(f"{BASE}/api/parse", files=files, headers={"X-CSRF-Token": h3.get("X-CSRF-Token", "")})
log("4.5 First statement upload → 200", r.status_code == 200, f"status={r.status_code}")

# Second upload should be blocked
files2 = {"file": ("test2.csv", csv_data.encode(), "text/csv")}
r = s3.post(f"{BASE}/api/parse", files=files2, headers={"X-CSRF-Token": h3.get("X-CSRF-Token", "")})
log("4.6 Second statement → 403", r.status_code == 403, f"status={r.status_code}")

# Bulk blocked
r = s3.post(f"{BASE}/api/parse-receipts-bulk", files={"files": ("r.csv", b"x", "text/csv")}, headers={"X-CSRF-Token": h3.get("X-CSRF-Token", "")})
log("4.7 Bulk upload → 403", r.status_code in [403, 401], f"status={r.status_code}")

# Chat blocked
r = s3.post(f"{BASE}/api/chat", json={"message": "test", "context_type": "statement", "context_data": {}}, headers=h3)
log("4.8 AI Chat → 403", r.status_code == 403, f"status={r.status_code}")

# =============================================
# SECTION 5: CONFIG & PRICING ENDPOINT
# =============================================
print("\n--- SECTION 5: Config & Pricing ---")

r = requests.get(f"{BASE}/api/config")
config = r.json()
plans = config.get("plans", {})

log("5.1 Config has 4 plans", len(plans) == 4, list(plans.keys()))
log("5.2 Starter price £7.99", "7.99" in str(plans.get("starter", {}).get("price", "")))
log("5.3 Pro price £24.99", "24.99" in str(plans.get("pro", {}).get("price", "")))
log("5.4 Business price £59.99", "59.99" in str(plans.get("business", {}).get("price", "")))
log("5.5 Enterprise price £149", "149" in str(plans.get("enterprise", {}).get("price", "")))
log("5.6 Pro has 300 statements", plans.get("pro", {}).get("statements") == 300)
log("5.7 Business has 5000 receipts", plans.get("business", {}).get("receipts") == 5000)

# =============================================
# SECTION 6: STRIPE CHECKOUT
# =============================================
print("\n--- SECTION 6: Stripe Checkout ---")

email6 = f"e2e_checkout_{TS}@test.com"
s6, h6, ok = get_session(email6, "TestPass123!")
log("6.1 Checkout test user created", ok)

for plan in ["starter", "pro", "business", "enterprise"]:
    r = s6.post(f"{BASE}/api/create-checkout", json={"plan": plan}, headers=h6)
    has_url = "checkout.stripe.com" in r.json().get("checkout_url", "") if r.status_code == 200 else False
    log(f"6.{['starter','pro','business','enterprise'].index(plan)+2} {plan.title()} checkout URL", has_url, f"status={r.status_code}")

# =============================================
# SECTION 7: PAYWALL UI (Playwright)
# =============================================
print("\n--- SECTION 7: Paywall UI ---")

# Full UI-based flow: landing → register → dashboard → paywall
email7 = f"e2e_paywall_{TS}@test.com"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()

    # Start from landing, click Try Free
    page.goto(f"{BASE}/landing")
    page.wait_for_load_state("networkidle")
    page.click("a.btn-hero")
    page.wait_for_load_state("networkidle")
    time.sleep(1)

    # Should be on /login — click "Create Account" tab
    create_tab = page.query_selector(".tab:not(.active)")
    if create_tab:
        create_tab.click()
        time.sleep(0.5)

    # Fill registration form
    page.fill("input#email", email7)
    page.fill("input#password", "TestPass123!")
    page.click("button#submitBtn")
    time.sleep(2)
    page.wait_for_load_state("networkidle")
    time.sleep(3)

    # Should be on dashboard now (or at least not on /login)
    on_dashboard = "/login" not in page.url and "/landing" not in page.url
    log("7.0 UI Registration → dashboard", on_dashboard, page.url)

    # Trigger paywall
    page.evaluate("() => { if (typeof showPaywall === 'function') showPaywall(); }")
    time.sleep(1)
    content = page.content()

    log("7.1 Paywall has Starter card", "Starter" in content and "7.99" in content)
    log("7.2 Paywall has Pro card", "Pro" in content and "24.99" in content)
    log("7.3 Paywall has Business card", "Business" in content and "59.99" in content)
    log("7.4 Paywall has Enterprise card", "Enterprise" in content and "149" in content)
    log("7.5 Paywall has email input", page.query_selector('#paywallEmail') is not None)

    # Check usage bar
    bar = page.query_selector('#usageBar')
    bar_text = bar.inner_text() if bar else ""
    log("7.6 Usage bar shows Free", "free" in bar_text.lower() or "Free" in bar_text, bar_text[:60])

    browser.close()

# =============================================
# SECTION 8: PLAN CTA → LOGIN → CHECKOUT FLOW
# =============================================
print("\n--- SECTION 8: Plan CTA Flow ---")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()

    # Click "Get Pro" on landing
    page.goto(f"{BASE}/landing")
    page.wait_for_load_state("networkidle")
    pro_link = page.query_selector('a[href*="plan=pro"]')
    if pro_link:
        pro_link.click()
        page.wait_for_load_state("networkidle")
        log("8.1 Get Pro → /login?plan=pro", "plan=pro" in page.url, page.url)
    else:
        log("8.1 Get Pro link found", False, "link not found")

    browser.close()

# =============================================
# SECTION 9: LOGOUT
# =============================================
print("\n--- SECTION 9: Logout ---")

email9 = f"e2e_logout_{TS}@test.com"
s9, h9, ok = get_session(email9, "TestPass123!")

r = s9.post(f"{BASE}/api/logout", headers=h9)
log("9.1 Logout → 200", r.status_code == 200)

r = s9.get(f"{BASE}/api/usage", headers=h9)
tier = r.json().get("tier", "")
log("9.2 After logout, no tier", tier == "" or r.json().get("email") is None, f"tier={tier}")

# =============================================
# SECTION 10: MANAGE BILLING
# =============================================
print("\n--- SECTION 10: Manage Billing ---")

email10 = f"e2e_billing_{TS}@test.com"
s10, h10, ok = get_session(email10, "TestPass123!")

r = s10.post(f"{BASE}/api/manage-billing", headers=h10)
log("10.1 Free user manage-billing → 400", r.status_code == 400, f"status={r.status_code}")

# =============================================
# SUMMARY
# =============================================
print("\n" + "=" * 70)
passed = sum(1 for _, p, _ in RESULTS if p)
failed = sum(1 for _, p, _ in RESULTS if not p)
total = len(RESULTS)
print(f"TOTAL: {passed}/{total} passed, {failed} failed ({100*passed//total}%)")
print("=" * 70)

if failed:
    print("\nFailed tests:")
    for name, p, detail in RESULTS:
        if not p:
            print(f"  [FAIL] {name} — {detail}")

exit(0 if failed == 0 else 1)
