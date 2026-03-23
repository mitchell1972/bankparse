"""
BankParse — Database Layer
Uses Turso HTTP API for persistent cloud storage when TURSO_DATABASE_URL is set.
Falls back to local SQLite for development. Zero native dependencies for Vercel.
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
USE_TURSO = bool(TURSO_URL and TURSO_TOKEN)

_turso_client = None
_sqlite_conn = None


def _get_turso():
    """Get or create the Turso HTTP client."""
    global _turso_client
    if _turso_client is None:
        from turso_http import TursoHTTPClient
        _turso_client = TursoHTTPClient(TURSO_URL, TURSO_TOKEN)
        logger.info("Connected to Turso HTTP API")
    return _turso_client


def _get_sqlite():
    """Get or create a local SQLite connection."""
    global _sqlite_conn
    if _sqlite_conn is None:
        import sqlite3
        db_path = str(Path(__file__).parent / "bankparse.db")
        _sqlite_conn = sqlite3.connect(db_path, timeout=10)
        _sqlite_conn.execute("PRAGMA journal_mode=WAL")
        _sqlite_conn.execute("PRAGMA foreign_keys=ON")
        logger.info("Using local SQLite: %s", db_path)
    return _sqlite_conn


# --- Unified query helpers ---

def _execute(sql: str, params: tuple = ()):
    """Execute a statement, return result."""
    if USE_TURSO:
        return _get_turso().execute(sql, params)
    else:
        conn = _get_sqlite()
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor


def _fetchone_dict(sql: str, params: tuple = ()) -> dict | None:
    """Execute and return the first row as a dict."""
    if USE_TURSO:
        result = _get_turso().execute(sql, params)
        row = result.fetchone()
        return row.to_dict() if row else None
    else:
        conn = _get_sqlite()
        cursor = conn.execute(sql, params)
        row = cursor.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))


def _fetchall_dicts(sql: str, params: tuple = ()) -> list[dict]:
    """Execute and return all rows as dicts."""
    if USE_TURSO:
        result = _get_turso().execute(sql, params)
        return [row.to_dict() for row in result.fetchall()]
    else:
        conn = _get_sqlite()
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        if not rows:
            return []
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]


def _execute_insert(sql: str, params: tuple = ()) -> int:
    """Execute an INSERT and return last_insert_rowid."""
    if USE_TURSO:
        result = _get_turso().execute(sql, params)
        return result.last_insert_rowid or 0
    else:
        conn = _get_sqlite()
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid


_USER_COLS = "id, email, password_hash, stripe_customer_id, statements_used, receipts_used, subscription_status, subscription_checked_at, chat_count, chat_date, created_at"


# --- Schema ---

def init_db():
    """Create tables if they don't exist."""
    stmts = [
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            stripe_customer_id TEXT,
            statements_used INTEGER DEFAULT 0,
            receipts_used INTEGER DEFAULT 0,
            subscription_status TEXT DEFAULT NULL,
            subscription_checked_at REAL DEFAULT NULL,
            chat_count INTEGER DEFAULT 0,
            chat_date TEXT DEFAULT NULL,
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
        """CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT PRIMARY KEY,
            window_start REAL NOT NULL,
            count INTEGER NOT NULL DEFAULT 0
        )""",
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        "CREATE INDEX IF NOT EXISTS idx_users_stripe ON users(stripe_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_stripe ON sessions(stripe_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_codes(email)",
        "CREATE INDEX IF NOT EXISTS idx_output_created ON output_files(created_at)",
    ]
    for stmt in stmts:
        _execute(stmt)

    # Migrate: add chat_count and chat_date columns if missing (existing databases)
    for col, col_def in [("chat_count", "INTEGER DEFAULT 0"), ("chat_date", "TEXT DEFAULT NULL")]:
        try:
            _execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
        except Exception:
            pass  # Column already exists

    logger.info("Database schema initialized")


# --- User functions ---

def create_user(email: str, password_hash: str) -> int:
    return _execute_insert("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash))


def get_user_by_email(email: str) -> dict | None:
    return _fetchone_dict(f"SELECT {_USER_COLS} FROM users WHERE email = ?", (email,))


def get_user_by_id(user_id: int) -> dict | None:
    return _fetchone_dict(f"SELECT {_USER_COLS} FROM users WHERE id = ?", (user_id,))


def get_user_by_stripe_customer(stripe_customer_id: str) -> dict | None:
    if not stripe_customer_id:
        return None
    return _fetchone_dict(f"SELECT {_USER_COLS} FROM users WHERE stripe_customer_id = ?", (stripe_customer_id,))


def update_user(user_id: int, **kwargs):
    allowed = {"stripe_customer_id", "statements_used", "receipts_used", "email", "password_hash", "subscription_status", "subscription_checked_at", "chat_count", "chat_date"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = tuple(fields.values()) + (user_id,)
    _execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)


def increment_user_usage(user_id: int, mode: str):
    column = "statements_used" if mode == "statement" else "receipts_used"
    _execute(f"UPDATE users SET {column} = {column} + 1 WHERE id = ?", (user_id,))


def get_chat_usage(user_id: int) -> int:
    """Return the number of chat messages used today. Resets if chat_date is not today."""
    import datetime
    row = _fetchone_dict("SELECT chat_count, chat_date FROM users WHERE id = ?", (user_id,))
    if not row:
        return 0
    today = datetime.date.today().isoformat()
    if row.get("chat_date") != today:
        return 0
    return row.get("chat_count", 0) or 0


def increment_chat_usage(user_id: int):
    """Increment the daily chat counter. Resets to 1 if the date has changed."""
    import datetime
    today = datetime.date.today().isoformat()
    row = _fetchone_dict("SELECT chat_date FROM users WHERE id = ?", (user_id,))
    if row and row.get("chat_date") == today:
        _execute("UPDATE users SET chat_count = chat_count + 1 WHERE id = ?", (user_id,))
    else:
        _execute("UPDATE users SET chat_count = 1, chat_date = ? WHERE id = ?", (today, user_id))


# --- Session functions (legacy, used by restore flow) ---

def get_usage(session_id: str) -> dict:
    if not session_id:
        return {"statements": 0, "receipts": 0, "stripe_customer_id": None, "email": None}
    row = _fetchone_dict(
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
    _execute("""
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
    _execute(f"""
        INSERT INTO sessions (session_id, {column})
        VALUES (?, 1)
        ON CONFLICT(session_id) DO UPDATE SET
            {column} = {column} + 1,
            updated_at = strftime('%s', 'now')
    """, (session_id,))


# --- OTP functions ---

def store_otp(email: str, code: str, session_id: str):
    _execute("DELETE FROM otp_codes WHERE email = ?", (email,))
    _execute("INSERT INTO otp_codes (email, code, session_id) VALUES (?, ?, ?)", (email, code, session_id))


def verify_otp(email: str, code: str) -> str | None:
    row = _fetchone_dict(
        "SELECT session_id, created_at FROM otp_codes WHERE email = ? AND code = ? AND used = 0",
        (email, code)
    )
    if not row:
        return None
    if time.time() - row["created_at"] > 600:
        _execute("DELETE FROM otp_codes WHERE email = ? AND code = ?", (email, code))
        return None
    _execute("UPDATE otp_codes SET used = 1 WHERE email = ? AND code = ?", (email, code))
    return row["session_id"]


def cleanup_expired_otps():
    _execute("DELETE FROM otp_codes WHERE created_at < ? OR used = 1", (time.time() - 600,))


# --- Output file tracking ---

def track_output_file(filename: str):
    _execute("INSERT OR REPLACE INTO output_files (filename, created_at) VALUES (?, strftime('%s', 'now'))", (filename,))


def get_stale_output_files(max_age_seconds: int = 3600) -> list[str]:
    rows = _fetchall_dicts("SELECT filename FROM output_files WHERE created_at < ?", (time.time() - max_age_seconds,))
    return [row["filename"] for row in rows]


def remove_output_file_record(filename: str):
    _execute("DELETE FROM output_files WHERE filename = ?", (filename,))


# Initialize on import
init_db()
