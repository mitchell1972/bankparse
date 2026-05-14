"""
BankScan AI — Playwright Screen Recording Demo
Records two videos: statement processing and receipt processing.
Output: recordings/statement_demo.webm, recordings/receipt_demo.webm
"""

import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "https://bankscanai.com"
RECORDING_DIR = Path(__file__).parent / "recordings"
RECORDING_DIR.mkdir(exist_ok=True)

# Test files
STATEMENT_PDF = "/Users/mitchellagoma/Downloads/Statements/2026-01-21_Statement_2_copy_12.pdf"
RECEIPT_IMG = "/Users/mitchellagoma/Downloads/IMG_3451.HEIC"

# Login credentials (reads from env or uses defaults)
EMAIL = os.environ.get("DEMO_EMAIL", "mitchellagoma@gmail.com")
PASSWORD = os.environ.get("DEMO_PASSWORD", "")


def login(page):
    """Log in to the app."""
    page.goto(f"{BASE_URL}/login", wait_until="networkidle")
    page.fill('input[type="email"]', EMAIL)
    page.fill('input[type="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url("**/", timeout=15000)
    time.sleep(1)


def record_statement_demo():
    """Record the bank statement upload and conversion flow."""
    print("Recording statement demo...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(RECORDING_DIR),
            record_video_size={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            # Login
            login(page)
            print("  Logged in")

            # Ensure we're on the main page with Bank Statement tab
            page.wait_for_selector('text=Bank Statement', timeout=10000)
            time.sleep(1)

            # Click Bank Statement tab
            page.click('text=Bank Statement')
            time.sleep(0.5)
            print("  Selected Bank Statement tab")

            # Upload the PDF
            file_input = page.locator('input[type="file"]').first
            file_input.set_input_files(STATEMENT_PDF)
            time.sleep(1)
            print("  File selected")

            # Click Convert button
            convert_btn = page.locator('button:has-text("Convert")')
            convert_btn.click()
            print("  Converting... (waiting for results)")

            # Wait for results to appear (up to 120 seconds for AI parsing)
            page.wait_for_selector('.results.show, [id="statementResults"].show', timeout=120000)
            print("  Results appeared!")
            time.sleep(2)

            # Scroll through the results
            page.evaluate("document.querySelector('.results.show, [id=statementResults]')?.scrollIntoView({behavior: 'smooth'})")
            time.sleep(2)

            # Scroll down to see more transactions
            page.mouse.wheel(0, 400)
            time.sleep(1)
            page.mouse.wheel(0, 400)
            time.sleep(1)

            # Scroll to download link
            page.mouse.wheel(0, 400)
            time.sleep(2)

            print("  Statement demo complete")

        except Exception as e:
            print(f"  Error: {e}")
            page.screenshot(path=str(RECORDING_DIR / "statement_error.png"))

        finally:
            context.close()
            browser.close()

    # Rename the video
    videos = sorted(RECORDING_DIR.glob("*.webm"), key=lambda f: f.stat().st_mtime, reverse=True)
    if videos:
        target = RECORDING_DIR / "statement_demo.webm"
        if target.exists():
            target.unlink()
        videos[0].rename(target)
        print(f"  Saved: {target}")


def record_receipt_demo():
    """Record the receipt upload and extraction flow."""
    print("\nRecording receipt demo...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(RECORDING_DIR),
            record_video_size={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            # Login
            login(page)
            print("  Logged in")

            # Click Store Receipt tab using its ID and JS function
            page.click('#modeReceipt')
            time.sleep(1)
            print("  Selected Store Receipt tab")

            # Upload the receipt via the file input (set_input_files bypasses accept attr)
            page.set_input_files('#fileInput', RECEIPT_IMG)
            time.sleep(2)
            print("  File selected")

            # Click the parse button
            page.click('#parseBtn')
            time.sleep(1)
            print("  Extracting... (waiting for results)")

            # Wait for results (up to 120 seconds)
            page.wait_for_selector('.results.show, [id="receiptResults"].show', timeout=120000)
            print("  Results appeared!")
            time.sleep(2)

            # Scroll through the results
            page.evaluate("document.querySelector('.results.show, [id=receiptResults]')?.scrollIntoView({behavior: 'smooth'})")
            time.sleep(2)

            page.mouse.wheel(0, 300)
            time.sleep(1)
            page.mouse.wheel(0, 300)
            time.sleep(2)

            print("  Receipt demo complete")

        except Exception as e:
            print(f"  Error: {e}")
            page.screenshot(path=str(RECORDING_DIR / "receipt_error.png"))

        finally:
            context.close()
            browser.close()

    # Rename the video
    videos = sorted(RECORDING_DIR.glob("*.webm"), key=lambda f: f.stat().st_mtime, reverse=True)
    if videos:
        target = RECORDING_DIR / "receipt_demo.webm"
        if target.exists():
            target.unlink()
        videos[0].rename(target)
        print(f"  Saved: {target}")


if __name__ == "__main__":
    if not PASSWORD:
        print("Set DEMO_PASSWORD env var to run the recording.")
        print("  export DEMO_PASSWORD='your_password'")
        print("  python record_demo.py")
        exit(1)

    if not Path(STATEMENT_PDF).exists():
        print(f"Statement PDF not found: {STATEMENT_PDF}")
        exit(1)

    if not Path(RECEIPT_IMG).exists():
        print(f"Receipt image not found: {RECEIPT_IMG}")
        exit(1)

    record_statement_demo()
    record_receipt_demo()

    print(f"\nRecordings saved to: {RECORDING_DIR}/")
    print("  - statement_demo.webm")
    print("  - receipt_demo.webm")
