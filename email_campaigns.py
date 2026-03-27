"""
BankParse — Email Campaign Module
Send targeted email campaigns to registered users.
Supports segmentation by tier, usage, and signup date.
Uses the same SMTP config as OTP emails.
"""

import os
import time
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from database import _execute, _fetchall_dicts, _execute_insert, _fetchone_dict

logger = logging.getLogger("bankparse.campaigns")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "noreply@bankparse.com")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "BankScan AI")

# Admin key for campaign endpoints (set via env var)
CAMPAIGN_ADMIN_KEY = os.environ.get("CAMPAIGN_ADMIN_KEY", "")

# Rate limit: max emails per batch to avoid SMTP throttling
BATCH_SIZE = int(os.environ.get("CAMPAIGN_BATCH_SIZE", "50"))
BATCH_DELAY = float(os.environ.get("CAMPAIGN_BATCH_DELAY", "1.0"))


def init_campaign_tables():
    """Create campaign tracking tables if they don't exist."""
    stmts = [
        """CREATE TABLE IF NOT EXISTS email_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            subject TEXT NOT NULL,
            body_html TEXT NOT NULL,
            body_text TEXT,
            segment TEXT DEFAULT 'all',
            status TEXT DEFAULT 'draft',
            total_recipients INTEGER DEFAULT 0,
            sent_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            created_at REAL DEFAULT (strftime('%s', 'now')),
            sent_at REAL
        )""",
        """CREATE TABLE IF NOT EXISTS campaign_sends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            user_email TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            sent_at REAL,
            error TEXT,
            FOREIGN KEY (campaign_id) REFERENCES email_campaigns(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_campaign_sends_campaign ON campaign_sends(campaign_id)",
        "CREATE INDEX IF NOT EXISTS idx_campaign_sends_email ON campaign_sends(user_email)",
    ]
    for stmt in stmts:
        _execute(stmt)
    logger.info("Campaign tables initialized")


def verify_admin_key(key: str) -> bool:
    """Check if the provided key matches the admin campaign key."""
    if not CAMPAIGN_ADMIN_KEY:
        return False
    return key == CAMPAIGN_ADMIN_KEY


def get_recipients(segment: str = "all") -> list[dict]:
    """Get user emails filtered by segment.

    Segments:
      - all: all registered users
      - free: users without active subscription
      - paid: users with active subscription
      - inactive: users who signed up but have 0 total usage
      - active: users who have used the service at least once
    """
    base = "SELECT id, email, subscription_status, statements_used, receipts_used FROM users"

    if segment == "free":
        return _fetchall_dicts(
            f"{base} WHERE subscription_status IS NULL OR subscription_status NOT IN ('active', 'trialing')"
        )
    elif segment == "paid":
        return _fetchall_dicts(
            f"{base} WHERE subscription_status IN ('active', 'trialing')"
        )
    elif segment == "inactive":
        return _fetchall_dicts(
            f"{base} WHERE statements_used = 0 AND receipts_used = 0"
        )
    elif segment == "active":
        return _fetchall_dicts(
            f"{base} WHERE statements_used > 0 OR receipts_used > 0"
        )
    else:
        return _fetchall_dicts(base)


def create_campaign(name: str, subject: str, body_html: str, body_text: str = "",
                    segment: str = "all") -> int:
    """Create a new campaign in draft status. Returns the campaign ID."""
    return _execute_insert(
        "INSERT INTO email_campaigns (name, subject, body_html, body_text, segment) VALUES (?, ?, ?, ?, ?)",
        (name, subject, body_html, body_text, segment),
    )


def get_campaign(campaign_id: int) -> dict | None:
    return _fetchone_dict("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,))


def list_campaigns() -> list[dict]:
    return _fetchall_dicts("SELECT * FROM email_campaigns ORDER BY created_at DESC")


def _send_single_email(to_email: str, subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    """Send a single campaign email. Returns (success, error_message)."""
    if not SMTP_HOST:
        logger.warning("SMTP not configured — would send campaign to %s", to_email)
        return True, ""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM}>"
    msg["To"] = to_email
    msg["List-Unsubscribe"] = f"<mailto:{SMTP_FROM}?subject=unsubscribe>"

    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            if SMTP_PORT != 25:
                server.starttls()
                server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        return True, ""
    except Exception as e:
        logger.exception("Failed to send campaign email to %s", to_email)
        return False, str(e)


def send_campaign(campaign_id: int) -> dict:
    """Send a campaign to all recipients in its segment.

    Returns a summary dict with sent/failed counts.
    """
    campaign = get_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    if campaign["status"] == "sent":
        return {"error": "Campaign already sent"}

    recipients = get_recipients(campaign["segment"])
    if not recipients:
        return {"error": "No recipients for this segment"}

    _execute(
        "UPDATE email_campaigns SET status = 'sending', total_recipients = ? WHERE id = ?",
        (len(recipients), campaign_id),
    )

    sent = 0
    failed = 0

    for i, user in enumerate(recipients):
        email = user["email"]
        success, error = _send_single_email(
            email, campaign["subject"], campaign["body_html"],
            campaign.get("body_text", ""),
        )

        status = "sent" if success else "failed"
        _execute_insert(
            "INSERT INTO campaign_sends (campaign_id, user_email, status, sent_at, error) VALUES (?, ?, ?, ?, ?)",
            (campaign_id, email, status, time.time(), error or None),
        )

        if success:
            sent += 1
        else:
            failed += 1

        # Batch delay to avoid SMTP throttling
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(recipients):
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import threading
                    threading.Event().wait(BATCH_DELAY)
                else:
                    time.sleep(BATCH_DELAY)
            except RuntimeError:
                time.sleep(BATCH_DELAY)

    _execute(
        "UPDATE email_campaigns SET status = 'sent', sent_count = ?, failed_count = ?, sent_at = ? WHERE id = ?",
        (sent, failed, time.time(), campaign_id),
    )

    return {"campaign_id": campaign_id, "sent": sent, "failed": failed, "total": len(recipients)}


def send_test_email(campaign_id: int, test_email: str) -> dict:
    """Send a test/preview email for a campaign to a single address."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    success, error = _send_single_email(
        test_email, f"[TEST] {campaign['subject']}",
        campaign["body_html"], campaign.get("body_text", ""),
    )
    return {"success": success, "error": error}


def get_campaign_stats(campaign_id: int) -> dict:
    """Get send statistics for a campaign."""
    campaign = get_campaign(campaign_id)
    if not campaign:
        return {"error": "Campaign not found"}

    sends = _fetchall_dicts(
        "SELECT status, COUNT(*) as count FROM campaign_sends WHERE campaign_id = ? GROUP BY status",
        (campaign_id,),
    )
    status_counts = {row["status"]: row["count"] for row in sends}

    return {
        "campaign_id": campaign_id,
        "name": campaign["name"],
        "status": campaign["status"],
        "total_recipients": campaign["total_recipients"],
        "sent": status_counts.get("sent", 0),
        "failed": status_counts.get("failed", 0),
        "pending": status_counts.get("pending", 0),
    }


# --- Pre-built campaign templates ---

CAMPAIGN_TEMPLATES = {
    "welcome": {
        "name": "Welcome to BankScan AI",
        "subject": "Welcome to BankScan AI - Get Started Today!",
        "body_html": """
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 2rem; background: #ffffff;">
            <div style="text-align: center; margin-bottom: 2rem;">
                <h1 style="color: #1B4F72; margin: 0;">BankScan AI</h1>
                <p style="color: #666; margin-top: 0.5rem;">AI-Powered Bank Statement & Receipt Intelligence</p>
            </div>
            <h2 style="color: #1B4F72;">Welcome aboard!</h2>
            <p>Thank you for signing up for BankScan AI. Here's what you can do:</p>
            <ul style="line-height: 1.8;">
                <li><strong>Convert bank statements</strong> from PDF to structured Excel spreadsheets</li>
                <li><strong>Parse receipts</strong> with AI-powered extraction</li>
                <li><strong>Bulk processing</strong> for multiple files at once</li>
                <li><strong>AI Chat</strong> to ask questions about your documents</li>
            </ul>
            <div style="text-align: center; margin: 2rem 0;">
                <a href="https://bankscanai.com" style="background: #1B4F72; color: white; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: bold;">Start Scanning</a>
            </div>
            <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0;">
            <p style="color: #999; font-size: 0.85rem; text-align: center;">BankScan AI — AI-powered bank statement & receipt intelligence</p>
        </div>
        """,
        "body_text": "Welcome to BankScan AI! Convert bank statements, parse receipts, and more. Visit https://bankscanai.com to get started.",
    },
    "upgrade": {
        "name": "Upgrade Your Plan",
        "subject": "Unlock More with BankScan AI Pro",
        "body_html": """
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 2rem; background: #ffffff;">
            <div style="text-align: center; margin-bottom: 2rem;">
                <h1 style="color: #1B4F72; margin: 0;">BankScan AI</h1>
            </div>
            <h2 style="color: #1B4F72;">You're running out of free scans!</h2>
            <p>You've been using BankScan AI and we hope you're finding it valuable. Upgrade to unlock:</p>
            <table style="width: 100%; border-collapse: collapse; margin: 1.5rem 0;">
                <tr style="background: #F0F4F8;">
                    <td style="padding: 12px; border: 1px solid #ddd;"><strong>Starter</strong></td>
                    <td style="padding: 12px; border: 1px solid #ddd;">10 statements + 20 receipts/mo</td>
                </tr>
                <tr>
                    <td style="padding: 12px; border: 1px solid #ddd;"><strong>Pro</strong></td>
                    <td style="padding: 12px; border: 1px solid #ddd;">50 statements + 100 receipts/mo</td>
                </tr>
                <tr style="background: #F0F4F8;">
                    <td style="padding: 12px; border: 1px solid #ddd;"><strong>Business</strong></td>
                    <td style="padding: 12px; border: 1px solid #ddd;">200 statements + 500 receipts/mo</td>
                </tr>
            </table>
            <div style="text-align: center; margin: 2rem 0;">
                <a href="https://bankscanai.com/#pricing" style="background: #1B4F72; color: white; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: bold;">View Plans</a>
            </div>
            <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0;">
            <p style="color: #999; font-size: 0.85rem; text-align: center;">BankScan AI — AI-powered bank statement & receipt intelligence</p>
        </div>
        """,
        "body_text": "You're running out of free scans! Upgrade your BankScan AI plan to unlock more. Visit https://bankscanai.com/#pricing",
    },
    "feature_update": {
        "name": "New Feature Announcement",
        "subject": "New in BankScan AI: Exciting Updates!",
        "body_html": """
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 2rem; background: #ffffff;">
            <div style="text-align: center; margin-bottom: 2rem;">
                <h1 style="color: #1B4F72; margin: 0;">BankScan AI</h1>
                <p style="color: #2E86C1; font-weight: bold;">Product Update</p>
            </div>
            <h2 style="color: #1B4F72;">What's New</h2>
            <p>We've been busy building new features to make your document processing even better:</p>
            <div style="background: #F0F4F8; padding: 1.5rem; border-radius: 8px; margin: 1.5rem 0;">
                <p style="margin: 0;"><strong>Replace this with your feature details.</strong></p>
                <p style="margin: 0.5rem 0 0 0;">Describe the new features and improvements here.</p>
            </div>
            <div style="text-align: center; margin: 2rem 0;">
                <a href="https://bankscanai.com" style="background: #1B4F72; color: white; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: bold;">Try It Now</a>
            </div>
            <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0;">
            <p style="color: #999; font-size: 0.85rem; text-align: center;">BankScan AI — AI-powered bank statement & receipt intelligence</p>
        </div>
        """,
        "body_text": "We've got exciting new features for BankScan AI! Visit https://bankscanai.com to check them out.",
    },
    "re_engage": {
        "name": "We Miss You!",
        "subject": "We Miss You at BankScan AI",
        "body_html": """
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 2rem; background: #ffffff;">
            <div style="text-align: center; margin-bottom: 2rem;">
                <h1 style="color: #1B4F72; margin: 0;">BankScan AI</h1>
            </div>
            <h2 style="color: #1B4F72;">It's been a while!</h2>
            <p>We noticed you haven't used BankScan AI recently. We've made a lot of improvements since your last visit:</p>
            <ul style="line-height: 1.8;">
                <li>Faster and more accurate PDF parsing</li>
                <li>AI-powered receipt extraction</li>
                <li>Bulk file processing</li>
                <li>New chat feature to analyze your documents</li>
            </ul>
            <p>Come back and give it another try — your first scan is on us!</p>
            <div style="text-align: center; margin: 2rem 0;">
                <a href="https://bankscanai.com" style="background: #1B4F72; color: white; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: bold;">Come Back</a>
            </div>
            <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0;">
            <p style="color: #999; font-size: 0.85rem; text-align: center;">BankScan AI — AI-powered bank statement & receipt intelligence</p>
        </div>
        """,
        "body_text": "We miss you at BankScan AI! We've made lots of improvements. Visit https://bankscanai.com to check it out.",
    },
}
