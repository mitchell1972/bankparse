"""
Tests for the Business Details API integration.

These mock `hmrc.services.client.request` so we don't talk to the real
HMRC sandbox. They cover:

  - mapping HMRC wire format -> our UiBusiness shape
  - normalising 'uk-property' and 'foreign-property' to 'property'
  - falling back to a sensible label when HMRC doesn't send a trading name
  - the /api/hmrc/connect-businesses endpoint validation + persistence

Once the sandbox test user is created, we'll add a real recorded fixture.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_business_details.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    test_key = base64.b64encode(secrets.token_bytes(32)).decode()
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("HMRC_DEMO_OBLIGATIONS", raising=False)

    # As per test_obligations.py — `hmrc.config` reads the key at import
    # time, so patch the module value + reset the AES cache when a prior
    # test file imported config without the env set.
    from hmrc import config as _cfg
    from hmrc.services import crypto as _crypto
    monkeypatch.setattr(_cfg, "HMRC_TOKEN_ENCRYPTION_KEY", test_key)
    monkeypatch.setattr(_crypto, "_KEY_CACHE", None)

    # Disable slowapi register cap during this file's user registrations.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_csrf(client):
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_user(email="bd-test@example.com"):
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    csrf = _seed_csrf(client)
    r = client.post("/api/register",
                    json={"email": email, "password": "password12345"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    user = _db.get_user_by_email(email)
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], grandfathered_trial=1)
    return client, csrf, user


def _connect_oauth(user_id: int):
    """Simulate a completed OAuth flow — tokens only, no NINO yet."""
    from hmrc.repositories import tokens as _tokens
    _tokens.save_tokens(
        user_id=user_id, access_token="sandbox-access-token",
        refresh_token="sandbox-refresh-token",
        expires_in_seconds=14400, scope="read:self-assessment write:self-assessment",
    )


_HMRC_BUSINESSES_RESPONSE = {
    "listOfBusinesses": [
        {
            "businessId": "XAIS00000000001",
            "typeOfBusiness": "self-employment",
            "tradingName": "Mitoba Consulting",
            "accountingType": "CASH",
        },
        {
            "businessId": "XPIS00000000002",
            "typeOfBusiness": "uk-property",
            "tradingName": "Ipswich SA portfolio",
        },
        {
            # Trading name omitted on purpose — service should fall back.
            "businessId": "XFIS00000000003",
            "typeOfBusiness": "foreign-property",
        },
    ],
}


# ---------------------------------------------------------------------------
# Service-level mapping tests (no HTTP layer)
# ---------------------------------------------------------------------------

def test_fetch_for_nino_maps_hmrc_wire_to_ui_shape():
    from hmrc.services import business_details as _svc

    mock_resp = MagicMock(
        status_code=200, json=_HMRC_BUSINESSES_RESPONSE,
        headers={}, audit_id="audit-x",
    )
    with patch("hmrc.services.business_details._client.request", return_value=mock_resp) as mock_call:
        result = _svc.fetch_for_nino(
            user_id=42, nino="AB123456C", request_obj=None,
        )

    # The HMRC URL we hit
    kwargs = mock_call.call_args.kwargs
    assert kwargs["path"] == "/individuals/business/details/AB123456C/list"
    assert kwargs["method"] == "GET"

    assert len(result) == 3
    se = result[0]
    assert se.business_id == "XAIS00000000001"
    assert se.type_of_business == "self-employment"
    assert se.label == "Mitoba Consulting"

    prop = result[1]
    assert prop.type_of_business == "property"   # normalised from uk-property
    assert prop.label == "Ipswich SA portfolio"

    foreign = result[2]
    # foreign-property collapses to "property" because submissions go to
    # the same Property Business API endpoint.
    assert foreign.type_of_business == "property"
    # No trading name on the wire -> we fall back to a sensible label.
    assert foreign.label == "UK property"


def test_fetch_for_nino_handles_empty_business_list():
    from hmrc.services import business_details as _svc

    mock_resp = MagicMock(
        status_code=200, json={"listOfBusinesses": []},
        headers={}, audit_id="audit-y",
    )
    with patch("hmrc.services.business_details._client.request", return_value=mock_resp):
        result = _svc.fetch_for_nino(
            user_id=42, nino="AB123456C", request_obj=None,
        )
    assert result == []


# ---------------------------------------------------------------------------
# Router-level tests for POST /api/hmrc/connect-businesses
# ---------------------------------------------------------------------------

def test_connect_businesses_requires_authentication():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        csrf = _seed_csrf(client)
        r = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "AB123456C"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 401


def test_connect_businesses_rejects_malformed_nino():
    client, csrf, user = _client_with_user()
    _connect_oauth(user["id"])
    r = client.post(
        "/api/hmrc/connect-businesses",
        json={"nino": "1234"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "AB123456C" in r.json()["detail"]


def test_connect_businesses_requires_oauth_first():
    client, csrf, _ = _client_with_user()  # no _connect_oauth
    r = client.post(
        "/api/hmrc/connect-businesses",
        json={"nino": "AB123456C"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409


def test_connect_businesses_calls_hmrc_and_persists():
    client, csrf, user = _client_with_user()
    _connect_oauth(user["id"])

    mock_resp = MagicMock(
        status_code=200, json=_HMRC_BUSINESSES_RESPONSE,
        headers={}, audit_id="audit-z",
    )
    with patch(
        "hmrc.services.business_details._client.request",
        return_value=mock_resp,
    ) as mock_call:
        r = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "ab123456c"},  # lower-case on purpose; we upper-case it
            headers={"X-CSRF-Token": csrf},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["businesses_found"] == 3
    assert {b["type_of_business"] for b in body["businesses"]} == {
        "self-employment", "property",
    }
    # The HMRC URL we hit
    assert mock_call.call_args.kwargs["path"] == (
        "/individuals/business/details/AB123456C/list"
    )

    # Persisted to hmrc_connections
    from hmrc.repositories import tokens as _tokens
    info = _tokens.get_tokens(user["id"])
    assert info["nino"] == "AB123456C"
    assert len(info["businesses"]) == 3


def test_connect_businesses_returns_404_when_no_businesses():
    client, csrf, user = _client_with_user()
    _connect_oauth(user["id"])
    mock_resp = MagicMock(
        status_code=200, json={"listOfBusinesses": []},
        headers={}, audit_id="audit-empty",
    )
    with patch(
        "hmrc.services.business_details._client.request",
        return_value=mock_resp,
    ):
        r = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "AB123456C"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 404
    assert "MTD ITSA businesses" in r.json()["detail"]


def test_connect_businesses_surfaces_hmrc_error_to_user():
    from hmrc.services.client import HmrcApiError
    client, csrf, user = _client_with_user()
    _connect_oauth(user["id"])
    with patch(
        "hmrc.services.business_details._client.request",
        side_effect=HmrcApiError(403, {"code": "CLIENT_OR_AGENT_NOT_AUTHORISED",
                                       "message": "Not authorised"}),
    ):
        r = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "AB123456C"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 400
    assert "403" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Regression: when Discover returns 404 or empty list, we must STILL
# persist the NINO so the sandbox-setup follow-up endpoint can find it.
# Without this fix the user got "Need a NINO before we can create a test
# business" even though they just typed one.
# ---------------------------------------------------------------------------


def test_404_response_still_persists_the_nino():
    """HMRC's MATCHING_RESOURCE_NOT_FOUND must save the NINO + empty
    business list so /api/hmrc/sandbox/setup-complete can read it."""
    from hmrc.services.client import HmrcApiError
    from hmrc.repositories import tokens as _tokens
    client, csrf, user = _client_with_user()
    _connect_oauth(user["id"])

    # Sanity: nothing stored yet
    assert (_tokens.get_tokens(user["id"]) or {}).get("nino") is None

    with patch(
        "hmrc.services.business_details._client.request",
        side_effect=HmrcApiError(404,
                                 {"code": "MATCHING_RESOURCE_NOT_FOUND",
                                  "message": "no businesses"}),
    ):
        r = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "GT787697B"},
            headers={"X-CSRF-Token": csrf},
        )

    # User-facing 404 with the friendly hint is preserved
    assert r.status_code == 404
    # And the NINO IS persisted now
    info = _tokens.get_tokens(user["id"])
    assert info["nino"] == "GT787697B"
    assert info["businesses"] == []  # empty list, but NINO is saved


def test_empty_businesses_response_still_persists_the_nino():
    """HMRC returning an explicit empty list (rather than 404) — same fix."""
    from hmrc.repositories import tokens as _tokens
    client, csrf, user = _client_with_user()
    _connect_oauth(user["id"])

    mock_resp = MagicMock(
        status_code=200, json={"listOfBusinesses": []},
        headers={}, audit_id="audit-empty-2",
    )
    with patch(
        "hmrc.services.business_details._client.request",
        return_value=mock_resp,
    ):
        r = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "GT787697B"},
            headers={"X-CSRF-Token": csrf},
        )

    assert r.status_code == 404
    info = _tokens.get_tokens(user["id"])
    assert info["nino"] == "GT787697B"


def test_404_persistence_does_not_clobber_existing_businesses():
    """If the user re-runs Discover after partially setting up, the
    existing business list must NOT be wiped out by the 404 branch."""
    from hmrc.services.client import HmrcApiError
    from hmrc.repositories import tokens as _tokens
    client, csrf, user = _client_with_user()
    _connect_oauth(user["id"])

    # Pre-seed an existing business (e.g. they ran setup-complete before)
    _tokens.save_nino_and_businesses(user["id"], "GT787697B", [{
        "business_id": "XAIS999001",
        "type_of_business": "self-employment",
        "label": "Pre-existing",
    }])

    with patch(
        "hmrc.services.business_details._client.request",
        side_effect=HmrcApiError(404, {"code": "MATCHING_RESOURCE_NOT_FOUND"}),
    ):
        r = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "GT787697B"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 404
    # Existing business survives
    info = _tokens.get_tokens(user["id"])
    assert len(info["businesses"]) == 1
    assert info["businesses"][0]["business_id"] == "XAIS999001"


def test_setup_complete_works_after_discover_returned_404():
    """End-to-end: typing a NINO + clicking Discover (gets 404) + then
    clicking 'Set me up with a complete sandbox' must succeed."""
    from hmrc.services.client import HmrcApiError
    from hmrc.repositories import tokens as _tokens
    client, csrf, user = _client_with_user()
    _connect_oauth(user["id"])

    # Step 1: Discover returns 404 (no businesses yet)
    with patch(
        "hmrc.services.business_details._client.request",
        side_effect=HmrcApiError(404, {"code": "MATCHING_RESOURCE_NOT_FOUND"}),
    ):
        r1 = client.post(
            "/api/hmrc/connect-businesses",
            json={"nino": "GT787697B"},
            headers={"X-CSRF-Token": csrf},
        )
    assert r1.status_code == 404

    # Step 2: Setup-complete must NOT bounce on "no NINO" — the previous
    # call persisted it. Mock HMRC's create-business response.
    se_resp = MagicMock(status_code=201,
                        json={"businessId": "XAIS00000999100"},
                        headers={}, audit_id="audit-se")
    prop_resp = MagicMock(status_code=201,
                          json={"businessId": "XPIS00000999200"},
                          headers={}, audit_id="audit-prop")
    with patch(
        "hmrc.services.sandbox._client.request",
        side_effect=[se_resp, prop_resp],
    ):
        r2 = client.post(
            "/api/hmrc/sandbox/setup-complete",
            json={}, headers={"X-CSRF-Token": csrf},
        )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["nino"] == "GT787697B"
    assert len(body["created"]) == 2


def test_persist_nino_only_refuses_to_overwrite_a_live_nino():
    """Regression: a probe call to connect-businesses with the wrong NINO
    must NOT clobber a working setup. Discovered live on bankscanai.com
    during the 2026-05-22 Playwright journey — a stale auto-Discover or
    sloppy debug call could demote a Live HMRC connection to demo mode
    by overwriting the NINO with garbage, then the next Set-me-up tried
    to provision against the wrong NINO and surfaced OAUTH_NINO_MISMATCH.

    Guard: persist_nino_only does nothing when the user has businesses
    already saved against a DIFFERENT NINO.
    """
    from hmrc.services import business_details as _bd
    from hmrc.repositories import tokens as _tokens

    client, csrf, user = _client_with_user(email="nino-overwrite@example.com")
    # Seed a Live state: NINO + businesses already saved.
    _tokens.save_tokens(
        user_id=user["id"], access_token="at",
        refresh_token="rt", expires_in_seconds=14400,
        scope="read:self-assessment write:self-assessment",
    )
    _tokens.save_nino_and_businesses(
        user["id"], "AA111111B",
        [{"business_id": "XAIS001", "type_of_business": "self-employment",
          "label": "Mitoba - sole trader"}],
    )

    # Stale probe with a DIFFERENT NINO must not demote.
    _bd.persist_nino_only(user["id"], "ZZ999999Z")

    info = _tokens.get_tokens(user["id"])
    assert info["nino"] == "AA111111B", (
        f"Expected the original NINO to survive a stale probe, got {info['nino']!r}"
    )
    assert len(info["businesses"]) == 1


def test_persist_nino_only_allows_initial_setup():
    """The guard must NOT block first-time NINO save (no prior NINO + no
    businesses). That's the normal happy-path on first Discover."""
    from hmrc.services import business_details as _bd
    from hmrc.repositories import tokens as _tokens

    client, csrf, user = _client_with_user(email="nino-initial@example.com")
    _tokens.save_tokens(
        user_id=user["id"], access_token="at",
        refresh_token="rt", expires_in_seconds=14400,
        scope="read:self-assessment write:self-assessment",
    )

    _bd.persist_nino_only(user["id"], "AA111111B")

    info = _tokens.get_tokens(user["id"])
    assert info["nino"] == "AA111111B"


def test_persist_nino_only_allows_replacing_a_demo_nino_without_businesses():
    """If the user has a NINO saved but NO businesses (demo state — typed
    NINO but Discover returned 404), they may legitimately want to switch
    to a different NINO. The guard must allow that — only Live state
    (businesses saved) is sticky."""
    from hmrc.services import business_details as _bd
    from hmrc.repositories import tokens as _tokens

    client, csrf, user = _client_with_user(email="nino-switch@example.com")
    _tokens.save_tokens(
        user_id=user["id"], access_token="at",
        refresh_token="rt", expires_in_seconds=14400,
        scope="read:self-assessment write:self-assessment",
    )
    # NINO saved, but NO businesses (demo state)
    _tokens.save_nino_and_businesses(user["id"], "AA111111B", [])

    _bd.persist_nino_only(user["id"], "BB222222C")

    info = _tokens.get_tokens(user["id"])
    assert info["nino"] == "BB222222C", (
        "Should allow NINO switch in demo state — only Live state is sticky."
    )
