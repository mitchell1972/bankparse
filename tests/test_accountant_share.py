"""
Tests for the shareable accountant-pack delivery feature.

Coverage:
  - Share token generation: random, URL-safe, long
  - Create / resolve roundtrip
  - Expiry honoured (resolve returns None for stale tokens)
  - Revoke honoured (resolve returns None after revoke)
  - POST /api/accountant-export/share returns a URL + sends an email
    when accountant_email is given
  - GET /share/accountant-pack/{token} (public, no auth) renders the
    landing page with the user's totals
  - GET /share/accountant-pack/{token}/download streams the ZIP and
    increments download_count + last_downloaded_at
  - Invalid token returns the friendly 404 page
  - Ownership: can't revoke someone else's share
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_accountant_share.db"


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


def _make_authed_client(email: str = "owner@example.com"):
    """Register + verify + subscribe a user, return (client, user, csrf)."""
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


def _seed_one_tx(user_id: int):
    import database as _db
    _db.insert_ledger_transaction(
        user_id, extracted_data_id=None,
        date_iso="2026-08-15", description="STAPLES",
        amount=-15.99, hmrc_category="adminCosts",
    )


# ---------------------------------------------------------------------------
# Token generation + service layer
# ---------------------------------------------------------------------------


def test_mint_token_is_long_and_url_safe():
    from services.accountant_share import mint_token
    t = mint_token()
    assert len(t) >= 24
    # URL-safe: only alphanumerics + - + _
    import re
    assert re.match(r"^[A-Za-z0-9_-]+$", t)


def test_mint_token_is_unique_across_calls():
    """Token entropy must be high enough that collisions are
    astronomically unlikely. 1000 mints should never collide."""
    from services.accountant_share import mint_token
    tokens = {mint_token() for _ in range(1000)}
    assert len(tokens) == 1000


def test_create_and_resolve_share_roundtrip():
    from services.accountant_share import create_share, resolve_share
    import database as _db
    _db.create_user("a@example.com", "pwhash")
    uid = _db.get_user_by_email("a@example.com")["id"]
    share = create_share(
        user_id=uid, period_label="Q2 2026-27",
        client_name="Acme Ltd",
        accountant_email="acc@firm.co.uk",
        accountant_name="Anna",
    )
    resolved = resolve_share(share["token"])
    assert resolved is not None
    assert resolved["user_id"] == uid
    assert resolved["period_label"] == "Q2 2026-27"
    assert resolved["client_name"] == "Acme Ltd"
    assert resolved["accountant_email"] == "acc@firm.co.uk"


def test_resolve_returns_none_for_expired_share():
    """An expired share must NOT resolve — even though the row still exists."""
    from services.accountant_share import create_share, resolve_share
    import database as _db
    _db.create_user("exp@example.com", "pwhash")
    uid = _db.get_user_by_email("exp@example.com")["id"]
    share = create_share(
        user_id=uid, period_label="X", client_name=None,
        accountant_email=None, accountant_name=None,
    )
    # Manually backdate expiry
    _db._execute(
        "UPDATE accountant_pack_shares SET expires_at = ? WHERE id = ?",
        (time.time() - 60, share["id"]),
    )
    assert resolve_share(share["token"]) is None


def test_resolve_returns_none_for_revoked_share():
    from services.accountant_share import create_share, resolve_share
    import database as _db
    _db.create_user("rev@example.com", "pwhash")
    uid = _db.get_user_by_email("rev@example.com")["id"]
    share = create_share(
        user_id=uid, period_label="X", client_name=None,
        accountant_email=None, accountant_name=None,
    )
    assert resolve_share(share["token"]) is not None
    assert _db.revoke_accountant_pack_share(uid, int(share["id"])) is True
    assert resolve_share(share["token"]) is None


def test_resolve_returns_none_for_unknown_token():
    from services.accountant_share import resolve_share
    assert resolve_share("not-a-real-token-xxxxxxxxxxxxxxxxxxxxxxxxxxxx") is None
    assert resolve_share("") is None
    assert resolve_share("short") is None


# ---------------------------------------------------------------------------
# POST /api/accountant-export/share
# ---------------------------------------------------------------------------


def test_post_share_creates_url_and_returns_token():
    client, user, csrf = _make_authed_client("creator@example.com")
    _seed_one_tx(user["id"])
    r = client.post(
        "/api/accountant-export/share",
        json={"period": "Q2 2026-27 (Jul-Sep)",
              "client_name": "Mitoba",
              "accountant_email": "anna@firm.co.uk",
              "accountant_name": "Anna",
              "send_email": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["share_url"].endswith("/share/accountant-pack/" + body["token"])
    assert len(body["token"]) >= 24
    assert body["email_sent"] is False  # send_email=False


def test_post_share_with_send_email_calls_resend():
    """When send_email=true and accountant_email is set, we should call
    send_accountant_pack_email exactly once."""
    client, user, csrf = _make_authed_client("emailer@example.com")
    _seed_one_tx(user["id"])
    with patch("otp.send_accountant_pack_email", return_value=True) as mock_send:
        r = client.post(
            "/api/accountant-export/share",
            json={"period": "Q2 2026-27 (Jul-Sep)",
                  "client_name": "Mitoba",
                  "accountant_email": "anna@firm.co.uk",
                  "accountant_name": "Anna",
                  "send_email": True},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["email_sent"] is True
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["accountant_email"] == "anna@firm.co.uk"
    assert call_kwargs["sender_email"] == "emailer@example.com"
    assert call_kwargs["client_name"] == "Mitoba"
    assert call_kwargs["period_label"] == "Q2 2026-27 (Jul-Sep)"
    assert call_kwargs["share_url"].endswith(body["token"])


def test_post_share_without_email_still_returns_link():
    """User wants a copyable link without sending an email — that path
    must work (send_email=false, accountant_email omitted)."""
    client, user, csrf = _make_authed_client("linkonly@example.com")
    _seed_one_tx(user["id"])
    r = client.post(
        "/api/accountant-export/share",
        json={"period": None, "client_name": None, "send_email": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["share_url"].startswith("http")
    assert body["email_sent"] is False


def test_post_share_requires_auth():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    r = client.post(
        "/api/accountant-export/share",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /share/accountant-pack/{token} (public — no auth)
# ---------------------------------------------------------------------------


def test_landing_page_renders_for_valid_token():
    client, user, csrf = _make_authed_client("landing@example.com")
    _seed_one_tx(user["id"])
    create = client.post(
        "/api/accountant-export/share",
        json={"period": "Q2 2026-27 (Jul-Sep)",
              "client_name": "Acme Ltd",
              "send_email": False},
        headers={"X-CSRF-Token": csrf},
    )
    token = create.json()["token"]
    # Anonymous client (no cookies) hits the landing page
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get(f"/share/accountant-pack/{token}")
    assert r.status_code == 200
    html = r.text
    assert "Acme Ltd" in html
    assert "Q2 2026-27 (Jul-Sep)" in html
    # Should NOT leak the owner's authenticated session cookies — it's
    # accessed by an anonymous client.
    assert "Download pack" in html
    assert f"/share/accountant-pack/{token}/download" in html


def test_landing_page_shows_friendly_404_for_unknown_token():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get("/share/accountant-pack/not-real-xxxxxxxxxxxxxxxxxxxxxxxx")
    assert r.status_code == 404
    assert "isn't valid" in r.text
    # No sensitive details leaked
    assert "exception" not in r.text.lower()
    assert "traceback" not in r.text.lower()


# ---------------------------------------------------------------------------
# GET /share/accountant-pack/{token}/download
# ---------------------------------------------------------------------------


def test_public_download_streams_zip_bumps_counter():
    client, user, csrf = _make_authed_client("download@example.com")
    _seed_one_tx(user["id"])
    token = client.post(
        "/api/accountant-export/share",
        json={"send_email": False},
        headers={"X-CSRF-Token": csrf},
    ).json()["token"]

    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get(f"/share/accountant-pack/{token}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.content[:2] == b"PK"

    # The download counter should have ticked
    import database as _db
    rows = _db.list_user_accountant_pack_shares(user["id"])
    assert rows[0]["download_count"] == 1
    assert rows[0]["last_downloaded_at"] is not None

    # A second download bumps it again
    anon.get(f"/share/accountant-pack/{token}/download")
    rows = _db.list_user_accountant_pack_shares(user["id"])
    assert rows[0]["download_count"] == 2


def test_public_download_rejects_unknown_token():
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get("/share/accountant-pack/not-real-xxxxxxxxxxxxxxxxxxxxxxxx/download")
    assert r.status_code == 404


def test_public_download_rejects_revoked_token():
    client, user, csrf = _make_authed_client("revoked-dl@example.com")
    _seed_one_tx(user["id"])
    body = client.post(
        "/api/accountant-export/share",
        json={"send_email": False},
        headers={"X-CSRF-Token": csrf},
    ).json()
    token = body["token"]
    # Revoke via the endpoint
    import database as _db
    share_id = _db.list_user_accountant_pack_shares(user["id"])[0]["id"]
    client.post(
        f"/api/accountant-export/share/{share_id}/revoke",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    from app import app
    anon = TestClient(app, raise_server_exceptions=False)
    anon.__enter__()
    r = anon.get(f"/share/accountant-pack/{token}/download")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Shares list + revoke
# ---------------------------------------------------------------------------


def test_shares_list_returns_history():
    client, user, csrf = _make_authed_client("history@example.com")
    _seed_one_tx(user["id"])
    for em in ["a@firm.co.uk", "b@firm.co.uk"]:
        client.post(
            "/api/accountant-export/share",
            json={"accountant_email": em, "send_email": False},
            headers={"X-CSRF-Token": csrf},
        )
    r = client.get("/api/accountant-export/shares")
    assert r.status_code == 200
    shares = r.json()["shares"]
    assert len(shares) == 2
    emails = {s["accountant_email"] for s in shares}
    assert emails == {"a@firm.co.uk", "b@firm.co.uk"}
    # Raw token is NOT exposed in the list — only the tail for identification
    for s in shares:
        assert "token" not in s
        assert "token_tail" in s


def test_revoke_blocks_other_users():
    """Mallory cannot revoke Alice's share even with a valid CSRF token
    on her own session."""
    alice_c, alice, alice_csrf = _make_authed_client("alice@example.com")
    _seed_one_tx(alice["id"])
    alice_c.post(
        "/api/accountant-export/share",
        json={"send_email": False},
        headers={"X-CSRF-Token": alice_csrf},
    )
    import database as _db
    alice_share = _db.list_user_accountant_pack_shares(alice["id"])[0]

    mallory_c, mallory, mallory_csrf = _make_authed_client("mallory@example.com")
    r = mallory_c.post(
        f"/api/accountant-export/share/{alice_share['id']}/revoke",
        headers={"X-CSRF-Token": mallory_csrf, "Content-Type": "application/json"},
    )
    assert r.status_code == 404
    # Alice's share is still resolvable
    from services.accountant_share import resolve_share
    assert resolve_share(alice_share["token"]) is not None


def test_revoke_is_idempotent():
    client, user, csrf = _make_authed_client("idem@example.com")
    _seed_one_tx(user["id"])
    client.post(
        "/api/accountant-export/share",
        json={"send_email": False},
        headers={"X-CSRF-Token": csrf},
    )
    import database as _db
    share_id = _db.list_user_accountant_pack_shares(user["id"])[0]["id"]
    # First revoke succeeds
    r1 = client.post(
        f"/api/accountant-export/share/{share_id}/revoke",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    assert r1.status_code == 200
    # Second revoke returns 404 (already revoked)
    r2 = client.post(
        f"/api/accountant-export/share/{share_id}/revoke",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
    )
    assert r2.status_code == 404
