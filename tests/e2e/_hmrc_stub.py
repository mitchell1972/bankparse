"""
Stub HMRC MTD API server for E2E tests.

Stands in for ``https://test-api.service.hmrc.gov.uk`` so the Playwright
journey can exercise the full submit flow (OAuth round-trip → obligations →
quarterly update POST) without depending on a real HMRC sandbox app or
sandbox creds. Returns the canonical wire shapes documented at
https://developer.service.hmrc.gov.uk so any divergence in our payload
builders (e.g. the #83 path bug or the #84 missing-zeros bug) surfaces as
a test failure here rather than in production.

Captures every inbound request on `recorded_requests` so tests can assert
on what we actually sent.

Run via :class:`HmrcStub` (context manager). Pick a port with
``socket.bind(('127.0.0.1', 0))`` before instantiating.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger("bankparse.tests.hmrc_stub")


# Canonical values returned to the caller — tests assert against these.
STUB_ACCESS_TOKEN = "stub-hmrc-access-token-abcdef"
STUB_REFRESH_TOKEN = "stub-hmrc-refresh-token-123456"
STUB_TRANSACTION_REFERENCE = "STUB-TX-REF-001"
STUB_TRANSACTION_REFERENCE_PROP = "STUB-TX-REF-PROP-001"
STUB_EOPS_REFERENCE = "STUB-EOPS-REF-001"
STUB_FINAL_DECL_REFERENCE = "STUB-FINAL-DECL-001"
STUB_CALCULATION_ID = "stub-calc-id-001"
STUB_TOTAL_TAX_AMOUNT = 1980.0  # what the calculation endpoint returns
STUB_BUSINESS_ID_SE = "XAIS00000000001"
STUB_BUSINESS_ID_PROP = "XPIS00000000002"
STUB_NINO = "AA123456A"


def _today() -> _dt.date:
    return _dt.date.today()


def _open_quarter_window() -> tuple[str, str, str]:
    """Pick a quarter [start, end, due] whose due date is within 14 days so
    the obligations service marks it ``open`` (the file.html UI only shows
    a Submit button on open or overdue rows). ``period_end`` is today so
    transactions dated today still fall inside [start, end]."""
    today = _today()
    start = today - _dt.timedelta(days=30)
    end = today
    due = today + _dt.timedelta(days=7)
    return start.isoformat(), end.isoformat(), due.isoformat()


class _Handler(BaseHTTPRequestHandler):
    """Routes are matched longest-prefix-first in :meth:`_dispatch`."""

    server: "HmrcStub.Server"  # type: ignore[assignment]

    # Quiet — pytest captures stderr so default logging would spam.
    def log_message(self, fmt, *args):  # noqa: N802 - stdlib override
        logger.debug("hmrc-stub %s - %s", self.address_string(), fmt % args)

    # -- request capture -----------------------------------------------------

    def _record(self, method: str, body: Any) -> None:
        self.server.recorded_requests.append({
            "method": method,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body,
        })

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"_raw": raw.decode("utf-8", "replace")}

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def _json(self, status: int, body: dict | list) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    # -- HTTP verb dispatch --------------------------------------------------

    def do_GET(self):  # noqa: N802
        self._record("GET", None)
        self._dispatch_get()

    def do_POST(self):  # noqa: N802
        ctype = (self.headers.get("Content-Type") or "").lower()
        body = self._read_form() if "application/x-www-form-urlencoded" in ctype else self._read_json()
        self._record("POST", body)
        self._dispatch_post(body)

    def do_PUT(self):  # noqa: N802
        body = self._read_json()
        self._record("PUT", body)
        self._json(200, {"ok": True})

    # -- routing -------------------------------------------------------------

    def _dispatch_get(self) -> None:
        path = self.path.split("?", 1)[0]

        # OAuth authorize: pretend the user clicked Approve and immediately
        # bounce back to the redirect_uri with code + state intact. Real HMRC
        # would render a Government Gateway sign-in page here.
        if path == "/oauth/authorize":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            redirect_uri = (qs.get("redirect_uri") or [""])[0]
            state = (qs.get("state") or [""])[0]
            target = (
                f"{redirect_uri}?code=stub-auth-code&state={urllib.parse.quote(state)}"
            )
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return

        # Obligations — return one open quarter per business type so the
        # dashboard renders a Submit button on the current period for both
        # SE (sole trader) and property (landlord). Most BankScan users
        # have both, so the journey exercises both.
        # Paths:
        #   /individuals/business/self-employment/{nino}/{bizId}/obligations
        #   /individuals/business/property/{nino}/{bizId}/obligations
        if "/obligations" in path and "/self-employment/" in path:
            start, end, due = _open_quarter_window()
            self._json(200, {"obligations": [{
                "identification": {
                    "referenceType": "selfEmploymentId",
                    "referenceNumber": STUB_BUSINESS_ID_SE,
                    "incomeSourceType": "self-employment",
                },
                "obligationDetails": [{
                    "status": "Open",
                    "inboundCorrespondenceFromDate": start,
                    "inboundCorrespondenceToDate": end,
                    "inboundCorrespondenceDueDate": due,
                    "periodKey": "#001",
                }],
            }]})
            return

        if "/obligations" in path and "/property/" in path:
            start, end, due = _open_quarter_window()
            self._json(200, {"obligations": [{
                "identification": {
                    "referenceType": "incomeSourceId",
                    "referenceNumber": STUB_BUSINESS_ID_PROP,
                    "incomeSourceType": "uk-property",
                },
                "obligationDetails": [{
                    "status": "Open",
                    "inboundCorrespondenceFromDate": start,
                    "inboundCorrespondenceToDate": end,
                    "inboundCorrespondenceDueDate": due,
                    "periodKey": "#001",
                }],
            }]})
            return

        # Business details list — returned to setup-complete to enumerate
        # existing businesses (we return none so setup-complete provisions a
        # fresh SE business).
        if "/individuals/business/details/" in path and path.endswith("/list"):
            self._json(200, {"listOfBusinesses": []})
            return

        # Fraud-prevention validator — accept everything in tests.
        if path.endswith("/fraud-prevention-headers/validate"):
            self._json(200, {"errors": [], "warnings": []})
            return

        # Tax calculation result — GET /individuals/calculations/{nino}/
        # self-assessment/{taxYear}/{calculationId}. Returns the mature
        # body with totalTaxAmount populated so the journey can assert
        # a non-zero tax owed.
        if (
            "/individuals/calculations/" in path
            and "/self-assessment/" in path
            and "/final-declaration" not in path
            and path.endswith(STUB_CALCULATION_ID)
        ):
            self._json(200, {
                "calculation": {
                    "taxCalculation": {
                        "incomeTax": {"payPensionsProfit": {
                            "incomeTaxAmount": 1480.0,
                        }},
                        "nics": {"class4Nics": {"nicsAmount": 500.0}},
                        "totalTaxAmount": STUB_TOTAL_TAX_AMOUNT,
                    },
                    "totalIncome": {"totalIncomeReceived": 16450.0},
                },
                "metadata": {"calculationId": STUB_CALCULATION_ID},
            })
            return

        self._json(404, {"code": "MATCHING_RESOURCE_NOT_FOUND", "_stub_path": path})

    def _dispatch_post(self, body: Any) -> None:
        path = self.path.split("?", 1)[0]

        # OAuth token endpoint — covers both grant_type=authorization_code
        # and grant_type=refresh_token. Body is form-encoded per RFC 6749.
        if path == "/oauth/token":
            self._json(200, {
                "access_token": STUB_ACCESS_TOKEN,
                "refresh_token": STUB_REFRESH_TOKEN,
                "token_type": "bearer",
                "expires_in": 14400,
                "scope": (
                    "read:self-assessment write:self-assessment "
                    "read:test-support write:test-support"
                ),
            })
            return

        # Create test individual — sandbox test-support endpoint.
        if path == "/create-test-user/individuals":
            self._json(201, {
                "nino": STUB_NINO,
                "userId": "stub-gg-userid-001",
                "password": "stubpass",
                "mtdItId": "XAIT0000000001",
                "saUtr": "1234567890",
                "userFullName": "Stub Tester",
                "emailAddress": "stub@example.test",
                "groupIdentifier": "stub-group-001",
            })
            return

        # Create business — sandbox test-support endpoint, used by
        # /api/hmrc/sandbox/create-test-business and setup-complete.
        if "/individuals/business/details/" in path and path.endswith("/create"):
            self._json(201, {"businessId": STUB_BUSINESS_ID_SE})
            return

        # THE actual Submit endpoint — what file.html ultimately hits.
        # Path: /individuals/business/self-employment/{nino}/{bizId}/period
        if "/self-employment/" in path and path.endswith("/period"):
            # Catch the bugs from PR #83 (/period-summaries) and PR #84
            # (empty periodIncome/periodExpenses → RULE_INCORRECT_OR_EMPTY_
            # BODY_SUBMITTED) right here in the stub.
            if not isinstance(body, dict):
                self._json(400, {"code": "INVALID_BODY", "message": "Body is not JSON object"})
                return
            for required in ("periodDates", "periodIncome", "periodExpenses"):
                if required not in body:
                    self._json(400, {
                        "code": "RULE_INCORRECT_OR_EMPTY_BODY_SUBMITTED",
                        "message": f"Missing required field: {required}",
                    })
                    return
            self._json(200, {
                "transactionReference": STUB_TRANSACTION_REFERENCE,
                "paymentReference": "PMTREF-001",
            })
            return

        # End of Period Statement — finalises one business for the tax year.
        # Path: /individuals/business/{nino}/{bizId}/end-of-period-statements
        if path.endswith("/end-of-period-statements"):
            if not isinstance(body, dict) or not body.get("finalised"):
                self._json(400, {
                    "code": "RULE_BUSINESS_INCOME_PERIOD_NOT_FINALISED",
                    "message": "Body must set finalised: true",
                })
                return
            self._json(204, {"transactionReference": STUB_EOPS_REFERENCE})
            return

        # Trigger tax calculation. POST returns calculationId; body irrelevant.
        # Path: /individuals/calculations/{nino}/self-assessment/{taxYear}
        if (
            "/individuals/calculations/" in path
            and "/self-assessment/" in path
            and not path.endswith("/final-declaration")
            and "/" + STUB_CALCULATION_ID not in path
        ):
            self._json(202, {"calculationId": STUB_CALCULATION_ID})
            return

        # Final declaration submit — the annual return. No body. Returns 204.
        # Path: /individuals/calculations/{nino}/self-assessment/{taxYear}/
        #       {calculationId}/final-declaration
        if path.endswith("/final-declaration"):
            self._json(204, {"transactionReference": STUB_FINAL_DECL_REFERENCE})
            return

        # UK property submit — what the landlord flow ultimately hits.
        # Path: /individuals/business/property/{nino}/{bizId}/uk/period-summaries
        if "/property/" in path and "/period-summaries" in path:
            if not isinstance(body, dict):
                self._json(400, {"code": "INVALID_BODY", "message": "Body is not JSON object"})
                return
            for required in ("periodDates", "periodIncome", "periodExpenses"):
                if required not in body:
                    self._json(400, {
                        "code": "RULE_INCORRECT_OR_EMPTY_BODY_SUBMITTED",
                        "message": f"Missing required field: {required}",
                    })
                    return
            self._json(200, {
                "transactionReference": STUB_TRANSACTION_REFERENCE_PROP,
                "paymentReference": "PMTREF-PROP-001",
            })
            return

        self._json(404, {"code": "MATCHING_RESOURCE_NOT_FOUND", "_stub_path": path})


class HmrcStub:
    """Context-manager wrapper around the threaded stub server.

    Usage::

        with HmrcStub(port=33333) as stub:
            os.environ["HMRC_BASE_URL"] = stub.base_url
            # ... drive the app ...
            assert any("/period" in r["path"] for r in stub.recorded_requests)
    """

    class Server(ThreadingHTTPServer):
        """Extends ThreadingHTTPServer with a per-instance request log."""
        recorded_requests: list[dict[str, Any]]

    def __init__(self, port: int):
        self.port = port
        self._server: HmrcStub.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def recorded_requests(self) -> list[dict[str, Any]]:
        if not self._server:
            return []
        return self._server.recorded_requests

    def __enter__(self) -> "HmrcStub":
        server = HmrcStub.Server(("127.0.0.1", self.port), _Handler)
        server.recorded_requests = []
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)
