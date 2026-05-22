"""
Sandbox-only test-data helpers.

HMRC's sandbox doesn't seed a freshly-created test individual with any
MTD ITSA businesses. The result is that the FIRST time a developer plugs
their sandbox NINO into BankScan AI, the Business Details API returns
`404 MATCHING_RESOURCE_NOT_FOUND`. That's correct — there's nothing
registered for that NINO — but it's a dead-end UX.

This service calls HMRC's Test Support endpoint to provision a test
self-employment OR property business so the rest of the dashboard can
light up. Production builds NEVER call this — the router gate (see
`routers/sandbox.py`) refuses the request when `HMRC_ENV=production`.

Reference (subject to HMRC versioning — update the path if HMRC moves it):
  POST /individuals/business/details/{nino}/test-only/create-business

Body expected by HMRC's sandbox:
  {
    "typeOfBusiness": "self-employment" | "uk-property",
    "tradingName":  "...",
    "firstAccountingPeriodStartDate": "YYYY-MM-DD",
    "firstAccountingPeriodEndDate":   "YYYY-MM-DD",
    "accountingType": "CASH"
  }

Returns the freshly-created business id which we can immediately stash
under `hmrc_connections.businesses_json` so the panel flips to Live.
"""

from __future__ import annotations

import logging
import os
from datetime import date

from .. import config as _cfg
from ..repositories import tokens as _tokens
from ..schemas import categories as _cats
from . import client as _client

logger = logging.getLogger("bankparse.hmrc.sandbox")


# HMRC's Business Details API ships its test-support endpoints behind a
# distinct media-type version. Bump this when HMRC publishes a new version
# of the Test Support API we validated against.
_TEST_API_VERSION = "application/vnd.hmrc.1.0+json"


def is_sandbox() -> bool:
    """Refuse to run outside sandbox to prevent accidental prod calls."""
    return os.environ.get("HMRC_ENV", "sandbox").lower() != "production"


def create_test_business(
    *,
    user_id: int,
    request_obj,
    type_of_business: str = "self-employment",
    trading_name: str | None = None,
) -> dict:
    """Provision one HMRC sandbox business for the user's connected NINO.

    Returns the HMRC response (typically the new businessId). Raises
    `HmrcNotConnectedError` if the user hasn't OAuthed yet, or
    `HmrcApiError` on a non-2xx from HMRC.
    """
    info = _tokens.get_tokens(user_id) or {}
    nino = info.get("nino")
    if not nino:
        raise _client.HmrcNotConnectedError(
            "Need a NINO before we can create a test business. "
            "Type a NINO into the dashboard first (we'll persist it then call this)."
        )

    # Normalise our internal `property` to HMRC's wire value `uk-property`.
    if type_of_business == "property":
        wire_type = "uk-property"
        default_name = "Sandbox property"
    else:
        wire_type = "self-employment"
        default_name = "Sandbox sole trader"

    today = date.today()
    body = {
        "typeOfBusiness": wire_type,
        "tradingName": (trading_name or default_name)[:105],
        # MTD ITSA tax year runs 6 April → 5 April. Pick the current
        # tax-year window so HMRC happily accepts the dates.
        "firstAccountingPeriodStartDate": _tax_year_start(today).isoformat(),
        "firstAccountingPeriodEndDate":   _tax_year_end(today).isoformat(),
        "accountingType": "CASH",
    }

    path = f"/individuals/business/details/{nino}/test-only/create-business"
    resp = _client.request(
        user_id=user_id, method="POST", path=path,
        request_obj=request_obj,
        json_body=body,
        accept_version=_TEST_API_VERSION,
    )
    return resp.json or {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tax_year_start(d: date) -> date:
    """Most recent 6 April on or before `d`."""
    cutoff = date(d.year, 4, 6)
    return cutoff if d >= cutoff else date(d.year - 1, 4, 6)


def _tax_year_end(d: date) -> date:
    """5 April that closes the tax year `_tax_year_start(d)` began."""
    start = _tax_year_start(d)
    return date(start.year + 1, 4, 5)


# ---------------------------------------------------------------------------
# Bulk bootstrap — one-click "create both SE + property" for a fresh NINO.
# Used by the dashboard's "Set me up with a complete sandbox" button.
# ---------------------------------------------------------------------------


WANTED_BUSINESS_TYPES: tuple[tuple[str, str], ...] = (
    ("self-employment", "Sandbox sole trader"),
    ("property",        "Sandbox property"),
)


def fetch_application_token() -> str:
    """Get a server-to-server (application-restricted) access token from
    HMRC sandbox via the client_credentials grant.

    Used for endpoints that authenticate as the *application*, not as a
    specific user — most notably Create Test User. Returns the raw
    bearer token string; the caller is expected to put it on a single
    Authorization header.
    """
    import httpx
    payload = {
        "grant_type": "client_credentials",
        "client_id": _cfg.HMRC_CLIENT_ID,
        "client_secret": _cfg.HMRC_CLIENT_SECRET,
        "scope": "hello",  # required by HMRC's token endpoint
    }
    url = f"{_cfg.HMRC_BASE_URL}{_cfg.OAUTH_TOKEN_PATH}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, data=payload)
    if resp.status_code >= 400:
        raise _client.HmrcApiError(
            status_code=resp.status_code,
            body=f"client_credentials token request failed: {resp.text[:300]}",
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise _client.HmrcApiError(
            status_code=0,
            body=f"HMRC token endpoint returned no access_token: {data}",
        )
    return str(token)


# Service names HMRC's Create Test User accepts. We subscribe the new user
# to every MTD ITSA-adjacent service so they can OAuth + submit immediately
# without HMRC support having to add anything by hand.
_TEST_USER_MTD_SERVICES = (
    "national-insurance",
    "self-assessment",
    "mtd-income-tax",
    # Property + self-employment APIs read from these subscriptions:
    # "agent-services",  # only useful if the test user IS an agent
)


def create_test_individual() -> dict:
    """Mint a fresh HMRC sandbox test individual.

    Returns the raw HMRC response which carries the new user's NINO,
    Government Gateway userId + password, sa_utr, mtdItId, group identifier,
    full name and address. The dashboard surfaces these in a copy-able
    card so the user can plug them into the dashboard NINO field and
    OAuth with the GG creds.

    Sandbox-only (HMRC's create-test-user API does not exist in
    production). Caller MUST gate on is_sandbox().
    """
    import httpx
    token = fetch_application_token()
    url = f"{_cfg.HMRC_BASE_URL}/create-test-user/individuals"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.hmrc.1.0+json",
        "Content-Type": "application/json",
    }
    body = {"serviceNames": list(_TEST_USER_MTD_SERVICES)}
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=headers, json=body)
    if resp.status_code >= 400:
        raise _client.HmrcApiError(
            status_code=resp.status_code,
            body=f"create-test-user/individuals failed: {resp.text[:500]}",
        )
    return resp.json()


def setup_complete_sandbox(*, user_id: int, request_obj) -> dict:
    """Idempotent. Creates whichever of (SE, property) businesses the user
    doesn't already have. Returns a structured response listing what was
    newly created vs what already existed, plus the user's NINO.

    Raises:
      ValueError       — if the user hasn't entered + saved their NINO.
      HmrcApiError     — from underlying HMRC client on a non-2xx.
    """
    info = _tokens.get_tokens(user_id) or {}
    nino = info.get("nino")
    if not nino:
        raise ValueError(
            "Enter your sandbox NINO in the dashboard and click "
            "'Discover my businesses' once before using this setup."
        )

    existing = list(info.get("businesses") or [])
    existing_types = {(b.get("type_of_business") or "").lower() for b in existing}

    created: list[dict] = []
    already: list[dict] = []

    for type_of_business, default_name in WANTED_BUSINESS_TYPES:
        if type_of_business in existing_types:
            for b in existing:
                if (b.get("type_of_business") or "").lower() == type_of_business:
                    already.append(b)
                    break
            continue

        try:
            result = create_test_business(
                user_id=user_id, request_obj=request_obj,
                type_of_business=type_of_business,
                trading_name=default_name,
            )
        except _client.HmrcApiError as exc:
            # MATCHING_RESOURCE_NOT_FOUND on create-business almost always
            # means the OAuth session is still tied to a PREVIOUS test
            # user, not the NINO we just minted. Translate the opaque
            # HMRC code into a clear next step instead of passing it
            # through raw.
            if _is_nino_oauth_mismatch(exc):
                # Page-agnostic message + a sentinel substring the UI
                # can detect on either /hmrc/file or the dashboard to
                # render a 'Disconnect & start over' recovery button.
                raise ValueError(
                    f"OAUTH_NINO_MISMATCH: HMRC doesn't recognise NINO "
                    f"{nino} with your current HMRC sign-in — your OAuth "
                    "session is for a different test user. To fix this, "
                    "disconnect from HMRC (red Disconnect button on the "
                    "Connect-to-HMRC page) and sign in again with the "
                    "Government Gateway userId + password that matches "
                    "this NINO. If you don't have those credentials, "
                    "mint a fresh test user on the dashboard and OAuth "
                    "as that new identity."
                ) from exc
            # Annotate the error with WHICH business type failed so the
            # endpoint surfaces something diagnostic to the dashboard.
            raise _client.HmrcApiError(
                status_code=exc.status_code,
                body=f"creating the {type_of_business} business: {exc.body}",
            ) from exc
        biz_id = (
            result.get("businessId")
            or (result.get("business") or {}).get("businessId")
        )
        if not biz_id:
            # HMRC accepted the call but the shape's unexpected — surface
            # the raw response so the caller can show it to the dev.
            raise _client.HmrcApiError(
                status_code=0,
                body=f"HMRC accepted the {type_of_business} create but "
                     f"returned no businessId. Raw: {result}",
            )

        new_business = {
            "business_id": biz_id,
            "type_of_business": type_of_business,
            "label": default_name,
        }
        existing.append(new_business)
        existing_types.add(type_of_business)
        created.append(new_business)

    # One persistence flush at the end regardless of how many we created.
    if created:
        _tokens.save_nino_and_businesses(user_id, nino, existing)

    return {"created": created, "already_existed": already, "nino": nino}


def _is_nino_oauth_mismatch(exc: _client.HmrcApiError) -> bool:
    """True if HMRC's response means the OAuth session is for a different
    NINO than the one we're trying to mutate.

    HMRC returns 404 MATCHING_RESOURCE_NOT_FOUND in two cases:
      1. NINO has no businesses yet (we already handle this on Discover).
      2. OAuth identity doesn't match the NINO in the URL.

    On create-test-business the second case is the only one that can fire
    — the endpoint doesn't lookup-by-NINO, it CREATES against the NINO in
    the URL, which HMRC validates against the OAuth token's individual.
    """
    if exc.status_code != 404:
        return False
    body = exc.body or {}
    code = body.get("code") if isinstance(body, dict) else ""
    return (code or "").upper() == "MATCHING_RESOURCE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Sample transaction seeding — gives a freshly-provisioned sandbox account
# enough ledger data that the quarterly submit buttons produce non-£0.00
# totals out of the box. Sandbox-only.
# ---------------------------------------------------------------------------

# Realistic-feeling fixtures for a small UK sole trader + landlord. Mix
# is deliberately tilted so SE submits show consulting income/expenses and
# property submits show rent/repairs. Same ledger feeds both (this is how
# the production product works today — one user has one ledger), so the
# AI categoriser routes each row to the right business-type category on
# each submit.
_SAMPLE_TRANSACTIONS: tuple[dict, ...] = (
    # --- SE-flavoured income (consulting / sole-trader) ---
    {"days_from_quarter_start": 3,  "desc": "Consulting fee — Acme Ltd invoice #1042", "amount": 2400.00,
     "cat": _cats.SE_INCOME},
    {"days_from_quarter_start": 17, "desc": "Workshop facilitation — Northridge Co",   "amount": 1850.00,
     "cat": _cats.SE_INCOME},
    {"days_from_quarter_start": 32, "desc": "Sale of services — Beacon Studios",        "amount": 1200.00,
     "cat": _cats.SE_INCOME},
    {"days_from_quarter_start": 60, "desc": "Retainer — Helmsley Partners May",         "amount": 1500.00,
     "cat": _cats.SE_INCOME},
    # --- Property-flavoured income (rent) ---
    {"days_from_quarter_start": 5,  "desc": "Rent received — Flat 1 Tower Mill Lane",   "amount": 950.00,
     "cat": _cats.PROP_INCOME_RENT, "prop": True},
    {"days_from_quarter_start": 6,  "desc": "Rent received — 14 Foundry Street",         "amount": 1300.00,
     "cat": _cats.PROP_INCOME_RENT, "prop": True},
    {"days_from_quarter_start": 35, "desc": "Rent received — Flat 1 Tower Mill Lane",   "amount": 950.00,
     "cat": _cats.PROP_INCOME_RENT, "prop": True},
    {"days_from_quarter_start": 36, "desc": "Rent received — 14 Foundry Street",         "amount": 1300.00,
     "cat": _cats.PROP_INCOME_RENT, "prop": True},
    {"days_from_quarter_start": 65, "desc": "Rent received — Flat 1 Tower Mill Lane",   "amount": 950.00,
     "cat": _cats.PROP_INCOME_RENT, "prop": True},
    {"days_from_quarter_start": 66, "desc": "Rent received — 14 Foundry Street",         "amount": 1300.00,
     "cat": _cats.PROP_INCOME_RENT, "prop": True},
    # --- SE expenses (travel, admin, professional) ---
    {"days_from_quarter_start": 8,  "desc": "Train — London Liverpool St to Ipswich",   "amount": -42.30,
     "cat": _cats.SE_EXPENSE_TRAVEL},
    {"days_from_quarter_start": 19, "desc": "Hotel — Premier Inn Manchester (1 night)", "amount": -89.00,
     "cat": _cats.SE_EXPENSE_TRAVEL},
    {"days_from_quarter_start": 22, "desc": "Subscription — Microsoft 365 Business",    "amount": -12.50,
     "cat": _cats.SE_EXPENSE_ADMIN},
    {"days_from_quarter_start": 28, "desc": "Accountancy — quarterly payroll filing",   "amount": -180.00,
     "cat": _cats.SE_EXPENSE_PROFESSIONAL},
    # --- Property expenses (repairs, services, premises) ---
    {"days_from_quarter_start": 12, "desc": "Plumber callout — Flat 1 leak",             "amount": -185.00,
     "cat": _cats.PROP_EXPENSE_REPAIRS, "prop": True},
    {"days_from_quarter_start": 40, "desc": "Letting agent fee — Foundry St May",        "amount": -156.00,
     "cat": _cats.PROP_EXPENSE_SERVICES, "prop": True},
    {"days_from_quarter_start": 47, "desc": "Council tax — 14 Foundry Street (void)",    "amount": -142.50,
     "cat": _cats.PROP_EXPENSE_PREMISES, "prop": True},
    {"days_from_quarter_start": 53, "desc": "Boiler service — Flat 1 annual",            "amount": -98.00,
     "cat": _cats.PROP_EXPENSE_REPAIRS, "prop": True},
)


def _current_quarter_start(today: date | None = None) -> date:
    """First day of the MTD ITSA quarter containing `today`.

    MTD ITSA tax year quarters: 6 Apr → 5 Jul, 6 Jul → 5 Oct,
    6 Oct → 5 Jan, 6 Jan → 5 Apr. We pick the most-recent boundary
    so seeded data lands in the period the dashboard is currently
    nudging the user to submit.
    """
    d = today or date.today()
    boundaries = [
        date(d.year, 4, 6), date(d.year, 7, 6),
        date(d.year, 10, 6), date(d.year + 1, 1, 6),
        date(d.year - 1, 10, 6), date(d.year, 1, 6),
    ]
    past = [b for b in boundaries if b <= d]
    return max(past)


def seed_sample_transactions(user_id: int, *, today: date | None = None) -> dict:
    """Insert the canonical sample transactions into the user's ledger
    for the current MTD ITSA quarter. Sandbox-only — caller MUST gate on
    `is_sandbox()`.

    Returns: {"inserted": N, "skipped_existing": M, "period_start": iso,
              "period_end": iso}.

    Idempotency: the ledger_transactions content_hash column has an INDEX
    but no UNIQUE constraint, so we pre-check by hash + user_id before
    inserting. A second invocation goes entirely down the skipped path.
    """
    from database import insert_ledger_transaction, _hash_transaction, _fetchall_dicts
    from datetime import timedelta

    q_start = _current_quarter_start(today)
    q_end = q_start + timedelta(days=90)  # ~3 months — generous; HMRC quarter is 91 days

    # One round-trip to read every existing content_hash for this user,
    # so per-row work is a Python set lookup.
    existing_rows = _fetchall_dicts(
        "SELECT content_hash FROM ledger_transactions WHERE user_id = ?",
        (user_id,),
    )
    existing_hashes = {r["content_hash"] for r in existing_rows if r.get("content_hash")}

    inserted = 0
    skipped = 0
    for tx in _SAMPLE_TRANSACTIONS:
        day = q_start + timedelta(days=int(tx["days_from_quarter_start"]))
        # Defensive: never seed outside the quarter — clip if the fixture
        # offset overshoots the quarter end.
        if day > q_end:
            day = q_end
        h = _hash_transaction(day.isoformat(), str(tx["desc"]), float(tx["amount"]))
        if h in existing_hashes:
            skipped += 1
            continue
        insert_ledger_transaction(
            user_id=user_id,
            extracted_data_id=None,
            date_iso=day.isoformat(),
            description=str(tx["desc"]),
            amount=float(tx["amount"]),
            currency="GBP",
            transaction_type=("credit" if tx["amount"] > 0 else "debit"),
            hmrc_category=str(tx["cat"]),
            hmrc_category_confidence=95,
            hmrc_category_reason="Sandbox seed — pre-categorised demo data.",
            reference=None,
        )
        existing_hashes.add(h)
        inserted += 1

    return {
        "inserted": inserted,
        "skipped_existing": skipped,
        "period_start": q_start.isoformat(),
        "period_end": (q_start + timedelta(days=90)).isoformat(),
    }
