"""
Encrypted token storage for HMRC OAuth connections.

Schema (see `hmrc_connections` table created in database.py):
    user_id              INTEGER PRIMARY KEY  -- one HMRC connection per BankParse user
    access_token_enc     TEXT      AES-GCM(plaintext); short-lived (4h)
    refresh_token_enc    TEXT      AES-GCM(plaintext); rotates on every refresh
    expires_at           REAL      unix epoch — when access_token expires
    scope                TEXT      granted scopes (e.g. 'read:self-assessment write:self-assessment')
    connected_at         REAL
    updated_at           REAL
    nino_enc             TEXT      AES-GCM(NINO); set after user provides NINO
    businesses_json      TEXT      JSON list of {business_id, type_of_business, label}

`access_token_enc`, `refresh_token_enc`, and `nino_enc` all go through
hmrc.services.crypto before write and after read. The DB never sees plaintext
for any of these PII fields.
"""

from __future__ import annotations

import json
import time

from ..services import crypto as _crypto


def save_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    expires_in_seconds: int,
    scope: str,
) -> None:
    """Encrypt and upsert this user's HMRC tokens.

    Idempotent — calling twice with the same tokens overwrites with new ones.
    """
    from database import _execute

    now = time.time()
    expires_at = now + float(expires_in_seconds)
    _execute(
        """
        INSERT INTO hmrc_connections
          (user_id, access_token_enc, refresh_token_enc, expires_at, scope, connected_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          access_token_enc = excluded.access_token_enc,
          refresh_token_enc = excluded.refresh_token_enc,
          expires_at = excluded.expires_at,
          scope = excluded.scope,
          updated_at = excluded.updated_at
        """,
        (
            user_id,
            _crypto.encrypt(access_token),
            _crypto.encrypt(refresh_token),
            expires_at,
            scope,
            now,
            now,
        ),
    )


def get_tokens(user_id: int) -> dict | None:
    """Fetch + decrypt the user's HMRC tokens. Returns None if not connected."""
    from database import _fetchone_dict

    row = _fetchone_dict(
        "SELECT access_token_enc, refresh_token_enc, expires_at, scope, connected_at, "
        "       nino_enc, businesses_json "
        "FROM hmrc_connections WHERE user_id = ?",
        (user_id,),
    )
    if not row:
        return None
    return {
        "access_token": _crypto.decrypt(row["access_token_enc"]),
        "refresh_token": _crypto.decrypt(row["refresh_token_enc"]),
        "expires_at": float(row["expires_at"]),
        "scope": row["scope"],
        "connected_at": float(row["connected_at"]),
        # NINO + businesses may be NULL until the user provides them.
        "nino": _crypto.decrypt(row["nino_enc"]) if row.get("nino_enc") else None,
        "businesses": _parse_businesses(row.get("businesses_json")),
    }


def save_nino_and_businesses(
    user_id: int, nino: str, businesses: list[dict],
) -> None:
    """Persist the user's NINO + the list of HMRC businesses we'll submit for.

    `businesses` is a list of dicts like:
        [{"business_id": "XAIS12345678901", "type_of_business": "self-employment",
          "label": "Mitoba Property Services"}, ...]
    """
    from database import _execute
    _execute(
        "UPDATE hmrc_connections "
        "SET nino_enc = ?, businesses_json = ?, updated_at = ? "
        "WHERE user_id = ?",
        (
            _crypto.encrypt(nino),
            json.dumps(businesses or []),
            time.time(),
            user_id,
        ),
    )


def revoke(user_id: int) -> None:
    """Forget a user's HMRC connection. Doesn't notify HMRC."""
    from database import _execute
    _execute("DELETE FROM hmrc_connections WHERE user_id = ?", (user_id,))


def _parse_businesses(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        return json.loads(raw) or []
    except (TypeError, ValueError):
        return []
