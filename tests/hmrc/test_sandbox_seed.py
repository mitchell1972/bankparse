"""
Tests for the sandbox sample-data seeder.

Background: after Mint + OAuth + Discover, a brand-new sandbox NINO has
zero ledger transactions. The dashboard shows live obligations but every
quarterly Submit button produces £0.00 because the categoriser has
nothing to total. This seeder drops ~18 pre-categorised transactions
into the user's ledger in the current MTD ITSA quarter so the demo path
actually shows realistic numbers.

Service: hmrc.services.sandbox.seed_sample_transactions
Route:   POST /api/hmrc/sandbox/seed-sample-data
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


TEST_DB_PATH = "/tmp/test_bankparse_hmrc_sandbox_seed.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("HMRC_ENV", "sandbox")

    from hmrc import config as _cfg
    from hmrc.services import crypto as _crypto
    monkeypatch.setattr(_cfg, "HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setattr(_crypto, "_KEY_CACHE", None)

    try:
        from app import app as _app
        if getattr(_app.state, "limiter", None) is not None:
            _app.state.limiter.enabled = False
    except Exception:
        pass
    yield
    try:
        from app import app as _app
        if getattr(_app.state, "limiter", None) is not None:
            _app.state.limiter.enabled = True
    except Exception:
        pass


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


def _seed_csrf(client):
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_user(email="sandbox-seed-test@example.com"):
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/register",
        json={"email": email, "password": "password12345"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], grandfathered_trial=1)
    return client, csrf, user


# ---------------------------------------------------------------------------
# Pure-service tests
# ---------------------------------------------------------------------------

def test_seed_inserts_transactions_into_current_quarter():
    """seed_sample_transactions must put rows in the user's ledger and
    date them inside the MTD ITSA quarter that contains `today`."""
    from hmrc.services import sandbox as _sandbox
    from database import get_user_ledger_transactions

    client, _, user = _client_with_user()
    today = date(2026, 5, 22)  # squarely in Q1 2026/27 (6 Apr → 5 Jul)

    result = _sandbox.seed_sample_transactions(user["id"], today=today)

    assert result["inserted"] > 0, "Expected the seeder to insert rows."
    assert result["skipped_existing"] == 0
    assert result["period_start"] == "2026-04-06"

    rows = get_user_ledger_transactions(user["id"], limit=200)
    assert len(rows) == result["inserted"]
    # Every seeded row must fall in the declared quarter window.
    for r in rows:
        assert r["date_iso"] >= "2026-04-06"
        assert r["date_iso"] <= "2026-07-06"  # generous (90d, not 91)


def test_seed_categorises_each_row_with_an_hmrc_category():
    """Every seeded row carries an hmrc_category so the dashboard's
    quarterly preview shows realistic totals without re-running the AI
    classifier on demo data."""
    from hmrc.services import sandbox as _sandbox
    from database import get_user_ledger_transactions

    client, _, user = _client_with_user()
    _sandbox.seed_sample_transactions(user["id"], today=date(2026, 5, 22))

    rows = get_user_ledger_transactions(user["id"], limit=200)
    assert rows, "Seeder produced no rows."
    for r in rows:
        assert r["hmrc_category"], f"Row missing hmrc_category: {r}"
        assert r["hmrc_category_confidence"] is not None


def test_seed_is_idempotent():
    """Calling seed twice on the same user must not duplicate rows —
    the second call goes through entirely on the skipped_existing path
    (content_hash collisions)."""
    from hmrc.services import sandbox as _sandbox
    from database import get_user_ledger_transactions

    client, _, user = _client_with_user()
    today = date(2026, 5, 22)

    first = _sandbox.seed_sample_transactions(user["id"], today=today)
    rows_first = get_user_ledger_transactions(user["id"], limit=200)

    second = _sandbox.seed_sample_transactions(user["id"], today=today)
    rows_second = get_user_ledger_transactions(user["id"], limit=200)

    assert second["inserted"] == 0
    assert second["skipped_existing"] == first["inserted"]
    assert len(rows_first) == len(rows_second)


def test_quarter_start_picks_most_recent_boundary():
    """The MTD ITSA quarter boundaries are 6 Apr, 6 Jul, 6 Oct, 6 Jan.
    The seeder must pick the most recent one as the quarter start."""
    from hmrc.services.sandbox import _current_quarter_start

    assert _current_quarter_start(date(2026, 4, 6)) == date(2026, 4, 6)
    assert _current_quarter_start(date(2026, 5, 22)) == date(2026, 4, 6)
    assert _current_quarter_start(date(2026, 7, 5)) == date(2026, 4, 6)
    assert _current_quarter_start(date(2026, 7, 6)) == date(2026, 7, 6)
    assert _current_quarter_start(date(2026, 11, 1)) == date(2026, 10, 6)
    assert _current_quarter_start(date(2027, 1, 6)) == date(2027, 1, 6)
    assert _current_quarter_start(date(2027, 2, 15)) == date(2027, 1, 6)


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------

def test_seed_route_requires_authentication():
    """Anonymous POSTs must be rejected with 401."""
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post(
        "/api/hmrc/sandbox/seed-sample-data",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 401, r.text


def test_seed_route_returns_inserted_count_and_period_dates():
    """Authenticated POST must return inserted > 0 and the quarter window."""
    client, csrf, _ = _client_with_user()
    r = client.post(
        "/api/hmrc/sandbox/seed-sample-data",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["inserted"] > 0
    assert body["period_start"] is not None
    assert body["period_end"] is not None


def test_seed_route_is_idempotent():
    """Second POST returns inserted=0, skipped_existing>0."""
    client, csrf, _ = _client_with_user()
    first = client.post(
        "/api/hmrc/sandbox/seed-sample-data",
        json={},
        headers={"X-CSRF-Token": csrf},
    ).json()
    second = client.post(
        "/api/hmrc/sandbox/seed-sample-data",
        json={},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert first["inserted"] > 0
    assert second["inserted"] == 0
    assert second["skipped_existing"] == first["inserted"]


def test_seed_route_refuses_in_production(monkeypatch):
    """When HMRC_ENV=production, seed-sample-data must 404 to keep test
    fixtures out of real customers' ledgers."""
    monkeypatch.setenv("HMRC_ENV", "production")
    client, csrf, _ = _client_with_user()
    r = client.post(
        "/api/hmrc/sandbox/seed-sample-data",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 404, r.text


def test_seed_route_csrf_required():
    """Without CSRF the middleware blocks before reaching the handler."""
    client, _, _ = _client_with_user()
    r = client.post("/api/hmrc/sandbox/seed-sample-data", json={})
    assert r.status_code == 403, r.text
