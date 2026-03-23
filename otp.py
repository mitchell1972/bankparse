"""
BankParse — OTP Email Verification
Generates and sends OTP codes for email-based subscription restoration.
Uses SMTP for email delivery.
"""

import os
import secrets
import string
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("bankparse.otp")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "noreply@bankparse.com")


def generate_otp(length: int = 6) -> str:
    """Generate a numeric OTP code."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def send_otp_email(to_email: str, code: str) -> bool:
    """Send an OTP verification email. Returns True on success."""
    if not SMTP_HOST:
        logger.warning("SMTP not configured — OTP code for %s: %s", to_email, code)
        return True

    subject = "BankScan AI — Your verification code"
    html_body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto; padding: 2rem;">
        <h2 style="color: #1B4F72;">BankScan AI Verification</h2>
        <p>Your verification code is:</p>
        <div style="background: #F0F4F8; padding: 1.5rem; text-align: center; border-radius: 8px; margin: 1.5rem 0;">
            <span style="font-size: 2rem; font-weight: bold; letter-spacing: 0.3em; color: #1B4F72;">{code}</span>
        </div>
        <p style="color: #666;">This code expires in <strong>10 minutes</strong>.</p>
        <p style="color: #666;">If you didn't request this code, you can safely ignore this email.</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 1.5rem 0;">
        <p style="color: #999; font-size: 0.85rem;">BankScan AI — AI-powered bank statement &amp; receipt intelligence</p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(f"Your BankScan AI verification code is: {code}\n\nThis code expires in 10 minutes.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            if SMTP_PORT != 25:
                server.starttls()
                server.ehlo()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, to_email, msg.as_string())
        logger.info("OTP email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send OTP email to %s", to_email)
        return False
