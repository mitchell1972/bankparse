"""
Tests for the first-party blog read counter.

Covers the database layer (increment + ordered read-back), the bot filter
(so Googlebot et al. don't inflate the "readers" number), and the route
wiring (a human load counts, a crawler load does not).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEST_DB_PATH = "/tmp/test_bankparse_blog_views.db"


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    import database
    import sqlite3

    if os.path.exists(TEST_DB_PATH):
        os.unlink(TEST_DB_PATH)
    if database._sqlite_conn is not None:
        try:
            database._sqlite_conn.close()
        except Exception:
            pass
    database._sqlite_conn = None

    def _get_sqlite_test():
        if database._sqlite_conn is None:
            conn = sqlite3.connect(TEST_DB_PATH, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            database._sqlite_conn = conn
        return database._sqlite_conn

    monkeypatch.setattr(database, "_get_sqlite", _get_sqlite_test)
    # USE_TURSO is False in tests, so _execute hits the sqlite path above.
    monkeypatch.setattr(database, "USE_TURSO", False, raising=False)
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


# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------

def test_increment_creates_then_bumps_count():
    import database
    database.increment_blog_view("convert-bank-statement-pdf-to-excel")
    database.increment_blog_view("convert-bank-statement-pdf-to-excel")
    database.increment_blog_view("convert-bank-statement-pdf-to-excel")
    rows = {r["slug"]: r for r in database.get_blog_views()}
    assert rows["convert-bank-statement-pdf-to-excel"]["views"] == 3


def test_get_blog_views_ordered_by_views_desc():
    import database
    for _ in range(5):
        database.increment_blog_view("popular")
    database.increment_blog_view("quiet")
    for _ in range(3):
        database.increment_blog_view("middle")
    slugs = [r["slug"] for r in database.get_blog_views()]
    assert slugs == ["popular", "middle", "quiet"]


def test_unread_slug_has_no_row():
    import database
    database.increment_blog_view("read-me")
    slugs = [r["slug"] for r in database.get_blog_views()]
    assert "never-read" not in slugs


# ---------------------------------------------------------------------------
# Bot filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ua", [
    "",  # empty UA — treat as bot
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "facebookexternalhit/1.1",
    "python-requests/2.31.0",
    "curl/8.1.2",
    "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/120.0",
])
def test_looks_like_bot_flags_crawlers(ua):
    from app import _looks_like_bot
    assert _looks_like_bot(ua) is True


@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
])
def test_looks_like_bot_passes_real_browsers(ua):
    from app import _looks_like_bot
    assert _looks_like_bot(ua) is False


# ---------------------------------------------------------------------------
# Route wiring
# ---------------------------------------------------------------------------

def _first_blog_slug():
    from app import BLOG_POSTS
    return next(iter(BLOG_POSTS))


def test_blog_post_route_counts_a_human_read():
    import database
    from fastapi.testclient import TestClient
    from app import app

    slug = _first_blog_slug()
    client = TestClient(app, raise_server_exceptions=False)
    # Default TestClient UA ("testclient") is not a bot marker → counts.
    client.get(f"/blog/{slug}")
    rows = {r["slug"]: r["views"] for r in database.get_blog_views()}
    assert rows.get(slug, 0) == 1


def test_blog_post_route_skips_a_crawler():
    import database
    from fastapi.testclient import TestClient
    from app import app

    slug = _first_blog_slug()
    client = TestClient(app, raise_server_exceptions=False)
    client.get(f"/blog/{slug}", headers={"User-Agent": "Googlebot/2.1"})
    rows = {r["slug"]: r["views"] for r in database.get_blog_views()}
    assert rows.get(slug, 0) == 0


def test_unknown_slug_does_not_create_a_row():
    import database
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/blog/this-slug-does-not-exist")
    assert r.status_code == 404
    assert database.get_blog_views() == []
