"""
Tests for the live tax-due forecast (services/tax_forecast.py) and
the accountant export ZIP (services/accountant_export.py).
"""
from __future__ import annotations

import base64
import datetime as _dt
import io
import json
import os
import secrets
import sys
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_tax_forecast_export.db"


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


def _seed_user(email: str = "u@example.com") -> int:
    import database as _db
    _db.create_user(email, "pwhash")
    return _db.get_user_by_email(email)["id"]


def _add_tx(user_id: int, **kwargs) -> int:
    import database as _db
    base = {"extracted_data_id": None,
            "date_iso": _dt.date.today().isoformat(),
            "description": "X", "amount": -10.0}
    base.update(kwargs)
    return _db.insert_ledger_transaction(user_id, **base)


# ---------------------------------------------------------------------------
# Tax forecast
# ---------------------------------------------------------------------------


def test_forecast_returns_zeros_for_empty_user():
    from services.tax_forecast import forecast_tax_due
    uid = _seed_user()
    f = forecast_tax_due(uid)
    assert f["combined"]["total_due"] == 0
    assert f["self_employment"]["profit"] == 0
    assert f["property"]["profit"] == 0


def test_forecast_below_personal_allowance_is_zero_tax():
    """Profit under £12,570 — no income tax due."""
    from services.tax_forecast import forecast_tax_due
    uid = _seed_user()
    _add_tx(uid, hmrc_category="se_turnover", amount=10000.0)
    _add_tx(uid, hmrc_category="se_office_expenses", amount=-1000.0)
    f = forecast_tax_due(uid)
    # Profit £9,000 < £12,570 PA → £0 tax
    assert f["combined"]["income_tax_due"] == 0
    assert f["combined"]["class_4_ni_due"] == 0
    assert any("personal allowance" in n.lower() for n in f["notes"])


def test_forecast_basic_rate_band_se():
    """Profit £30,000 - £12,570 PA = £17,430 in basic rate band = 20% = £3,486 tax.
    Plus Class 4 NI: (30000 - 12570) * 0.06 = £1,045.80."""
    from services.tax_forecast import forecast_tax_due
    uid = _seed_user()
    _add_tx(uid, hmrc_category="se_turnover", amount=30000.0)
    f = forecast_tax_due(uid)
    assert abs(f["combined"]["income_tax_due"] - 3486.0) < 1.0
    assert abs(f["combined"]["class_4_ni_due"] - 1045.80) < 1.0


def test_forecast_combined_se_plus_property():
    """SE profit £10k + property profit £20k = £30k total profit.
    Property profit doesn't attract Class 4 NI.
    Income tax = (30k - 12570) * 0.20 = £3,486.
    Class 4 NI = only on SE profit (£10k - £12,570 floor → £0, but
    NI is calculated on SE income alone, not combined). The forecast
    treats SE alone for NI."""
    from services.tax_forecast import forecast_tax_due
    uid = _seed_user()
    _add_tx(uid, hmrc_category="se_turnover", amount=10000.0)
    _add_tx(uid, hmrc_category="property_rent_income", amount=20000.0)
    f = forecast_tax_due(uid)
    # Combined profit £30k → £3,486 income tax across both streams
    assert abs(f["combined"]["income_tax_due"] - 3486.0) < 1.0
    # Class 4 NI = SE only. SE profit £10k < £12,570 floor → £0
    assert f["combined"]["class_4_ni_due"] == 0
    # Apportioned: SE share = 10k / 30k ≈ 33%, property = 67%
    assert f["self_employment"]["income_tax_due"] > 0
    assert f["property"]["income_tax_due"] > 0


def test_forecast_excludes_personal_transactions():
    from services.tax_forecast import forecast_tax_due
    import database as _db
    uid = _seed_user()
    _add_tx(uid, hmrc_category="se_turnover", amount=20000.0)
    personal = _add_tx(uid, hmrc_category="se_general_admin_costs", amount=-5000.0)
    _db.update_transaction_status(personal, receipt_status="excluded",
                                   exclusion_reason="personal")
    f = forecast_tax_due(uid)
    # Personal excluded → SE expenses = 0 → profit = 20k
    assert f["self_employment"]["expenses"] == 0
    assert f["self_employment"]["profit"] == 20000


def test_forecast_excludes_capital_items_from_revenue_expenses():
    """A £350 laptop marked is_capital=1 doesn't reduce SE profit (it's
    a capital allowance, not a revenue expense)."""
    from services.tax_forecast import forecast_tax_due
    import database as _db
    uid = _seed_user()
    _add_tx(uid, hmrc_category="se_turnover", amount=20000.0)
    cap = _add_tx(uid, hmrc_category="se_office_expenses", amount=-350.0)
    _db.update_transaction_status(cap, is_capital=1)
    f = forecast_tax_due(uid)
    assert f["self_employment"]["expenses"] == 0
    assert f["self_employment"]["profit"] == 20000


def test_forecast_counts_uncategorised_credits_as_provisional_income():
    """An uploaded statement whose transactions haven't been categorised
    yet should still produce a meaningful forecast — credits go into SE
    income provisionally, debits into SE expenses, with a caveat note.
    Pinned after Mitchell's 2026-05-20 audit."""
    from services.tax_forecast import forecast_tax_due
    uid = _seed_user()
    _add_tx(uid, hmrc_category=None, amount=20_000.0)
    _add_tx(uid, hmrc_category=None, amount=-3_000.0)
    f = forecast_tax_due(uid)
    assert f["provisional"]["uncategorised_income"] == 20_000.0
    assert f["provisional"]["uncategorised_expenses"] == 3_000.0
    # Flowed into SE buckets
    assert f["self_employment"]["income"] == 20_000.0
    assert f["self_employment"]["expenses"] == 3_000.0
    # Profit £17k > £12,570 → some income tax
    assert f["combined"]["income_tax_due"] > 0
    assert any("uncategorised" in n.lower() for n in f["notes"])


def test_forecast_uncategorised_handles_mitchell_real_data():
    """Real numbers from Mitchell's bank statement: credits £10,699.61,
    debits £8,439.72 → net £2,259.89, no tax due (under PA)."""
    from services.tax_forecast import forecast_tax_due
    uid = _seed_user()
    _add_tx(uid, hmrc_category=None, amount=10_699.61)
    _add_tx(uid, hmrc_category=None, amount=-8_439.72)
    f = forecast_tax_due(uid)
    assert abs(f["combined"]["profit"] - 2_259.89) < 0.01
    assert f["combined"]["income_tax_due"] == 0  # under PA
    assert any("uncategorised" in n.lower() for n in f["notes"])


def test_forecast_business_pct_proportional():
    """A 60% business expense reduces profit by 60% of the amount."""
    from services.tax_forecast import forecast_tax_due
    import database as _db
    uid = _seed_user()
    _add_tx(uid, hmrc_category="se_turnover", amount=20000.0)
    tx = _add_tx(uid, hmrc_category="se_motor_expenses", amount=-1000.0)
    _db.update_transaction_status(tx, business_pct=60)
    f = forecast_tax_due(uid)
    assert abs(f["self_employment"]["expenses"] - 600.0) < 0.01


# ---------------------------------------------------------------------------
# Capital prompt
# ---------------------------------------------------------------------------


def test_capital_prompt_fires_above_200_for_se_expense():
    from services.tax_forecast import should_prompt_capital
    assert should_prompt_capital({
        "amount": -350.0,
        "hmrc_category": "se_office_expenses",
    }) is True


def test_capital_prompt_silent_under_threshold():
    from services.tax_forecast import should_prompt_capital
    assert should_prompt_capital({
        "amount": -50.0,
        "hmrc_category": "se_office_expenses",
    }) is False


def test_capital_prompt_silent_for_income():
    from services.tax_forecast import should_prompt_capital
    assert should_prompt_capital({
        "amount": 500.0,  # positive = inflow
        "hmrc_category": "se_turnover",
    }) is False


def test_capital_prompt_silent_for_already_marked():
    from services.tax_forecast import should_prompt_capital
    assert should_prompt_capital({
        "amount": -350.0,
        "hmrc_category": "se_office_expenses",
        "is_capital": 1,
    }) is False


# ---------------------------------------------------------------------------
# Accountant export ZIP
# ---------------------------------------------------------------------------


def test_export_zip_contains_required_files():
    from services.accountant_export import build_export_zip
    uid = _seed_user("export@example.com")
    _add_tx(uid, hmrc_category="se_office_expenses",
            description="AMAZON", amount=-42.99,
            date_iso="2026-08-04")
    zip_bytes = build_export_zip(uid, "export@example.com", period_label="Q2-2026")
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = set(zf.namelist())
    assert "summary.html" in names
    assert "transactions.csv" in names
    assert "mismatch_report.csv" in names
    assert "reasoning_log.csv" in names
    assert "manifest.json" in names


def test_export_zip_transactions_csv_includes_required_columns():
    from services.accountant_export import build_export_zip
    uid = _seed_user("cols@example.com")
    _add_tx(uid, hmrc_category="se_office_expenses",
            description="AMAZON", amount=-42.99,
            date_iso="2026-08-04")
    zip_bytes = build_export_zip(uid, "cols@example.com")
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    with zf.open("transactions.csv") as fh:
        text = fh.read().decode()
    header = text.splitlines()[0]
    for col in ("hmrc_category", "vat_amount", "content_hash",
                "business_pct", "is_capital", "linked_receipt_ids"):
        assert col in header
    # The row data appears
    assert "AMAZON" in text


def test_export_zip_manifest_lists_all_file_hashes():
    from services.accountant_export import build_export_zip
    uid = _seed_user("manifest@example.com")
    _add_tx(uid, hmrc_category="se_office_expenses", amount=-50.0)
    zip_bytes = build_export_zip(uid, "manifest@example.com")
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    manifest = json.loads(zf.read("manifest.json"))
    file_hashes = manifest["file_hashes"]
    # summary, transactions, mismatch, reasoning all have hashes
    for f in ("summary.html", "transactions.csv",
              "mismatch_report.csv", "reasoning_log.csv"):
        assert f in file_hashes
        assert len(file_hashes[f]) == 64  # SHA-256


def test_export_zip_mismatch_report_only_includes_unmatched():
    """A transaction with a linked receipt should NOT appear in the
    mismatch report."""
    from services.accountant_export import build_export_zip
    import database as _db
    uid = _seed_user("mm@example.com")
    tx_matched = _add_tx(uid, hmrc_category="se_office_expenses",
                         description="MATCHED", amount=-42.99)
    tx_missing = _add_tx(uid, hmrc_category="se_motor_expenses",
                         description="MISSING", amount=-25.00)
    rc = _db.insert_ledger_receipt(
        uid, extracted_data_id=None, file_path=None, source_filename="r.pdf",
        store_name="A", date_iso=_dt.date.today().isoformat(),
        total_amount=42.99, tax_amount=7.16,
    )
    _db.insert_ledger_link(transaction_id=tx_matched, receipt_id=rc,
                           match_strategy="exact", confidence=100)

    zip_bytes = build_export_zip(uid, "mm@example.com")
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    mismatch = zf.read("mismatch_report.csv").decode()
    assert "MISSING" in mismatch
    assert "MATCHED" not in mismatch


def test_export_zip_endpoint_returns_application_zip():
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    client.post("/api/register",
                json={"email": "zipuser@example.com", "password": "password12345"},
                headers={"X-CSRF-Token": csrf})
    user = _db.get_user_by_email("zipuser@example.com")
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], subscription_status="trialing",
                    stripe_subscription_id="sub_zip",
                    trial_end_at=time.time() + 7*86400)

    r = client.get("/api/accountant-export?period=Q2-2026")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "Q2-2026" in r.headers.get("content-disposition", "")


def test_export_endpoint_requires_auth():
    from app import app
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    r = client.get("/api/accountant-export")
    assert r.status_code == 401


def test_tax_forecast_endpoint_returns_shape():
    from app import app
    import database as _db
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    client.get("/login")
    csrf = client.cookies.get("bp_csrf", "")
    client.post("/api/register",
                json={"email": "forecast@example.com", "password": "password12345"},
                headers={"X-CSRF-Token": csrf})
    user = _db.get_user_by_email("forecast@example.com")
    _db.mark_email_verified(user["id"])
    _db.update_user(user["id"], subscription_status="trialing",
                    stripe_subscription_id="sub_fc",
                    trial_end_at=time.time() + 7*86400)

    r = client.get("/api/tax-forecast")
    assert r.status_code == 200
    body = r.json()
    assert "self_employment" in body
    assert "property" in body
    assert "combined" in body
    assert "tax_year" in body
