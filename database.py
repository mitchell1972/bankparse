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


_USER_COLS = "id, email, password_hash, stripe_customer_id, statements_used, receipts_used, subscription_status, subscription_checked_at, chat_count, chat_date, scans_this_month, scan_month, statements_this_month, receipts_this_month, usage_month, email_verified, ai_credit_balance_gbp, ai_spend_this_month, created_at"


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
            scans_this_month INTEGER DEFAULT 0,
            scan_month TEXT DEFAULT NULL,
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
        """CREATE TABLE IF NOT EXISTS ai_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            mode TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_gbp REAL NOT NULL DEFAULT 0,
            success INTEGER NOT NULL DEFAULT 1,
            usage_day TEXT NOT NULL,
            usage_month TEXT NOT NULL,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
        "CREATE INDEX IF NOT EXISTS idx_users_stripe ON users(stripe_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_stripe ON sessions(stripe_customer_id)",
        "CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_codes(email)",
        "CREATE INDEX IF NOT EXISTS idx_output_created ON output_files(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_ai_usage_user ON ai_usage_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_ai_usage_day ON ai_usage_log(usage_day)",
        "CREATE INDEX IF NOT EXISTS idx_ai_usage_user_day ON ai_usage_log(user_id, usage_day)",
        "CREATE INDEX IF NOT EXISTS idx_ai_usage_user_month ON ai_usage_log(user_id, usage_month)",
    ]
    for stmt in stmts:
        _execute(stmt)

    # Migrate: add columns if missing (existing databases)
    for col, col_def in [
        ("chat_count", "INTEGER DEFAULT 0"),
        ("chat_date", "TEXT DEFAULT NULL"),
        ("scans_this_month", "INTEGER DEFAULT 0"),
        ("scan_month", "TEXT DEFAULT NULL"),
        ("statements_this_month", "INTEGER DEFAULT 0"),
        ("receipts_this_month", "INTEGER DEFAULT 0"),
        ("usage_month", "TEXT DEFAULT NULL"),
        ("email_verified", "INTEGER DEFAULT 0"),
        ("ai_credit_balance_gbp", "REAL DEFAULT 0"),
        ("ai_spend_this_month", "REAL DEFAULT 0"),
    ]:
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
    allowed = {"stripe_customer_id", "statements_used", "receipts_used", "email", "password_hash", "subscription_status", "subscription_checked_at", "chat_count", "chat_date", "scans_this_month", "scan_month", "statements_this_month", "receipts_this_month", "usage_month", "email_verified", "ai_credit_balance_gbp", "ai_spend_this_month"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = tuple(fields.values()) + (user_id,)
    _execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)


def delete_user(user_id: int):
    _execute("DELETE FROM users WHERE id = ?", (user_id,))


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


def get_monthly_scans(user_id: int) -> int:
    """Return the number of scans used this calendar month. Resets if scan_month != current month."""
    import datetime
    row = _fetchone_dict("SELECT scans_this_month, scan_month FROM users WHERE id = ?", (user_id,))
    if not row:
        return 0
    current_month = datetime.date.today().strftime("%Y-%m")
    if row.get("scan_month") != current_month:
        return 0
    return row.get("scans_this_month", 0) or 0


def increment_monthly_scans(user_id: int, count: int = 1):
    """Increment the monthly scan counter. Resets to count if the month has changed."""
    import datetime
    current_month = datetime.date.today().strftime("%Y-%m")
    row = _fetchone_dict("SELECT scan_month FROM users WHERE id = ?", (user_id,))
    if row and row.get("scan_month") == current_month:
        _execute("UPDATE users SET scans_this_month = scans_this_month + ? WHERE id = ?", (count, user_id))
    else:
        _execute("UPDATE users SET scans_this_month = ?, scan_month = ? WHERE id = ?", (count, current_month, user_id))


def get_monthly_statements(user_id: int) -> int:
    """Return statements uploaded this calendar month."""
    import datetime
    row = _fetchone_dict("SELECT statements_this_month, usage_month FROM users WHERE id = ?", (user_id,))
    if not row:
        return 0
    current_month = datetime.date.today().strftime("%Y-%m")
    if row.get("usage_month") != current_month:
        return 0
    return row.get("statements_this_month", 0) or 0


def get_monthly_receipts(user_id: int) -> int:
    """Return receipts uploaded this calendar month."""
    import datetime
    row = _fetchone_dict("SELECT receipts_this_month, usage_month FROM users WHERE id = ?", (user_id,))
    if not row:
        return 0
    current_month = datetime.date.today().strftime("%Y-%m")
    if row.get("usage_month") != current_month:
        return 0
    return row.get("receipts_this_month", 0) or 0


def increment_monthly_statements(user_id: int, count: int = 1):
    """Increment monthly statement counter. Resets if month changed."""
    import datetime
    current_month = datetime.date.today().strftime("%Y-%m")
    row = _fetchone_dict("SELECT usage_month FROM users WHERE id = ?", (user_id,))
    if row and row.get("usage_month") == current_month:
        _execute("UPDATE users SET statements_this_month = statements_this_month + ? WHERE id = ?", (count, user_id))
    else:
        _execute("UPDATE users SET statements_this_month = ?, receipts_this_month = 0, usage_month = ? WHERE id = ?", (count, current_month, user_id))


def increment_monthly_receipts(user_id: int, count: int = 1):
    """Increment monthly receipt counter. Resets if month changed."""
    import datetime
    current_month = datetime.date.today().strftime("%Y-%m")
    row = _fetchone_dict("SELECT usage_month FROM users WHERE id = ?", (user_id,))
    if row and row.get("usage_month") == current_month:
        _execute("UPDATE users SET receipts_this_month = receipts_this_month + ? WHERE id = ?", (count, user_id))
    else:
        _execute("UPDATE users SET receipts_this_month = ?, statements_this_month = 0, usage_month = ? WHERE id = ?", (count, current_month, user_id))


# --- AI spend + usage log functions ---
#
# Every billable AI call must be recorded here. The log is the authoritative
# source for daily / monthly spend queries; the `ai_spend_this_month` column
# on users is a fast running-total cache reset each calendar month.

def log_ai_usage(
    user_id: int | None,
    mode: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_gbp: float,
    success: bool = True,
) -> int:
    """Append a row to ``ai_usage_log``. Returns the new row ID.

    ``user_id`` may be None for anonymous/session calls (e.g. global caps only).
    ``mode`` should be 'receipt' or 'statement'. ``cost_gbp`` must come from
    ``ai_pricing.calculate_cost_gbp`` using the real post-call token counts.
    """
    import datetime
    now = datetime.datetime.utcnow()
    day = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m")
    return _execute_insert(
        """INSERT INTO ai_usage_log
           (user_id, mode, model, input_tokens, output_tokens, cost_gbp, success, usage_day, usage_month)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, mode, model, int(input_tokens), int(output_tokens), float(cost_gbp), 1 if success else 0, day, month),
    )


def get_monthly_ai_spend(user_id: int) -> float:
    """Return total AI spend (GBP) this calendar month for a user.

    Reads from the fast ``ai_spend_this_month`` column, resetting to 0 if the
    stored ``usage_month`` is not the current month. This is authoritative for
    budget checks in the hot path — log-sum queries are reserved for admin.
    """
    import datetime
    row = _fetchone_dict("SELECT ai_spend_this_month, usage_month FROM users WHERE id = ?", (user_id,))
    if not row:
        return 0.0
    current_month = datetime.date.today().strftime("%Y-%m")
    if row.get("usage_month") != current_month:
        return 0.0
    return float(row.get("ai_spend_this_month", 0) or 0)


def add_to_monthly_ai_spend(user_id: int, amount_gbp: float):
    """Increase the running monthly spend by ``amount_gbp``. Resets the
    counter (and ``statements_this_month`` / ``receipts_this_month``) if the
    month has rolled over."""
    import datetime
    current_month = datetime.date.today().strftime("%Y-%m")
    row = _fetchone_dict("SELECT usage_month FROM users WHERE id = ?", (user_id,))
    if row and row.get("usage_month") == current_month:
        _execute(
            "UPDATE users SET ai_spend_this_month = ai_spend_this_month + ? WHERE id = ?",
            (float(amount_gbp), user_id),
        )
    else:
        _execute(
            "UPDATE users SET ai_spend_this_month = ?, statements_this_month = 0, receipts_this_month = 0, usage_month = ? WHERE id = ?",
            (float(amount_gbp), current_month, user_id),
        )


def get_user_today_spend(user_id: int) -> float:
    """Return today's (UTC) total AI spend for a specific user from the log."""
    import datetime
    day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    row = _fetchone_dict(
        "SELECT COALESCE(SUM(cost_gbp), 0) AS total FROM ai_usage_log WHERE user_id = ? AND usage_day = ?",
        (user_id, day),
    )
    return float(row["total"]) if row and row.get("total") is not None else 0.0


def get_global_daily_ai_spend() -> float:
    """Return today's (UTC) total AI spend across ALL users from the log.
    Used for the global daily ceiling hard-fail."""
    import datetime
    day = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    row = _fetchone_dict(
        "SELECT COALESCE(SUM(cost_gbp), 0) AS total FROM ai_usage_log WHERE usage_day = ?",
        (day,),
    )
    return float(row["total"]) if row and row.get("total") is not None else 0.0


def get_recent_ai_usage(limit: int = 50) -> list[dict]:
    """Fetch the most recent AI usage rows. For the admin /admin/ai-spend endpoint."""
    return _fetchall_dicts(
        "SELECT id, user_id, mode, model, input_tokens, output_tokens, cost_gbp, success, usage_day, created_at FROM ai_usage_log ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )


# --- Credit balance (pre-purchased overage packs) ---

def get_credit_balance(user_id: int) -> float:
    """Return the user's pre-purchased AI credit balance in GBP."""
    row = _fetchone_dict("SELECT ai_credit_balance_gbp FROM users WHERE id = ?", (user_id,))
    if not row:
        return 0.0
    return float(row.get("ai_credit_balance_gbp", 0) or 0)


def add_credit_balance(user_id: int, amount_gbp: float):
    """Atomically increase the user's credit balance (e.g. after a Stripe
    one-time checkout.session.completed for a credit pack)."""
    _execute(
        "UPDATE users SET ai_credit_balance_gbp = COALESCE(ai_credit_balance_gbp, 0) + ? WHERE id = ?",
        (float(amount_gbp), user_id),
    )


def deduct_credit_balance(user_id: int, amount_gbp: float) -> bool:
    """Atomically decrease the user's credit balance.

    Returns ``True`` if the deduction succeeded (balance was sufficient),
    ``False`` otherwise. The caller should have already verified affordability
    before calling, but we guard here with a conditional UPDATE to avoid races
    between concurrent requests.
    """
    # Conditional update: only deducts if balance is sufficient. Both the
    # Turso HTTP client and sqlite3 expose a row-count attribute we can use
    # to detect whether the WHERE clause matched.
    if USE_TURSO:
        result = _get_turso().execute(
            "UPDATE users SET ai_credit_balance_gbp = ai_credit_balance_gbp - ? WHERE id = ? AND ai_credit_balance_gbp >= ?",
            (float(amount_gbp), user_id, float(amount_gbp)),
        )
        return (getattr(result, "affected_row_count", 0) or 0) > 0
    else:
        conn = _get_sqlite()
        cursor = conn.execute(
            "UPDATE users SET ai_credit_balance_gbp = ai_credit_balance_gbp - ? WHERE id = ? AND ai_credit_balance_gbp >= ?",
            (float(amount_gbp), user_id, float(amount_gbp)),
        )
        conn.commit()
        return cursor.rowcount > 0


# --- Email verification ---

def mark_email_verified(user_id: int):
    """Mark a user's email as verified. Idempotent."""
    _execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))


def is_email_verified(user_id: int) -> bool:
    row = _fetchone_dict("SELECT email_verified FROM users WHERE id = ?", (user_id,))
    if not row:
        return False
    return bool(row.get("email_verified", 0))


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
