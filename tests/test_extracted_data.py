"""
Unit tests for the per-user persisted extraction store (commits 2-3) and
the day-5 trial reminder helpers (commit 4).

Mirrors the test_auth.py harness — isolated SQLite at /tmp/test_bankparse.db,
schema reinitialised before each test.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_extracted.db"


@pytest.fixture(autouse=True)
def clean_db():
    import database

    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)

    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
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
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
        database._sqlite_conn = None
    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)


def _make_user(email: str = "user@example.com") -> int:
    import database
    uid = database.create_user(email, "pwhash")
    database.mark_email_verified(uid)
    return uid


# ---------------------------------------------------------------------------
# save_extracted_data + get_user_extracted_files + flatten
# ---------------------------------------------------------------------------

def test_save_and_retrieve_extracted_rows():
    import database
    uid = _make_user("stmt@example.com")

    database.save_extracted_data(uid, "statement", "natwest.pdf",
                                  [{"date": "2026-03-01", "amount": -45.20},
                                   {"date": "2026-03-03", "amount": -29.99}],
                                  source_size_bytes=12345)

    files = database.get_user_extracted_files(uid, "statement")
    assert len(files) == 1
    assert files[0]["source_filename"] == "natwest.pdf"
    assert files[0]["row_count"] == 2
    assert len(files[0]["rows"]) == 2

    rows = database.get_user_extracted_rows(uid, "statement")
    assert len(rows) == 2


def test_save_extracted_rejects_unknown_mode():
    import database
    uid = _make_user("bad-mode@example.com")
    with pytest.raises(ValueError):
        database.save_extracted_data(uid, "balance-sheet", "x.pdf", [], 0)


def test_extracted_files_are_isolated_by_user():
    import database
    a = _make_user("a@example.com")
    b = _make_user("b@example.com")

    database.save_extracted_data(a, "statement", "a.pdf", [{"x": 1}], 100)
    database.save_extracted_data(b, "statement", "b.pdf", [{"x": 2}], 200)

    assert len(database.get_user_extracted_files(a, "statement")) == 1
    assert len(database.get_user_extracted_files(b, "statement")) == 1
    assert database.get_user_extracted_files(a, "statement")[0]["source_filename"] == "a.pdf"
    assert database.get_user_extracted_files(b, "statement")[0]["source_filename"] == "b.pdf"


def test_summary_counts_files_and_rows():
    import database
    uid = _make_user("summary@example.com")

    database.save_extracted_data(uid, "statement", "s1.pdf", [{"x": 1}, {"x": 2}], 100)
    database.save_extracted_data(uid, "statement", "s2.pdf", [{"x": 3}], 200)
    database.save_extracted_data(uid, "receipt", "r1.jpg", [{"item": "Milk"}], 50)

    summary = database.get_user_extracted_summary(uid)
    assert summary["statement"]["file_count"] == 2
    assert summary["statement"]["row_count"] == 3
    assert summary["receipt"]["file_count"] == 1
    assert summary["receipt"]["row_count"] == 1


def test_clear_user_extracted_wipes_all_modes():
    import database
    uid = _make_user("clear@example.com")

    database.save_extracted_data(uid, "statement", "s.pdf", [{"x": 1}], 100)
    database.save_extracted_data(uid, "receipt", "r.jpg", [{"x": 2}], 50)

    deleted = database.clear_user_extracted_data(uid)
    assert deleted == 2
    assert database.get_user_extracted_files(uid, "statement") == []
    assert database.get_user_extracted_files(uid, "receipt") == []


# ---------------------------------------------------------------------------
# Session-byte cap helpers
# ---------------------------------------------------------------------------

def test_total_bytes_sums_across_modes():
    import database
    uid = _make_user("bytes@example.com")

    database.save_extracted_data(uid, "statement", "s.pdf", [{"x": 1}], 1_500_000)
    database.save_extracted_data(uid, "receipt", "r.jpg", [{"x": 2}], 250_000)

    assert database.get_user_extracted_total_bytes(uid) == 1_750_000


def test_total_bytes_zero_when_empty():
    import database
    uid = _make_user("empty@example.com")
    assert database.get_user_extracted_total_bytes(uid) == 0


def test_total_bytes_resets_after_clear():
    import database
    uid = _make_user("reset@example.com")
    database.save_extracted_data(uid, "statement", "s.pdf", [{"x": 1}], 5_000_000)
    assert database.get_user_extracted_total_bytes(uid) == 5_000_000
    database.clear_user_extracted_data(uid)
    assert database.get_user_extracted_total_bytes(uid) == 0


# ---------------------------------------------------------------------------
# Day-5 trial reminder filter
# ---------------------------------------------------------------------------

def test_reminder_filter_picks_day_5_users_only():
    import database
    now = time.time()

    # Day 1: too early
    fresh = database.create_user("fresh@x.test", "h"); database.mark_email_verified(fresh)
    database._execute("UPDATE users SET created_at = ? WHERE id = ?", (now - 1 * 86400, fresh))

    # Day 5.2: due
    due = database.create_user("due@x.test", "h"); database.mark_email_verified(due)
    database._execute("UPDATE users SET created_at = ? WHERE id = ?", (now - 5.2 * 86400, due))

    # Day 8: too late
    old = database.create_user("old@x.test", "h"); database.mark_email_verified(old)
    database._execute("UPDATE users SET created_at = ? WHERE id = ?", (now - 8 * 86400, old))

    candidates = database.find_users_due_trial_reminder()
    emails = sorted(u["email"] for u in candidates)
    assert emails == ["due@x.test"]


def test_reminder_filter_excludes_paid_subscribers():
    import database
    now = time.time()

    paid = database.create_user("paid@x.test", "h")
    database.mark_email_verified(paid)
    database._execute(
        "UPDATE users SET created_at = ?, subscription_status = 'active' WHERE id = ?",
        (now - 5.2 * 86400, paid),
    )

    assert database.find_users_due_trial_reminder() == []


def test_reminder_filter_excludes_unverified():
    import database
    now = time.time()

    unv = database.create_user("unv@x.test", "h")
    # not verified
    database._execute("UPDATE users SET created_at = ? WHERE id = ?", (now - 5.2 * 86400, unv))

    assert database.find_users_due_trial_reminder() == []


def test_mark_reminder_sent_prevents_re_send():
    import database
    now = time.time()
    uid = database.create_user("once@x.test", "h"); database.mark_email_verified(uid)
    database._execute("UPDATE users SET created_at = ? WHERE id = ?", (now - 5.2 * 86400, uid))

    # First sweep: due
    assert [u["email"] for u in database.find_users_due_trial_reminder()] == ["once@x.test"]
    database.mark_trial_reminder_sent(uid)

    # Second sweep: skipped
    assert database.find_users_due_trial_reminder() == []
