"""
BankScan AI — Landing Page & Navigation UI Tests (Headless Playwright)
Tests: page load, nav links, pricing cards, CTAs, mobile responsive,
footer, FAQ accordion, scroll behavior, trust badges, SEO elements.

Usage:
    pip install playwright
    python -m playwright install chromium
    python tests/ui_test_landing.py
"""

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import time

BASE_URL = "https://bankscanai.com"
LANDING_URL = f"{BASE_URL}/landing"

results = []


def report(step, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    msg = f"[{status}] Step {step}"
    if detail:
        msg += f" — {detail}"
    results.append((step, passed, detail))
    print(msg)


def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) UI-Landing-Test"
        )
        page = context.new_page()
        page.set_default_timeout(15000)

        # ================================================================
        # STEP 1: Landing page loads — title contains "BankScan", hero visible
        # ================================================================
        try:
            page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            title = page.title()
            has_bankscan = "BankScan" in title

            hero = page.locator("section.hero, #hero")
            hero_visible = hero.count() > 0 and hero.first.is_visible()

            hero_h1 = page.locator(".hero h1")
            hero_h1_text = hero_h1.text_content() if hero_h1.count() > 0 else ""

            if has_bankscan and hero_visible:
                report(1, True, f"Title='{title[:60]}', hero visible, h1='{hero_h1_text[:50]}'")
            else:
                details = []
                if not has_bankscan:
                    details.append(f"title='{title}' missing BankScan")
                if not hero_visible:
                    details.append("hero section not visible")
                report(1, False, "; ".join(details))
        except Exception as e:
            report(1, False, f"Exception: {e}")

        # ================================================================
        # STEP 2: Nav links — How It Works, Banks, Features, Pricing, FAQ
        # ================================================================
        try:
            nav = page.locator("nav#navMenu, header nav")
            nav_links = nav.locator("a")
            link_count = nav_links.count()

            link_texts = []
            for i in range(link_count):
                link_texts.append(nav_links.nth(i).text_content().strip())

            expected_links = ["How It Works", "Banks", "Features", "Pricing", "FAQ"]
            found = [exp for exp in expected_links if any(exp.lower() in t.lower() for t in link_texts)]
            missing = [exp for exp in expected_links if exp not in found]

            # Also check the "Try Free" CTA in nav
            has_try_free = any("try free" in t.lower() for t in link_texts)

            if len(found) == len(expected_links) and has_try_free:
                report(2, True, f"All nav links present: {link_texts}")
            else:
                detail = f"Found: {found}, Missing: {missing}, Try Free: {has_try_free}, All links: {link_texts}"
                report(2, len(found) >= 4, detail)
        except Exception as e:
            report(2, False, f"Exception: {e}")

        # ================================================================
        # STEP 3: Pricing section — 4 cards with correct prices
        # ================================================================
        try:
            pricing_section = page.locator("#pricing")
            pricing_visible = pricing_section.is_visible()

            cards = page.locator(".price-card")
            card_count = cards.count()

            page_content = page.content()
            price_checks = {
                "Starter £7.99": "7.99" in page_content and "Starter" in page_content,
                "Pro £24.99": "24.99" in page_content and "Pro" in page_content,
                "Business £59.99": "59.99" in page_content and "Business" in page_content,
                "Enterprise £149": "149" in page_content and "Enterprise" in page_content,
            }
            prices_found = [name for name, found in price_checks.items() if found]
            prices_missing = [name for name, found in price_checks.items() if not found]

            if card_count == 4 and len(prices_found) == 4 and pricing_visible:
                report(3, True, f"{card_count} pricing cards, all prices correct: {prices_found}")
            else:
                report(3, False, f"Cards={card_count}, visible={pricing_visible}, found={prices_found}, missing={prices_missing}")
        except Exception as e:
            report(3, False, f"Exception: {e}")

        # ================================================================
        # STEP 4: "Try Free" hero CTA clicks through to /login
        # ================================================================
        try:
            # Navigate back to landing to ensure clean state
            page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            hero_btn = page.locator("a.btn-hero")
            if hero_btn.count() > 0:
                hero_btn_text = hero_btn.text_content().strip()
                hero_btn_href = hero_btn.get_attribute("href")

                hero_btn.click()
                page.wait_for_load_state("domcontentloaded")
                time.sleep(2)

                current_url = page.url
                goes_to_login = "/login" in current_url

                if goes_to_login:
                    report(4, True, f"Hero CTA '{hero_btn_text}' -> {current_url}")
                else:
                    report(4, False, f"Hero CTA href='{hero_btn_href}' navigated to {current_url} (expected /login)")
            else:
                report(4, False, "Hero CTA button (a.btn-hero) not found")
        except Exception as e:
            report(4, False, f"Exception: {e}")

        # ================================================================
        # STEP 5: Plan buttons link to /login?plan=<plan>
        # ================================================================
        try:
            page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            plan_checks = {
                "starter": 'a[href*="plan=starter"]',
                "pro": 'a[href*="plan=pro"]',
                "business": 'a[href*="plan=business"]',
                "enterprise": 'a[href*="plan=enterprise"]',
            }

            found_plans = []
            missing_plans = []

            for plan_name, selector in plan_checks.items():
                btn = page.locator(selector)
                if btn.count() > 0:
                    href = btn.first.get_attribute("href")
                    text = btn.first.text_content().strip()
                    expected_href_part = f"/login?plan={plan_name}"
                    if expected_href_part in href:
                        found_plans.append(f"{plan_name} ('{text}' -> {href})")
                    else:
                        missing_plans.append(f"{plan_name} href='{href}' unexpected")
                else:
                    missing_plans.append(f"{plan_name} link not found")

            if len(found_plans) == 4:
                report(5, True, f"All 4 plan buttons correct: {', '.join(found_plans)}")
            else:
                report(5, False, f"Found: {found_plans}, Issues: {missing_plans}")
        except Exception as e:
            report(5, False, f"Exception: {e}")

        # ================================================================
        # STEP 6: Mobile responsive — 375px: hamburger visible, nav hidden, cards stack
        # ================================================================
        try:
            # Close current context and open a mobile one
            mobile_context = browser.new_context(
                viewport={"width": 375, "height": 812},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) UI-Landing-Test"
            )
            mobile_page = mobile_context.new_page()
            mobile_page.set_default_timeout(15000)

            mobile_page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            # Hamburger menu should be visible
            hamburger = mobile_page.locator("#menuToggle, .menu-toggle")
            hamburger_visible = hamburger.count() > 0 and hamburger.first.is_visible()

            # Nav should be hidden (display: none at 640px breakpoint)
            nav_menu = mobile_page.locator("#navMenu, nav.nav")
            nav_hidden = nav_menu.count() > 0 and not nav_menu.first.is_visible()

            # Pricing cards should stack (grid-template-columns: 1fr at 640px)
            # Check that pricing cards exist and the grid is single column
            price_cards = mobile_page.locator(".price-card")
            cards_exist = price_cards.count() == 4

            # Verify cards stack by checking first and second card positions
            cards_stack = False
            if cards_exist and price_cards.nth(0).is_visible() and price_cards.nth(1).is_visible():
                box0 = price_cards.nth(0).bounding_box()
                box1 = price_cards.nth(1).bounding_box()
                if box0 and box1:
                    # If stacked, second card's top should be below first card's bottom
                    # and their left positions should be similar (same column)
                    cards_stack = abs(box0["x"] - box1["x"]) < 20

            details = []
            details.append(f"hamburger visible={hamburger_visible}")
            details.append(f"nav hidden={nav_hidden}")
            details.append(f"cards stack={cards_stack}")

            all_pass = hamburger_visible and nav_hidden and cards_stack
            report(6, all_pass, "; ".join(details))

            mobile_page.close()
            mobile_context.close()
        except Exception as e:
            report(6, False, f"Exception: {e}")

        # ================================================================
        # STEP 7: Footer — company name, legal links, copyright
        # ================================================================
        try:
            # Make sure we're back on landing in the desktop context
            page.goto(LANDING_URL, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)

            footer = page.locator("footer.footer, footer")
            footer_exists = footer.count() > 0

            if footer_exists:
                footer_html = footer.inner_text()

                has_company = "Mitoba" in footer_html or "BankScan" in footer_html
                has_copyright = "\u00a9" in footer_html or "copyright" in footer_html.lower() or "2025" in footer_html
                has_privacy = "Privacy" in footer_html
                has_terms = "Terms" in footer_html
                has_cookie = "Cookie" in footer_html
                has_legal_links = has_privacy and has_terms

                details = []
                details.append(f"company name={has_company}")
                details.append(f"copyright={has_copyright}")
                details.append(f"Privacy={has_privacy}")
                details.append(f"Terms={has_terms}")
                details.append(f"Cookie={has_cookie}")

                all_pass = has_company and has_copyright and has_legal_links
                report(7, all_pass, "; ".join(details))
            else:
                report(7, False, "Footer element not found")
        except Exception as e:
            report(7, False, f"Exception: {e}")

        # ================================================================
        # STEP 8: FAQ accordion — click question expands answer, click another closes first
        # ================================================================
        try:
            faq_items = page.locator("details.faq-item")
            faq_count = faq_items.count()

            if faq_count >= 2:
                # Scroll to FAQ section first
                page.locator("#faq").scroll_into_view_if_needed()
                time.sleep(1)

                # Ensure all FAQ items start closed
                for i in range(faq_count):
                    is_open = faq_items.nth(i).get_attribute("open")
                    if is_open is not None:
                        faq_items.nth(i).evaluate("el => el.removeAttribute('open')")
                time.sleep(0.3)

                # Click first question — it should open
                first_question = faq_items.nth(0).locator("summary.faq-question")
                first_question.click()
                time.sleep(0.5)

                first_open = faq_items.nth(0).get_attribute("open") is not None
                first_answer = faq_items.nth(0).locator(".faq-answer")
                first_answer_visible = first_answer.is_visible()

                # Click second question — first should close, second should open
                second_question = faq_items.nth(1).locator("summary.faq-question")
                second_question.click()
                time.sleep(0.5)

                second_open = faq_items.nth(1).get_attribute("open") is not None
                first_still_open = faq_items.nth(0).get_attribute("open") is not None
                first_closed_after = not first_still_open

                details = []
                details.append(f"first opened={first_open}")
                details.append(f"first answer visible={first_answer_visible}")
                details.append(f"second opened={second_open}")
                details.append(f"first closed after second click={first_closed_after}")

                all_pass = first_open and first_answer_visible and second_open and first_closed_after
                report(8, all_pass, "; ".join(details))
            else:
                report(8, False, f"Only {faq_count} FAQ items found (need at least 2)")
        except Exception as e:
            report(8, False, f"Exception: {e}")

        # ================================================================
        # STEP 9: Scroll behavior — clicking "Pricing" in nav scrolls to pricing section
        # ================================================================
        try:
            # Scroll to top first
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.5)

            # Get initial scroll position
            scroll_before = page.evaluate("window.scrollY")

            # Get the pricing section's position for comparison
            pricing_top = page.evaluate("document.getElementById('pricing').getBoundingClientRect().top + window.scrollY")

            # Click the "Pricing" nav link
            pricing_nav_link = page.locator('nav a[href="#pricing"]')
            if pricing_nav_link.count() > 0:
                pricing_nav_link.click()
                # Wait for smooth scroll to complete
                time.sleep(1.5)

                scroll_after = page.evaluate("window.scrollY")
                did_scroll = scroll_after > scroll_before

                # Check that we scrolled close to the pricing section
                # (allow some tolerance for sticky header offset)
                scroll_near_pricing = abs(scroll_after - pricing_top) < 150

                # Verify the pricing section is now in the viewport
                pricing_in_view = page.evaluate("""
                    () => {
                        const el = document.getElementById('pricing');
                        const rect = el.getBoundingClientRect();
                        return rect.top >= -100 && rect.top <= window.innerHeight;
                    }
                """)

                details = f"scrollBefore={scroll_before:.0f}, scrollAfter={scroll_after:.0f}, pricingTop={pricing_top:.0f}, inView={pricing_in_view}"
                report(9, did_scroll and pricing_in_view, details)
            else:
                report(9, False, "Pricing nav link not found")
        except Exception as e:
            report(9, False, f"Exception: {e}")

        # ================================================================
        # STEP 10: Trust badges — encryption, UK data centres, GDPR compliant
        # ================================================================
        try:
            trust_section = page.locator(".trust-badges")
            if trust_section.count() > 0:
                trust_text = trust_section.inner_text()

                has_encryption = "Bank-grade encryption" in trust_text
                has_uk_data = "UK data centres" in trust_text
                has_gdpr = "GDPR compliant" in trust_text

                badges = page.locator(".trust-badge")
                badge_count = badges.count()

                details = []
                details.append(f"badges={badge_count}")
                details.append(f"encryption={has_encryption}")
                details.append(f"UK data centres={has_uk_data}")
                details.append(f"GDPR={has_gdpr}")

                all_pass = has_encryption and has_uk_data and has_gdpr
                report(10, all_pass, "; ".join(details))
            else:
                report(10, False, "Trust badges section not found")
        except Exception as e:
            report(10, False, f"Exception: {e}")

        # ================================================================
        # STEP 11: SEO elements — meta description, og:title, JSON-LD
        # ================================================================
        try:
            html_content = page.content()

            # Meta description
            meta_desc = page.locator('meta[name="description"]')
            has_meta_desc = meta_desc.count() > 0
            meta_desc_content = ""
            if has_meta_desc:
                meta_desc_content = meta_desc.get_attribute("content") or ""

            # Open Graph title
            og_title = page.locator('meta[property="og:title"]')
            has_og_title = og_title.count() > 0
            og_title_content = ""
            if has_og_title:
                og_title_content = og_title.get_attribute("content") or ""

            # JSON-LD structured data
            jsonld_scripts = page.locator('script[type="application/ld+json"]')
            has_jsonld = jsonld_scripts.count() > 0
            jsonld_count = jsonld_scripts.count()

            # Check for specific JSON-LD types
            has_software_app = "SoftwareApplication" in html_content
            has_faq_page = "FAQPage" in html_content

            details = []
            details.append(f"meta desc={has_meta_desc} ('{meta_desc_content[:50]}')")
            details.append(f"og:title={has_og_title} ('{og_title_content[:50]}')")
            details.append(f"JSON-LD scripts={jsonld_count}")
            details.append(f"SoftwareApplication={has_software_app}")
            details.append(f"FAQPage={has_faq_page}")

            all_pass = has_meta_desc and has_og_title and has_jsonld
            report(11, all_pass, "; ".join(details))
        except Exception as e:
            report(11, False, f"Exception: {e}")

        # ================================================================
        # STEP 12: Smooth navigation — page scrolls smoothly between sections
        # ================================================================
        try:
            # Check that scroll-behavior: smooth is set on html element
            scroll_behavior = page.evaluate(
                "window.getComputedStyle(document.documentElement).scrollBehavior"
            )
            has_smooth_scroll = scroll_behavior == "smooth"

            # Test actual smooth scroll by clicking a nav link and sampling scroll position
            page.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.5)

            # Click "How It Works" nav link
            how_link = page.locator('nav a[href="#how-it-works"]')
            if how_link.count() > 0:
                how_link.click()
                # Sample scroll position immediately (should be in transit if smooth)
                time.sleep(0.1)
                scroll_mid = page.evaluate("window.scrollY")
                time.sleep(1.5)
                scroll_final = page.evaluate("window.scrollY")

                # Verify we scrolled down
                did_scroll = scroll_final > 0

                # Verify section is in viewport
                how_in_view = page.evaluate("""
                    () => {
                        const el = document.getElementById('how-it-works');
                        if (!el) return false;
                        const rect = el.getBoundingClientRect();
                        return rect.top >= -100 && rect.top <= window.innerHeight;
                    }
                """)

                # Now click "FAQ" to scroll further down
                faq_link = page.locator('nav a[href="#faq"]')
                if faq_link.count() > 0:
                    faq_link.click()
                    time.sleep(1.5)
                    faq_in_view = page.evaluate("""
                        () => {
                            const el = document.getElementById('faq');
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            return rect.top >= -100 && rect.top <= window.innerHeight;
                        }
                    """)
                else:
                    faq_in_view = False

                details = f"scroll-behavior={scroll_behavior}, scrolled={did_scroll}, how-it-works inView={how_in_view}, faq inView={faq_in_view}"
                all_pass = has_smooth_scroll and did_scroll and how_in_view and faq_in_view
                report(12, all_pass, details)
            else:
                report(12, False, "How It Works nav link not found")
        except Exception as e:
            report(12, False, f"Exception: {e}")

        browser.close()

    # ================================================================
    # Summary
    # ================================================================
    print("\n" + "=" * 64)
    print("UI LANDING PAGE & NAVIGATION TEST SUMMARY")
    print("=" * 64)
    passed = sum(1 for _, p, _ in results if p)
    total = len(results)
    for step, p, detail in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] Step {step}: {detail[:90]}")
    print(f"\nResult: {passed}/{total} steps passed")
    print("=" * 64)

    return 0 if passed == total else 1


if __name__ == "__main__":
    exit(run_tests())
