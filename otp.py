"""
BankParse — OTP Email Verification
Generates and sends OTP codes via Resend's HTTP API
(https://resend.com/docs/api-reference/emails/send-email).

Falls back to console logging when RESEND_API_KEY is not set, so local dev
and CI work without credentials.
"""

import os
import secrets
import string
import logging

import httpx

logger = logging.getLogger("bankparse.otp")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# Sends from mitobaconsulting.com until bankscanai.com is verified in Resend
# (Pro plan upgrade required for a second domain). Override via RESEND_FROM env var.
RESEND_FROM = os.environ.get("RESEND_FROM", "BankScan AI <noreply@mitobaconsulting.com>")
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 10.0


def generate_otp(length: int = 6) -> str:
    """Generate a numeric OTP code."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def send_otp_email(to_email: str, code: str) -> bool:
    """Send an OTP verification email via Resend. Returns True on success."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — OTP code for %s: %s", to_email, code)
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
    text_body = (
        f"Your BankScan AI verification code is: {code}\n\n"
        "This code expires in 10 minutes.\n\n"
        "If you didn't request this code, you can safely ignore this email."
    )

    try:
        response = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            },
            timeout=RESEND_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.exception("Resend request failed for %s", to_email)
        return False

    if response.status_code >= 400:
        logger.error(
            "Resend rejected OTP to %s — status=%d body=%s",
            to_email,
            response.status_code,
            response.text[:500],
        )
        return False

    try:
        message_id = response.json().get("id", "<no-id>")
    except ValueError:
        message_id = "<unparseable-json>"
    logger.info("OTP email sent to %s via Resend (id=%s)", to_email, message_id)
    return True


def send_trial_reminder_email(to_email: str, days_left: int = 2) -> bool:
    """Day-5 trial nudge: "Your trial ends in N days". Uses the same Resend
    client + RESEND_FROM as the OTP path. Returns True on success."""
    if not RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY not set — trial reminder for %s skipped (would say %d days left)",
            to_email, days_left,
        )
        return True

    subject = f"Your BankScan AI trial ends in {days_left} day{'s' if days_left != 1 else ''}"
    html_body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto; padding: 2rem;">
        <h2 style="color: #1B4F72;">Your BankScan AI trial ends in {days_left} day{'s' if days_left != 1 else ''}</h2>
        <p>Thanks for trying BankScan AI. Your 7-day free trial ends soon.</p>
        <p>To keep parsing bank statements and receipts, pick a plan:</p>
        <div style="text-align: center; margin: 1.5rem 0;">
            <a href="https://bankscanai.com/credits"
               style="display: inline-block; padding: 0.75rem 1.5rem; background: #1B4F72; color: white; text-decoration: none; border-radius: 6px; font-weight: 600;">
                View plans
            </a>
        </div>
        <p style="color: #666;">If you've already subscribed, you can ignore this — the reminder fires once per account.</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 1.5rem 0;">
        <p style="color: #999; font-size: 0.85rem;">BankScan AI — AI-powered bank statement &amp; receipt intelligence</p>
    </div>
    """
    text_body = (
        f"Your BankScan AI trial ends in {days_left} day{'s' if days_left != 1 else ''}.\n\n"
        "Pick a plan to keep parsing: https://bankscanai.com/credits\n\n"
        "If you've already subscribed, ignore this — the reminder only fires once."
    )

    try:
        response = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            },
            timeout=RESEND_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.exception("Trial reminder request failed for %s", to_email)
        return False

    if response.status_code >= 400:
        logger.error(
            "Resend rejected trial reminder to %s — status=%d body=%s",
            to_email, response.status_code, response.text[:500],
        )
        return False

    try:
        message_id = response.json().get("id", "<no-id>")
    except ValueError:
        message_id = "<unparseable-json>"
    logger.info("Trial reminder sent to %s via Resend (id=%s)", to_email, message_id)
    return True


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    """Send the password-reset email. Returns True on success.
    If RESEND_API_KEY is not set, logs the link and returns True so
    local development still works."""
    if not RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY not set — password reset link for %s: %s",
            to_email, reset_link,
        )
        return True

    subject = "BankScan AI — Reset your password"
    html_body = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto; padding: 2rem;">
        <h2 style="color: #1B4F72;">Reset your BankScan AI password</h2>
        <p>Someone (hopefully you) asked to reset the password for this account.
        Use the button below to choose a new one:</p>
        <div style="text-align: center; margin: 1.5rem 0;">
            <a href="{reset_link}"
               style="display: inline-block; padding: 0.75rem 1.5rem; background: #1B4F72; color: white; text-decoration: none; border-radius: 6px; font-weight: 600;">
                Choose a new password
            </a>
        </div>
        <p style="color: #666;">This link expires in <strong>30 minutes</strong>.</p>
        <p style="color: #666;">If you didn't ask for this, you can safely ignore this email — nothing has changed.</p>
        <hr style="border: none; border-top: 1px solid #eee; margin: 1.5rem 0;">
        <p style="color: #999; font-size: 0.85rem;">BankScan AI — AI-powered bank statement &amp; receipt intelligence</p>
    </div>
    """
    text_body = (
        "Reset your BankScan AI password\n\n"
        f"Click this link to choose a new password (expires in 30 minutes):\n{reset_link}\n\n"
        "If you didn't ask for this, ignore the email — nothing has changed."
    )

    try:
        response = httpx.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            },
            timeout=RESEND_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.exception("Password reset email failed for %s", to_email)
        return False

    if response.status_code >= 400:
        logger.error(
            "Resend rejected password reset to %s — status=%d body=%s",
            to_email, response.status_code, response.text[:500],
        )
        return False

    try:
        message_id = response.json().get("id", "<no-id>")
    except ValueError:
        message_id = "<unparseable-json>"
    logger.info("Password reset email sent to %s via Resend (id=%s)", to_email, message_id)
    return True
