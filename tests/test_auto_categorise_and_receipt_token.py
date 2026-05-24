"""
Tests for the two fixes Mitchell flagged on 2026-05-20:

  1. AI categoriser must run automatically when statement transactions
     are ingested into the ledger — otherwise every row shows up as
     'Uncategorised' on /ledger.

  2. The receipts forwarding address must use a per-user random token
     (e.g. "x7f2k9q3@receipts.bankscanai.com") rather than the bare
     integer user id ("4@..."), which looked invalid to users and
     trivially enumerated user ids.
"""
from __future__ import annotations

import base64
import os
import re
import secrets
import sys
import time
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_auto_categorise.db"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("ENVIRONMENT", "development")


@pytest.fixture(autouse=True)
def clean_db():
    import database
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)
    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
    database._sqlite_conn = None
    import sqlite3
    def _get_sqlite_test():
        if database._sqlite_conn is None:
            conn = sqlite3.connect(TEST_DB_PATH, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            database._sqlite_conn = conn
        return database._sqlite_conn
    database._get_sqlite = _get_sqlite_test
    database.init_db()
    yield
    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


def _client(email: str = "auto@example.com") -> tuple:
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    client.post("/api/register",
                json={"email": email, "password": "password12345"},
                headers={"X-CSRF-Token": csrf})
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], subscription_status="trialing",
                    stripe_subscription_id="sub_auto",
                    trial_end_at=time.time() + 7*86400)
    return client, user, csrf


# ---------------------------------------------------------------------------
# 1. auto_categorise_user_transactions
# ---------------------------------------------------------------------------


def _stub_categorise_response(rows: list, user_id: int):
    """Build a CategoriseResponse that maps each input row to a stub
    category — so the test doesn't need a real Claude key."""
    from hmrc.schemas.categorise import (
        CategoriseResponse, TransactionOut, HmrcClassification,
    )
    from hmrc.services.categorisation import CategorisationMetrics

    out_rows = []
    for r in rows.rows if hasattr(rows, "rows") else rows:
        # Negative amounts → expense; positive → income (stub heuristic)
        is_income = r.amount > 0
        category = "se_turnover" if is_income else "se_office_expenses"
        out_rows.append(TransactionOut(
            description=r.description,
            amount=r.amount,
            hmrc=HmrcClassification(
                category=category,
                confidence=0.9,
                is_income=is_income,
                reasoning="Stub test categorisation",
                source="rule",
            ),
        ))
    return (
        CategoriseResponse(business_type=rows.business_type, rows=out_rows),
        CategorisationMetrics(0, 0, 0, 0, 0, 0),
    )


def test_auto_categorise_assigns_hmrc_categories():
    """The headline behaviour: after the helper runs, every previously
    uncategorised transaction has a category attached."""
    import database as _db
    _db.create_user("u@example.com", "h")
    uid = _db.get_user_by_email("u@example.com")["id"]
    _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="AMAZON UK",
        amount=-42.99, hmrc_category=None,
    )
    _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="STRIPE PAYOUT",
        amount=2500.0, hmrc_category=None,
    )

    import asyncio
    from services.ledger_ingest import auto_categorise_user_transactions
    with patch("hmrc.services.categorisation.resolve",
               new_callable=AsyncMock, side_effect=_stub_categorise_response):
        n = asyncio.run(auto_categorise_user_transactions(uid))
    assert n == 2

    txs = _db.get_user_ledger_transactions(uid)
    by_amt = {t["amount"]: t for t in txs}
    assert by_amt[-42.99]["hmrc_category"] == "se_office_expenses"
    assert by_amt[2500.0]["hmrc_category"] == "se_turnover"
    assert by_amt[-42.99]["hmrc_category_confidence"] == 90
    assert by_amt[-42.99]["hmrc_category_reason"] == "Stub test categorisation"


def test_auto_categorise_splits_property_rows_from_se_rows():
    """Property-flavoured descriptions (rent received, letting agent, plumber
    callout) must route through the property categoriser; everything else
    goes through the caller-supplied stream (default ``se``). Before this
    split, mixed sole-trader+landlord statements had every rent receipt
    classified as SE income on the dashboard tax tile.

    Caught by the Playwright submit journey 2026-05-24."""
    import database as _db
    _db.create_user("split@example.com", "h")
    uid = _db.get_user_by_email("split@example.com")["id"]
    _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="RENT RECEIVED TOWER MILL LANE",
        amount=950.0, hmrc_category=None,
    )
    _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="INVOICE ACME CONSULTING",
        amount=2400.0, hmrc_category=None,
    )

    seen_business_types: list[str] = []
    async def _capturing_stub(rows, user_id):
        seen_business_types.append(rows.business_type)
        return _stub_categorise_response(rows, user_id)

    import asyncio
    from services.ledger_ingest import auto_categorise_user_transactions
    with patch("hmrc.services.categorisation.resolve",
               new_callable=AsyncMock, side_effect=_capturing_stub):
        n = asyncio.run(auto_categorise_user_transactions(uid))
    assert n == 2
    # Two separate categoriser calls — one per business stream.
    assert sorted(seen_business_types) == ["property", "se"], (
        f"Expected one 'property' + one 'se' resolve call, got: "
        f"{seen_business_types!r}"
    )


def test_auto_categorise_only_touches_uncategorised_rows_by_default():
    """only_uncategorised=True must NOT overwrite categories the user has
    already confirmed."""
    import database as _db
    _db.create_user("preserve@example.com", "h")
    uid = _db.get_user_by_email("preserve@example.com")["id"]
    # User-confirmed
    tx_user = _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="ALREADY_CONFIRMED",
        amount=-10.0,
        hmrc_category="se_business_travel_costs",
        hmrc_category_confidence=100,
    )
    # New uncategorised
    _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="NEW_ROW",
        amount=-20.0, hmrc_category=None,
    )

    import asyncio
    from services.ledger_ingest import auto_categorise_user_transactions
    with patch("hmrc.services.categorisation.resolve",
               new_callable=AsyncMock, side_effect=_stub_categorise_response):
        n = asyncio.run(auto_categorise_user_transactions(uid))
    assert n == 1  # only the new row, not the confirmed one

    fresh = _db.get_transaction_by_id(tx_user, uid)
    assert fresh["hmrc_category"] == "se_business_travel_costs"
    assert fresh["hmrc_category_confidence"] == 100


def test_auto_categorise_handles_failure_gracefully():
    """If the categoriser raises, we log and return 0 — never crash the
    upload pipeline."""
    import database as _db
    _db.create_user("crash@example.com", "h")
    uid = _db.get_user_by_email("crash@example.com")["id"]
    _db.insert_ledger_transaction(
        uid, extracted_data_id=None,
        date_iso="2026-05-15", description="X", amount=-10.0,
        hmrc_category=None,
    )

    import asyncio
    from services.ledger_ingest import auto_categorise_user_transactions
    with patch("hmrc.services.categorisation.resolve",
               new_callable=AsyncMock, side_effect=RuntimeError("AI down")):
        n = asyncio.run(auto_categorise_user_transactions(uid))
    assert n == 0  # graceful failure


def test_auto_categorise_empty_user_is_noop():
    import database as _db
    _db.create_user("empty@example.com", "h")
    uid = _db.get_user_by_email("empty@example.com")["id"]
    import asyncio
    from services.ledger_ingest import auto_categorise_user_transactions
    n = asyncio.run(auto_categorise_user_transactions(uid))
    assert n == 0


# ---------------------------------------------------------------------------
# 2. /api/ledger/categorise-all endpoint
# ---------------------------------------------------------------------------


def test_categorise_all_endpoint_requires_auth():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    anon.get("/login")
    csrf = anon.cookies.get("bp_csrf", "")
    r = anon.post("/api/ledger/categorise-all", json={},
                  headers={"X-CSRF-Token": csrf})
    assert r.status_code == 401


def test_categorise_all_endpoint_runs_categoriser():
    client, user, csrf = _client("call@example.com")
    import database as _db
    _db.insert_ledger_transaction(
        user["id"], extracted_data_id=None,
        date_iso="2026-05-15", description="AMAZON",
        amount=-50.0, hmrc_category=None,
    )
    with patch("hmrc.services.categorisation.resolve",
               new_callable=AsyncMock, side_effect=_stub_categorise_response):
        r = client.post("/api/ledger/categorise-all", json={},
                        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["categorised"] == 1

    fresh = _db.get_user_ledger_transactions(user["id"])
    assert fresh[0]["hmrc_category"] == "se_office_expenses"


# ---------------------------------------------------------------------------
# 3. Receipts forwarding token
# ---------------------------------------------------------------------------


def test_forwarding_address_uses_random_token_not_user_id():
    """The address must NOT be {id}@... — that gives away user count + looks
    like spam. Should be an 8-char alphanumeric token."""
    client, user, _ = _client("token@example.com")
    r = client.get("/api/receipts/forwarding-address")
    assert r.status_code == 200
    addr = r.json()["address"]
    local = addr.split("@")[0]
    # Crucially: NOT just the integer id
    assert local != str(user["id"])
    # Token is 8 alphanumeric chars
    assert re.fullmatch(r"[a-z0-9]{8}", local), f"Bad token format: {local!r}"
    # Hosted on the right domain
    assert addr.endswith("@receipts.bankscanai.com")


def test_forwarding_token_is_stable_across_calls():
    """Two consecutive calls return the same address (token is persisted)."""
    client, _, _ = _client("stable@example.com")
    a1 = client.get("/api/receipts/forwarding-address").json()["address"]
    a2 = client.get("/api/receipts/forwarding-address").json()["address"]
    assert a1 == a2


def test_forwarding_tokens_are_unique_per_user():
    """Two different users get two different tokens."""
    c1, u1, _ = _client("u1@example.com")
    c2, u2, _ = _client("u2@example.com")
    a1 = c1.get("/api/receipts/forwarding-address").json()["address"]
    a2 = c2.get("/api/receipts/forwarding-address").json()["address"]
    assert a1 != a2


def test_email_in_webhook_accepts_token_local_part():
    """The webhook must route to the right user when the local-part is the
    new random token."""
    client, user, _ = _client("webhook@example.com")
    # Make sure the user has a token
    token = client.get("/api/receipts/forwarding-address").json()["address"].split("@")[0]

    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.post("/api/receipts/email-in",
                  json={
                      "to": f"{token}@receipts.bankscanai.com",
                      "from": "billing@stripe.com",
                      "attachments": [{"filename": "x.pdf",
                                       "content_b64": "ZmFrZQ=="}],
                  })
    assert r.status_code == 200
    body = r.json()
    assert body["received"] == 1
    assert body["saved"] == 1


def test_email_in_webhook_still_accepts_legacy_numeric_local_part():
    """Backwards-compat: any address users may have already shared in
    the wild (numeric id) must keep working."""
    client, user, _ = _client("legacy@example.com")

    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.post("/api/receipts/email-in",
                  json={
                      "to": f"{user['id']}@receipts.bankscanai.com",
                      "from": "x@example.com",
                      "attachments": [{"filename": "x.pdf",
                                       "content_b64": "ZmFrZQ=="}],
                  })
    assert r.status_code == 200
