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

# Boot diagnostic — confirms env vars are reaching the runtime.
print(
    f"[BOOT][env] USE_TURSO={USE_TURSO} "
    f"TURSO_URL_len={len(TURSO_URL)} TURSO_TOKEN_len={len(TURSO_TOKEN)} "
    f"ANTHROPIC_KEY_len={len(os.environ.get('ANTHROPIC_API_KEY', ''))} "
    f"RESEND_KEY_len={len(os.environ.get('RESEND_API_KEY', ''))}",
    flush=True,
)

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
    """Get or create a local SQLite connection.

    DATABASE_PATH env var overrides the default location — used by E2E
    tests to isolate against a temp DB instead of the dev database.
    """
    global _sqlite_conn
    if _sqlite_conn is None:
        import os
        import sqlite3
        db_path = os.environ.get("DATABASE_PATH") or str(Path(__file__).parent / "bankparse.db")
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


_USER_COLS = "id, email, password_hash, stripe_customer_id, stripe_subscription_id, statements_used, receipts_used, subscription_status, subscription_checked_at, chat_count, chat_date, scans_this_month, scan_month, statements_this_month, receipts_this_month, usage_month, email_verified, ai_credit_balance_gbp, ai_spend_this_month, created_at, trial_reminder_sent_at, trial_end_at, grandfathered_trial"


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
        """CREATE TABLE IF NOT EXISTS mileage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date_iso TEXT NOT NULL,
            from_location TEXT,
            to_location TEXT,
            miles REAL NOT NULL,
            purpose TEXT,
            vehicle TEXT DEFAULT 'car',  -- 'car'|'motorcycle'|'bicycle'
            business_pct INTEGER DEFAULT 100,
            -- HMRC rate is calculated at query time, never stored, so the
            -- annual 10k threshold can re-bucket as the user adds more.
            created_at REAL DEFAULT (strftime('%s','now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL DEFAULT (strftime('%s', 'now')),
            used INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
        """CREATE TABLE IF NOT EXISTS qbo_connections (
            user_id INTEGER PRIMARY KEY,
            realm_id TEXT NOT NULL,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL,
            access_expires_at REAL NOT NULL,
            refresh_expires_at REAL NOT NULL,
            environment TEXT NOT NULL DEFAULT 'sandbox',
            company_name TEXT,
            connected_at REAL DEFAULT (strftime('%s', 'now')),
            updated_at REAL DEFAULT (strftime('%s', 'now'))
        )""",
        """CREATE TABLE IF NOT EXISTS user_extracted_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            source_filename TEXT,
            source_size_bytes INTEGER NOT NULL DEFAULT 0,
            rows_json TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0,
            parsed_at REAL DEFAULT (strftime('%s', 'now'))
        )""",
        # --- Structured ledger (Phase 1 of receipt-to-bank matching) ---
        # One row per bank-statement transaction. Lives alongside the JSON
        # blob in user_extracted_data so we don't have to migrate existing
        # data — we just write to BOTH on every parse going forward.
        """CREATE TABLE IF NOT EXISTS ledger_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            extracted_data_id INTEGER,        -- FK back to user_extracted_data (the parent batch)
            date_iso TEXT,                    -- YYYY-MM-DD
            description TEXT,
            amount REAL NOT NULL,             -- negative = money out, positive = money in
            currency TEXT DEFAULT 'GBP',
            balance REAL,
            transaction_type TEXT,            -- 'debit' / 'credit'
            -- HMRC categorisation
            hmrc_category TEXT,
            hmrc_category_confidence INTEGER, -- 0-100
            hmrc_category_reason TEXT,        -- the AI's stated rationale
            -- Receipt linking
            receipt_status TEXT DEFAULT 'missing',  -- 'matched'|'missing'|'na'|'excluded'
            exclusion_reason TEXT,            -- 'personal'|'cash'|'dd'|'subscription' or null
            vat_amount REAL,                  -- inherited from linked receipt
            -- Audit trail
            content_hash TEXT NOT NULL,       -- SHA-256 of (date|description|amount)
            -- Capital allowance flag
            is_capital INTEGER DEFAULT 0,     -- 1 if user confirmed this is a capital item
            -- Personal/business split (0-100; 100 = fully business)
            business_pct INTEGER DEFAULT 100,
            created_at REAL DEFAULT (strftime('%s', 'now')),
            updated_at REAL DEFAULT (strftime('%s', 'now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (extracted_data_id) REFERENCES user_extracted_data(id) ON DELETE CASCADE
        )""",
        # One row per receipt (parsed from PDF/image/photo).
        """CREATE TABLE IF NOT EXISTS ledger_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            extracted_data_id INTEGER,
            file_path TEXT,                   -- where the original is stored (if kept)
            source_filename TEXT,
            store_name TEXT,
            date_iso TEXT,
            total_amount REAL,
            currency TEXT DEFAULT 'GBP',
            subtotal REAL,
            tax_amount REAL,                  -- VAT in GBP
            payment_method TEXT,              -- 'card'|'cash'|null
            items_json TEXT,                  -- list of line items
            -- Match status (cached for fast list queries; canonical link lives in ledger_links)
            match_status TEXT DEFAULT 'unmatched',  -- 'matched'|'unmatched'|'cash'|'orphan'
            -- Audit trail
            content_hash TEXT NOT NULL,       -- SHA-256 of (store_name|date|total)
            created_at REAL DEFAULT (strftime('%s', 'now')),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (extracted_data_id) REFERENCES user_extracted_data(id) ON DELETE CASCADE
        )""",
        # Many-to-many: a receipt can match >1 transaction (split payment) and
        # a transaction can have >1 receipt (split bill). The canonical link.
        """CREATE TABLE IF NOT EXISTS ledger_links (
            transaction_id INTEGER NOT NULL,
            receipt_id INTEGER NOT NULL,
            match_strategy TEXT NOT NULL,     -- 'exact'|'strong'|'ai'|'manual'
            confidence INTEGER NOT NULL,      -- 0-100
            user_confirmed INTEGER DEFAULT 0, -- 0 = auto, 1 = user clicked Confirm
            reason TEXT,                      -- human-readable why this matched
            created_at REAL DEFAULT (strftime('%s', 'now')),
            PRIMARY KEY (transaction_id, receipt_id),
            FOREIGN KEY (transaction_id) REFERENCES ledger_transactions(id) ON DELETE CASCADE,
            FOREIGN KEY (receipt_id) REFERENCES ledger_receipts(id) ON DELETE CASCADE
        )""",
        # Webhook idempotency — Stripe retries failed deliveries for up to 3 days,
        # so every handler must be safely replayable. We dedupe on event.id.
        """CREATE TABLE IF NOT EXISTS processed_webhooks (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            received_at REAL NOT NULL
        )""",
        # HMRC MTD ITSA — OAuth connections per user.
        # Tokens are AES-GCM encrypted before storage (hmrc.services.crypto).
        # Refresh tokens are rotated by HMRC on every use; this table is
        # upserted on each refresh.
        """CREATE TABLE IF NOT EXISTS hmrc_connections (
            user_id INTEGER PRIMARY KEY,
            access_token_enc TEXT NOT NULL,
            refresh_token_enc TEXT NOT NULL,
            expires_at REAL NOT NULL,
            scope TEXT,
            connected_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""",
        # Per-session browser-collected fraud-prevention fields. Pairs with
        # the server-collected fields on every outbound MTD call.
        """CREATE TABLE IF NOT EXISTS hmrc_fraud_sessions (
            session_id TEXT PRIMARY KEY,
            fraud_context_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )""",
        # Immutable audit log of every HMRC call we make. Required for
        # software recognition — HMRC asks to see proof of submissions.
        # Append-only; bearer tokens are stripped from stored headers.
        """CREATE TABLE IF NOT EXISTS hmrc_submissions (
            audit_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            endpoint TEXT NOT NULL,
            method TEXT NOT NULL,
            request_headers_json TEXT,
            request_body_json TEXT,
            response_status INTEGER NOT NULL,
            response_headers_json TEXT,
            response_body_json TEXT,
            idempotency_key TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_hmrc_submissions_user ON hmrc_submissions(user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_hmrc_submissions_idempotency ON hmrc_submissions(idempotency_key)",
        # Per-user merchant → HMRC category overrides. When a user corrects an
        # auto-categorisation, we save the mapping so next time the same
        # merchant appears it's auto-categorised the user's way. Composite
        # PK: a single merchant can have one override per business_type
        # (e.g. 'amazon' is admin for SE but might be excluded for property).
        """CREATE TABLE IF NOT EXISTS hmrc_merchant_overrides (
            user_id INTEGER NOT NULL,
            merchant_key TEXT NOT NULL,
            business_type TEXT NOT NULL,
            category TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (user_id, merchant_key, business_type),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_hmrc_overrides_user ON hmrc_merchant_overrides(user_id)",
        # Global merchant → HMRC category cache. Populated by the AI classifier
        # whenever it produces a high-confidence result for a new merchant key.
        # Shared across ALL users — when one user's statement has Costa Coffee
        # classified, every subsequent user gets that classification for free.
        # User overrides take precedence over this cache; this cache takes
        # precedence over a fresh AI call.
        """CREATE TABLE IF NOT EXISTS hmrc_merchant_cache (
            merchant_key TEXT NOT NULL,
            business_type TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence REAL NOT NULL,
            reasoning TEXT,
            hits INTEGER NOT NULL DEFAULT 1,
            updated_at REAL NOT NULL,
            PRIMARY KEY (merchant_key, business_type)
        )""",
        # One row per /api/hmrc/categorise call — used to answer "what's our
        # cache hit rate?" and "how often does AI actually fire?" from SQL
        # without bolting on a paid observability service. Written best-effort
        # so a metric failure never breaks a user response.
        """CREATE TABLE IF NOT EXISTS hmrc_categorisation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            business_type TEXT NOT NULL,
            total_rows INTEGER NOT NULL,
            overrides INTEGER NOT NULL DEFAULT 0,
            cache_hits INTEGER NOT NULL DEFAULT 0,
            ai_calls INTEGER NOT NULL DEFAULT 0,
            rule_fallbacks INTEGER NOT NULL DEFAULT 0,
            elapsed_ms INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS idx_hmrc_categorisation_events_user_created "
        "ON hmrc_categorisation_events(user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_extracted_user_mode ON user_extracted_data(user_id, mode)",
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
        # Structured ledger lookups
        "CREATE INDEX IF NOT EXISTS idx_ledger_tx_user ON ledger_transactions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_tx_user_date ON ledger_transactions(user_id, date_iso)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_tx_hash ON ledger_transactions(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_rc_user ON ledger_receipts(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_rc_user_date ON ledger_receipts(user_id, date_iso)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_rc_hash ON ledger_receipts(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_links_tx ON ledger_links(transaction_id)",
        "CREATE INDEX IF NOT EXISTS idx_ledger_links_rc ON ledger_links(receipt_id)",
        "CREATE INDEX IF NOT EXISTS idx_mileage_user_date ON mileage_logs(user_id, date_iso)",
    ]
    for stmt in stmts:
        _execute(stmt)

    # Migrate: add hmrc_connections columns added after table creation.
    # Storing the NINO + a JSON list of business IDs lets the obligations
    # endpoint hit the real HMRC API without re-prompting the user every
    # time. NINO is per-user PII — encrypted at rest the same way tokens are.
    for col, col_def in [
        ("nino_enc", "TEXT DEFAULT NULL"),
        ("businesses_json", "TEXT DEFAULT NULL"),  # encrypted JSON list of business IDs
    ]:
        try:
            _execute(f"ALTER TABLE hmrc_connections ADD COLUMN {col} {col_def}")
        except Exception:
            pass  # already added

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
        ("trial_reminder_sent_at", "REAL DEFAULT NULL"),
        # Card-on-file trial (Stripe Subscription with trial_period_days):
        ("stripe_subscription_id", "TEXT DEFAULT NULL"),
        ("trial_end_at", "REAL DEFAULT NULL"),
        ("grandfathered_trial", "INTEGER DEFAULT 0"),
    ]:
        try:
            _execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
        except Exception:
            pass  # Column already exists

    # One-shot grandfather backfill: every user that existed before the
    # card-on-file flow shipped keeps the legacy 7-days-from-registration trial
    # and is never force-walled into entering a card retroactively. New users
    # (signups after the cutoff below) default to grandfathered_trial=0 and
    # are routed through Stripe Checkout for card collection.
    #
    # Cutoff is fixed at the migration-design time (2026-05-18 00:00 UTC).
    # Anyone with created_at < cutoff is grandfathered; anyone created after
    # the deploy uses the new flow. Idempotent: re-running the UPDATE is a
    # no-op because the WHERE clause excludes already-flagged rows.
    GRANDFATHER_CUTOFF = 1779062400.0  # 2026-05-18T00:00:00Z
    try:
        _execute(
            "UPDATE users SET grandfathered_trial = 1 "
            "WHERE grandfathered_trial = 0 "
            "  AND created_at IS NOT NULL "
            "  AND created_at < ?",
            (GRANDFATHER_CUTOFF,),
        )
    except Exception:
        logger.exception("grandfather backfill failed (non-fatal)")

    # Idempotent un-grandfather: the founder's gmail account got swept up
    # in the cutoff above but the product owner wants to be paywalled like
    # a real customer (the yahoo address is the actual admin account).
    # Idempotent — re-running just no-ops because the WHERE clause matches
    # a single row. Stripe state (subscription_status, trial_end_at) is
    # untouched, so if they DO subscribe later nothing here interferes.
    try:
        _execute(
            "UPDATE users SET grandfathered_trial = 0 "
            "WHERE LOWER(email) = ? AND grandfathered_trial = 1",
            ("mitchellagoma@gmail.com",),
        )
    except Exception:
        logger.exception("un-grandfather mitchellagoma@gmail.com failed (non-fatal)")

    # Migrate user_extracted_data — source_size_bytes added in commit 3
    try:
        _execute("ALTER TABLE user_extracted_data ADD COLUMN source_size_bytes INTEGER NOT NULL DEFAULT 0")
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
    allowed = {"stripe_customer_id", "stripe_subscription_id", "statements_used", "receipts_used", "email", "password_hash", "subscription_status", "subscription_checked_at", "chat_count", "chat_date", "scans_this_month", "scan_month", "statements_this_month", "receipts_this_month", "usage_month", "email_verified", "ai_credit_balance_gbp", "ai_spend_this_month", "trial_end_at", "grandfathered_trial"}
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


# --- Password reset tokens ---

PASSWORD_RESET_TTL_SECONDS = 30 * 60  # 30 minutes


def create_password_reset_token(user_id: int) -> str:
    """Generate a 32-byte URL-safe token, store it, return it. Any prior
    unused tokens for the user are invalidated so only the latest works."""
    import secrets
    _execute("UPDATE password_reset_tokens SET used = 1 WHERE user_id = ? AND used = 0", (user_id,))
    token = secrets.token_urlsafe(32)
    _execute(
        "INSERT INTO password_reset_tokens (token, user_id) VALUES (?, ?)",
        (token, user_id),
    )
    return token


def consume_password_reset_token(token: str) -> int | None:
    """Validate the token: unused, within TTL. On success, mark used and
    return the user_id. Otherwise return None."""
    row = _fetchone_dict(
        "SELECT user_id, created_at, used FROM password_reset_tokens WHERE token = ?",
        (token,),
    )
    if row is None:
        return None
    if row["used"]:
        return None
    if time.time() - float(row["created_at"]) > PASSWORD_RESET_TTL_SECONDS:
        return None
    _execute("UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (token,))
    return int(row["user_id"])


def cleanup_expired_password_reset_tokens():
    _execute(
        "DELETE FROM password_reset_tokens WHERE created_at < ? OR used = 1",
        (time.time() - PASSWORD_RESET_TTL_SECONDS,),
    )


# --- Output file tracking ---

def track_output_file(filename: str):
    _execute("INSERT OR REPLACE INTO output_files (filename, created_at) VALUES (?, strftime('%s', 'now'))", (filename,))


def get_stale_output_files(max_age_seconds: int = 3600) -> list[str]:
    rows = _fetchall_dicts("SELECT filename FROM output_files WHERE created_at < ?", (time.time() - max_age_seconds,))
    return [row["filename"] for row in rows]


def remove_output_file_record(filename: str):
    _execute("DELETE FROM output_files WHERE filename = ?", (filename,))


# --- QuickBooks Online connection helpers ---

def upsert_qbo_connection(
    user_id: int,
    realm_id: str,
    access_token: str,
    refresh_token: str,
    access_expires_at: float,
    refresh_expires_at: float,
    environment: str,
    company_name: str | None = None,
):
    _execute(
        """INSERT INTO qbo_connections
           (user_id, realm_id, access_token, refresh_token, access_expires_at,
            refresh_expires_at, environment, company_name, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%s', 'now'))
           ON CONFLICT(user_id) DO UPDATE SET
             realm_id = excluded.realm_id,
             access_token = excluded.access_token,
             refresh_token = excluded.refresh_token,
             access_expires_at = excluded.access_expires_at,
             refresh_expires_at = excluded.refresh_expires_at,
             environment = excluded.environment,
             company_name = COALESCE(excluded.company_name, qbo_connections.company_name),
             updated_at = strftime('%s', 'now')""",
        (user_id, realm_id, access_token, refresh_token, access_expires_at,
         refresh_expires_at, environment, company_name),
    )


def get_qbo_connection(user_id: int) -> dict | None:
    return _fetchone_dict(
        "SELECT user_id, realm_id, access_token, refresh_token, access_expires_at, "
        "refresh_expires_at, environment, company_name, connected_at, updated_at "
        "FROM qbo_connections WHERE user_id = ?",
        (user_id,),
    )


def update_qbo_tokens(
    user_id: int,
    access_token: str,
    refresh_token: str,
    access_expires_at: float,
    refresh_expires_at: float,
):
    _execute(
        "UPDATE qbo_connections SET access_token = ?, refresh_token = ?, "
        "access_expires_at = ?, refresh_expires_at = ?, updated_at = strftime('%s', 'now') "
        "WHERE user_id = ?",
        (access_token, refresh_token, access_expires_at, refresh_expires_at, user_id),
    )


def delete_qbo_connection(user_id: int):
    _execute("DELETE FROM qbo_connections WHERE user_id = ?", (user_id,))


# ---------------------------------------------------------------------------
# Per-user persisted extraction (cumulative across uploads, cleared only by
# the user clicking "Clear & Upload New"). Survives logout, browser close,
# device switch.
# ---------------------------------------------------------------------------

def save_extracted_data(
    user_id: int,
    mode: str,
    source_filename: str,
    rows: list[dict],
    source_size_bytes: int = 0,
) -> int:
    """Append a parse's extracted rows to the user's cumulative store.

    `mode` is 'statement' or 'receipt'. `source_size_bytes` is the byte size
    of the original uploaded file — used for the 25MB session-cumulative cap.
    Returns the new row id.
    """
    import json
    if mode not in ("statement", "receipt"):
        raise ValueError(f"invalid mode: {mode!r}")
    rows_json = json.dumps(rows or [])
    return _execute_insert(
        "INSERT INTO user_extracted_data "
        "(user_id, mode, source_filename, source_size_bytes, rows_json, row_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, mode, source_filename or "", int(source_size_bytes or 0),
         rows_json, len(rows or [])),
    )


def get_user_extracted_total_bytes(user_id: int) -> int:
    """Total bytes of all files this user has uploaded since last clear."""
    row = _fetchone_dict(
        "SELECT COALESCE(SUM(source_size_bytes), 0) AS total "
        "FROM user_extracted_data WHERE user_id = ?",
        (user_id,),
    )
    return int((row or {}).get("total") or 0)


def get_user_extracted_files(user_id: int, mode: str) -> list[dict]:
    """Return each parsed-file record for this user/mode, oldest first.

    Each record has: id, source_filename, row_count, parsed_at, rows (parsed JSON list).
    """
    import json
    if mode not in ("statement", "receipt"):
        raise ValueError(f"invalid mode: {mode!r}")
    raw = _fetchall_dicts(
        "SELECT id, source_filename, row_count, parsed_at, rows_json "
        "FROM user_extracted_data WHERE user_id = ? AND mode = ? "
        "ORDER BY parsed_at ASC, id ASC",
        (user_id, mode),
    )
    out = []
    for r in raw:
        try:
            rows = json.loads(r.get("rows_json") or "[]")
        except Exception:
            rows = []
        out.append({
            "id": r["id"],
            "source_filename": r.get("source_filename") or "",
            "row_count": r.get("row_count") or 0,
            "parsed_at": r.get("parsed_at"),
            "rows": rows,
        })
    return out


def get_user_extracted_rows(user_id: int, mode: str) -> list[dict]:
    """Flatten every parse's rows for this user/mode into one list."""
    flat: list[dict] = []
    for f in get_user_extracted_files(user_id, mode):
        flat.extend(f["rows"])
    return flat


def get_user_extracted_summary(user_id: int) -> dict:
    """Light summary for the dashboard banner: counts of files and rows per mode."""
    raw = _fetchall_dicts(
        "SELECT mode, COUNT(*) AS file_count, COALESCE(SUM(row_count), 0) AS row_count "
        "FROM user_extracted_data WHERE user_id = ? GROUP BY mode",
        (user_id,),
    )
    summary = {
        "statement": {"file_count": 0, "row_count": 0},
        "receipt": {"file_count": 0, "row_count": 0},
    }
    for r in raw:
        mode = r.get("mode")
        if mode in summary:
            summary[mode]["file_count"] = int(r.get("file_count") or 0)
            summary[mode]["row_count"] = int(r.get("row_count") or 0)
    return summary


def find_users_due_trial_reminder(min_days: float = 5.0, max_days: float = 6.0) -> list[dict]:
    """Return users whose trial is ~5 days in and who haven't been reminded.

    Filters: verified email, no active Stripe subscription, no reminder yet,
    and created_at falls inside (now - max_days, now - min_days) — i.e.
    they have between 1 and 2 days of trial left.
    """
    import time
    now = time.time()
    upper = now - (min_days * 86400.0)
    lower = now - (max_days * 86400.0)
    return _fetchall_dicts(
        f"SELECT {_USER_COLS} FROM users "
        "WHERE email_verified = 1 "
        "  AND trial_reminder_sent_at IS NULL "
        "  AND (subscription_status IS NULL OR subscription_status NOT IN ('active','trialing')) "
        "  AND created_at IS NOT NULL "
        "  AND created_at > ? "
        "  AND created_at <= ?",
        (lower, upper),
    )


def mark_trial_reminder_sent(user_id: int):
    """Stamp the user record so the cron doesn't re-send."""
    import time
    _execute(
        "UPDATE users SET trial_reminder_sent_at = ? WHERE id = ?",
        (time.time(), user_id),
    )


def clear_user_extracted_data(user_id: int) -> int:
    """Wipe all persisted extracted rows for this user. Returns the count
    of file records deleted."""
    raw = _fetchone_dict(
        "SELECT COUNT(*) AS c FROM user_extracted_data WHERE user_id = ?",
        (user_id,),
    )
    count = int((raw or {}).get("c") or 0)
    _execute("DELETE FROM user_extracted_data WHERE user_id = ?", (user_id,))
    return count


# ---------------------------------------------------------------------------
# Structured ledger — transactions, receipts, links
# ---------------------------------------------------------------------------

def _hash_transaction(date_iso: str | None, description: str | None, amount: float) -> str:
    """SHA-256 of the immutable trio. If any of these change later the row's
    hash changes — but the original is preserved as a separate audit record."""
    import hashlib
    payload = f"{date_iso or ''}|{(description or '').strip().lower()}|{round(float(amount), 2)}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _hash_receipt(store_name: str | None, date_iso: str | None, total: float | None) -> str:
    import hashlib
    payload = f"{(store_name or '').strip().lower()}|{date_iso or ''}|{round(float(total or 0), 2)}"
    return hashlib.sha256(payload.encode()).hexdigest()


def insert_ledger_transaction(
    user_id: int,
    *,
    extracted_data_id: int | None,
    date_iso: str | None,
    description: str | None,
    amount: float,
    currency: str = "GBP",
    balance: float | None = None,
    transaction_type: str | None = None,
    hmrc_category: str | None = None,
    hmrc_category_confidence: int | None = None,
    hmrc_category_reason: str | None = None,
) -> int:
    """Insert one transaction. Returns the new row id."""
    content_hash = _hash_transaction(date_iso, description, amount)
    return _execute_insert(
        """INSERT INTO ledger_transactions
        (user_id, extracted_data_id, date_iso, description, amount, currency,
         balance, transaction_type, hmrc_category, hmrc_category_confidence,
         hmrc_category_reason, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, extracted_data_id, date_iso, description, float(amount),
         currency, balance, transaction_type,
         hmrc_category, hmrc_category_confidence, hmrc_category_reason,
         content_hash),
    )


def insert_ledger_receipt(
    user_id: int,
    *,
    extracted_data_id: int | None,
    file_path: str | None,
    source_filename: str | None,
    store_name: str | None,
    date_iso: str | None,
    total_amount: float | None,
    currency: str = "GBP",
    subtotal: float | None = None,
    tax_amount: float | None = None,
    payment_method: str | None = None,
    items: list[dict] | None = None,
) -> int:
    import json
    content_hash = _hash_receipt(store_name, date_iso, total_amount)
    return _execute_insert(
        """INSERT INTO ledger_receipts
        (user_id, extracted_data_id, file_path, source_filename, store_name,
         date_iso, total_amount, currency, subtotal, tax_amount,
         payment_method, items_json, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, extracted_data_id, file_path, source_filename, store_name,
         date_iso,
         float(total_amount) if total_amount is not None else None,
         currency,
         float(subtotal) if subtotal is not None else None,
         float(tax_amount) if tax_amount is not None else None,
         payment_method,
         json.dumps(items or []),
         content_hash),
    )


def insert_ledger_link(
    *,
    transaction_id: int,
    receipt_id: int,
    match_strategy: str,
    confidence: int,
    user_confirmed: bool = False,
    reason: str | None = None,
) -> None:
    """Link a receipt to a transaction. Idempotent — re-linking the same pair
    is a no-op (PRIMARY KEY (transaction_id, receipt_id))."""
    _execute(
        """INSERT OR REPLACE INTO ledger_links
        (transaction_id, receipt_id, match_strategy, confidence, user_confirmed, reason)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (transaction_id, receipt_id, match_strategy, int(confidence),
         1 if user_confirmed else 0, reason),
    )
    # Update cached match_status on both sides so the unified ledger view
    # doesn't need to JOIN ledger_links on every read.
    _execute(
        "UPDATE ledger_transactions SET receipt_status = 'matched', "
        "vat_amount = (SELECT tax_amount FROM ledger_receipts WHERE id = ?) "
        "WHERE id = ?",
        (receipt_id, transaction_id),
    )
    _execute(
        "UPDATE ledger_receipts SET match_status = 'matched' WHERE id = ?",
        (receipt_id,),
    )


def remove_ledger_link(transaction_id: int, receipt_id: int) -> None:
    _execute(
        "DELETE FROM ledger_links WHERE transaction_id = ? AND receipt_id = ?",
        (transaction_id, receipt_id),
    )
    # If no other receipts remain for the transaction, mark as missing again.
    remaining = _fetchone_dict(
        "SELECT COUNT(*) AS c FROM ledger_links WHERE transaction_id = ?",
        (transaction_id,),
    )
    if (remaining or {}).get("c", 0) == 0:
        _execute(
            "UPDATE ledger_transactions SET receipt_status = 'missing', vat_amount = NULL "
            "WHERE id = ?",
            (transaction_id,),
        )
    other_links = _fetchone_dict(
        "SELECT COUNT(*) AS c FROM ledger_links WHERE receipt_id = ?",
        (receipt_id,),
    )
    if (other_links or {}).get("c", 0) == 0:
        _execute(
            "UPDATE ledger_receipts SET match_status = 'unmatched' WHERE id = ?",
            (receipt_id,),
        )


def get_user_ledger_transactions(
    user_id: int,
    *,
    limit: int = 1000,
) -> list[dict]:
    return _fetchall_dicts(
        """SELECT * FROM ledger_transactions
        WHERE user_id = ?
        ORDER BY date_iso DESC, id DESC
        LIMIT ?""",
        (user_id, int(limit)),
    )


def get_user_ledger_receipts(
    user_id: int,
    *,
    only_unmatched: bool = False,
    limit: int = 1000,
) -> list[dict]:
    if only_unmatched:
        return _fetchall_dicts(
            """SELECT * FROM ledger_receipts
            WHERE user_id = ? AND match_status = 'unmatched'
            ORDER BY date_iso DESC, id DESC LIMIT ?""",
            (user_id, int(limit)),
        )
    return _fetchall_dicts(
        """SELECT * FROM ledger_receipts
        WHERE user_id = ?
        ORDER BY date_iso DESC, id DESC LIMIT ?""",
        (user_id, int(limit)),
    )


def get_links_for_transaction(transaction_id: int) -> list[dict]:
    return _fetchall_dicts(
        """SELECT l.*, r.store_name, r.total_amount, r.tax_amount, r.date_iso, r.file_path
        FROM ledger_links l
        JOIN ledger_receipts r ON l.receipt_id = r.id
        WHERE l.transaction_id = ?""",
        (transaction_id,),
    )


def get_transaction_by_id(transaction_id: int, user_id: int) -> dict | None:
    return _fetchone_dict(
        "SELECT * FROM ledger_transactions WHERE id = ? AND user_id = ?",
        (transaction_id, user_id),
    )


def get_receipt_by_id(receipt_id: int, user_id: int) -> dict | None:
    return _fetchone_dict(
        "SELECT * FROM ledger_receipts WHERE id = ? AND user_id = ?",
        (receipt_id, user_id),
    )


def update_transaction_status(
    transaction_id: int,
    *,
    receipt_status: str | None = None,
    exclusion_reason: str | None = None,
    is_capital: int | None = None,
    business_pct: int | None = None,
    hmrc_category: str | None = None,
    hmrc_category_confidence: int | None = None,
    hmrc_category_reason: str | None = None,
) -> None:
    updates = []
    params: list = []
    for col, val in (
        ("receipt_status", receipt_status),
        ("exclusion_reason", exclusion_reason),
        ("is_capital", is_capital),
        ("business_pct", business_pct),
        ("hmrc_category", hmrc_category),
        ("hmrc_category_confidence", hmrc_category_confidence),
        ("hmrc_category_reason", hmrc_category_reason),
    ):
        if val is not None:
            updates.append(f"{col} = ?")
            params.append(val)
    if not updates:
        return
    updates.append("updated_at = strftime('%s', 'now')")
    params.append(transaction_id)
    _execute(
        f"UPDATE ledger_transactions SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )


def clear_user_ledger(user_id: int) -> int:
    """Delete every transaction, receipt, and link for the user. Returns
    the number of transactions deleted."""
    raw = _fetchone_dict(
        "SELECT COUNT(*) AS c FROM ledger_transactions WHERE user_id = ?",
        (user_id,),
    )
    count = int((raw or {}).get("c") or 0)
    # ledger_links is cleared by foreign-key CASCADE.
    _execute("DELETE FROM ledger_receipts WHERE user_id = ?", (user_id,))
    _execute("DELETE FROM ledger_transactions WHERE user_id = ?", (user_id,))
    return count


# Initialize on import
init_db()
