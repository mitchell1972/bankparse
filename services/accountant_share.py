"""
Generate + validate shareable accountant-pack download links.

The user clicks "Email pack to my accountant", types the accountant's
email, hits send. Behind the scenes:

  1. We build the ZIP just like the existing download path does.
  2. We mint a 32-char URL-safe token, store it in
     accountant_pack_shares with a 60-day default expiry.
  3. We send the accountant an email containing the link.
  4. The accountant clicks the link → /share/accountant-pack/{token} →
     they get a nice landing page with key totals + a Download button.
  5. Every download bumps download_count + last_downloaded_at so the
     user can see if/when the accountant actually opened it.

The token IS the auth — anyone holding the URL can download until
expiry or revoke. We never expose the user's account credentials.
"""
from __future__ import annotations

import secrets
import time
from typing import Iterable

import database


DEFAULT_EXPIRY_DAYS = 60


def mint_token() -> str:
    """A URL-safe random token with ~192 bits of entropy. Long enough that
    brute-forcing the link space is computationally infeasible — the
    accountant could share the link safely over normal email."""
    return secrets.token_urlsafe(24)


def create_share(
    *,
    user_id: int,
    period_label: str | None,
    client_name: str | None,
    accountant_email: str | None,
    accountant_name: str | None,
    expiry_days: int = DEFAULT_EXPIRY_DAYS,
) -> dict:
    """Mint a token + persist the share row. Returns the inserted row
    (re-fetched so the caller gets exact stored values)."""
    token = mint_token()
    expires_at = time.time() + (max(1, int(expiry_days)) * 86400)
    share_id = database.create_accountant_pack_share(
        user_id=user_id,
        token=token,
        period_label=period_label,
        client_name=client_name,
        accountant_email=accountant_email,
        accountant_name=accountant_name,
        expires_at=expires_at,
    )
    return database.get_accountant_pack_share_by_token(token) or {"id": share_id, "token": token}


def resolve_share(token: str) -> dict | None:
    """Look up a share by token, returning it ONLY if it's still active.
    Returns None for missing, expired, or revoked shares."""
    if not token or len(token) < 16:
        return None
    row = database.get_accountant_pack_share_by_token(token)
    if not row:
        return None
    if row.get("revoked_at"):
        return None
    if float(row.get("expires_at") or 0) < time.time():
        return None
    return row


def record_download(share_id: int) -> None:
    database.mark_accountant_pack_share_downloaded(share_id)
