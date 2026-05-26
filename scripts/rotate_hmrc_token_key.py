"""
Re-encrypt every HMRC token blob under the current active key.

Used during AES key rotation. The pattern:

  1. Generate a new key:
        NEW=$(python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())")
  2. On Railway, set:
        HMRC_TOKEN_ENCRYPTION_KEY_OLD = <the current active value>
        HMRC_TOKEN_ENCRYPTION_KEY     = $NEW
     and redeploy. Decrypt now falls back to the old key for any blob
     still encrypted with it; encrypt uses the new key.
  3. Run THIS SCRIPT against prod (Railway shell). It walks every row in
     hmrc_connections, decrypts each token field, and re-encrypts under
     the new active key. The on-disk blob is replaced in place.
  4. Once the script completes cleanly, REMOVE
     HMRC_TOKEN_ENCRYPTION_KEY_OLD from Railway and redeploy. Old key
     can be safely deleted from your password manager.

Read-modify-write is wrapped in per-row transactions — if the script
dies halfway, you can re-run it. Already-rotated rows decrypt fine
under the active key on the first try; un-rotated rows go through the
fallback. Either way the script normalises everything.

Run against the SAME database the app uses. On Railway:

    DATABASE_PATH=/data/bankparse.db \
      python3 scripts/rotate_hmrc_token_key.py

Use --dry-run to count rows without writing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the project importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rotate_hmrc_token_key")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count rows that would be touched, don't write.",
    )
    args = parser.parse_args()

    # Late-import so the script can be linted without app deps available.
    from hmrc.services import crypto as _crypto

    # Sanity: refuse to run unless an OLD key is configured. Otherwise
    # you're either (a) about to no-op or (b) doing something destructive
    # without a fallback to roll back to.
    import os
    if not args.dry_run and not os.environ.get("HMRC_TOKEN_ENCRYPTION_KEY_OLD"):
        logger.error(
            "HMRC_TOKEN_ENCRYPTION_KEY_OLD is not set. Set it to the "
            "previously-active key before running rotation. Aborting."
        )
        return 2

    from database import _conn

    conn = _conn()
    cursor = conn.cursor()
    # Fetch every row + the two token columns. Column names may differ —
    # adjust to match your schema if you renamed them.
    cursor.execute(
        "SELECT user_id, access_token, refresh_token FROM hmrc_connections "
        "WHERE access_token IS NOT NULL OR refresh_token IS NOT NULL"
    )
    rows = cursor.fetchall()
    logger.info("Found %d rows with stored tokens", len(rows))

    if args.dry_run:
        logger.info("Dry run — no writes. Exiting.")
        return 0

    rotated = 0
    failed = 0
    for row in rows:
        user_id = row["user_id"]
        try:
            new_access = (
                _crypto.encrypt(_crypto.decrypt(row["access_token"]))
                if row["access_token"] else None
            )
            new_refresh = (
                _crypto.encrypt(_crypto.decrypt(row["refresh_token"]))
                if row["refresh_token"] else None
            )
            cursor.execute(
                "UPDATE hmrc_connections SET access_token = ?, "
                "refresh_token = ? WHERE user_id = ?",
                (new_access, new_refresh, user_id),
            )
            conn.commit()
            rotated += 1
        except Exception as e:
            logger.exception("Failed to rotate user_id=%s: %s", user_id, e)
            failed += 1
            conn.rollback()

    logger.info("Rotation complete: %d rotated, %d failed", rotated, failed)

    if failed:
        logger.warning(
            "%d rows failed — investigate before clearing "
            "HMRC_TOKEN_ENCRYPTION_KEY_OLD.", failed,
        )
        return 1
    logger.info(
        "All rows re-encrypted under the active key. "
        "You can now REMOVE HMRC_TOKEN_ENCRYPTION_KEY_OLD from the env."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
