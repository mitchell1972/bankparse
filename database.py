"""
BankParse — SQLite Database Layer
Replaces file-based JSON usage tracking with SQLite for durability and concurrency.
"""

import sqlite3
import os
import time
import threading
from pathlib import Path
from contextlib import contextmanager

def _default_db_path() -> str:
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "/tmp/bankparse.db"
    return str(Path(__file__).parent / "bankparse.db")

DB_PATH = os.environ.get("BANKPARSE_DB_PATH", _default_db_path())

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    if not hasattr(_local, "connection") or _local.connection is None:
        _local.connection = sqlite3.connect(DB_PATH, timeout=10)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA foreign_keys=ON")
    return _local.connection


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                statements_used INTEGER DEFAULT 0,
                receipts_used INTEGER DEFAULT 0,
                stripe_customer_id TEXT,
                email TEXT,
                created_at REAL DEFAULT (strftime('%s', 'now')),
                updated_at REAL DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS otp_codes (
                email TEXT NOT NULL,
                code TEXT NOT NULL,
                session_id TEXT NOT NULL,
                created_at REAL DEFAULT (strftime('%s', 'now')),
                used INTEGER DEFAULT 0,
                PRIMARY KEY (email, code)
            );

            CREATE TABLE IF NOT EXISTS output_files (
                filename TEXT PRIMARY KEY,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email);
            CREATE INDEX IF NOT EXISTS idx_sessions_stripe ON sessions(stripe_customer_id);
            CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_codes(email);
            CREATE INDEX IF NOT EXISTS idx_output_created ON output_files(created_at);
        """)


def get_usage(session_id: str) -> dict:
    """Get usage data for a session. Returns dict matching the old format."""
    if not session_id:
        return {"statements": 0, "receipts": 0, "stripe_customer_id": None, "email": None}
    with get_db() as conn:
        row = conn.execute(
            "SELECT statements_used, receipts_used, stripe_customer_id, email FROM sessions WHERE session_id = ?",
            (session_id,)
        ).fetchone()
    if row:
        return {
            "statements": row["statements_used"],
            "receipts": row["receipts_used"],
            "stripe_customer_id": row["stripe_customer_id"],
            "email": row["email"],
        }
    return {"statements": 0, "receipts": 0, "stripe_customer_id": None, "email": None}


def save_usage(session_id: str, usage: dict):
    """Save usage data for a session (upsert)."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO sessions (session_id, statements_used, receipts_used, stripe_customer_id, email, updated_at)
            VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))
            ON CONFLICT(session_id) DO UPDATE SET
                statements_used = excluded.statements_used,
                receipts_used = excluded.receipts_used,
                stripe_customer_id = excluded.stripe_customer_id,
                email = excluded.email,
                updated_at = strftime('%s', 'now')
        """, (session_id, usage["statements"], usage["receipts"], usage.get("stripe_customer_id"), usage.get("email")))


def increment_usage(session_id: str, mode: str):
    """Atomically increment usage counter."""
    column = "statements_used" if mode == "statement" else "receipts_used"
    with get_db() as conn:
        conn.execute(f"""
            INSERT INTO sessions (session_id, {column})
            VALUES (?, 1)
            ON CONFLICT(session_id) DO UPDATE SET
                {column} = {column} + 1,
                updated_at = strftime('%s', 'now')
        """, (session_id,))


# --- OTP functions ---

def store_otp(email: str, code: str, session_id: str):
    """Store an OTP code for email verification. Invalidates previous codes for this email."""
    with get_db() as conn:
        conn.execute("DELETE FROM otp_codes WHERE email = ?", (email,))
        conn.execute(
            "INSERT INTO otp_codes (email, code, session_id) VALUES (?, ?, ?)",
            (email, code, session_id)
        )


def verify_otp(email: str, code: str) -> str | None:
    """Verify an OTP code. Returns session_id if valid, None if invalid/expired.
    OTP expires after 10 minutes. Single use."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT session_id, created_at FROM otp_codes WHERE email = ? AND code = ? AND used = 0",
            (email, code)
        ).fetchone()
        if not row:
            return None
        if time.time() - row["created_at"] > 600:  # 10 min expiry
            conn.execute("DELETE FROM otp_codes WHERE email = ? AND code = ?", (email, code))
            return None
        conn.execute("UPDATE otp_codes SET used = 1 WHERE email = ? AND code = ?", (email, code))
        return row["session_id"]


def cleanup_expired_otps():
    """Remove expired and used OTP codes."""
    cutoff = time.time() - 600
    with get_db() as conn:
        conn.execute("DELETE FROM otp_codes WHERE created_at < ? OR used = 1", (cutoff,))


# --- Output file tracking ---

def track_output_file(filename: str):
    """Track an output file for cleanup."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO output_files (filename, created_at) VALUES (?, strftime('%s', 'now'))",
            (filename,)
        )


def get_stale_output_files(max_age_seconds: int = 3600) -> list[str]:
    """Get output files older than max_age_seconds."""
    cutoff = time.time() - max_age_seconds
    with get_db() as conn:
        rows = conn.execute(
            "SELECT filename FROM output_files WHERE created_at < ?",
            (cutoff,)
        ).fetchall()
    return [row["filename"] for row in rows]


def remove_output_file_record(filename: str):
    """Remove an output file record after deletion."""
    with get_db() as conn:
        conn.execute("DELETE FROM output_files WHERE filename = ?", (filename,))


# Initialize on import
init_db()
