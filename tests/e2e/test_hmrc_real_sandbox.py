"""
Real HMRC sandbox round-trip — proves our wire shapes match HMRC's
expectations against ``test-api.service.hmrc.gov.uk``.

Skipped by default. To run::

    export HMRC_REAL_SANDBOX_E2E=1
    export HMRC_CLIENT_ID=<from developer.service.hmrc.gov.uk>
    export HMRC_CLIENT_SECRET=<same>
    pytest tests/e2e/test_hmrc_real_sandbox.py -xvs

What it does (no Playwright — talks straight to HMRC):

  1. Get an application-restricted access token via client_credentials.
     Proves the OAuth client is registered correctly with HMRC and is
     subscribed to at least the Self Assessment Test Support API.

  2. POST /create-test-user/individuals to mint a fresh sandbox NINO +
     Government Gateway credentials. Proves the application has the right
     scopes to provision a test individual.

  3. POST /individuals/self-assessment-test-support/business/{nino} to
     provision an SE business under that NINO. Proves the Test Support
     create-business wire shape is accepted exactly as we send it.

If any of those rejects with 400/403, the recognition application would
fail in the same way — so this test is the cheapest way to catch a
shape regression before submitting to HMRC for SDST conformance.

We do NOT drive the full user OAuth (Government Gateway login → callback →
token exchange) here because that requires Playwright against a third-
party page and the test would couple to HMRC's UI cadence. The application
token + Test Support endpoints exercise the most failure-prone parts of
our wire format without that brittleness.
"""

from __future__ import annotations

import os
import time
from datetime import date

import httpx
import pytest

pytestmark = pytest.mark.hmrc_sandbox

REAL_SANDBOX_BASE = "https://test-api.service.hmrc.gov.uk"
TOKEN_PATH = "/oauth/token"
TEST_USER_PATH = "/create-test-user/individuals"
TEST_BUSINESS_PATH_TPL = "/individuals/self-assessment-test-support/business/{nino}"

_GATE_REASON = (
    "Real-sandbox E2E gated: set HMRC_REAL_SANDBOX_E2E=1 + HMRC_CLIENT_ID "
    "+ HMRC_CLIENT_SECRET to run. Skip is intentional — no creds = no test."
)


@pytest.fixture(scope="module")
def _real_sandbox_env():
    if os.environ.get("HMRC_REAL_SANDBOX_E2E", "").lower() not in ("1", "true", "yes"):
        pytest.skip(_GATE_REASON)
    cid = os.environ.get("HMRC_CLIENT_ID", "").strip()
    csec = os.environ.get("HMRC_CLIENT_SECRET", "").strip()
    if not (cid and csec):
        pytest.skip(_GATE_REASON)
    return {"client_id": cid, "client_secret": csec}


def _application_token(creds: dict) -> str:
    r = httpx.post(
        f"{REAL_SANDBOX_BASE}{TOKEN_PATH}",
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            # 'hello' is the scope HMRC docs ask for on client_credentials.
            "scope": "hello",
        },
        timeout=30.0,
    )
    if r.status_code >= 400:
        pytest.fail(
            f"HMRC sandbox token request failed: {r.status_code} {r.text[:500]}. "
            "Check the OAuth client is registered in developer.service.hmrc.gov.uk "
            "and subscribed to the Self Assessment Test Support API."
        )
    return r.json()["access_token"]


def test_application_token_exchange_against_real_sandbox(_real_sandbox_env):
    token = _application_token(_real_sandbox_env)
    assert isinstance(token, str) and len(token) > 16


def test_create_test_individual_against_real_sandbox(_real_sandbox_env):
    token = _application_token(_real_sandbox_env)
    r = httpx.post(
        f"{REAL_SANDBOX_BASE}{TEST_USER_PATH}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.hmrc.1.0+json",
            "Content-Type": "application/json",
        },
        json={
            "serviceNames": [
                "national-insurance", "self-assessment", "mtd-income-tax",
            ],
        },
        timeout=30.0,
    )
    assert r.status_code in (200, 201), (
        f"Create test user rejected by real sandbox: "
        f"{r.status_code} {r.text[:500]}"
    )
    body = r.json()
    assert body.get("nino"), f"No NINO in HMRC response: {body!r}"
    assert body.get("userId"), f"No GG userId in HMRC response: {body!r}"


def test_create_test_business_shape_accepted_by_real_sandbox(_real_sandbox_env):
    """The exact wire body our `services/sandbox.create_test_business` sends.
    Most expensive way for HMRC to reject our app at recognition time, so we
    prove it works against the real sandbox here."""
    token = _application_token(_real_sandbox_env)
    # Mint a fresh test user so we get a NINO this app token can mutate.
    mint = httpx.post(
        f"{REAL_SANDBOX_BASE}{TEST_USER_PATH}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.hmrc.1.0+json",
            "Content-Type": "application/json",
        },
        json={"serviceNames": ["mtd-income-tax", "self-assessment"]},
        timeout=30.0,
    )
    assert mint.status_code in (200, 201), (
        f"Mint failed: {mint.status_code} {mint.text[:500]}"
    )
    nino = mint.json()["nino"]

    # Tax-year window — same shape our service uses.
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
        "tradingName": "BankScan E2E sole trader"[:35],
        "businessAddressLineOne": "47 Union Walk",
        "businessAddressPostcode": "TS25 1PA",
        "businessAddressCountryCode": "GB",
    }
    r = httpx.post(
        REAL_SANDBOX_BASE + TEST_BUSINESS_PATH_TPL.format(nino=nino),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.hmrc.1.0+json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30.0,
    )
    assert r.status_code in (200, 201), (
        f"Create-business rejected by real sandbox — wire shape regression. "
        f"Status: {r.status_code} Body: {r.text[:500]}. Request body sent: {body!r}"
    )
    out = r.json()
    assert out.get("businessId") or (out.get("business") or {}).get("businessId"), (
        f"No businessId in HMRC response: {out!r}"
    )
