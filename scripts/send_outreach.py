#!/usr/bin/env python3
"""
BankScan AI — Cold Outreach Email Sender

Sends personalised outreach emails to accounting/bookkeeping firms
from the scraped CSV data. Supports Amazon SES API (preferred) or SMTP.

Requirements:
    Add these to your .env file:
        # Amazon SES (preferred)
        AWS_ACCESS_KEY_ID=your_key
        AWS_SECRET_ACCESS_KEY=your_secret
        AWS_DEFAULT_REGION=us-east-1
        OUTREACH_USE_SES=true

        # OR legacy SMTP
        OUTREACH_SMTP_HOST=smtp.example.com
        OUTREACH_SMTP_PORT=587
        OUTREACH_SMTP_USER=info@mitobaconsulting.com
        OUTREACH_SMTP_PASSWORD=your_password

        # Common
        OUTREACH_FROM_EMAIL=info@mitobaconsulting.com
        OUTREACH_FROM_NAME=Mitchell Agoma

Usage:
    # Preview first 3 emails (dry run — no sending)
    python scripts/send_outreach.py --input accountants_alabama_full.csv --dry-run --limit 3

    # Send to first 10 firms
    python scripts/send_outreach.py --input accountants_alabama_full.csv --limit 10

    # Send to all firms with emails
    python scripts/send_outreach.py --input accountants_alabama_full.csv

    # Resume from where you left off (skips already-sent)
    python scripts/send_outreach.py --input accountants_alabama_full.csv --resume
"""

import argparse
import csv
import json
import os
import random
import smtplib
import sys
import time
import logging
import uuid
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate, make_msgid
from pathlib import Path
from dotenv import load_dotenv

try:
    import boto3
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("outreach")

# ── Email config ─────────────────────────────────────────────────────
USE_SES = os.getenv("OUTREACH_USE_SES", "").lower() in ("true", "1", "yes")
SMTP_HOST = os.getenv("OUTREACH_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("OUTREACH_SMTP_PORT", "587"))
SMTP_USER = os.getenv("OUTREACH_SMTP_USER", "")
SMTP_PASSWORD = os.getenv("OUTREACH_SMTP_PASSWORD", "")
FROM_EMAIL = os.getenv("OUTREACH_FROM_EMAIL", "info@mitobaconsulting.com")
FROM_NAME = os.getenv("OUTREACH_FROM_NAME", "Mitchell Agoma")

# ── SES client (lazy init) ──────────────────────────────────────────
_ses_client = None

def get_ses_client():
    global _ses_client
    if _ses_client is None:
        _ses_client = boto3.client(
            'ses',
            region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1'),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        )
    return _ses_client

# ── Rate limiting (conservative to protect deliverability) ───────────
DELAY_BETWEEN_EMAILS = 90       # ~90s between emails ≈ 40/hour (safe for cold outreach)
DELAY_JITTER = 30               # random ±30s so timing looks human
BATCH_SIZE = 25                 # pause after this many emails
BATCH_PAUSE = 300               # 5-min pause between batches

# ── Sent-log file (tracks who we already emailed) ───────────────────
SENT_LOG = Path(__file__).resolve().parent.parent / "outreach_sent.json"


def load_sent_log() -> dict:
    if SENT_LOG.exists():
        with open(SENT_LOG, "r") as f:
            return json.load(f)
    return {}


def save_sent_log(sent: dict):
    with open(SENT_LOG, "w") as f:
        json.dump(sent, f, indent=2)


# ── Email template ───────────────────────────────────────────────────

def build_subject(firm_name: str) -> str:
    return f"Save hours on bank statement processing — for {firm_name}"


def build_plain_text(firm_name: str, county: str) -> str:
    location = f"in {county} County" if county else ""
    return f"""Dear {firm_name} Team,

I hope this message finds you well. I'm Mitchell from Mitoba Consulting, and I wanted to reach out because we've built a tool that could streamline your bookkeeping workflow {location}.

BankScan AI converts US bank statement PDFs and CSVs into clean, color-coded Excel spreadsheets in seconds. It supports all major US banks including Chase, Bank of America, Wells Fargo, Citi, US Bank, PNC, Capital One, and many more.

As a focused accounting practice, every hour saved on data entry is an hour you can spend advising clients and growing your business.

What sets BankScan AI apart:
- Instant results: Upload a statement, download a formatted spreadsheet in seconds
- Smart parsing: Automatically handles tricky multi-page and multi-line statement formats
- Receipt scanning: Extract line items, totals, and sales tax from store receipt images
- Fully secure: US-hosted infrastructure, SOC 2 aligned, files deleted immediately after processing

See it in action — no download needed:
- Bank statement parsing demo: https://bankscanai-demos.s3.amazonaws.com/statement_demo.webm
- Receipt scanning demo: https://bankscanai-demos.s3.amazonaws.com/receipt_demo.webm

You can try it free right now with no sign-up required — 1 statement and 1 receipt: https://bankscanai.com/landing

Our Starter plan at $9.99/month would be a great fit for {firm_name}, and you can cancel at any time.

Would you be open to a brief 10-minute call this week? I'd love to show you how it works.

Warm regards,
Mitchell Agoma
Mitoba Consulting
info@mitobaconsulting.com
"""


def build_html(firm_name: str, county: str) -> str:
    location = f"in {county} County" if county else ""
    return f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 1.5rem; color: #333; line-height: 1.6;">

    <p>Dear <strong>{firm_name}</strong> Team,</p>

    <p>I hope this message finds you well. I'm Mitchell from <strong>Mitoba Consulting</strong>, and I wanted to reach out because we've built a tool that could streamline your bookkeeping workflow {location}.</p>

    <p><strong>BankScan AI</strong> converts US bank statement PDFs and CSVs into clean, color-coded Excel spreadsheets in seconds. It supports all major US banks including Chase, Bank of America, Wells Fargo, Citi, US Bank, PNC, Capital One, and many more.</p>

    <p>As a focused accounting practice, every hour saved on data entry is an hour you can spend advising clients and growing your business.</p>

    <h3 style="color: #1B4F72; margin-top: 1.5rem;">What sets BankScan AI apart:</h3>
    <ul style="padding-left: 1.2rem;">
        <li><strong>Instant results:</strong> Upload a statement, download a formatted spreadsheet in seconds</li>
        <li><strong>Smart parsing:</strong> Automatically handles tricky multi-page and multi-line statement formats</li>
        <li><strong>Receipt scanning:</strong> Extract line items, totals, and sales tax from store receipt images</li>
        <li><strong>Fully secure:</strong> US-hosted infrastructure, SOC 2 aligned, files deleted immediately after processing</li>
    </ul>

    <h3 style="color: #1B4F72; margin-top: 1.5rem;">See it in action — no download needed:</h3>
    <table role="presentation" style="margin: 1rem 0; border-spacing: 0;">
        <tr>
            <td style="padding: 8px 16px; background: #f0f7ff; border-radius: 6px; margin-right: 10px;">
                <a href="https://bankscanai-demos.s3.amazonaws.com/statement_demo.webm" style="color: #1B4F72; text-decoration: none; font-weight: 600;">&#9654; Bank Statement Demo</a>
            </td>
            <td style="width: 12px;"></td>
            <td style="padding: 8px 16px; background: #f0f7ff; border-radius: 6px;">
                <a href="https://bankscanai-demos.s3.amazonaws.com/receipt_demo.webm" style="color: #1B4F72; text-decoration: none; font-weight: 600;">&#9654; Receipt Scanning Demo</a>
            </td>
        </tr>
    </table>

    <p style="text-align: center; margin: 1.5rem 0;">
        <a href="https://bankscanai.com/landing" style="display: inline-block; background: #1B4F72; color: #fff; padding: 12px 28px; border-radius: 6px; text-decoration: none; font-weight: 600;">Try BankScan AI Free &rarr;</a>
    </p>
    <p style="text-align: center; color: #666; font-size: 0.9rem;">No sign-up required — 1 free statement + 1 free receipt</p>

    <p>Our Starter plan at <strong>$9.99/month</strong> would be a great fit for {firm_name}, and you can cancel at any time.</p>

    <p>Would you be open to a brief 10-minute call this week? I'd love to show you how it works.</p>

    <p style="margin-top: 1.5rem;">
        Warm regards,<br>
        <strong>Mitchell Agoma</strong><br>
        <span style="color: #666;">Mitoba Consulting</span><br>
        <a href="mailto:info@mitobaconsulting.com" style="color: #1B4F72;">info@mitobaconsulting.com</a>
    </p>

    <hr style="border: none; border-top: 1px solid #eee; margin: 2rem 0 1rem;">
    <p style="color: #999; font-size: 0.8rem;">
        You're receiving this because {firm_name} is listed as an accounting or bookkeeping practice {location}.
        If you'd prefer not to receive further messages, simply reply with "unsubscribe" and we'll remove you immediately.
    </p>
</div>
"""


# ── Sending ──────────────────────────────────────────────────────────

def send_email_ses(to_email: str, subject: str, plain: str, html: str) -> bool:
    """Send a single email via Amazon SES API. Returns True on success."""
    try:
        ses = get_ses_client()
        resp = ses.send_email(
            Source=f"{FROM_NAME} <{FROM_EMAIL}>",
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": plain, "Charset": "UTF-8"},
                    "Html": {"Data": html, "Charset": "UTF-8"},
                },
            },
            ReplyToAddresses=[FROM_EMAIL],
        )
        return True
    except ClientError as e:
        log.error("SES failed for %s: %s", to_email, e.response["Error"]["Message"])
        return False
    except Exception as e:
        log.error("SES failed for %s: %s", to_email, e)
        return False


def send_email_smtp(to_email: str, subject: str, plain: str, html: str) -> bool:
    """Send a single email via SMTP. Returns True on success."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Reply-To"] = FROM_EMAIL
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="mitobaconsulting.com")
    msg["List-Unsubscribe"] = f"<mailto:{FROM_EMAIL}?subject=Unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            if SMTP_PORT != 25:
                server.starttls()
                server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        log.error("SMTP failed for %s: %s", to_email, e)
        return False


def send_email(to_email: str, subject: str, plain: str, html: str) -> bool:
    """Send email via SES (preferred) or SMTP fallback."""
    if USE_SES and HAS_BOTO3:
        return send_email_ses(to_email, subject, plain, html)
    return send_email_smtp(to_email, subject, plain, html)


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Send BankScan AI outreach emails")
    parser.add_argument("--input", default="accountants_alabama_full.csv", help="Input CSV")
    parser.add_argument("--dry-run", action="store_true", help="Preview emails without sending")
    parser.add_argument("--limit", type=int, default=0, help="Max emails to send (0 = all)")
    parser.add_argument("--resume", action="store_true", help="Skip already-sent emails")
    parser.add_argument("--delay", type=float, default=DELAY_BETWEEN_EMAILS, help="Seconds between emails")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    input_path = project_root / args.input

    if not input_path.exists():
        print(f"ERROR: {input_path} not found")
        sys.exit(1)

    # Validate sending config (unless dry run)
    if not args.dry_run:
        if USE_SES and HAS_BOTO3:
            log.info("Using Amazon SES API for sending")
        elif USE_SES and not HAS_BOTO3:
            print("ERROR: OUTREACH_USE_SES=true but boto3 is not installed")
            print("  pip install boto3")
            sys.exit(1)
        elif SMTP_HOST:
            log.info("Using SMTP (%s:%s) for sending", SMTP_HOST, SMTP_PORT)
        else:
            print("ERROR: No sending method configured. Add to .env:")
            print("  OUTREACH_USE_SES=true  (with AWS credentials)")
            print("  OR  OUTREACH_SMTP_HOST=smtp.example.com")
            sys.exit(1)

    # Load CSV and deduplicate by email address
    with open(input_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        all_firms = [row for row in reader if row.get("email")]

    seen_emails = set()
    firms = []
    for f in all_firms:
        email_lower = f["email"].strip().lower()
        if email_lower not in seen_emails:
            seen_emails.add(email_lower)
            firms.append(f)

    dupes_removed = len(all_firms) - len(firms)
    print(f"Loaded {len(all_firms)} rows → {len(firms)} unique email addresses", flush=True)
    if dupes_removed:
        print(f"  (removed {dupes_removed} duplicate emails)", flush=True)

    # Load sent log
    sent_log = load_sent_log() if args.resume else {}

    # Filter out already-sent
    if args.resume:
        before = len(firms)
        firms = [f for f in firms if f["email"].strip().lower() not in sent_log]
        skipped = before - len(firms)
        if skipped:
            print(f"Resuming: skipped {skipped} already-sent emails", flush=True)

    # Apply limit
    if args.limit > 0:
        firms = firms[:args.limit]

    print(f"Will {'preview' if args.dry_run else 'send'} {len(firms)} emails\n", flush=True)

    if not firms:
        print("Nothing to send.")
        return

    sent_count = 0
    fail_count = 0

    for i, firm in enumerate(firms, 1):
        name = firm.get("name", "").strip()
        email = firm.get("email", "").strip().lower()
        county = firm.get("county", "").strip()

        if not email:
            continue

        subject = build_subject(name)
        plain = build_plain_text(name, county)
        html = build_html(name, county)

        if args.dry_run:
            print(f"─── [{i}/{len(firms)}] DRY RUN ───")
            print(f"  To:      {email}")
            print(f"  Subject: {subject}")
            print(f"  Firm:    {name}")
            print(f"  County:  {county}")
            print(f"  Preview: {plain[:200]}...")
            print()
            sent_count += 1
            continue

        # Send
        log.info("[%d/%d] Sending to %s (%s)...", i, len(firms), email, name)
        ok = send_email(email, subject, plain, html)

        if ok:
            sent_count += 1
            sent_log[email] = {
                "name": name,
                "county": county,
                "sent_at": datetime.now().isoformat(),
            }
            save_sent_log(sent_log)
            log.info("  ✓ Sent (%d/%d successful)", sent_count, i)
        else:
            fail_count += 1
            log.warning("  ✗ Failed (%d failures so far)", fail_count)

        # Rate limiting with jitter to look human
        if i < len(firms):
            if i % BATCH_SIZE == 0:
                log.info("Batch pause: waiting %ds after %d emails...", BATCH_PAUSE, i)
                time.sleep(BATCH_PAUSE)
            else:
                jitter = random.uniform(-DELAY_JITTER, DELAY_JITTER)
                delay = max(10, args.delay + jitter)  # never less than 10s
                time.sleep(delay)

    # Summary
    print(f"\n{'═' * 50}", flush=True)
    print(f"{'DRY RUN ' if args.dry_run else ''}COMPLETE", flush=True)
    print(f"  Sent:    {sent_count}", flush=True)
    print(f"  Failed:  {fail_count}", flush=True)
    print(f"  Total:   {sent_count + fail_count}", flush=True)
    if not args.dry_run:
        print(f"  Log:     {SENT_LOG}", flush=True)
    print(f"{'═' * 50}", flush=True)


if __name__ == "__main__":
    main()
