"""
QuickBooks Online (Intuit) integration.

Implements OAuth 2.0 (auth-code flow), token refresh, and the minimum
Accounting API calls needed to push parsed bank statement transactions
into a connected QuickBooks Online company.

App credentials live in env vars (see core.py: INTUIT_*). Per-user tokens
are stored in the qbo_connections table (database.py).

Push strategy: each parsed transaction becomes either a `Purchase`
(debits / money out) or a `Deposit` (credits / money in) against the
user-selected bank account, so they show up directly in the QBO bank
register. Uncategorised by default — the user re-classifies inside QBO.
"""

from __future__ import annotations

import logging
import secrets
import time
import urllib.parse
from typing import Any

import httpx
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from core import (
    INTUIT_CLIENT_ID, INTUIT_CLIENT_SECRET, INTUIT_ENVIRONMENT,
    INTUIT_REDIRECT_URI, INTUIT_SCOPES, INTUIT_AVAILABLE, SECRET_KEY,
)
from database import (
    upsert_qbo_connection, get_qbo_connection, update_qbo_tokens,
    delete_qbo_connection,
)

logger = logging.getLogger("bankparse.qbo")

# --- Intuit endpoints (sandbox vs production) ---
AUTH_BASE = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"


def _api_base(environment: str | None = None) -> str:
    env = (environment or INTUIT_ENVIRONMENT).lower()
    if env == "production":
        return "https://quickbooks.api.intuit.com"
    return "https://sandbox-quickbooks.api.intuit.com"


# --- OAuth state token (CSRF protection) ---

_STATE_TTL = 600  # 10 minutes


def _state_serializer() -> URLSafeTimedSerializer:
    # Salt the serializer so QBO state tokens can't be confused with auth cookies.
    return URLSafeTimedSerializer(SECRET_KEY, salt="qbo-oauth-state")


def make_state(user_id: int) -> str:
    nonce = secrets.token_urlsafe(16)
    return _state_serializer().dumps({"uid": user_id, "n": nonce})


def verify_state(state: str) -> int | None:
    try:
        data = _state_serializer().loads(state, max_age=_STATE_TTL)
        return data.get("uid")
    except (BadSignature, SignatureExpired):
        return None


# --- OAuth flow ---

def build_authorize_url(user_id: int) -> str:
    """Return the URL the user should be redirected to in order to grant access."""
    if not INTUIT_AVAILABLE:
        raise RuntimeError("Intuit integration is not configured on this server.")
    params = {
        "client_id": INTUIT_CLIENT_ID,
        "response_type": "code",
        "scope": INTUIT_SCOPES,
        "redirect_uri": INTUIT_REDIRECT_URI,
        "state": make_state(user_id),
    }
    return f"{AUTH_BASE}?{urllib.parse.urlencode(params)}"


def _basic_auth() -> tuple[str, str]:
    return (INTUIT_CLIENT_ID, INTUIT_CLIENT_SECRET)


def exchange_code_for_tokens(code: str) -> dict:
    """Trade an auth code for access + refresh tokens."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": INTUIT_REDIRECT_URI,
        },
        auth=_basic_auth(),
        headers={"Accept": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=_basic_auth(),
        headers={"Accept": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def revoke_token(token: str) -> bool:
    """Best-effort revocation. Returns True on success, False otherwise (still proceed with local delete)."""
    try:
        resp = httpx.post(
            REVOKE_URL,
            json={"token": token},
            auth=_basic_auth(),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        return resp.status_code == 200
    except Exception:
        logger.exception("QBO token revoke failed")
        return False


def store_initial_connection(user_id: int, token_response: dict, realm_id: str) -> None:
    now = time.time()
    upsert_qbo_connection(
        user_id=user_id,
        realm_id=realm_id,
        access_token=token_response["access_token"],
        refresh_token=token_response["refresh_token"],
        access_expires_at=now + int(token_response.get("expires_in", 3600)) - 60,
        refresh_expires_at=now + int(token_response.get("x_refresh_token_expires_in", 8640000)) - 3600,
        environment=INTUIT_ENVIRONMENT,
        company_name=None,
    )


def disconnect(user_id: int) -> None:
    conn = get_qbo_connection(user_id)
    if conn:
        revoke_token(conn["refresh_token"])
    delete_qbo_connection(user_id)


# --- Token lifecycle ---

def get_valid_access_token(user_id: int) -> tuple[str, str, str] | None:
    """
    Return (access_token, realm_id, environment) for a user, refreshing if needed.
    Returns None if the user is not connected.
    """
    conn = get_qbo_connection(user_id)
    if not conn:
        return None

    now = time.time()
    if now >= conn["refresh_expires_at"]:
        # Refresh token itself expired — user must re-connect.
        logger.warning("QBO refresh token expired for user %s; deleting connection.", user_id)
        delete_qbo_connection(user_id)
        return None

    if now < conn["access_expires_at"]:
        return conn["access_token"], conn["realm_id"], conn["environment"]

    # Refresh the access token.
    try:
        refreshed = refresh_access_token(conn["refresh_token"])
    except httpx.HTTPStatusError as e:
        logger.error("QBO refresh failed (HTTP %s): %s", e.response.status_code, e.response.text[:200])
        if e.response.status_code in (400, 401):
            delete_qbo_connection(user_id)
        return None
    except Exception:
        logger.exception("QBO refresh failed")
        return None

    update_qbo_tokens(
        user_id=user_id,
        access_token=refreshed["access_token"],
        refresh_token=refreshed.get("refresh_token", conn["refresh_token"]),
        access_expires_at=now + int(refreshed.get("expires_in", 3600)) - 60,
        refresh_expires_at=now + int(refreshed.get("x_refresh_token_expires_in", 8640000)) - 3600,
    )
    return refreshed["access_token"], conn["realm_id"], conn["environment"]


# --- QBO API helpers ---

def _api_get(user_id: int, path: str, params: dict | None = None) -> dict:
    creds = get_valid_access_token(user_id)
    if not creds:
        raise RuntimeError("QBO not connected")
    access_token, realm_id, env = creds
    url = f"{_api_base(env)}/v3/company/{realm_id}{path}"
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _api_post(user_id: int, path: str, body: dict) -> dict:
    creds = get_valid_access_token(user_id)
    if not creds:
        raise RuntimeError("QBO not connected")
    access_token, realm_id, env = creds
    url = f"{_api_base(env)}/v3/company/{realm_id}{path}"
    resp = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_company_info(user_id: int) -> dict | None:
    """Fetch company name / metadata. Also opportunistically updates the cached company_name."""
    try:
        creds = get_valid_access_token(user_id)
        if not creds:
            return None
        access_token, realm_id, env = creds
        url = f"{_api_base(env)}/v3/company/{realm_id}/companyinfo/{realm_id}"
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        info = resp.json().get("CompanyInfo", {})
        # Cache the company name.
        name = info.get("CompanyName") or info.get("LegalName")
        if name:
            conn = get_qbo_connection(user_id)
            if conn:
                upsert_qbo_connection(
                    user_id=user_id,
                    realm_id=conn["realm_id"],
                    access_token=conn["access_token"],
                    refresh_token=conn["refresh_token"],
                    access_expires_at=conn["access_expires_at"],
                    refresh_expires_at=conn["refresh_expires_at"],
                    environment=conn["environment"],
                    company_name=name,
                )
        return info
    except Exception:
        logger.exception("QBO get_company_info failed")
        return None


def list_accounts(user_id: int, account_type: str | None = None) -> list[dict]:
    """List accounts in the connected company. Filter by type (e.g. 'Bank', 'Expense', 'Income')."""
    where = "Active = true"
    if account_type:
        # Escape single quotes for QBO SQL-like syntax.
        safe = account_type.replace("'", "")
        where += f" AND AccountType = '{safe}'"
    q = f"SELECT Id, Name, AccountType, AccountSubType, CurrencyRef FROM Account WHERE {where} MAXRESULTS 1000"
    result = _api_get(user_id, "/query", params={"query": q, "minorversion": "75"})
    return result.get("QueryResponse", {}).get("Account", [])


# --- Transaction push ---

def _parse_amount(value) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = s.replace("£", "").replace("$", "").replace("€", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(value) -> str:
    """Return YYYY-MM-DD (QBO TxnDate format). Falls back to today on parse failure."""
    import datetime
    if not value:
        return datetime.date.today().isoformat()
    s = str(value).strip()
    fmts = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%Y/%m/%d", "%d %b", "%d/%m/%y")
    for fmt in fmts:
        try:
            d = datetime.datetime.strptime(s, fmt).date()
            if d.year < 1990:
                d = d.replace(year=datetime.date.today().year)
            return d.isoformat()
        except ValueError:
            continue
    return datetime.date.today().isoformat()


def push_transactions(
    user_id: int,
    transactions: list[dict],
    bank_account_id: str,
    expense_account_id: str,
    income_account_id: str,
) -> dict:
    """
    Push parsed bank statement transactions into the user's QBO company.

    Each transaction becomes either a Purchase (if amount is negative — money
    out of the bank) or a Deposit (if amount is positive — money in).

    Returns a summary: counts of created/failed transactions, plus errors.
    """
    created = 0
    failed = 0
    errors: list[str] = []

    for idx, txn in enumerate(transactions):
        amount = _parse_amount(txn.get("amount") or txn.get("Amount"))
        # Some statements split into credit/debit fields.
        if amount == 0:
            credit = _parse_amount(txn.get("credit") or txn.get("Credit") or txn.get("money_in"))
            debit = _parse_amount(txn.get("debit") or txn.get("Debit") or txn.get("money_out"))
            amount = credit - debit
        if amount == 0:
            continue  # Skip zero-amount rows (often summary lines).

        txn_date = _parse_date(txn.get("date") or txn.get("Date") or txn.get("transaction_date"))
        description = (
            txn.get("description") or txn.get("Description")
            or txn.get("payee") or txn.get("memo") or "Imported from BankScan AI"
        )
        description = str(description)[:4000]

        try:
            if amount < 0:
                # Money out → Purchase
                body = {
                    "PaymentType": "Cash",
                    "AccountRef": {"value": bank_account_id},
                    "TxnDate": txn_date,
                    "PrivateNote": "Imported via BankScan AI",
                    "Line": [{
                        "Amount": round(abs(amount), 2),
                        "DetailType": "AccountBasedExpenseLineDetail",
                        "Description": description,
                        "AccountBasedExpenseLineDetail": {
                            "AccountRef": {"value": expense_account_id},
                        },
                    }],
                }
                _api_post(user_id, "/purchase?minorversion=75", body)
            else:
                # Money in → Deposit
                body = {
                    "DepositToAccountRef": {"value": bank_account_id},
                    "TxnDate": txn_date,
                    "PrivateNote": "Imported via BankScan AI",
                    "Line": [{
                        "Amount": round(amount, 2),
                        "DetailType": "DepositLineDetail",
                        "Description": description,
                        "DepositLineDetail": {
                            "AccountRef": {"value": income_account_id},
                        },
                    }],
                }
                _api_post(user_id, "/deposit?minorversion=75", body)
            created += 1
        except httpx.HTTPStatusError as e:
            failed += 1
            msg = f"Row {idx + 1}: QBO {e.response.status_code} — {e.response.text[:200]}"
            errors.append(msg)
            logger.warning(msg)
            if failed >= 5 and created == 0:
                errors.append("Aborted after 5 consecutive failures.")
                break
        except Exception as e:
            failed += 1
            errors.append(f"Row {idx + 1}: {type(e).__name__}: {str(e)[:200]}")
            logger.exception("QBO push row %d failed", idx + 1)

    return {"created": created, "failed": failed, "errors": errors[:20]}
