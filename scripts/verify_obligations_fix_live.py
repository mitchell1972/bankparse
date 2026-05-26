"""
One-off verification that the obligations friendly-error fix is LIVE
on bankscanai.com after the PR #86 merge.

Reproduces the exact scenario from the screenshot bug report:

  1. Logs into bankscanai.com as the supplied user
  2. Mints a fresh HMRC sandbox test individual via the existing route
  3. Drives Playwright through the full GG sandbox OAuth handshake using
     those minted credentials
  4. POSTs /api/hmrc/connect-businesses with a DIFFERENT NINO than the
     one we OAuth'd as → persists the foreign NINO + empty businesses
  5. POSTs /api/hmrc/sandbox/setup-complete → creates SE + property
     businesses on the foreign NINO (succeeds because it uses an app-
     restricted token, ignoring the OAuth identity)
  6. GETs /api/hmrc/obligations → this is where the bug fired before
     the fix. The endpoint now tries to fetch obligations for the
     foreign NINO using the OAuth token bound to the minted NINO,
     and HMRC returns 404 MATCHING_RESOURCE_NOT_FOUND.
  7. Asserts the error field contains OAUTH_NINO_MISMATCH (new
     friendly text) and NOT "MATCHING_RESOURCE_NOT_FOUND" (old raw
     dump).
  8. POSTs /api/hmrc/disconnect to clean up.

Run with:

    PROD_TEST_USER_EMAIL=... PROD_TEST_USER_PASSWORD=... \
      python scripts/verify_obligations_fix_live.py

Outputs PASS / FAIL with the actual error text observed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Reuse the smoke test's helpers so this script benefits from every fix the
# sub-agent put in (CSRF seeding, OAuth interstitials, GG selectors, etc).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.e2e.test_prod_hmrc_smoke import (  # noqa: E402
    PROD_URL,
    _seed_csrf_via_browser,
    _complete_oauth_handshake,
)

# We can't pull from playwright.sync_api at module import time inside a script
# that doesn't run under pytest — import here.
from playwright.sync_api import sync_playwright  # noqa: E402


# A NINO that won't match the OAuth'd identity. UK NINO format: AA######A.
# `MA` prefix isn't in HMRC's list of unallocated leading pairs, so it'll
# pass our regex; the sandbox won't have any data for it.
FOREIGN_NINO = "MA123456A"


def _login(page) -> None:
    email = os.environ["PROD_TEST_USER_EMAIL"]
    password = os.environ["PROD_TEST_USER_PASSWORD"]
    csrf = _seed_csrf_via_browser(page)
    r = page.request.post(
        PROD_URL + "/api/login",
        data=json.dumps({"email": email, "password": password}),
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        timeout=20_000,
    )
    if r.status != 200:
        raise SystemExit(f"Login failed: {r.status} {r.text()[:300]}")


def _disconnect(page) -> None:
    try:
        csrf = _seed_csrf_via_browser(page)
        page.request.post(
            PROD_URL + "/api/hmrc/disconnect",
            headers={"X-CSRF-Token": csrf},
            timeout=10_000,
        )
    except Exception as e:
        print(f"  cleanup warning: {e}")


def main() -> int:
    if not (os.environ.get("PROD_TEST_USER_EMAIL")
            and os.environ.get("PROD_TEST_USER_PASSWORD")):
        print("FAIL: set PROD_TEST_USER_EMAIL and PROD_TEST_USER_PASSWORD")
        return 2

    print(f"== Verifying obligations friendly-error fix on {PROD_URL} ==\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            print("1. Logging in as test user...")
            _login(page)

            # Clear any prior state so the OAuth dance starts fresh.
            print("2. Disconnecting any prior HMRC state...")
            _disconnect(page)

            print("3. Driving full OAuth handshake (mints sandbox user + signs in via HMRC GG)...")
            _complete_oauth_handshake(page)  # ends with tokens + NINO stored
            print("   OAuth complete.")

            print(f"4. Overwriting stored NINO with a foreign one ({FOREIGN_NINO})...")
            csrf = _seed_csrf_via_browser(page)
            r = page.request.post(
                PROD_URL + "/api/hmrc/connect-businesses",
                data=json.dumps({"nino": FOREIGN_NINO}),
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                timeout=15_000,
            )
            print(f"   connect-businesses: {r.status} {r.text()[:120]}")
            # 404 expected — friendly 'no businesses on this NINO yet' — NINO persisted

            print("5. Provisioning businesses on the foreign NINO via setup-complete...")
            csrf = _seed_csrf_via_browser(page)
            r = page.request.post(
                PROD_URL + "/api/hmrc/sandbox/setup-complete",
                headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
                data="{}",
                timeout=30_000,
            )
            print(f"   setup-complete: {r.status}")
            if r.status != 200:
                print(f"   body: {r.text()[:400]}")
                # Could happen if the test individual we OAuth'd as has restrictions
                # — not a failure of THIS verification, but means we can't induce
                # the 404 condition.
                print("FAIL: couldn't induce the mismatched state; setup failed.")
                return 1

            print("6. Fetching obligations — this is the path the fix targets...")
            r = page.request.get(
                PROD_URL + "/api/hmrc/obligations", timeout=15_000,
            )
            print(f"   obligations: {r.status}")
            body = r.json()
            err = body.get("error") or ""
            print(f"   error field: {err[:250]!r}")
            print()

            if "OAUTH_NINO_MISMATCH" in err:
                print("PASS — friendly error live on prod. The fix is deployed.")
                rc = 0
            elif "MATCHING_RESOURCE_NOT_FOUND" in err and "OAUTH_NINO_MISMATCH" not in err:
                print("FAIL — raw HMRC error still leaking. Deploy hasn't picked up.")
                rc = 1
            elif not err:
                # Possible if obligations succeeded — would happen if HMRC sandbox
                # decided the OAuth WAS valid for the foreign NINO, or if businesses
                # ended up empty for some other reason.
                print("INDETERMINATE — no error returned. Couldn't trigger the path.")
                print(f"   full body: {json.dumps(body)[:600]}")
                rc = 3
            else:
                print(f"INDETERMINATE — unexpected error format: {err[:300]}")
                rc = 3

            print("\n7. Cleaning up — disconnecting HMRC tokens...")
            _disconnect(page)
            print("   done.")
            return rc

        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
