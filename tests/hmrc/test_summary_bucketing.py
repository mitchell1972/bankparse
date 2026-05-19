"""
Regression tests for the summary aggregator's income vs expense bucketing.

Bug observed in production:
    Income · £10,699.61
      Turnover (income)   £10,549.42
      Other expense       £150.00      <-- ?!
      Other income        £0.19
    Expenses · £8,439.72
      ...
      Other expense       £6,889.49

The category "other" is `SE_EXPENSE_OTHER` — an expense category. It
showed up in the Income column because the aggregator was routing rows
by the upstream classifier's `is_income` boolean, and the classifier had
occasionally tagged a credit as `(category=other, is_income=True)`. The
fix routes by canonical category membership instead: `other` is an
expense category by definition, regardless of the flag or the sign.

These tests pin the new behaviour.
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

TEST_DB_PATH = "/tmp/test_bankparse_hmrc_summary_bucketing.db"


# ---------------------------------------------------------------------------
# Unit tests for the new schema helper
# ---------------------------------------------------------------------------

class TestIsIncomeCategoryHelper:
    def test_se_turnover_is_income(self):
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("turnover", "se") is True

    def test_se_other_income_is_income(self):
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("otherIncome", "se") is True

    def test_se_other_is_expense_not_income(self):
        """The exact regression — `other` is an expense category for SE."""
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("other", "se") is False

    def test_se_admin_costs_is_expense(self):
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("adminCosts", "se") is False

    def test_property_rent_income_is_income(self):
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("rentIncome", "property") is True

    def test_property_premiums_is_income(self):
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("premiumsOfLeaseGrant", "property") is True

    def test_property_other_income_is_income(self):
        """Property has its own otherIncome — same code as SE but property bucket."""
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("otherIncome", "property") is True

    def test_property_other_is_expense(self):
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("other", "property") is False

    def test_property_rent_income_not_treated_as_income_for_se_business(self):
        """A property income category in a self-employment business shouldn't
        be flagged as income — the business types have different lists."""
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("rentIncome", "se") is False

    def test_unknown_category_defaults_to_expense(self):
        """If we receive a category we don't know, treat it as an expense
        rather than guessing income — safer for HMRC totals."""
        from hmrc.schemas.categories import is_income_category
        assert is_income_category("madeUpCategory", "se") is False
        assert is_income_category("", "se") is False


# ---------------------------------------------------------------------------
# End-to-end regression test via the /summary endpoint
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv(
        "HMRC_TOKEN_ENCRYPTION_KEY",
        base64.b64encode(secrets.token_bytes(32)).decode(),
    )
    monkeypatch.setenv("HMRC_AI_CATEGORISE", "1")        # force AI path
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setenv("ENVIRONMENT", "development")

    # Disable slowapi's 10/min /api/register cap so this file's user
    # registrations don't trip 429 when run after other HMRC test files
    # in the full suite. Production behaviour is unaffected — only this
    # test session.
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
    return client.get("/login").cookies.get("bp_csrf", "")


def _client_with_user(email="bucket-test@example.com"):
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


def _mock_anthropic_with_classifications(classifications):
    """Return a context manager that makes classify_batch return EXACTLY
    these classifications — one per row, in order. Anthropic SDK is mocked
    so we don't burn API credits."""
    def _fake_create(*, model, max_tokens, messages, **_):
        return MagicMock(content=[
            MagicMock(text=str(classifications).replace("'", '"')
                       .replace("True", "true").replace("False", "false"))
        ])
    mock_anthropic = MagicMock()
    mock_anthropic.Anthropic.return_value.messages.create.side_effect = _fake_create
    return patch.dict("sys.modules", {"anthropic": mock_anthropic})


def test_other_with_is_income_true_lands_in_expenses_not_income():
    """The exact reported bug:
       row tagged (category='other', is_income=True) was landing in Income
       as "Other expense" alongside the real Income lines."""
    client, csrf = _client_with_user()

    # Three rows. The classifier WRONGLY tags the credit (£150 in) as
    # `other` (an expense category) with `is_income=True`. The aggregator
    # must ignore the flag and route by the category.
    classifications = [
        # Row 1: credit, classifier mislabels as 'other' (the bug case)
        {"category": "other", "confidence": 0.6,
         "is_income": True, "reasoning": "ambiguous credit"},
        # Row 2: clear turnover credit
        {"category": "turnover", "confidence": 0.9,
         "is_income": True, "reasoning": "Stripe payout"},
        # Row 3: clear admin debit
        {"category": "adminCosts", "confidence": 0.85,
         "is_income": False, "reasoning": "AWS subscription"},
    ]
    with _mock_anthropic_with_classifications(classifications):
        r = client.post(
            "/api/hmrc/categorise/summary",
            json={"business_type": "se", "rows": [
                {"description": "Ambiguous credit", "amount": 150.00},
                {"description": "STRIPE PAYOUT", "amount": 1000.00},
                {"description": "AWS", "amount": -42.00},
            ]},
            headers={"X-CSRF-Token": csrf},
        )

    assert r.status_code == 200, r.text
    summary = r.json()["summary"]

    # THE FIX: `other` MUST be in expenses, never in income.
    assert "other" not in summary["income"], (
        f"Bug regression: 'other' should never appear in income. Income: {summary['income']}"
    )
    assert "other" in summary["expenses"]
    assert summary["expenses"]["other"] == 150.00

    # And the legitimate rows still bucket correctly.
    assert summary["income"]["turnover"] == 1000.00
    assert summary["expenses"]["adminCosts"] == 42.00


def test_turnover_with_is_income_false_still_lands_in_income():
    """Symmetry check: if the classifier mis-flags turnover as is_income=False
    (e.g. a refund of a previous sale that the classifier sees as outflow),
    we still bucket it as income because `turnover` IS an income category.
    The negative amount on the row itself takes care of any netting."""
    client, csrf = _client_with_user()
    with _mock_anthropic_with_classifications([
        {"category": "turnover", "confidence": 0.7,
         "is_income": False, "reasoning": "looks like refund"},
    ]):
        r = client.post(
            "/api/hmrc/categorise/summary",
            json={"business_type": "se", "rows": [
                {"description": "Refund", "amount": -50.00},
            ]},
            headers={"X-CSRF-Token": csrf},
        )
    summary = r.json()["summary"]
    assert "turnover" in summary["income"]
    assert "turnover" not in summary["expenses"]


def test_property_other_routes_to_expenses_not_income():
    """Same regression but on the Property income flow — `other` is the
    property expense bucket, not income."""
    client, csrf = _client_with_user()
    with _mock_anthropic_with_classifications([
        {"category": "other", "confidence": 0.5,
         "is_income": True, "reasoning": "ambiguous credit"},
        {"category": "rentIncome", "confidence": 0.95,
         "is_income": True, "reasoning": "Monthly rent"},
    ]):
        r = client.post(
            "/api/hmrc/categorise/summary",
            json={"business_type": "property", "rows": [
                {"description": "Mystery credit", "amount": 25.00},
                {"description": "Rent from tenant", "amount": 800.00},
            ]},
            headers={"X-CSRF-Token": csrf},
        )
    summary = r.json()["summary"]
    assert "other" not in summary["income"]
    assert summary["expenses"]["other"] == 25.00
    assert summary["income"]["rentIncome"] == 800.00


def test_owner_transfer_carve_out_preempts_bucket_routing():
    """The owner-transfer sentinel must be respected by the aggregator —
    a row with `category == _owner_transfer` goes straight to `excluded`,
    never to income or expenses.

    We bypass the AI path here (the canonical-category filter would strip
    the sentinel) and call the service directly with a pre-built response.
    """
    import asyncio
    from hmrc.services import categorisation as _svc
    from hmrc.schemas.categorise import (
        CategoriseResponse, TransactionOut, HmrcClassification, CategoriseRequest,
    )

    cat_resp = CategoriseResponse(
        business_type="se",
        rows=[
            TransactionOut(
                description="Transfer to savings", amount=100.0,
                hmrc=HmrcClassification(
                    category="_owner_transfer", confidence=0.95,
                    is_income=True,
                    reasoning="Self-transfer",
                    source="rule",
                ),
            ),
        ],
    )
    metrics = _svc.CategorisationMetrics(1, 0, 0, 0, 1, 0)

    async def _fake_resolve(req, *, user_id):
        return cat_resp, metrics

    req = CategoriseRequest(business_type="se", rows=[])
    with patch.object(_svc, "resolve", side_effect=_fake_resolve):
        summary_resp, _ = asyncio.run(_svc.summarise(req, user_id=1))

    summary = summary_resp.summary.model_dump()
    assert summary["income"] == {}
    assert summary["expenses"] == {}
    assert len(summary["excluded"]) == 1
