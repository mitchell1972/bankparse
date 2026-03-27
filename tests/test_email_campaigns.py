"""Tests for the email campaign module."""

import os
import sys
import pytest

# Ensure campaign admin key is set for tests
os.environ.setdefault("CAMPAIGN_ADMIN_KEY", "test-campaign-key-123")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from email_campaigns import (
    init_campaign_tables, verify_admin_key, create_campaign,
    get_campaign, list_campaigns, get_recipients, send_campaign,
    get_campaign_stats, CAMPAIGN_TEMPLATES,
    unsubscribe_email, is_unsubscribed, resubscribe_email,
    get_unsubscribe_url, verify_unsubscribe_token,
    _make_unsubscribe_token, _build_message, _html_to_plain,
)


@pytest.fixture(autouse=True)
def setup_db():
    database.init_db()
    init_campaign_tables()
    # Clean up test data
    database._execute("DELETE FROM campaign_sends")
    database._execute("DELETE FROM email_campaigns")
    database._execute("DELETE FROM email_unsubscribes")
    database._execute("DELETE FROM users")
    yield
    database._execute("DELETE FROM campaign_sends")
    database._execute("DELETE FROM email_campaigns")
    database._execute("DELETE FROM email_unsubscribes")
    database._execute("DELETE FROM users")


def _create_test_user(email, subscription_status=None, statements=0, receipts=0):
    from core import hash_password
    user_id = database.create_user(email, hash_password("testpass123"))
    if subscription_status:
        database.update_user(user_id, subscription_status=subscription_status)
    if statements > 0:
        for _ in range(statements):
            database.increment_user_usage(user_id, "statement")
    if receipts > 0:
        for _ in range(receipts):
            database.increment_user_usage(user_id, "receipt")
    return user_id


def test_verify_admin_key():
    assert verify_admin_key("test-campaign-key-123") is True
    assert verify_admin_key("wrong-key") is False
    assert verify_admin_key("") is False


def test_create_and_get_campaign():
    cid = create_campaign(
        name="Test Campaign",
        subject="Hello!",
        body_html="<p>Hello world</p>",
        body_text="Hello world",
        segment="all",
    )
    assert cid > 0

    campaign = get_campaign(cid)
    assert campaign is not None
    assert campaign["name"] == "Test Campaign"
    assert campaign["subject"] == "Hello!"
    assert campaign["status"] == "draft"
    assert campaign["segment"] == "all"


def test_list_campaigns():
    create_campaign("C1", "Subject 1", "<p>Body 1</p>")
    create_campaign("C2", "Subject 2", "<p>Body 2</p>")
    campaigns = list_campaigns()
    assert len(campaigns) >= 2


def test_get_recipients_all():
    _create_test_user("user1@test.com")
    _create_test_user("user2@test.com")
    recipients = get_recipients("all")
    assert len(recipients) == 2


def test_get_recipients_free():
    _create_test_user("free@test.com")
    _create_test_user("paid@test.com", subscription_status="active")
    free = get_recipients("free")
    assert len(free) == 1
    assert free[0]["email"] == "free@test.com"


def test_get_recipients_paid():
    _create_test_user("free@test.com")
    _create_test_user("paid@test.com", subscription_status="active")
    paid = get_recipients("paid")
    assert len(paid) == 1
    assert paid[0]["email"] == "paid@test.com"


def test_get_recipients_inactive():
    _create_test_user("inactive@test.com")
    _create_test_user("active@test.com", statements=3)
    inactive = get_recipients("inactive")
    assert len(inactive) == 1
    assert inactive[0]["email"] == "inactive@test.com"


def test_get_recipients_active():
    _create_test_user("inactive@test.com")
    _create_test_user("active@test.com", statements=3)
    active = get_recipients("active")
    assert len(active) == 1
    assert active[0]["email"] == "active@test.com"


def test_send_campaign_no_smtp():
    """Campaign sends succeed when SMTP is not configured (dry run)."""
    _create_test_user("user@test.com")
    cid = create_campaign("Test", "Subject", "<p>Body</p>", segment="all")
    result = send_campaign(cid)
    assert result["sent"] == 1
    assert result["failed"] == 0


def test_send_campaign_already_sent():
    _create_test_user("user@test.com")
    cid = create_campaign("Test", "Subject", "<p>Body</p>", segment="all")
    send_campaign(cid)
    result = send_campaign(cid)
    assert result.get("error") == "Campaign already sent"


def test_campaign_stats():
    _create_test_user("user1@test.com")
    _create_test_user("user2@test.com")
    cid = create_campaign("Stats Test", "Subject", "<p>Body</p>", segment="all")
    send_campaign(cid)
    stats = get_campaign_stats(cid)
    assert stats["sent"] == 2
    assert stats["failed"] == 0


def test_campaign_templates_exist():
    assert "welcome" in CAMPAIGN_TEMPLATES
    assert "upgrade" in CAMPAIGN_TEMPLATES
    assert "feature_update" in CAMPAIGN_TEMPLATES
    assert "re_engage" in CAMPAIGN_TEMPLATES
    for key, tmpl in CAMPAIGN_TEMPLATES.items():
        assert "name" in tmpl
        assert "subject" in tmpl
        assert "body_html" in tmpl


def test_campaign_not_found():
    result = get_campaign(99999)
    assert result is None


def test_campaign_stats_not_found():
    result = get_campaign_stats(99999)
    assert result.get("error") == "Campaign not found"


# --- Unsubscribe tests ---

def test_unsubscribe_and_resubscribe():
    assert is_unsubscribed("unsub@test.com") is False
    unsubscribe_email("unsub@test.com")
    assert is_unsubscribed("unsub@test.com") is True
    resubscribe_email("unsub@test.com")
    assert is_unsubscribed("unsub@test.com") is False


def test_unsubscribe_token_verification():
    email = "verify@test.com"
    token = _make_unsubscribe_token(email)
    assert verify_unsubscribe_token(email, token) is True
    assert verify_unsubscribe_token(email, "bad-token") is False
    assert verify_unsubscribe_token("other@test.com", token) is False


def test_unsubscribe_url_contains_token():
    url = get_unsubscribe_url("user@test.com")
    assert "user@test.com" in url
    assert "token=" in url


def test_unsubscribed_users_excluded_from_recipients():
    _create_test_user("keep@test.com")
    _create_test_user("remove@test.com")
    unsubscribe_email("remove@test.com")
    recipients = get_recipients("all")
    emails = [r["email"] for r in recipients]
    assert "keep@test.com" in emails
    assert "remove@test.com" not in emails


def test_unsubscribed_users_skipped_during_send():
    _create_test_user("active@test.com")
    _create_test_user("unsub@test.com")
    unsubscribe_email("unsub@test.com")
    cid = create_campaign("Test", "Subject", "<p>Body</p>", segment="all")
    result = send_campaign(cid)
    assert result["sent"] == 1
    assert result["total"] == 1


def test_double_unsubscribe_no_error():
    unsubscribe_email("user@test.com")
    unsubscribe_email("user@test.com")  # Should not raise
    assert is_unsubscribed("user@test.com") is True


# --- Anti-spam header tests ---

def test_build_message_has_required_headers():
    msg = _build_message("to@test.com", "Test Subject", "<p>Hello</p>", "Hello")
    assert msg["Message-ID"] is not None
    assert msg["Date"] is not None
    assert msg["List-Unsubscribe"] is not None
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert msg["Precedence"] == "bulk"
    assert "BankScan" in msg["From"]
    assert msg["To"] is not None


def test_build_message_has_plain_and_html():
    msg = _build_message("to@test.com", "Test", "<p>Hi</p>", "Hi")
    payloads = msg.get_payload()
    assert len(payloads) == 2
    assert payloads[0].get_content_type() == "text/plain"
    assert payloads[1].get_content_type() == "text/html"


def test_build_message_includes_unsubscribe_in_body():
    msg = _build_message("to@test.com", "Test", "<p>Hi</p>", "Hi")
    html_part = msg.get_payload()[1].get_payload(decode=True).decode()
    text_part = msg.get_payload()[0].get_payload(decode=True).decode()
    assert "unsubscribe" in html_part.lower()
    assert "unsubscribe" in text_part.lower()


def test_html_to_plain():
    html = '<p>Hello <a href="https://example.com">World</a></p><ul><li>Item 1</li><li>Item 2</li></ul>'
    text = _html_to_plain(html)
    assert "Hello" in text
    assert "example.com" in text
    assert "Item 1" in text
