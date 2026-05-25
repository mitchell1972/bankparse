#!/usr/bin/env python3
"""
HMRC Developer Hub — local sandbox connectivity check.

Run after wiring your sandbox client credentials into ``.env.hmrc``:

    export $(grep -v '^#' .env.hmrc | xargs)
    python scripts/hmrc_dev_hub_check.py

Each check prints PASS/FAIL; any failure carries the exact next action
to take. Exits 0 when everything passes, non-zero otherwise — safe to
chain into CI or a Makefile.

What it proves:

  1. The four required env vars are present.
  2. ``client_credentials`` grant against the sandbox token endpoint
     returns an application-restricted access token.
  3. ``POST /create-test-user/individuals`` mints a fresh sandbox NINO.
  4. ``POST /individuals/self-assessment-test-support/business/{nino}``
     accepts our wire body shape unchanged.
  5. The Fraud Prevention Headers Validator accepts the headers our
     ``hmrc/services/fraud_headers.py`` builds.

Steps 3 + 4 + 5 leave artefacts on the sandbox account (a test user, a
business, an audit row); HMRC doesn't bill for them and they expire on
their own. Step 5 emits a single validator call per run.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable

# Allow the script to run from the repo root or from scripts/ — the imports
# below need ``hmrc`` and ``services`` on the path.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Pretty terminal output. ANSI colour without a dependency.
# ---------------------------------------------------------------------------

_USE_COLOUR = sys.stdout.isatty() and os.environ.get("NO_COLOR", "") == ""

def _ansi(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOUR else s

def _pass(label: str, detail: str = "") -> None:
    tag = _ansi("32;1", "PASS")
    print(f"  [{tag}] {label}" + (f" — {detail}" if detail else ""))

def _fail(label: str, fix: str) -> None:
    tag = _ansi("31;1", "FAIL")
    print(f"  [{tag}] {label}")
    print(f"         {_ansi('33', 'How to fix:')} {fix}")


# ---------------------------------------------------------------------------
# Step runner
# ---------------------------------------------------------------------------

class StopChecks(Exception):
    """Raised by a step when no later step could possibly succeed."""


def _run(label: str, fn: Callable[[], str | None]) -> bool:
    try:
        detail = fn() or ""
    except _FailWithFix as exc:
        _fail(label, exc.fix)
        if exc.fatal:
            raise StopChecks() from None
        return False
    except Exception as exc:  # noqa: BLE001 — surface every error path
        _fail(label, f"Unexpected error: {type(exc).__name__}: {exc}")
        return False
    _pass(label, detail)
    return True


class _FailWithFix(Exception):
    def __init__(self, fix: str, fatal: bool = False):
        self.fix = fix
        self.fatal = fatal


# ---------------------------------------------------------------------------
# The actual checks
# ---------------------------------------------------------------------------

REQUIRED_ENV = (
    "HMRC_CLIENT_ID",
    "HMRC_CLIENT_SECRET",
    "HMRC_REDIRECT_URI",
    "HMRC_TOKEN_ENCRYPTION_KEY",
)


def check_env() -> str | None:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise _FailWithFix(
            "Set these in `.env.hmrc` then re-export: " + ", ".join(missing) +
            ". See hmrc/docs/dev-hub-setup.md Step 3.",
            fatal=True,
        )
    env_kind = os.environ.get("HMRC_ENV", "sandbox").lower()
    if env_kind != "sandbox":
        # Block by default — this script is a sandbox self-test, not a prod
        # smoke test. Production has different secret hygiene rules.
        raise _FailWithFix(
            f"HMRC_ENV={env_kind!r} — this script targets sandbox only. "
            "Re-export with HMRC_ENV=sandbox before retrying.",
            fatal=True,
        )
    return f"HMRC_ENV=sandbox, client_id={os.environ['HMRC_CLIENT_ID'][:8]}…"


# Build a tiny stub request object so the HMRC client + fraud-headers module
# can extract per-call values without us booting the whole FastAPI app.

class _StubRequest:
    """Minimum interface required by `hmrc.services.client.request()`.

    The journey runs inside Playwright + FastAPI; the dev-hub check is a CLI
    script. We supply just enough request-shaped attributes (cookies, headers,
    client.host, state) that the HMRC client and fraud-header builder can run
    without raising AttributeError.
    """
    def __init__(self):
        self.cookies = {}
        self.headers = {}
        self.client = type("C", (), {"host": "127.0.0.1", "port": 0})()
        self.state = type("S", (), {"user": None})()

    @property
    def url(self):
        return type("U", (), {"path": "/dev-hub-check"})()


def check_application_token() -> str:
    """Step 2 — client_credentials grant returns an access token."""
    from hmrc.services import sandbox as _sandbox
    from hmrc.services.client import HmrcApiError

    try:
        token = _sandbox.fetch_application_token()
    except HmrcApiError as exc:
        # Token endpoint errors are almost always actionable: bad creds,
        # wrong env, or missing API subscription.
        raise _FailWithFix(
            f"HMRC token endpoint rejected the request: HTTP "
            f"{exc.status_code} — {str(exc.body)[:300]}. "
            "Check (a) client_id + secret are exactly what the Developer "
            "Hub shows, (b) the app is subscribed to Self Assessment "
            "Test Support v1.0.",
            fatal=True,
        )
    return f"token len={len(token)}"


# Persisted between Step 3 + Step 4 — we mint a user then use their NINO
# to create a business under it.
_MINTED: dict[str, str] = {}


def check_create_test_user() -> str:
    """Step 3 — mint a fresh sandbox individual."""
    from hmrc.services import sandbox as _sandbox
    from hmrc.services.client import HmrcApiError

    try:
        data = _sandbox.create_test_individual()
    except HmrcApiError as exc:
        # Most common rejection: app not subscribed to the right API
        # version, OR the sandbox is having a flake.
        if exc.status_code == 403:
            fix = (
                "HMRC returned 403. Almost always means the app isn't yet "
                "active for Create Test User. Wait ~60 s after subscribing "
                "to `Self Assessment Test Support`, then re-run."
            )
        else:
            fix = (
                f"HMRC HTTP {exc.status_code}: {str(exc.body)[:300]}. "
                "See hmrc/docs/dev-hub-setup.md Troubleshooting."
            )
        raise _FailWithFix(fix)

    nino = data.get("nino")
    user_id = data.get("userId")
    if not nino or not user_id:
        raise _FailWithFix(
            f"HMRC accepted the call but didn't return a NINO + userId. "
            f"Raw response: {data!r}"
        )
    _MINTED["nino"] = nino
    _MINTED["user_id"] = user_id
    return f"NINO={nino} userId={user_id}"


def check_create_test_business() -> str:
    """Step 4 — create a self-employment business under the NINO from
    step 3. Talks straight to HMRC's Test Support endpoint with the
    application bearer token — bypasses the user-OAuth flow because
    the check script isn't a browser.
    """
    import httpx
    from hmrc import config as _cfg
    from hmrc.services import sandbox as _sandbox

    nino = _MINTED.get("nino")
    if not nino:
        raise _FailWithFix("Step 3 didn't return a NINO; can't continue.")

    token = _sandbox.fetch_application_token()
    today = date.today()
    ty_start = (
        date(today.year, 4, 6) if today >= date(today.year, 4, 6)
        else date(today.year - 1, 4, 6)
    )
    ty_end = date(ty_start.year + 1, 4, 5)
    body = {
        "typeOfBusiness": "self-employment",
        "firstAccountingPeriodStartDate": ty_start.isoformat(),
        "firstAccountingPeriodEndDate": ty_end.isoformat(),
        "accountingType": "CASH",
        "tradingName": "BankScan dev-hub check"[:35],
        "businessAddressLineOne": "47 Union Walk",
        "businessAddressPostcode": "TS25 1PA",
        "businessAddressCountryCode": "GB",
    }
    url = (
        f"{_cfg.HMRC_BASE_URL}/individuals/self-assessment-test-support/"
        f"business/{nino}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.hmrc.1.0+json",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=body, headers=headers)

    if resp.status_code >= 400:
        raise _FailWithFix(
            f"HMRC HTTP {resp.status_code} on create-business: "
            f"{resp.text[:300]}. Most common cause: app not subscribed to "
            "`Self Assessment Test Support` v1.0 OR the application token "
            "doesn't have the create-business scope (re-tick on the "
            "subscriptions page)."
        )
    out = resp.json()
    biz_id = out.get("businessId") or (out.get("business") or {}).get("businessId")
    if not biz_id:
        raise _FailWithFix(
            f"HMRC accepted the call but didn't return a businessId: {out!r}"
        )
    return f"businessId={biz_id}"


def check_fraud_headers_validator() -> str:
    """Step 5 — submit the headers our fraud-headers builder produces to
    HMRC's validator. Fails noisily if any mandatory header is missing
    or malformed — top reason real recognition applications get rejected.
    """
    import httpx
    from hmrc import config as _cfg
    from hmrc.services import fraud_headers as _fh

    request = _StubRequest()
    headers = _fh.build_headers(request=request, fraud_context={}, user_id=0)
    # Filter empties just like the client does.
    headers = {k: v for k, v in headers.items() if v}
    # The validator endpoint is application-restricted but our test-api
    # validator accepts unauthenticated calls when only inspecting headers.
    url = f"{_cfg.HMRC_BASE_URL}/test/fraud-prevention-headers/validate"
    accept = "application/vnd.hmrc.1.0+json"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers={**headers, "Accept": accept})

    if resp.status_code == 401:
        # Validator requires app auth on some environments — fall back to
        # the application token.
        from hmrc.services import sandbox as _sandbox
        token = _sandbox.fetch_application_token()
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                url, headers={**headers, "Accept": accept,
                              "Authorization": f"Bearer {token}"},
            )

    if resp.status_code >= 400:
        raise _FailWithFix(
            f"Validator HTTP {resp.status_code}: {resp.text[:400]}. "
            "Check hmrc/services/fraud_headers.py — most likely cause is "
            "an env var that fraud_headers.build_headers reads being unset."
        )
    body = resp.json() if resp.headers.get("content-type", "").startswith(
        "application/json"
    ) else {}
    errors = body.get("errors") or body.get("fraudPreventionHeadersErrors") or []
    if errors:
        raise _FailWithFix(
            f"Validator returned errors: {errors[:5]!r}. Full body: "
            f"{str(body)[:500]}"
        )
    return "validator returned no errors"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(_ansi("1", "HMRC Developer Hub — sandbox connectivity check"))
    print(f"Started {time.strftime('%Y-%m-%d %H:%M:%S')} (sandbox)\n")

    steps = (
        ("Env vars present", check_env),
        ("OAuth client_credentials token exchange", check_application_token),
        ("Create sandbox test individual", check_create_test_user),
        ("Create sandbox SE business under that NINO", check_create_test_business),
        ("Fraud-prevention headers validator", check_fraud_headers_validator),
    )

    all_pass = True
    for label, fn in steps:
        try:
            if not _run(label, fn):
                all_pass = False
        except StopChecks:
            print(
                f"\n{_ansi('31', 'Stopping early')} — later checks depend on "
                "this passing. Fix and re-run."
            )
            return 2
    print()
    if all_pass:
        print(_ansi("32;1", "ALL CHECKS PASSED."))
        print("Next: run the real-sandbox tests")
        print("  HMRC_REAL_SANDBOX_E2E=1 pytest tests/e2e/test_hmrc_real_sandbox.py -xvs")
        return 0
    print(_ansi("31;1", "Some checks failed — see fix lines above."))
    return 1


if __name__ == "__main__":
    sys.exit(main())
