"""
Tests for the user-facing HMRC submission UI:

  - /hmrc/file       — page renders for authenticated users
  - /api/hmrc/submissions       — lists user's HMRC audit log
  - /api/hmrc/penalty-status    — penalty-points snapshot
  - /terms                      — public, returns 200 (HMRC checks this)
  - /privacy                    — still works
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_submit_ui.db"


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
    def _g():
        if database._sqlite_conn is None:
            conn = sqlite3.connect(TEST_DB_PATH, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            database._sqlite_conn = conn
        return database._sqlite_conn
    database._get_sqlite = _g
    database.init_db()
    yield
    if database._sqlite_conn is not None:
        try: database._sqlite_conn.close()
        except Exception: pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


def _authed(email: str = "filer@example.com"):
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
                    stripe_subscription_id="sub_test",
                    trial_end_at=time.time() + 7*86400)
    return client, user, csrf


# ---------------------------------------------------------------------------
# /hmrc/file page
# ---------------------------------------------------------------------------


def test_hmrc_file_page_renders_for_authed_user():
    client, user, _ = _authed("file@example.com")
    r = client.get("/hmrc/file")
    assert r.status_code == 200
    body = r.text
    # Key chrome that proves the file page rendered (not the connect page)
    assert "File with HMRC" in body
    assert "Submission history" in body
    assert "Penalty points" in body
    assert "Open File-with-HMRC" not in body  # that's the home page's link
    assert "Estimated tax owed" in body


def test_hmrc_file_page_shows_tax_estimate_accuracy_disclaimer():
    """HMRC minimum-functionality standard: any in-software estimate of the
    customer's Income Tax liability MUST carry an accuracy disclaimer
    (developer.service.hmrc.gov.uk how-to-integrate guide). Pin it so it
    can't silently regress before / after production approval.
    See hmrc/docs/production-approvals-checklist.md §2."""
    client, user, _ = _authed("disclaimer@example.com")
    r = client.get("/hmrc/file")
    assert r.status_code == 200
    body = r.text
    assert 'id="taxEstimateDisclaimer"' in body, (
        "tax-estimate accuracy disclaimer is missing from /hmrc/file — "
        "HMRC requires it on any in-software liability estimate"
    )
    # The disclaimer must actually say it's an estimate and not the final
    # liability — not just be an empty element.
    assert "estimate only" in body
    assert "not your final tax liability" in body


def test_hmrc_file_page_redirects_anonymous_to_login():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    anon.__enter__()
    r = anon.get("/hmrc/file")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# /api/hmrc/submissions
# ---------------------------------------------------------------------------


def test_submissions_endpoint_empty_when_no_history():
    client, user, _ = _authed("emptyhist@example.com")
    r = client.get("/api/hmrc/submissions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["submissions"] == []


def test_submissions_endpoint_lists_recorded_calls():
    """After we write a row to hmrc_submissions, the endpoint surfaces it
    with a human label + derived ok flag."""
    client, user, _ = _authed("hist@example.com")
    from hmrc.repositories import submissions as _repo

    audit_id = _repo.record(
        user_id=user["id"],
        endpoint="/individuals/business/self-employment-business/CX139207A/AB123/period-summaries",
        method="POST",
        request_headers={"Authorization": "Bearer secret"},
        request_body={"periodStartDate": "2026-04-06", "periodEndDate": "2026-07-05"},
        response_status=200,
        response_headers={"Content-Type": "application/json"},
        response_body={"transactionReference": "ABC123XYZ"},
        idempotency_key="idem-xyz",
    )
    assert audit_id

    r = client.get("/api/hmrc/submissions")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    s = body["submissions"][0]
    assert s["audit_id"] == audit_id
    assert "Quarterly update" in s["label"]  # classified from URL
    assert s["hmrc_reference"] == "ABC123XYZ"
    assert s["ok"] is True
    assert s["period_start"] == "2026-04-06"
    assert s["period_end"] == "2026-07-05"


def test_submissions_endpoint_marks_failed_calls_as_not_ok():
    client, user, _ = _authed("fail@example.com")
    from hmrc.repositories import submissions as _repo
    _repo.record(
        user_id=user["id"], endpoint="/individuals/calculations/CX139207A/self-assessment",
        method="POST", request_headers={}, request_body={},
        response_status=500, response_headers={},
        response_body={"code": "INTERNAL_SERVER_ERROR"},
    )
    r = client.get("/api/hmrc/submissions")
    body = r.json()
    assert body["submissions"][0]["ok"] is False
    assert body["submissions"][0]["response_status"] == 500


def test_submissions_endpoint_does_not_leak_authorization_header():
    """The repo strips bearer tokens at write-time; the endpoint must
    never return them even if a row somehow got one."""
    client, user, _ = _authed("noleak@example.com")
    from hmrc.repositories import submissions as _repo
    _repo.record(
        user_id=user["id"], endpoint="/x",
        method="POST",
        request_headers={"Authorization": "Bearer SECRET_TOKEN"},
        request_body=None,
        response_status=200, response_headers={}, response_body={},
    )
    body = client.get("/api/hmrc/submissions").json()
    import json as _json
    blob = _json.dumps(body)
    assert "SECRET_TOKEN" not in blob


def test_submissions_endpoint_requires_auth():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get("/api/hmrc/submissions")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /api/hmrc/penalty-status
# ---------------------------------------------------------------------------


def test_penalty_status_anonymous_returns_zeros():
    """The strip is rendered on the home page (which is publicly viewable
    for non-logged-in marketing visitors). The endpoint MUST tolerate
    no auth — returns a safe 'zero points' shape rather than 401."""
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get("/api/hmrc/penalty-status")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["points"] == 0
    assert body["threshold"] == 4
    assert body["next_fine_gbp"] == 200


def test_penalty_status_authenticated_returns_threshold():
    client, user, _ = _authed("pen@example.com")
    r = client.get("/api/hmrc/penalty-status")
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["threshold"] == 4
    assert body["points"] >= 0
    assert body["remaining"] == body["threshold"] - body["points"]


# ---------------------------------------------------------------------------
# /terms (HMRC recognition requirement) + /privacy regression
# ---------------------------------------------------------------------------


def test_terms_page_returns_200_with_substantive_content():
    """HMRC's recognition form pings the Terms URL — it must return 200
    with real content (not a placeholder)."""
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get("/terms")
    assert r.status_code == 200
    body = r.text
    # Substantive content checks — these are the section titles HMRC
    # looks for in a recognition Terms of Service.
    assert "Terms of Service" in body
    assert "MTD ITSA" in body
    assert "Liability" in body
    assert "Governing law" in body
    assert "England" in body  # UK jurisdiction


def test_privacy_page_still_returns_200():
    """Regression — make sure the existing privacy page didn't break."""
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get("/privacy")
    assert r.status_code == 200
    assert "Privacy" in r.text


# ---------------------------------------------------------------------------
# HMRC deadline reminder bookkeeping
# ---------------------------------------------------------------------------


def test_mark_deadline_reminder_is_idempotent():
    """Calling the helper twice for the same (user, deadline, lead) must
    return False on the second call — the cron uses this to skip re-sends."""
    import database as _db
    _db.create_user("rem@example.com", "pw")
    uid = _db.get_user_by_email("rem@example.com")["id"]
    ok1 = _db.mark_hmrc_deadline_reminder_sent(
        user_id=uid, deadline_iso="2026-08-05", lead_days=7,
        business_label="Self-employment",
    )
    ok2 = _db.mark_hmrc_deadline_reminder_sent(
        user_id=uid, deadline_iso="2026-08-05", lead_days=7,
        business_label="Self-employment",
    )
    assert ok1 is True
    assert ok2 is False
    assert _db.has_hmrc_deadline_reminder(
        user_id=uid, deadline_iso="2026-08-05", lead_days=7,
    ) is True


def test_mark_deadline_reminder_different_leads_are_distinct():
    """7-day and 1-day reminders for the same deadline are separate rows."""
    import database as _db
    _db.create_user("two@example.com", "pw")
    uid = _db.get_user_by_email("two@example.com")["id"]
    assert _db.mark_hmrc_deadline_reminder_sent(
        user_id=uid, deadline_iso="2026-08-05", lead_days=7,
    ) is True
    assert _db.mark_hmrc_deadline_reminder_sent(
        user_id=uid, deadline_iso="2026-08-05", lead_days=1,
    ) is True
