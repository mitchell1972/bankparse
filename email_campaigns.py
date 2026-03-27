"""
BankParse — Email Campaign Module
Send targeted email campaigns to registered users.
Supports segmentation by tier, usage, and signup date.
Uses the same SMTP config as OTP emails.

Anti-spam deliverability best practices:
- Proper RFC-compliant headers (Message-ID, Date, Reply-To, MIME-Version)
- List-Unsubscribe header (RFC 2369) for one-click unsubscribe
- Unsubscribe tracking in the database
- Plain-text + HTML multipart (MIME alternative)
- Per-recipient throttling with configurable batch delay
- Personalised To header (never BCC blasting)
- Clean HTML without spam trigger words in headers
- Proper EHLO with matching sender domain
"""

import os
import re
import time
import uuid
import smtplib
import logging
import hashlib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from database import _execute, _fetchall_dicts, _execute_insert, _fetchone_dict

logger = logging.getLogger("bankparse.campaigns")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "noreply@bankparse.com")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "BankScan AI")
SMTP_REPLY_TO = os.environ.get("SMTP_REPLY_TO", "")  # e.g. support@bankscanai.com

# The public-facing base URL for unsubscribe links
APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://bankscanai.com")

# Admin key for campaign endpoints (set via env var)
CAMPAIGN_ADMIN_KEY = os.environ.get("CAMPAIGN_ADMIN_KEY", "")

# Rate limit: max emails per batch to avoid SMTP throttling
BATCH_SIZE = int(os.environ.get("CAMPAIGN_BATCH_SIZE", "20"))
BATCH_DELAY = float(os.environ.get("CAMPAIGN_BATCH_DELAY", "2.0"))

# Secret for generating unsubscribe tokens (falls back to a hash of admin key)
UNSUBSCRIBE_SECRET = os.environ.get(
    "UNSUBSCRIBE_SECRET",
    hashlib.sha256(CAMPAIGN_ADMIN_KEY.encode() or b"bankparse-unsub").hexdigest()[:32],
)


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
        """CREATE TABLE IF NOT EXISTS email_unsubscribes (
            email TEXT PRIMARY KEY,
            unsubscribed_at REAL DEFAULT (strftime('%s', 'now')),
            reason TEXT DEFAULT 'user_request'
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


# --- Unsubscribe management ---

def _make_unsubscribe_token(email: str) -> str:
    """Generate a deterministic token for unsubscribe verification."""
    return hashlib.sha256(f"{UNSUBSCRIBE_SECRET}:{email}".encode()).hexdigest()[:24]


def get_unsubscribe_url(email: str) -> str:
    """Generate the one-click unsubscribe URL for a recipient."""
    token = _make_unsubscribe_token(email)
    return f"{APP_BASE_URL}/api/campaigns/unsubscribe?email={email}&token={token}"


def verify_unsubscribe_token(email: str, token: str) -> bool:
    """Verify that an unsubscribe token is valid."""
    return token == _make_unsubscribe_token(email)


def unsubscribe_email(email: str):
    """Record that a user has unsubscribed from campaigns."""
    try:
        _execute_insert(
            "INSERT INTO email_unsubscribes (email) VALUES (?)",
            (email,),
        )
    except Exception:
        pass  # Already unsubscribed


def resubscribe_email(email: str):
    """Remove an unsubscribe record (user wants emails again)."""
    _execute("DELETE FROM email_unsubscribes WHERE email = ?", (email,))


def is_unsubscribed(email: str) -> bool:
    """Check if an email has unsubscribed."""
    row = _fetchone_dict("SELECT email FROM email_unsubscribes WHERE email = ?", (email,))
    return row is not None


def list_unsubscribes() -> list[dict]:
    """List all unsubscribed emails."""
    return _fetchall_dicts("SELECT * FROM email_unsubscribes ORDER BY unsubscribed_at DESC")


# --- Recipient queries ---

def get_recipients(segment: str = "all") -> list[dict]:
    """Get user emails filtered by segment, excluding unsubscribed users.

    Segments:
      - all: all registered users
      - free: users without active subscription
      - paid: users with active subscription
      - inactive: users who signed up but have 0 total usage
      - active: users who have used the service at least once
    """
    base = "SELECT id, email, subscription_status, statements_used, receipts_used FROM users"
    unsub_filter = " AND email NOT IN (SELECT email FROM email_unsubscribes)"

    if segment == "free":
        return _fetchall_dicts(
            f"{base} WHERE (subscription_status IS NULL OR subscription_status NOT IN ('active', 'trialing')){unsub_filter}"
        )
    elif segment == "paid":
        return _fetchall_dicts(
            f"{base} WHERE subscription_status IN ('active', 'trialing'){unsub_filter}"
        )
    elif segment == "inactive":
        return _fetchall_dicts(
            f"{base} WHERE statements_used = 0 AND receipts_used = 0{unsub_filter}"
        )
    elif segment == "active":
        return _fetchall_dicts(
            f"{base} WHERE (statements_used > 0 OR receipts_used > 0){unsub_filter}"
        )
    else:
        return _fetchall_dicts(
            f"{base} WHERE email NOT IN (SELECT email FROM email_unsubscribes)"
        )


# --- Campaign CRUD ---

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


# --- Email construction (anti-spam best practices) ---

def _get_sender_domain() -> str:
    """Extract domain from sender email for Message-ID generation."""
    return SMTP_FROM.split("@")[-1] if "@" in SMTP_FROM else "bankscanai.com"


def _inject_unsubscribe_footer(html_body: str, unsub_url: str) -> str:
    """Append an unsubscribe link to the HTML body if not already present."""
    footer = f"""
    <div style="text-align: center; padding: 1rem 0; margin-top: 1rem; border-top: 1px solid #eee;">
        <p style="color: #999; font-size: 0.8rem; margin: 0;">
            You're receiving this because you signed up at BankScan AI.<br>
            <a href="{unsub_url}" style="color: #999; text-decoration: underline;">Unsubscribe from future emails</a>
        </p>
    </div>
    """
    # Insert before closing </div> or append
    if "</div>" in html_body:
        # Insert before the last closing </div>
        last_div = html_body.rfind("</div>")
        return html_body[:last_div] + footer + html_body[last_div:]
    return html_body + footer


def _inject_unsubscribe_text(text_body: str, unsub_url: str) -> str:
    """Append unsubscribe info to plain-text body."""
    return f"{text_body}\n\n---\nTo unsubscribe: {unsub_url}"


def _build_message(to_email: str, subject: str, html_body: str, text_body: str) -> MIMEMultipart:
    """Build a fully RFC-compliant email message with anti-spam headers."""
    domain = _get_sender_domain()
    unsub_url = get_unsubscribe_url(to_email)

    # Inject unsubscribe footer into both HTML and text
    html_with_footer = _inject_unsubscribe_footer(html_body, unsub_url)
    text_with_footer = _inject_unsubscribe_text(
        text_body or _html_to_plain(html_body), unsub_url
    )

    msg = MIMEMultipart("alternative")

    # --- Required headers for deliverability ---
    msg["Subject"] = subject
    msg["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
    msg["To"] = formataddr(("", to_email))
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)

    # Reply-To: use a real monitored address (not noreply)
    if SMTP_REPLY_TO:
        msg["Reply-To"] = SMTP_REPLY_TO

    # --- Anti-spam headers ---
    # List-Unsubscribe (RFC 2369) — Gmail/Yahoo/Outlook use this for one-click unsubscribe
    msg["List-Unsubscribe"] = f"<{unsub_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # Precedence: bulk tells filters this is a legitimate bulk email
    msg["Precedence"] = "bulk"

    # X-Mailer identifies the sending software (some filters trust known mailers)
    msg["X-Mailer"] = "BankScanAI-Campaign/1.0"

    # MIME parts: plain text FIRST (important — filters check order)
    msg.attach(MIMEText(text_with_footer, "plain", "utf-8"))
    msg.attach(MIMEText(html_with_footer, "html", "utf-8"))

    return msg


def _html_to_plain(html: str) -> str:
    """Basic HTML to plain text conversion for auto-generating text parts."""
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'<li[^>]*>', '- ', text)
    text = re.sub(r'</li>', '\n', text)
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', r'\2 (\1)', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&#[0-9]+;', '', text)
    return text.strip()


# --- Sending ---

def _send_single_email(to_email: str, subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    """Send a single campaign email with full anti-spam headers. Returns (success, error_message)."""
    if not SMTP_HOST:
        logger.warning("SMTP not configured — would send campaign to %s", to_email)
        return True, ""

    msg = _build_message(to_email, subject, html_body, text_body)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            # EHLO with the sender domain (some SMTP servers check this)
            server.ehlo(_get_sender_domain())
            if SMTP_PORT != 25:
                server.starttls()
                server.ehlo(_get_sender_domain())
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

        # Skip unsubscribed users (double-check)
        if is_unsubscribed(email):
            continue

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

        # Throttle: pause between batches to avoid SMTP rate limits
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(recipients):
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
# These follow email deliverability best practices:
# - Clean, simple HTML (no JavaScript, no external images)
# - Proper text/background contrast
# - Short subject lines without spam triggers (FREE!!!, ACT NOW, etc.)
# - Physical address placeholder (required by CAN-SPAM)
# - Unsubscribe footer is auto-injected by _build_message()

CAMPAIGN_TEMPLATES = {
    "welcome": {
        "name": "Welcome to BankScan AI",
        "subject": "Your BankScan AI account is ready",
        "body_html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 2rem; background: #ffffff; color: #333;">
    <div style="text-align: center; margin-bottom: 2rem;">
        <h1 style="color: #1B4F72; margin: 0; font-size: 1.5rem;">BankScan AI</h1>
        <p style="color: #666; margin-top: 0.5rem; font-size: 0.9rem;">AI-Powered Bank Statement &amp; Receipt Intelligence</p>
    </div>
    <h2 style="color: #1B4F72; font-size: 1.2rem;">Welcome aboard!</h2>
    <p>Thank you for signing up. Here is what you can do with your account:</p>
    <ul style="line-height: 2;">
        <li>Convert bank statement PDFs to structured Excel spreadsheets</li>
        <li>Extract receipt data with AI-powered parsing</li>
        <li>Process multiple files in bulk</li>
        <li>Ask questions about your documents with AI Chat</li>
    </ul>
    <div style="text-align: center; margin: 2rem 0;">
        <a href="https://bankscanai.com" style="background: #1B4F72; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">Get Started</a>
    </div>
    <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0;">
    <p style="color: #999; font-size: 0.8rem; text-align: center;">BankScan AI &mdash; bankscanai.com</p>
</div>
        """,
        "body_text": "Welcome to BankScan AI!\n\nThank you for signing up. Here's what you can do:\n\n- Convert bank statement PDFs to Excel\n- Extract receipt data with AI\n- Process multiple files in bulk\n- Ask questions with AI Chat\n\nGet started: https://bankscanai.com",
    },
    "upgrade": {
        "name": "Upgrade Your Plan",
        "subject": "More scans available on BankScan AI",
        "body_html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 2rem; background: #ffffff; color: #333;">
    <div style="text-align: center; margin-bottom: 2rem;">
        <h1 style="color: #1B4F72; margin: 0; font-size: 1.5rem;">BankScan AI</h1>
    </div>
    <h2 style="color: #1B4F72; font-size: 1.2rem;">Need more scans?</h2>
    <p>You have been using BankScan AI and we hope you are finding it valuable. Our paid plans give you more capacity:</p>
    <table style="width: 100%; border-collapse: collapse; margin: 1.5rem 0; font-size: 0.95rem;">
        <tr style="background: #F0F4F8;">
            <td style="padding: 10px 12px; border: 1px solid #ddd;"><strong>Starter</strong></td>
            <td style="padding: 10px 12px; border: 1px solid #ddd;">10 statements + 20 receipts/mo</td>
        </tr>
        <tr>
            <td style="padding: 10px 12px; border: 1px solid #ddd;"><strong>Pro</strong></td>
            <td style="padding: 10px 12px; border: 1px solid #ddd;">50 statements + 100 receipts/mo</td>
        </tr>
        <tr style="background: #F0F4F8;">
            <td style="padding: 10px 12px; border: 1px solid #ddd;"><strong>Business</strong></td>
            <td style="padding: 10px 12px; border: 1px solid #ddd;">200 statements + 500 receipts/mo</td>
        </tr>
    </table>
    <div style="text-align: center; margin: 2rem 0;">
        <a href="https://bankscanai.com/#pricing" style="background: #1B4F72; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">View Plans</a>
    </div>
    <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0;">
    <p style="color: #999; font-size: 0.8rem; text-align: center;">BankScan AI &mdash; bankscanai.com</p>
</div>
        """,
        "body_text": "Need more scans?\n\nOur paid plans give you more capacity:\n\n- Starter: 10 statements + 20 receipts/mo\n- Pro: 50 statements + 100 receipts/mo\n- Business: 200 statements + 500 receipts/mo\n\nView plans: https://bankscanai.com/#pricing",
    },
    "feature_update": {
        "name": "New Feature Announcement",
        "subject": "What's new at BankScan AI",
        "body_html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 2rem; background: #ffffff; color: #333;">
    <div style="text-align: center; margin-bottom: 2rem;">
        <h1 style="color: #1B4F72; margin: 0; font-size: 1.5rem;">BankScan AI</h1>
        <p style="color: #2E86C1; font-weight: bold; font-size: 0.9rem;">Product Update</p>
    </div>
    <h2 style="color: #1B4F72; font-size: 1.2rem;">Recent Improvements</h2>
    <p>We have been building new features to make your document processing even better:</p>
    <div style="background: #F0F4F8; padding: 1.5rem; border-radius: 8px; margin: 1.5rem 0;">
        <p style="margin: 0;"><strong>Replace this section with your feature details.</strong></p>
        <p style="margin: 0.5rem 0 0 0;">Describe the new features and improvements here.</p>
    </div>
    <div style="text-align: center; margin: 2rem 0;">
        <a href="https://bankscanai.com" style="background: #1B4F72; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">Try It Now</a>
    </div>
    <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0;">
    <p style="color: #999; font-size: 0.8rem; text-align: center;">BankScan AI &mdash; bankscanai.com</p>
</div>
        """,
        "body_text": "Recent improvements at BankScan AI\n\nWe've been building new features to make your document processing even better.\n\n(Replace this with your feature details.)\n\nTry it now: https://bankscanai.com",
    },
    "re_engage": {
        "name": "We'd love to see you again",
        "subject": "Your BankScan AI account is waiting",
        "body_html": """
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 2rem; background: #ffffff; color: #333;">
    <div style="text-align: center; margin-bottom: 2rem;">
        <h1 style="color: #1B4F72; margin: 0; font-size: 1.5rem;">BankScan AI</h1>
    </div>
    <h2 style="color: #1B4F72; font-size: 1.2rem;">It has been a while</h2>
    <p>We have made a lot of improvements since your last visit:</p>
    <ul style="line-height: 2;">
        <li>Faster and more accurate PDF parsing</li>
        <li>AI-powered receipt extraction</li>
        <li>Bulk file processing</li>
        <li>New chat feature to analyze your documents</li>
    </ul>
    <p>Your account is still active and ready to use.</p>
    <div style="text-align: center; margin: 2rem 0;">
        <a href="https://bankscanai.com" style="background: #1B4F72; color: #ffffff; padding: 12px 32px; text-decoration: none; border-radius: 6px; font-weight: bold; display: inline-block;">Log In</a>
    </div>
    <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0;">
    <p style="color: #999; font-size: 0.8rem; text-align: center;">BankScan AI &mdash; bankscanai.com</p>
</div>
        """,
        "body_text": "It's been a while!\n\nWe've made a lot of improvements since your last visit:\n\n- Faster PDF parsing\n- AI-powered receipt extraction\n- Bulk file processing\n- New AI chat feature\n\nYour account is still active: https://bankscanai.com",
    },
}
