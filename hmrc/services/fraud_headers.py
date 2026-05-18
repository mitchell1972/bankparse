"""
HMRC Fraud Prevention Headers — builder for WEB_APP_VIA_SERVER connection method.

Reference (read 2026-05-18, stored in memory/reference_hmrc_fraud_prevention_headers.md):
  https://developer.service.hmrc.gov.uk/guides/fraud-prevention/

Required headers for our topology (browser → FastAPI → HMRC):

    Gov-Client-Connection-Method            'WEB_APP_VIA_SERVER'
    Gov-Client-Browser-JS-User-Agent        browser navigator.userAgent
    Gov-Client-Device-ID                    persistent UUID in browser localStorage
    Gov-Client-Public-IP                    end user's public IP (from X-Forwarded-For)
    Gov-Client-Public-IP-Timestamp          when we observed that IP (UTC, ms precision)
    Gov-Client-Public-Port                  end user's TCP source port
    Gov-Client-Screens                      width/height/scaling/colour-depth
    Gov-Client-Timezone                     UTC±hh:mm
    Gov-Client-User-IDs                     bankscan-ai=<user_id>
    Gov-Client-Window-Size                  width/height in pixels
    Gov-Vendor-Forwarded                    server hop chain
    Gov-Vendor-Product-Name                 'BankScan AI' (url-encoded)
    Gov-Vendor-Version                      'bankscan-ai=<version>'

Optional but recommended:
    Gov-Vendor-Public-IP                    our server's outbound IP
    Gov-Client-Multi-Factor                 if user authed with MFA this session

Top reasons recognition applications fail (per the HMRC spec):
  1. Non-persistent Gov-Client-Device-ID
  2. Gov-Client-Public-IP is server IP (read X-Forwarded-For[0], not request.client.host)
  3. Timestamps without milliseconds
  4. IPv6 colons not percent-encoded in Gov-Vendor-Forwarded
  5. Never running the validator
"""

from __future__ import annotations

import datetime as _dt
import urllib.parse as _urlparse
from typing import Any

from .. import config as _cfg


def _now_utc_iso_ms() -> str:
    """UTC ISO-8601 timestamp with milliseconds — HMRC requires this exact format.

    Example: 2026-05-18T11:23:45.123Z
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    # %f is microseconds; truncate to ms then re-attach Z manually.
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _q(value: str) -> str:
    """URL-encode a single value per RFC 3986. Used inside structured headers.

    HMRC requires every key and value to be percent-encoded, but NOT the
    separators (=, &, ,). We call this on individual values only.
    """
    if value is None:
        return ""
    return _urlparse.quote(str(value), safe="")


def _serialise_kv(pairs: dict[str, Any]) -> str:
    """Serialise a single structured header value: k1=v1&k2=v2."""
    return "&".join(f"{_q(k)}={_q(v)}" for k, v in pairs.items() if v is not None and v != "")


def _serialise_list(items: list[dict[str, Any]]) -> str:
    """Serialise multi-struct headers (e.g. multiple screens): struct,struct."""
    return ",".join(_serialise_kv(it) for it in items)


def _public_ip_from_request(request) -> tuple[str, int]:
    """Extract the END USER's public IP + source port from an incoming request.

    Behind a reverse proxy (Railway edge), request.client.host is the proxy's
    inside-the-platform IP, NOT the user's public IP. We must walk
    X-Forwarded-For to find the originating client.

    Returns (ip, port). Port defaults to 0 if not available (Public-Port is
    required by HMRC, so prefer the X-Forwarded-Port header from the proxy if
    set; otherwise return 0 and let the caller decide whether to fail closed).
    """
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        # leftmost = original client
        ip = xff.split(",")[0].strip()
    else:
        ip = (request.client.host if getattr(request, "client", None) else "") or ""

    port_str = request.headers.get("x-forwarded-port", "").strip()
    try:
        port = int(port_str) if port_str else (request.client.port if getattr(request, "client", None) else 0)
    except (ValueError, TypeError):
        port = 0
    return ip, int(port or 0)


def _vendor_forwarded(request, our_public_ip: str = "") -> str:
    """Build Gov-Vendor-Forwarded — the hop chain from end user to us.

    For our topology there is typically one hop: Railway-edge → our app.
    `for` = the IP the request was forwarded from (the user's public IP).
    `by`  = the IP that received it (Railway's edge or our app — we use the
    `our_public_ip` if provided).

    Multiple hops are comma-separated. IPv6 colons must be percent-encoded
    inside the value.
    """
    user_ip, _ = _public_ip_from_request(request)
    hop = {"by": our_public_ip or "0.0.0.0", "for": user_ip or "0.0.0.0"}
    return _serialise_kv(hop)


def build_headers(
    *,
    request,
    fraud_context: dict,
    user_id: int | str,
    our_public_ip: str = "",
    vendor_version: str | None = None,
) -> dict[str, str]:
    """Build the dict of HMRC fraud prevention headers for an outbound MTD call.

    Args:
        request: the incoming Starlette/FastAPI Request, used to extract the
            user's Public-IP / Public-Port / Vendor-Forwarded.
        fraud_context: dict of browser-collected fields posted earlier by
            `static/hmrc/fraud-collect.js` and stored per-session:
                {
                  "device_id":            "uuid-v4 stable across visits",
                  "browser_user_agent":   "navigator.userAgent",
                  "timezone":             "UTC+01:00",
                  "screens":              [{"width":1920,"height":1080,
                                            "scaling-factor":1,"colour-depth":24}, ...],
                  "window":               {"width":1256,"height":803},
                  "mfa":                  [{"type":"AUTH_CODE",
                                            "timestamp":"2026-05-18T12:34Z",
                                            "unique-reference":"..."}],
                }
        user_id: BankParse's internal user id; used as Gov-Client-User-IDs value.
        our_public_ip: this server's outbound public IP (optional, recommended).
        vendor_version: software version string. Defaults to the value in
            hmrc/config.py.

    Returns: dict[str, str] suitable for httpx `headers=`.
    """
    user_ip, user_port = _public_ip_from_request(request)
    ip_timestamp = _now_utc_iso_ms()
    version = vendor_version or _cfg.DEFAULT_VENDOR_VERSION

    screens = fraud_context.get("screens") or []
    window = fraud_context.get("window") or {}
    mfa = fraud_context.get("mfa") or []

    headers: dict[str, str] = {
        "Gov-Client-Connection-Method": _cfg.CONNECTION_METHOD,
        "Gov-Client-Browser-JS-User-Agent": fraud_context.get("browser_user_agent", ""),
        "Gov-Client-Device-ID": fraud_context.get("device_id", ""),
        "Gov-Client-Public-IP": user_ip or "",
        "Gov-Client-Public-IP-Timestamp": ip_timestamp,
        "Gov-Client-Public-Port": str(user_port or 0),
        "Gov-Client-Screens": _serialise_list(screens) if screens else "",
        "Gov-Client-Timezone": fraud_context.get("timezone", ""),
        "Gov-Client-User-IDs": _serialise_kv({_cfg.HMRC_VENDOR_SOFTWARE_NAME: str(user_id)}),
        "Gov-Client-Window-Size": _serialise_kv(window) if window else "",
        "Gov-Vendor-Forwarded": _vendor_forwarded(request, our_public_ip),
        "Gov-Vendor-Product-Name": _q(_cfg.HMRC_VENDOR_PRODUCT_NAME),
        "Gov-Vendor-Version": _serialise_kv({_cfg.HMRC_VENDOR_SOFTWARE_NAME: version}),
    }

    if our_public_ip:
        headers["Gov-Vendor-Public-IP"] = our_public_ip

    if mfa:
        headers["Gov-Client-Multi-Factor"] = _serialise_list(mfa)

    return headers
