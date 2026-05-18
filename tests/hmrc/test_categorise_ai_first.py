"""
Tests for AI-first categorisation: cache hits, parallel batches, AI fallback.

Anthropic SDK is mocked so tests don't burn real API credits or require
network; the test asserts the orchestration (cache lookups, batching,
storage of fresh AI results) without depending on Claude's real responses.
"""

import base64
import os
import secrets
import sys

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_ai_first.db"


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("HMRC_TOKEN_ENCRYPTION_KEY",
                      base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("HMRC_AI_CATEGORISE", "1")        # AI ON
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setenv("ENVIRONMENT", "development")
    yield


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


def _seed_csrf(client: TestClient) -> str:
    resp = client.get("/login")
    return resp.cookies.get("bp_csrf", "") or client.cookies.get("bp_csrf", "")


def _client_with_user(email="ai-first@example.com"):
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
    return client, csrf


def _mock_anthropic_returning(category="adminCosts", confidence=0.85, reasoning="Mocked"):
    """Patch the Anthropic SDK so classify_batch returns one entry per row."""
    def _fake_create(*, model, max_tokens, messages, **_):
        prompt = messages[0]["content"]
        # Count how many rows the prompt asked about
        n = max(1, prompt.count("amount="))
        payload = [{
            "category": category,
            "confidence": confidence,
            "is_income": False,
            "reasoning": reasoning,
        } for _ in range(n)]
        return MagicMock(content=[MagicMock(text=str(payload).replace("'", '"').replace("False", "false"))])

    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value.messages.create.side_effect = _fake_create
    return patch.dict("sys.modules", {"anthropic": mock_anthropic})


def test_ai_first_classifies_via_claude_and_caches_result():
    """When AI is on, unknown merchants go to Claude and the result is
    written to the global merchant cache."""
    from hmrc.repositories import classifier_cache as _cache
    assert _cache.size() == 0

    client, csrf = _client_with_user()
    with _mock_anthropic_returning(category="travelCosts", confidence=0.9, reasoning="Parking app"):
        r = client.post(
            "/api/hmrc/categorise",
            json={"business_type": "se", "rows": [
                {"description": "OBSCURE_PARKING_APP_XYZ", "amount": -3.20},
            ]},
            headers={"X-CSRF-Token": csrf},
        )
    assert r.status_code == 200, r.text
    row = r.json()["rows"][0]
    assert row["hmrc"]["category"] == "travelCosts"
    assert row["hmrc"]["source"] == "ai"
    # Cache populated for next user
    assert _cache.size() == 1


def test_cache_hit_skips_claude_entirely():
    """Second call for the same merchant returns from cache without invoking AI."""
    from hmrc.repositories import classifier_cache as _cache

    client, csrf = _client_with_user()
    desc = "MYSTERY_VENDOR_42"

    # First call: AI runs, caches.
    with _mock_anthropic_returning(category="adminCosts", confidence=0.85) as p:
        r1 = client.post(
            "/api/hmrc/categorise",
            json={"business_type": "se", "rows": [{"description": desc, "amount": -10.0}]},
            headers={"X-CSRF-Token": csrf},
        )
    assert r1.json()["rows"][0]["hmrc"]["source"] == "ai"
    assert _cache.size() == 1

    # Second call: must NOT invoke Anthropic again.
    import sys
    fake_mod = MagicMock()
    fake_mod.Anthropic.return_value.messages.create.side_effect = AssertionError(
        "AI must NOT be called on a cache hit"
    )
    with patch.dict(sys.modules, {"anthropic": fake_mod}):
        r2 = client.post(
            "/api/hmrc/categorise",
            json={"business_type": "se", "rows": [{"description": desc, "amount": -10.0}]},
            headers={"X-CSRF-Token": csrf},
        )
    body = r2.json()
    assert body["rows"][0]["hmrc"]["category"] == "adminCosts"
    assert body["rows"][0]["hmrc"]["source"] == "ai_cached"
    assert body["rows"][0]["hmrc"]["confidence"] == 0.85


def test_user_override_beats_cache_and_ai():
    """If the user has a saved override for a merchant, AI is never called
    for that merchant — even if the cache also has an entry."""
    client, csrf = _client_with_user()

    # Save the user's override.
    r = client.post(
        "/api/hmrc/categorise/override",
        json={"description": "WEIRD_MERCHANT", "business_type": "se",
              "category": "professionalFees"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200

    # AI must not run.
    import sys
    fake_mod = MagicMock()
    fake_mod.Anthropic.return_value.messages.create.side_effect = AssertionError(
        "AI must NOT be called when an override exists"
    )
    with patch.dict(sys.modules, {"anthropic": fake_mod}):
        r = client.post(
            "/api/hmrc/categorise",
            json={"business_type": "se", "rows": [
                {"description": "WEIRD_MERCHANT", "amount": -50.0},
            ]},
            headers={"X-CSRF-Token": csrf},
        )
    row = r.json()["rows"][0]
    assert row["hmrc"]["category"] == "professionalFees"
    assert row["hmrc"]["source"] == "override"


def test_low_confidence_ai_result_is_not_cached():
    """We only persist high-confidence (>=0.7) AI results to the global cache."""
    from hmrc.repositories import classifier_cache as _cache
    client, csrf = _client_with_user()
    with _mock_anthropic_returning(category="other", confidence=0.40):
        client.post(
            "/api/hmrc/categorise",
            json={"business_type": "se", "rows": [
                {"description": "RANDOM_UNCLEAR_THING", "amount": -5.0},
            ]},
            headers={"X-CSRF-Token": csrf},
        )
    assert _cache.size() == 0, "low-confidence AI results must NOT pollute the cache"


def test_ai_disabled_falls_back_to_rules(monkeypatch):
    """With the feature flag off, we use the regex rules path."""
    monkeypatch.setenv("HMRC_AI_CATEGORISE", "0")
    client, csrf = _client_with_user()
    r = client.post(
        "/api/hmrc/categorise",
        json={"business_type": "se", "rows": [
            {"description": "MIPERMIT LTD CHIPPENHAM", "amount": -2.60},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    row = r.json()["rows"][0]
    assert row["hmrc"]["category"] == "travelCosts"
    assert row["hmrc"]["source"] == "rule"
