"""Opt-in test against HMRC's live fraud-prevention validator.

Skipped unless the env var BANKSCAN_RUN_HMRC_VALIDATOR=1 is set. CI should
enable this against the sandbox before any recognition submission.

Endpoint:
    POST https://test-api.service.hmrc.gov.uk/test/fraud-prevention-headers/validate

The validator returns 200 + {} on success, or a body listing
errors[] / warnings[] when something's off. We treat ANY errors as a test
failure; warnings are logged but don't fail.
"""

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

pytestmark = pytest.mark.skipif(
    os.environ.get("BANKSCAN_RUN_HMRC_VALIDATOR") != "1",
    reason="opt-in: set BANKSCAN_RUN_HMRC_VALIDATOR=1 (and HMRC env vars) to run",
)


class _FakeClient:
    def __init__(self, host="198.51.100.7", port=54321):
        self.host = host
        self.port = port


class _FakeRequest:
    def __init__(self):
        self.headers = {"x-forwarded-for": "198.51.100.7", "x-forwarded-port": "54321"}
        self.client = _FakeClient()


def test_validator_accepts_our_headers():
    import httpx
    from hmrc import config as cfg
    from hmrc.services import fraud_headers

    if not cfg.HMRC_CLIENT_ID or not cfg.HMRC_CLIENT_SECRET:
        pytest.skip("HMRC sandbox credentials not in env")

    ctx = {
        "device_id": "beec798b-b366-47fa-b1f8-92cede14a1ce",
        "browser_user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
        "timezone": "UTC+01:00",
        "screens": [{"width": 1920, "height": 1080, "scaling-factor": 2, "colour-depth": 24}],
        "window": {"width": 1280, "height": 800},
        "mfa": [],
    }
    headers = fraud_headers.build_headers(
        request=_FakeRequest(),
        fraud_context=ctx,
        user_id=12345,
        our_public_ip="34.107.222.10",
    )

    url = f"{cfg.SANDBOX_BASE_URL}{cfg.VALIDATOR_VALIDATE_PATH}"
    # The validator endpoint expects an Authorization bearer; in sandbox the
    # 'server token' from the app credentials page is also accepted. For this
    # CI test we assume HMRC_SERVER_TOKEN is set; otherwise skip.
    server_token = os.environ.get("HMRC_SERVER_TOKEN", "")
    if not server_token:
        pytest.skip("HMRC_SERVER_TOKEN not in env — needed to call the validator")

    auth_headers = {
        "Authorization": f"Bearer {server_token}",
        "Accept": "application/vnd.hmrc.1.0+json",
        **headers,
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=auth_headers, json={})

    # The validator returns 200 always; the body tells you pass/fail.
    assert resp.status_code in (200, 204), f"unexpected status {resp.status_code}: {resp.text}"
    if resp.status_code == 204:
        return  # no content, no issues

    body = resp.json() if resp.content else {}
    errors = body.get("errors") or body.get("failures") or []
    warnings = body.get("warnings") or []
    if warnings:
        print(f"[fraud-validator] warnings: {warnings}")
    assert not errors, f"validator reported errors: {errors}"
