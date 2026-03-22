"""
BankParse — Database Layer
Uses Turso (libSQL) for persistent cloud storage when TURSO_DATABASE_URL is set.
Falls back to local SQLite for development.
"""

import os
import time
import logging
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger("bankparse.db")

# --- Connection setup ---

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

_connection = None


def get_connection():
    """Get or create database connection. Uses Turso if configured, else local SQLite."""
    global _connection
    if _connection is not None:
        return _connection

    if TURSO_URL and TURSO_TOKEN:
        # Turso (libSQL) — persistent cloud database
        connected = False

        # Try libsql_experimental (embedded replica + sync)
        if not connected:
            try:
                import libsql_experimental as libsql
                db_file = "/tmp/bankparse.db" if os.environ.get("VERCEL") else "bankparse.db"
                _connection = libsql.connect(db_file, sync_url=TURSO_URL, auth_token=TURSO_TOKEN)
                _connection.sync()
                connected = True
                logger.info("Connected to Turso (libsql_experimental)")
            except (ImportError, Exception) as e:
                logger.warning("libsql_experimental failed: %s", e)

        # Fallback: local SQLite (data won't persist on Vercel but app won't crash)
        if not connected:
            import sqlite3
            db_path = "/tmp/bankparse.db" if os.environ.get("VERCEL") else str(Path(__file__).parent / "bankparse.db")
            _connection = sqlite3.connect(db_path, timeout=10)
            _connection.execute("PRAGMA journal_mode=WAL")
            logger.warning("Using local SQLite fallback: %s", db_path)
    else:
        # Local SQLite fallback for development
        import sqlite3
        db_path = str(Path(__file__).parent / "bankparse.db")
        _connection = sqlite3.connect(db_path, timeout=10)
        _connection.execute("PRAGMA journal_mode=WAL")
        logger.info("Using local SQLite: %s", db_path)

    _connection.execute("PRAGMA foreign_keys=ON")
    return _connection


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
        # Sync to Turso after writes
        if TURSO_URL and TURSO_TOKEN:
            try:
                conn.sync()
            except Exception:
                pass
    except Exception:
        conn.rollback()
        raise


def _row_to_dict(cursor, row) -> dict:
    """Convert a row to a dict using cursor description."""
    if row is None:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def _fetchone_dict(conn, sql, params=()) -> dict | None:
    """Execute a query and return the first row as a dict."""
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    return _row_to_dict(cursor, row)


def _fetchall_dicts(conn, sql, params=()) -> list[dict]:
    """Execute a query and return all rows as dicts."""
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall()
    if not rows:
        return []
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in rows]


# --- Schema ---

def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    statements = [
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            stripe_customer_id TEXT,
            statements_used INTEGER DEFAULT 0,
            receipts_used INTEGER DEFAULT 0,
            subscription_status TEXT DEFAULT NULL,
            subscription_checked_at REAL DEFAULT NULL,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        )""",
        """CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            statements_used INTEGER DEFAULT 0,
            receipts_used INTEGER DEFAULT 0,
            stripe_customer_id TEXT,
            email TEXT,
            created_at REAL DEFAULT (strftime('%s', 'now')),
            updated_at REAL DEFAULT (strftime('%s', 'now'))
        )""",
        """CREATE TABLE IF NOT EXISTS otp_codes (
            email TEXT NOT NULL,
            code TEXT NOT NULL,
            session_id TEXT NOT NULL,
            created_at REAL DEFAULT (strftime('%s', 'now')),
            used INTEGER DEFAULT 0,
            PRIMARY KEY (email, code)
        )""",
        """CREATE TABLE IF NOT EXISTS output_files (
            filename TEXT PRIMARY KEY,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        "CREATE INDEX IF NOT EXISTS idx_users_stripe ON users(stripe_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_stripe ON sessions(stripe_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_codes(email)",
        "CREATE INDEX IF NOT EXISTS idx_output_created ON output_files(created_at)",
    ]
    for stmt in statements:
        conn.execute(stmt)
    conn.commit()
    if TURSO_URL and TURSO_TOKEN:
        try:
            conn.sync()
        except Exception:
            pass
    logger.info("Database schema initialized")


# --- User functions ---

def create_user(email: str, password_hash: str) -> int:
    """Insert a new user, return their id."""
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, password_hash)
        )
        return cursor.lastrowid


def get_user_by_email(email: str) -> dict | None:
    """Return user row as dict, or None."""
    with get_db() as conn:
        return _fetchone_dict(conn,
            "SELECT id, email, password_hash, stripe_customer_id, statements_used, receipts_used, subscription_status, subscription_checked_at, created_at FROM users WHERE email = ?",
            (email,)
        )


def get_user_by_id(user_id: int) -> dict | None:
    """Return user row as dict, or None."""
    with get_db() as conn:
        return _fetchone_dict(conn,
            "SELECT id, email, password_hash, stripe_customer_id, statements_used, receipts_used, subscription_status, subscription_checked_at, created_at FROM users WHERE id = ?",
            (user_id,)
        )


def get_user_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    """Return user by Stripe customer ID."""
    if not stripe_customer_id:
        return None
    with get_db() as conn:
        return _fetchone_dict(conn,
            "SELECT id, email, password_hash, stripe_customer_id, statements_used, receipts_used, subscription_status, subscription_checked_at, created_at FROM users WHERE stripe_customer_id = ?",
            (stripe_customer_id,)
        )


def update_user(user_id: int, **kwargs):
    """Update user fields. Only allowed fields are updated."""
    allowed = {"stripe_customer_id", "statements_used", "receipts_used", "email", "password_hash", "subscription_status", "subscription_checked_at"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)


def increment_user_usage(user_id: int, mode: str):
    """Increment statements_used or receipts_used for a user."""
    column = "statements_used" if mode == "statement" else "receipts_used"
    with get_db() as conn:
        conn.execute(
            f"UPDATE users SET {column} = {column} + 1 WHERE id = ?",
            (user_id,)
        )


# --- Session functions (legacy, used by restore flow) ---

def get_usage(session_id: str) -> dict:
    if not session_id:
        return {"statements": 0, "receipts": 0, "stripe_customer_id": None, "email": None}
    with get_db() as conn:
        row = _fetchone_dict(conn,
            "SELECT statements_used, receipts_used, stripe_customer_id, email FROM sessions WHERE session_id = ?",
            (session_id,)
        )
    if row:
        return {
            "statements": row["statements_used"],
            "receipts": row["receipts_used"],
            "stripe_customer_id": row["stripe_customer_id"],
            "email": row["email"],
        }
    return {"statements": 0, "receipts": 0, "stripe_customer_id": None, "email": None}


def save_usage(session_id: str, usage: dict):
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
    with get_db() as conn:
        conn.execute("DELETE FROM otp_codes WHERE email = ?", (email,))
        conn.execute(
            "INSERT INTO otp_codes (email, code, session_id) VALUES (?, ?, ?)",
            (email, code, session_id)
        )


def verify_otp(email: str, code: str) -> str | None:
    with get_db() as conn:
        row = _fetchone_dict(conn,
            "SELECT session_id, created_at FROM otp_codes WHERE email = ? AND code = ? AND used = 0",
            (email, code)
        )
        if not row:
            return None
        if time.time() - row["created_at"] > 600:
            conn.execute("DELETE FROM otp_codes WHERE email = ? AND code = ?", (email, code))
            return None
        conn.execute("UPDATE otp_codes SET used = 1 WHERE email = ? AND code = ?", (email, code))
        return row["session_id"]


def cleanup_expired_otps():
    cutoff = time.time() - 600
    with get_db() as conn:
        conn.execute("DELETE FROM otp_codes WHERE created_at < ? OR used = 1", (cutoff,))


# --- Output file tracking ---

def track_output_file(filename: str):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO output_files (filename, created_at) VALUES (?, strftime('%s', 'now'))",
            (filename,)
        )


def get_stale_output_files(max_age_seconds: int = 3600) -> list[str]:
    cutoff = time.time() - max_age_seconds
    with get_db() as conn:
        rows = _fetchall_dicts(conn,
            "SELECT filename FROM output_files WHERE created_at < ?",
            (cutoff,)
        )
    return [row["filename"] for row in rows]


def remove_output_file_record(filename: str):
    with get_db() as conn:
        conn.execute("DELETE FROM output_files WHERE filename = ?", (filename,))


# Initialize on import
init_db()
