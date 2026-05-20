"""
Tests for the remaining ledger features:
  - Mileage tracker (services.mileage + /api/mileage endpoints)
  - Anomaly / missed-expense detection (services.anomaly_detector + /api/anomalies)
  - Email-in receipt webhook (/api/receipts/email-in)
  - Forwarding-address helper
  - Accountant bulk-approve (/api/ledger/bulk-approve)
"""
from __future__ import annotations

import base64
import datetime as _dt
import os
import secrets
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_remaining.db"


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


def _client(email: str) -> tuple:
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
                    stripe_subscription_id="sub_rm",
                    trial_end_at=time.time() + 7*86400)
    return client, user, csrf


# ===========================================================================
# Mileage tracker
# ===========================================================================


def test_mileage_add_car_journey_uses_45p_rate():
    from services.mileage import add_mileage_log, mileage_summary
    import database as _db
    _db.create_user("car@example.com", "h")
    uid = _db.get_user_by_email("car@example.com")["id"]

    today = _dt.date.today().isoformat()
    add_mileage_log(uid, date_iso=today, miles=100.0, vehicle="car")
    s = mileage_summary(uid)
    # 100 miles * 45p = £45.00
    assert s["totals"]["car_miles"] == 100.0
    assert s["totals"]["total_claim_gbp"] == 45.0
    assert s["totals"]["band_1_remaining"] == 9900.0


def test_mileage_band_1_threshold_splits_at_10000():
    """Mid-journey 10k crossing — first slice at 45p, rest at 25p."""
    from services.mileage import add_mileage_log, mileage_summary
    import database as _db
    _db.create_user("threshold@example.com", "h")
    uid = _db.get_user_by_email("threshold@example.com")["id"]

    today = _dt.date.today().isoformat()
    # First 9k miles at 45p
    add_mileage_log(uid, date_iso=today, miles=9000.0, vehicle="car")
    # Next 2k miles spans the 10k threshold: 1k at 45p + 1k at 25p
    add_mileage_log(uid, date_iso=today, miles=2000.0, vehicle="car")
    s = mileage_summary(uid)
    # 9000*0.45 + 1000*0.45 + 1000*0.25 = 4050 + 450 + 250 = 4750
    assert s["totals"]["total_claim_gbp"] == 4750.0
    assert s["totals"]["band_1_remaining"] == 0


def test_mileage_motorcycle_flat_24p_rate():
    from services.mileage import add_mileage_log, mileage_summary
    import database as _db
    _db.create_user("moto@example.com", "h")
    uid = _db.get_user_by_email("moto@example.com")["id"]
    today = _dt.date.today().isoformat()
    add_mileage_log(uid, date_iso=today, miles=100.0, vehicle="motorcycle")
    s = mileage_summary(uid)
    # 100 * 24p = £24.00
    assert s["totals"]["total_claim_gbp"] == 24.0
    assert s["totals"]["motorcycle_miles"] == 100.0


def test_mileage_business_pct_proportional():
    from services.mileage import add_mileage_log, mileage_summary
    import database as _db
    _db.create_user("mixed@example.com", "h")
    uid = _db.get_user_by_email("mixed@example.com")["id"]
    today = _dt.date.today().isoformat()
    # 100 miles at 60% business = 60 business miles * 45p = £27
    add_mileage_log(uid, date_iso=today, miles=100.0, vehicle="car", business_pct=60)
    s = mileage_summary(uid)
    assert s["totals"]["car_miles"] == 60.0
    assert s["totals"]["total_claim_gbp"] == 27.0


def test_mileage_rejects_zero_miles():
    from services.mileage import add_mileage_log
    import database as _db
    _db.create_user("zero@example.com", "h")
    uid = _db.get_user_by_email("zero@example.com")["id"]
    with pytest.raises(ValueError, match="positive"):
        add_mileage_log(uid, date_iso="2026-08-04", miles=0)


def test_mileage_rejects_invalid_vehicle():
    from services.mileage import add_mileage_log
    import database as _db
    _db.create_user("invalid@example.com", "h")
    uid = _db.get_user_by_email("invalid@example.com")["id"]
    with pytest.raises(ValueError, match="vehicle"):
        add_mileage_log(uid, date_iso="2026-08-04", miles=10, vehicle="hovercraft")


def test_mileage_endpoint_round_trip():
    client, user, csrf = _client("mileapi@example.com")
    today = _dt.date.today().isoformat()
    r = client.post("/api/mileage",
                    json={"date_iso": today, "miles": 25.0, "vehicle": "car",
                          "purpose": "Client site visit"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    log_id = r.json()["log_id"]

    s = client.get("/api/mileage")
    assert s.status_code == 200
    body = s.json()
    assert body["totals"]["total_claim_gbp"] == 11.25  # 25 * 0.45

    # Delete it
    d = client.delete(f"/api/mileage/{log_id}", headers={"X-CSRF-Token": csrf})
    assert d.status_code == 200
    s2 = client.get("/api/mileage")
    assert s2.json()["totals"]["car_miles"] == 0


def test_mileage_delete_404_for_other_users_log():
    client_a, user_a, csrf_a = _client("a@example.com")
    client_b, user_b, _ = _client("b@example.com")
    from services.mileage import add_mileage_log
    log_b = add_mileage_log(user_b["id"], date_iso="2026-08-04", miles=10.0)
    r = client_a.delete(f"/api/mileage/{log_b}", headers={"X-CSRF-Token": csrf_a})
    assert r.status_code == 404


def test_mileage_endpoint_rejects_invalid_body():
    client, user, csrf = _client("badbody@example.com")
    r = client.post("/api/mileage", json={"miles": 10},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400
    r = client.post("/api/mileage", json={"date_iso": "2026-08-04", "miles": -5},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


# ===========================================================================
# Anomaly detection
# ===========================================================================


def _seed_quarter_spend(uid: int, year: int, q: int, cat: str, amounts: list[float]):
    """Helper: insert N transactions into a specific calendar quarter."""
    import database as _db
    q_month_start = (q - 1) * 3 + 1
    for i, amt in enumerate(amounts):
        d = _dt.date(year, q_month_start, 1 + (i % 28))
        _db.insert_ledger_transaction(
            uid, extracted_data_id=None,
            date_iso=d.isoformat(), description=f"VENDOR {i}",
            amount=-amt, hmrc_category=cat,
        )


def test_anomaly_detects_missing_expenses_in_current_quarter():
    """Baseline £450/quarter, current quarter only £120 → flagged."""
    from services.anomaly_detector import detect_anomalies
    import database as _db
    _db.create_user("a@example.com", "h")
    uid = _db.get_user_by_email("a@example.com")["id"]

    today = _dt.date.today()
    current_q_start = _dt.date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    prev_q = current_q_start - _dt.timedelta(days=92)
    pprev_q = prev_q - _dt.timedelta(days=92)

    # Baseline: 3 quarters of motor expenses around £450 each
    _seed_quarter_spend(uid, prev_q.year, (prev_q.month - 1) // 3 + 1,
                        "se_motor_expenses", [120.0, 150.0, 180.0])  # = 450
    _seed_quarter_spend(uid, pprev_q.year, (pprev_q.month - 1) // 3 + 1,
                        "se_motor_expenses", [200.0, 250.0])  # = 450
    # Current quarter: only £120 spent
    _seed_quarter_spend(uid, today.year, (today.month - 1) // 3 + 1,
                        "se_motor_expenses", [120.0])

    result = detect_anomalies(uid, today=today)
    assert len(result["anomalies"]) >= 1
    motor = next((a for a in result["anomalies"] if a["category"] == "se_motor_expenses"), None)
    assert motor is not None
    assert motor["current_gbp"] == 120.0
    assert motor["baseline_avg_gbp"] == 450.0
    assert motor["drop_pct"] >= 70
    # The message is plain English with both numbers
    assert "£120" in motor["message"]
    assert "£450" in motor["message"]


def test_anomaly_silent_when_no_baseline_data():
    """A user with only current-quarter data has no baseline — should NOT
    flag (we'd be flagging on nothing)."""
    from services.anomaly_detector import detect_anomalies
    import database as _db
    _db.create_user("new@example.com", "h")
    uid = _db.get_user_by_email("new@example.com")["id"]

    today = _dt.date.today()
    _seed_quarter_spend(uid, today.year, (today.month - 1) // 3 + 1,
                        "se_motor_expenses", [100.0])
    result = detect_anomalies(uid, today=today)
    assert result["anomalies"] == []


def test_anomaly_silent_when_baseline_too_small():
    """Baseline < £50 → silent (avoids false positives on tiny categories)."""
    from services.anomaly_detector import detect_anomalies
    import database as _db
    _db.create_user("small@example.com", "h")
    uid = _db.get_user_by_email("small@example.com")["id"]

    today = _dt.date.today()
    current_q_start = _dt.date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    prev_q = current_q_start - _dt.timedelta(days=92)

    _seed_quarter_spend(uid, prev_q.year, (prev_q.month - 1) // 3 + 1,
                        "se_motor_expenses", [10.0, 15.0])  # tiny
    result = detect_anomalies(uid, today=today)
    assert result["anomalies"] == []


def test_anomaly_endpoint_returns_shape():
    client, user, csrf = _client("anomalyapi@example.com")
    r = client.get("/api/anomalies")
    assert r.status_code == 200
    body = r.json()
    assert "anomalies" in body
    assert "current_quarter" in body
    assert "baseline_quarters" in body


# ===========================================================================
# Email-in receipts
# ===========================================================================


def test_forwarding_address_endpoint():
    """The forwarding address is now a random 8-char token (not the bare
    integer user id) — see test_auto_categorise_and_receipt_token.py
    for the pinning tests on the token format."""
    import re
    client, user, _ = _client("forward@example.com")
    r = client.get("/api/receipts/forwarding-address")
    assert r.status_code == 200
    addr = r.json()["address"]
    assert addr.endswith("@receipts.bankscanai.com")
    local = addr.split("@")[0]
    assert re.fullmatch(r"[a-z0-9]{8}", local), f"Bad token format: {local!r}"
    # And explicitly NOT the user id
    assert local != str(user["id"])


def test_email_in_with_session_attaches_to_logged_in_user():
    """In-app 'I forwarded an invoice' button — uses the session cookie."""
    client, user, csrf = _client("emailin@example.com")
    payload_bytes = b"%PDF-1.4 fake receipt body"
    r = client.post("/api/receipts/email-in",
                    json={
                        "from": "billing@stripe.com",
                        "subject": "Invoice",
                        "attachments": [
                            {"filename": "stripe_invoice.pdf",
                             "content_b64": base64.b64encode(payload_bytes).decode()},
                        ],
                    },
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["saved"] == 1


def test_email_in_via_forwarding_address_routes_by_local_part():
    """Webhook from Resend/SES: anonymous, with `to` field. The
    user_id is the local-part of the recipient address."""
    client, user, _ = _client("router@example.com")
    # Now use a NEW client without auth cookies — simulates the webhook
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    payload_bytes = b"PDF"
    r = anon.post("/api/receipts/email-in",
                  json={
                      "to": f"{user['id']}@receipts.bankscanai.com",
                      "from": "billing@stripe.com",
                      "attachments": [
                          {"filename": "x.pdf",
                           "content_b64": base64.b64encode(payload_bytes).decode()},
                      ],
                  })
    assert r.status_code == 200
    body = r.json()
    assert body["received"] == 1


def test_email_in_rejects_no_attachments():
    client, user, csrf = _client("noatt@example.com")
    r = client.post("/api/receipts/email-in",
                    json={"from": "x@example.com", "attachments": []},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_email_in_rejects_bad_recipient_format():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.post("/api/receipts/email-in",
                  json={"to": "not-an-email", "attachments": [{}]})
    assert r.status_code == 401


def test_email_in_rejects_non_integer_local_part():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.post("/api/receipts/email-in",
                  json={"to": "abc@receipts.bankscanai.com",
                        "attachments": [{"filename": "x.pdf", "content_b64": "AA=="}]})
    assert r.status_code == 400


# ===========================================================================
# Accountant bulk-approve
# ===========================================================================


def test_bulk_approve_sets_confidence_100_on_category():
    client, user, csrf = _client("bulk@example.com")
    import database as _db
    # Seed 3 transactions in Office Expenses, all currently 50% confidence
    for i in range(3):
        _db.insert_ledger_transaction(
            user["id"], extracted_data_id=None,
            date_iso="2026-08-04", description=f"AMAZON {i}",
            amount=-(20.0 + i),
            hmrc_category="se_office_expenses",
            hmrc_category_confidence=50,
        )
    r = client.post("/api/ledger/bulk-approve",
                    json={"hmrc_category": "se_office_expenses"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert r.json()["approved"] == 3

    txs = _db.get_user_ledger_transactions(user["id"])
    assert all(t["hmrc_category_confidence"] == 100 for t in txs)


def test_bulk_approve_respects_max_amount():
    """Setting max_amount=50 only approves transactions ≤ £50."""
    client, user, csrf = _client("max@example.com")
    import database as _db
    _db.insert_ledger_transaction(user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="SMALL",
        amount=-30.0, hmrc_category="se_office_expenses",
        hmrc_category_confidence=50)
    _db.insert_ledger_transaction(user["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="BIG",
        amount=-500.0, hmrc_category="se_office_expenses",
        hmrc_category_confidence=50)

    r = client.post("/api/ledger/bulk-approve",
                    json={"hmrc_category": "se_office_expenses", "max_amount": 50},
                    headers={"X-CSRF-Token": csrf})
    assert r.json()["approved"] == 1
    txs = _db.get_user_ledger_transactions(user["id"])
    small = next(t for t in txs if "SMALL" in t["description"])
    big = next(t for t in txs if "BIG" in t["description"])
    assert small["hmrc_category_confidence"] == 100
    assert big["hmrc_category_confidence"] == 50  # untouched


def test_bulk_approve_requires_category():
    client, user, csrf = _client("nocat@example.com")
    r = client.post("/api/ledger/bulk-approve", json={},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_bulk_approve_only_touches_callers_transactions():
    """Cross-user: bulk approve must only affect the logged-in user's rows."""
    client_a, user_a, csrf_a = _client("attacker@example.com")
    client_b, user_b, _ = _client("victim@example.com")
    import database as _db
    _db.insert_ledger_transaction(
        user_b["id"], extracted_data_id=None,
        date_iso="2026-08-04", description="VICTIM",
        amount=-20.0, hmrc_category="se_office_expenses",
        hmrc_category_confidence=50,
    )
    r = client_a.post("/api/ledger/bulk-approve",
                      json={"hmrc_category": "se_office_expenses"},
                      headers={"X-CSRF-Token": csrf_a})
    assert r.json()["approved"] == 0  # attacker has no txs in that cat
    txs = _db.get_user_ledger_transactions(user_b["id"])
    assert txs[0]["hmrc_category_confidence"] == 50  # victim untouched


# ===========================================================================
# Auth gates
# ===========================================================================


def test_all_endpoints_require_auth():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    # Seed a CSRF cookie + token (server pairs cookie<->header)
    anon.get("/login")
    csrf = anon.cookies.get("bp_csrf", "")
    headers = {"X-CSRF-Token": csrf}

    assert anon.get("/api/mileage").status_code == 401
    assert anon.post("/api/mileage",
                     json={"date_iso": "2026-08-04", "miles": 10},
                     headers=headers).status_code == 401
    assert anon.get("/api/anomalies").status_code == 401
    assert anon.get("/api/receipts/forwarding-address").status_code == 401
    assert anon.post("/api/ledger/bulk-approve",
                     json={"hmrc_category": "se_office_expenses"},
                     headers=headers).status_code == 401
