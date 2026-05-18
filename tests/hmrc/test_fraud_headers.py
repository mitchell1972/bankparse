"""Unit tests for hmrc.services.fraud_headers.

Validates the structural correctness of every required header per the
WEB_APP_VIA_SERVER spec. The end-to-end validator-API test lives in
test_fraud_headers_validator.py and is opt-in.
"""

import datetime as dt
import os
import re
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


class _FakeClient:
    def __init__(self, host="10.0.0.99", port=43210):
        self.host = host
        self.port = port


class _FakeRequest:
    def __init__(self, *, headers=None, client_host="10.0.0.99", client_port=43210):
        self.headers = headers or {}
        self.client = _FakeClient(client_host, client_port)


def _ctx():
    return {
        "device_id": "beec798b-b366-47fa-b1f8-92cede14a1ce",
        "browser_user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "timezone": "UTC+01:00",
        "screens": [{"width": 1920, "height": 1080, "scaling-factor": 1, "colour-depth": 24}],
        "window": {"width": 1280, "height": 800},
        "mfa": [],
    }


def test_all_required_headers_present():
    from hmrc.services import fraud_headers
    req = _FakeRequest(headers={"x-forwarded-for": "198.51.100.7", "x-forwarded-port": "54321"})
    h = fraud_headers.build_headers(request=req, fraud_context=_ctx(), user_id=42)
    required = [
        "Gov-Client-Connection-Method",
        "Gov-Client-Browser-JS-User-Agent",
        "Gov-Client-Device-ID",
        "Gov-Client-Public-IP",
        "Gov-Client-Public-IP-Timestamp",
        "Gov-Client-Public-Port",
        "Gov-Client-Screens",
        "Gov-Client-Timezone",
        "Gov-Client-User-IDs",
        "Gov-Client-Window-Size",
        "Gov-Vendor-Forwarded",
        "Gov-Vendor-Product-Name",
        "Gov-Vendor-Version",
    ]
    for k in required:
        assert k in h, f"missing required header: {k}"
        assert h[k], f"header {k} must not be empty"


def test_connection_method_is_web_app_via_server():
    from hmrc.services import fraud_headers
    h = fraud_headers.build_headers(request=_FakeRequest(), fraud_context=_ctx(), user_id=1)
    assert h["Gov-Client-Connection-Method"] == "WEB_APP_VIA_SERVER"


def test_public_ip_reads_xforwardedfor_not_client_host():
    """Critical fraud-header rule: end user's IP, not the proxy's."""
    from hmrc.services import fraud_headers
    req = _FakeRequest(
        headers={"x-forwarded-for": "203.0.113.42, 10.0.0.1, 10.0.0.2"},
        client_host="10.0.0.99",
    )
    h = fraud_headers.build_headers(request=req, fraud_context=_ctx(), user_id=1)
    assert h["Gov-Client-Public-IP"] == "203.0.113.42"


def test_timestamp_format_iso_utc_with_millis():
    """HMRC requires yyyy-MM-ddTHH:mm:ss.sssZ exactly."""
    from hmrc.services import fraud_headers
    h = fraud_headers.build_headers(request=_FakeRequest(), fraud_context=_ctx(), user_id=1)
    ts = h["Gov-Client-Public-IP-Timestamp"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", ts), f"bad ts format: {ts}"
    # Parseable as a datetime.
    parsed = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
    assert (dt.datetime.utcnow() - parsed).total_seconds() < 10


def test_user_ids_uses_vendor_software_name():
    from hmrc.services import fraud_headers
    h = fraud_headers.build_headers(request=_FakeRequest(), fraud_context=_ctx(), user_id=42)
    # We default vendor software name to 'bankscan-ai' (urlencoded same).
    assert h["Gov-Client-User-IDs"] == "bankscan-ai=42"


def test_screens_serialised_as_ampersand_kv_with_url_encoded_keys():
    from hmrc.services import fraud_headers
    h = fraud_headers.build_headers(request=_FakeRequest(), fraud_context=_ctx(), user_id=1)
    s = h["Gov-Client-Screens"]
    # keys must be percent-encoded → 'scaling-factor' → 'scaling-factor'
    # (`-` is safe in RFC3986), but it must not be empty.
    assert "width=1920" in s
    assert "height=1080" in s
    assert re.search(r"scaling[-%2D]factor=1", s)


def test_vendor_product_name_is_urlencoded():
    """Spaces in product name must be %20."""
    from hmrc.services import fraud_headers
    h = fraud_headers.build_headers(request=_FakeRequest(), fraud_context=_ctx(), user_id=1)
    # The product name URL-encodes everything per spec.
    assert h["Gov-Vendor-Product-Name"] == "BankScan%20AI"


def test_vendor_forwarded_has_both_by_and_for():
    from hmrc.services import fraud_headers
    req = _FakeRequest(headers={"x-forwarded-for": "198.51.100.99"})
    h = fraud_headers.build_headers(
        request=req, fraud_context=_ctx(), user_id=1, our_public_ip="34.107.222.10",
    )
    fwd = h["Gov-Vendor-Forwarded"]
    assert fwd == "by=34.107.222.10&for=198.51.100.99"


def test_optional_mfa_included_when_provided():
    from hmrc.services import fraud_headers
    ctx = _ctx()
    ctx["mfa"] = [{"type": "AUTH_CODE", "timestamp": "2026-05-18T12:34Z", "unique-reference": "abc"}]
    h = fraud_headers.build_headers(request=_FakeRequest(), fraud_context=ctx, user_id=1)
    assert "Gov-Client-Multi-Factor" in h
    # `:` in timestamp must be percent-encoded inside the structured value.
    assert "%3A" in h["Gov-Client-Multi-Factor"]


def test_optional_mfa_omitted_when_empty():
    from hmrc.services import fraud_headers
    h = fraud_headers.build_headers(request=_FakeRequest(), fraud_context=_ctx(), user_id=1)
    assert "Gov-Client-Multi-Factor" not in h
