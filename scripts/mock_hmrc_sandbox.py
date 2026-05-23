"""
Mock HMRC sandbox — just enough to exercise the BankScan AI journey
end-to-end without real HMRC developer credentials.

Why this exists
---------------
The journey we want to verify: register → login → Mint sandbox test user →
OAuth round-trip → dashboard auto-fires setup-complete → seed sample
transactions → reload → see "Mitoba - sole trader" + numbers.

Real HMRC sandbox needs HMRC_CLIENT_ID/SECRET registered at
developer.service.hmrc.gov.uk. This mock substitutes for that, returning
the same response shapes the real sandbox does so our code can't tell
the difference.

What's mocked
-------------
* `GET /oauth/authorize` — immediately redirects back to the registered
  redirect_uri with `?code=mock_code_xxx&state=<state>`.
* `POST /oauth/token` — returns a fresh access + refresh token pair for
  both `client_credentials` (for create-test-user) and
  `authorization_code` grants.
* `POST /create-test-user/individuals` — returns a freshly-minted NINO +
  GG credentials, exactly the shape the real sandbox does.
* `POST /individuals/business/details/{nino}/test-only/create-business`
  — returns a businessId. Stateful: remembers what was created so the
  subsequent obligations call returns the right list.
* `GET /individuals/business/details/{nino}/list` — returns the list of
  businesses we've created against this NINO.
* `GET /individuals/business/details/{nino}/{businessId}/obligations` —
  returns four open quarterly obligations.

Run:
    python scripts/mock_hmrc_sandbox.py --port 9100
"""
from __future__ import annotations

import argparse
import logging
import secrets
import urllib.parse as _urlparse
from datetime import date, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

logger = logging.getLogger("mock_hmrc")
logging.basicConfig(level=logging.INFO, format="[mock-hmrc] %(message)s")

app = FastAPI(title="Mock HMRC sandbox")

# In-memory state — fine for a test mock. Resets on every restart.
_minted_users: dict[str, dict] = {}            # nino → {userId, password, mtdItId, saUtr}
_businesses_by_nino: dict[str, list[dict]] = {}  # nino → [{businessId, typeOfBusiness, tradingName, ...}]
_next_business_seq = [1000000]                 # auto-incrementing business id counter


# ---------------------------------------------------------------------------
# OAuth endpoints
# ---------------------------------------------------------------------------

@app.get("/oauth/authorize")
async def oauth_authorize(request: Request):
    """Skip the HMRC sign-in UI entirely. Redirect straight back to the
    caller's redirect_uri with a code + the state they passed. Real HMRC
    would render a Government Gateway sign-in form here, but for the
    journey test we trust the user is signing in correctly."""
    redirect_uri = request.query_params.get("redirect_uri", "")
    state = request.query_params.get("state", "")
    prompt = request.query_params.get("prompt", "")
    logger.info(
        "OAuth authorize: client_id=%s prompt=%s state=%s",
        request.query_params.get("client_id", "")[:8],
        prompt or "(none)",
        state[:12],
    )
    code = f"mock_code_{secrets.token_urlsafe(8)}"
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{sep}code={code}&state={_urlparse.quote(state)}",
        status_code=302,
    )


@app.post("/oauth/token")
async def oauth_token(request: Request):
    """Both authorization_code (per-user) and client_credentials (app
    token for create-test-user) grants resolve here. We return a
    realistic token bundle regardless — the journey doesn't need to
    validate scope strictness."""
    form = await request.form()
    grant = form.get("grant_type", "")
    logger.info("OAuth token: grant=%s", grant)
    return JSONResponse({
        "access_token":  f"mock_at_{secrets.token_urlsafe(12)}",
        "refresh_token": f"mock_rt_{secrets.token_urlsafe(12)}",
        "expires_in":    14400,
        "scope":         "read:self-assessment write:self-assessment",
        "token_type":    "bearer",
    })


# ---------------------------------------------------------------------------
# Test-support endpoints
# ---------------------------------------------------------------------------

@app.post("/create-test-user/individuals")
async def create_test_user_individual(request: Request):
    """Mint a fresh test individual. Returns the same fields the real
    HMRC create-test-user/individuals endpoint does — our code reads
    `nino`, `userId`, `password`, `mtdItId`, `saUtr`."""
    # Realistic-looking NINO. Pattern AA######B.
    nino_letters = secrets.choice(("AB", "CD", "EF", "GH", "JK"))
    digits = "".join(secrets.choice("0123456789") for _ in range(6))
    nino_suffix = secrets.choice("ABCD")
    nino = f"{nino_letters}{digits}{nino_suffix}"
    user_id = "".join(secrets.choice("0123456789") for _ in range(12))
    password = secrets.token_urlsafe(8)
    body = {
        "nino": nino,
        "userId": user_id,
        "password": password,
        "mtdItId": f"X{secrets.token_hex(7).upper()}",
        "saUtr": "".join(secrets.choice("0123456789") for _ in range(10)),
        "userFullName": "Test User",
        "emailAddress": f"test-{secrets.token_hex(3)}@example.com",
        "groupIdentifier": f"group-{secrets.token_hex(4)}",
    }
    _minted_users[nino] = body
    logger.info("Minted test individual: nino=%s userId=%s", nino, user_id)
    return JSONResponse(body, status_code=201)


@app.post("/individuals/business/details/{nino}/test-only/create-business")
async def create_test_business(nino: str, request: Request):
    """Provision a self-employment OR uk-property business under a NINO.
    Returns a businessId in the shape `{'businessId': '...'}`."""
    body = await request.json()
    type_of_business = body.get("typeOfBusiness")
    trading_name = body.get("tradingName")
    _next_business_seq[0] += 1
    biz_id = f"X{type_of_business[:3].upper()}{_next_business_seq[0]:08d}"
    record = {
        "businessId": biz_id,
        "typeOfBusiness": type_of_business,
        "tradingName": trading_name,
        "accountingType": body.get("accountingType", "CASH"),
        "firstAccountingPeriodStartDate": body.get("firstAccountingPeriodStartDate"),
        "firstAccountingPeriodEndDate":   body.get("firstAccountingPeriodEndDate"),
    }
    _businesses_by_nino.setdefault(nino, []).append(record)
    logger.info(
        "Created %s business %s under %s (tradingName=%r)",
        type_of_business, biz_id, nino, trading_name,
    )
    return JSONResponse({"businessId": biz_id}, status_code=201)


@app.get("/individuals/business/details/{nino}/list")
async def list_businesses(nino: str):
    """Business Details API — list businesses for a NINO."""
    rows = _businesses_by_nino.get(nino, [])
    if not rows:
        return JSONResponse(
            {"code": "MATCHING_RESOURCE_NOT_FOUND",
             "message": "Matching resource not found"},
            status_code=404,
        )
    return JSONResponse({
        "businessData": [
            {
                "businessId": b["businessId"],
                "typeOfBusiness": b["typeOfBusiness"],
                "tradingName": b["tradingName"],
                "accountingType": b["accountingType"],
                "firstAccountingPeriodStartDate": b["firstAccountingPeriodStartDate"],
                "firstAccountingPeriodEndDate":   b["firstAccountingPeriodEndDate"],
            }
            for b in rows
        ],
    })


@app.get("/individuals/business/details/{nino}/{business_id}")
async def get_business(nino: str, business_id: str):
    """Business Details API — single business lookup."""
    for b in _businesses_by_nino.get(nino, []):
        if b["businessId"] == business_id:
            return JSONResponse(b)
    return JSONResponse(
        {"code": "MATCHING_RESOURCE_NOT_FOUND",
         "message": "Matching resource not found"},
        status_code=404,
    )


# ---------------------------------------------------------------------------
# Obligations + ITSA submission endpoints (minimum needed by dashboard)
# ---------------------------------------------------------------------------

def _build_obligations_for(business_id: str, type_of_business: str) -> dict:
    """Return the HMRC obligations shape per business, with 4 open quarters."""
    today = date.today()
    cutoff = date(today.year, 4, 6)
    if today < cutoff:
        cutoff = date(today.year - 1, 4, 6)
    quarters: list[dict] = []
    for i in range(4):
        period_start = cutoff + timedelta(days=i * 91)
        period_end = period_start + timedelta(days=90)
        due_date = period_end + timedelta(days=31)
        quarters.append({
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "due": due_date.isoformat(),
            "status": "Open",
            "periodKey": f"Q{i+1}",
        })
    return {
        "identification": {
            "incomeSourceType": type_of_business,
            "referenceNumber": business_id,
            "referenceType": "MTDID",
        },
        "obligationDetails": quarters,
    }


@app.get("/individuals/business/{type_of_business}/{nino}/{business_id}/obligations")
async def get_obligations_per_business(type_of_business: str, nino: str, business_id: str):
    """Real HMRC path: /individuals/business/{type}/{nino}/{businessId}/obligations.
    Returns a FLAT list of obligations (one row per period) — bankparse's
    HmrcObligation schema expects periodKey/start/end/due/status at top level."""
    biz_list = _businesses_by_nino.get(nino, [])
    biz = next((b for b in biz_list if b["businessId"] == business_id), None)
    if not biz:
        return JSONResponse(
            {"code": "MATCHING_RESOURCE_NOT_FOUND",
             "message": "Matching resource not found"},
            status_code=404,
        )
    today = date.today()
    cutoff = date(today.year, 4, 6)
    if today < cutoff:
        cutoff = date(today.year - 1, 4, 6)
    obligations = []
    for i in range(4):
        period_start = cutoff + timedelta(days=i * 91)
        period_end = period_start + timedelta(days=90)
        due_date = period_end + timedelta(days=31)
        obligations.append({
            "periodKey": f"#{i+1:03d}",
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "due": due_date.isoformat(),
            "status": "Open",
        })
    return JSONResponse({"obligations": obligations})


@app.post("/individuals/business/self-employment/{nino}/{business_id}/period-summaries")
async def submit_se_quarter(nino: str, business_id: str, request: Request):
    """HMRC quarterly self-employment submission endpoint. Returns a fake
    transactionReference + submissionId so the dashboard shows a green
    confirmation row with a clickable reference."""
    body = await request.json()
    ref = f"SE-{secrets.token_hex(6).upper()}"
    logger.info(
        "Submitted SE quarter %s/%s for %s: income=%s expenses=%s",
        body.get("periodDates", {}).get("periodStartDate"),
        body.get("periodDates", {}).get("periodEndDate"),
        business_id, sum((body.get("periodIncome") or {}).values()),
        sum(v for v in (body.get("periodExpenses") or {}).values() if isinstance(v, (int, float))),
    )
    return JSONResponse({
        "transactionReference": ref,
        "submissionId": f"sub-{secrets.token_hex(8)}",
        "submittedAt": date.today().isoformat() + "T00:00:00Z",
    }, status_code=201)


@app.post("/individuals/business/property/{nino}/{business_id}/uk/period-summaries")
async def submit_property_quarter(nino: str, business_id: str, request: Request):
    """Property quarterly submission — same shape as SE, different URL."""
    body = await request.json()
    ref = f"PROP-{secrets.token_hex(6).upper()}"
    logger.info(
        "Submitted property quarter %s/%s for %s",
        body.get("periodDates", {}).get("periodStartDate"),
        body.get("periodDates", {}).get("periodEndDate"),
        business_id,
    )
    return JSONResponse({
        "transactionReference": ref,
        "submissionId": f"sub-{secrets.token_hex(8)}",
        "submittedAt": date.today().isoformat() + "T00:00:00Z",
    }, status_code=201)


@app.get("/health")
def health():
    return {"status": "ok", "users_minted": len(_minted_users),
            "businesses": sum(len(v) for v in _businesses_by_nino.values())}


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
