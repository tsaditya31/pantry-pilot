"""
Database layer — schema init, migrations, and all CRUD helpers.
Uses psycopg2 with PostgreSQL.
"""

import json
import logging
from datetime import datetime, date
from typing import Optional

import psycopg2
import psycopg2.extras

from config import settings

logger = logging.getLogger(__name__)


def _conn():
    return psycopg2.connect(settings.database_url)


def init_db():
    """Create all tables if they don't exist."""
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        telegram_id BIGINT UNIQUE NOT NULL,
        first_name TEXT,
        username TEXT,
        timezone TEXT DEFAULT 'UTC',
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS receipts (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        telegram_file_id TEXT,
        store_name TEXT,
        purchase_date DATE,
        total_amount NUMERIC(10,2),
        raw_extraction JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS receipt_items (
        id SERIAL PRIMARY KEY,
        receipt_id INTEGER NOT NULL REFERENCES receipts(id),
        user_id INTEGER NOT NULL REFERENCES users(id),
        item_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        category TEXT,
        quantity NUMERIC(10,3) DEFAULT 1,
        unit TEXT,
        price NUMERIC(10,2)
    );

    CREATE TABLE IF NOT EXISTS pantry_snapshots (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        snapshot_type TEXT NOT NULL,
        telegram_file_id TEXT,
        raw_extraction JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS pantry_items (
        id SERIAL PRIMARY KEY,
        snapshot_id INTEGER NOT NULL REFERENCES pantry_snapshots(id),
        user_id INTEGER NOT NULL REFERENCES users(id),
        item_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        category TEXT,
        estimated_qty TEXT,
        condition TEXT,
        is_current BOOLEAN DEFAULT TRUE
    );

    CREATE TABLE IF NOT EXISTS shopping_suggestions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        item_name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        reason TEXT,
        priority TEXT DEFAULT 'normal',
        last_purchased DATE,
        in_pantry BOOLEAN DEFAULT FALSE,
        dismissed BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS chat_messages (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS reminders (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        reminder_text TEXT NOT NULL,
        due_at TIMESTAMPTZ NOT NULL,
        sent BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS user_profiles (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) UNIQUE,
        family_size INTEGER DEFAULT 1,
        dietary_preferences JSONB DEFAULT '[]',
        preferred_shopping_day INTEGER,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS stocking_rules (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        normalized_name TEXT NOT NULL,
        display_name TEXT NOT NULL,
        min_quantity INTEGER DEFAULT 1,
        active BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, normalized_name)
    );

    CREATE TABLE IF NOT EXISTS consumption_rates (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        normalized_name TEXT NOT NULL,
        avg_interval_days NUMERIC,
        estimated_runout_date DATE,
        confidence TEXT,
        data_points INTEGER,
        last_computed TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, normalized_name)
    );
    CREATE INDEX IF NOT EXISTS idx_consumption_rates_runout
        ON consumption_rates(user_id, estimated_runout_date);

    CREATE TABLE IF NOT EXISTS restock_notifications (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        normalized_name TEXT NOT NULL,
        notified_for_date DATE NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, normalized_name, notified_for_date)
    );

    CREATE INDEX IF NOT EXISTS idx_receipt_items_user ON receipt_items(user_id);
    CREATE INDEX IF NOT EXISTS idx_receipt_items_normalized ON receipt_items(normalized_name);
    CREATE INDEX IF NOT EXISTS idx_pantry_items_user_current ON pantry_items(user_id, is_current);
    CREATE INDEX IF NOT EXISTS idx_receipts_user_date ON receipts(user_id, purchase_date);
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
    logger.info("Database initialized.")


# ── Users ────────────────────────────────────────────────────────────────────

def upsert_user(telegram_id: int, first_name: str = None, username: str = None) -> int:
    """Create or update user, return internal user id."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (telegram_id, first_name, username)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE
                    SET first_name = COALESCE(EXCLUDED.first_name, users.first_name),
                        username = COALESCE(EXCLUDED.username, users.username)
                RETURNING id
                """,
                (telegram_id, first_name, username),
            )
            user_id = cur.fetchone()[0]
        conn.commit()
    return user_id


def get_user_id(telegram_id: int) -> Optional[int]:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
            return row[0] if row else None


# ── Receipts ─────────────────────────────────────────────────────────────────

def insert_receipt(user_id: int, telegram_file_id: str, store_name: str,
                   purchase_date: date, total_amount: float,
                   raw_extraction: dict) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO receipts (user_id, telegram_file_id, store_name,
                                      purchase_date, total_amount, raw_extraction)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, telegram_file_id, store_name, purchase_date,
                 total_amount, json.dumps(raw_extraction)),
            )
            receipt_id = cur.fetchone()[0]
        conn.commit()
    return receipt_id


def insert_receipt_items(receipt_id: int, user_id: int, items: list[dict]):
    """Insert multiple receipt items. Each dict has: item_name, normalized_name,
    category, quantity, unit, price."""
    with _conn() as conn:
        with conn.cursor() as cur:
            for item in items:
                cur.execute(
                    """
                    INSERT INTO receipt_items
                        (receipt_id, user_id, item_name, normalized_name,
                         category, quantity, unit, price)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (receipt_id, user_id, item["item_name"], item["normalized_name"],
                     item.get("category"), item.get("quantity", 1),
                     item.get("unit"), item.get("price")),
                )
        conn.commit()


def get_recent_purchases(user_id: int, days: int = 7) -> list[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ri.item_name, ri.normalized_name, ri.category,
                       ri.quantity, ri.unit, ri.price,
                       r.store_name, r.purchase_date
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                WHERE ri.user_id = %s
                  AND r.purchase_date >= CURRENT_DATE - INTERVAL '%s days'
                ORDER BY r.purchase_date DESC, ri.item_name
                """,
                (user_id, days),
            )
            return [dict(row) for row in cur.fetchall()]


def get_purchase_history(user_id: int, days: int = 90) -> list[dict]:
    """Get purchase history grouped by normalized_name with stats."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ri.normalized_name,
                       COUNT(*) as purchase_count,
                       MAX(r.purchase_date) as last_purchased,
                       MIN(r.purchase_date) as first_purchased,
                       ri.category
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                WHERE ri.user_id = %s
                  AND r.purchase_date >= CURRENT_DATE - INTERVAL '%s days'
                GROUP BY ri.normalized_name, ri.category
                ORDER BY purchase_count DESC
                """,
                (user_id, days),
            )
            return [dict(row) for row in cur.fetchall()]


# ── Pantry ───────────────────────────────────────────────────────────────────

def insert_pantry_snapshot(user_id: int, snapshot_type: str,
                           telegram_file_id: str, raw_extraction: dict) -> int:
    """Insert snapshot and mark previous items for this location as not current."""
    with _conn() as conn:
        with conn.cursor() as cur:
            # Mark old items for this location type as not current
            cur.execute(
                """
                UPDATE pantry_items SET is_current = FALSE
                WHERE user_id = %s AND snapshot_id IN (
                    SELECT id FROM pantry_snapshots
                    WHERE user_id = %s AND snapshot_type = %s
                )
                """,
                (user_id, user_id, snapshot_type),
            )
            cur.execute(
                """
                INSERT INTO pantry_snapshots (user_id, snapshot_type, telegram_file_id, raw_extraction)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (user_id, snapshot_type, telegram_file_id, json.dumps(raw_extraction)),
            )
            snapshot_id = cur.fetchone()[0]
        conn.commit()
    return snapshot_id


def insert_pantry_items(snapshot_id: int, user_id: int, items: list[dict]):
    """Insert pantry items. Each dict: item_name, normalized_name, category,
    estimated_qty, condition."""
    with _conn() as conn:
        with conn.cursor() as cur:
            for item in items:
                cur.execute(
                    """
                    INSERT INTO pantry_items
                        (snapshot_id, user_id, item_name, normalized_name,
                         category, estimated_qty, condition, is_current)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                    """,
                    (snapshot_id, user_id, item["item_name"], item["normalized_name"],
                     item.get("category"), item.get("estimated_qty"),
                     item.get("condition")),
                )
        conn.commit()


def get_current_pantry_items(user_id: int) -> list[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT pi.item_name, pi.normalized_name, pi.category,
                       pi.estimated_qty, pi.condition,
                       ps.snapshot_type, ps.created_at
                FROM pantry_items pi
                JOIN pantry_snapshots ps ON ps.id = pi.snapshot_id
                WHERE pi.user_id = %s AND pi.is_current = TRUE
                ORDER BY ps.snapshot_type, pi.item_name
                """,
                (user_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def clear_pantry_items(user_id: int):
    """Mark all pantry items as not current for this user."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pantry_items SET is_current = FALSE WHERE user_id = %s",
                (user_id,),
            )
        conn.commit()


# ── Shopping Suggestions ─────────────────────────────────────────────────────

def save_suggestions(user_id: int, suggestions: list[dict]):
    """Replace current suggestions for user."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM shopping_suggestions WHERE user_id = %s AND dismissed = FALSE",
                (user_id,),
            )
            for s in suggestions:
                cur.execute(
                    """
                    INSERT INTO shopping_suggestions
                        (user_id, item_name, normalized_name, reason, priority,
                         last_purchased, in_pantry)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, s["item_name"], s["normalized_name"], s["reason"],
                     s["priority"], s.get("last_purchased"), s.get("in_pantry", False)),
                )
        conn.commit()


def get_suggestions(user_id: int) -> list[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT item_name, normalized_name, reason, priority,
                       last_purchased, in_pantry
                FROM shopping_suggestions
                WHERE user_id = %s AND dismissed = FALSE
                ORDER BY
                    CASE priority
                        WHEN 'high' THEN 1
                        WHEN 'normal' THEN 2
                        WHEN 'low' THEN 3
                    END,
                    item_name
                """,
                (user_id,),
            )
            return [dict(row) for row in cur.fetchall()]


# ── Chat message helpers ───────────────────────────────────────────────────

def insert_chat_message(user_id: int, role: str, content: str):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chat_messages (user_id, role, content) VALUES (%s, %s, %s)",
                (user_id, role, content),
            )
        conn.commit()


def get_recent_chat_messages(user_id: int, limit: int = 20) -> list[dict]:
    """Return last N messages oldest-first."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT role, content FROM chat_messages
                   WHERE user_id = %s
                   ORDER BY created_at DESC LIMIT %s""",
                (user_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
    return list(reversed(rows))


# ── Manual pantry helpers ─────────────────────────────────────────────────

def add_manual_pantry_item(
    user_id: int, item_name: str, normalized_name: str,
    location: str, category: Optional[str] = None,
):
    """Add a pantry item manually (no photo snapshot)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO pantry_snapshots (user_id, snapshot_type, telegram_file_id, raw_extraction)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (user_id, location, "manual", '{"source": "manual"}'),
            )
            snapshot_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO pantry_items
                    (snapshot_id, user_id, item_name, normalized_name,
                     category, estimated_qty, condition, is_current)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)""",
                (snapshot_id, user_id, item_name, normalized_name, category, "unknown", "good"),
            )
        conn.commit()


def remove_pantry_item(user_id: int, normalized_name: str) -> int:
    """Mark matching current pantry items as not current. Returns count removed."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE pantry_items SET is_current = FALSE
                   WHERE user_id = %s AND normalized_name = %s AND is_current = TRUE""",
                (user_id, normalized_name),
            )
            count = cur.rowcount
        conn.commit()
    return count


# ── Reminder helpers ─────────────────────────────────────────────────────

def insert_reminder(user_id: int, reminder_text: str, due_at: str):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO reminders (user_id, reminder_text, due_at) VALUES (%s, %s, %s)",
                (user_id, reminder_text, due_at),
            )
        conn.commit()


def get_due_reminders() -> list[dict]:
    """Return unsent reminders where due_at <= NOW()."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM reminders WHERE sent = FALSE AND due_at <= NOW()"
            )
            return [dict(r) for r in cur.fetchall()]


def mark_reminder_sent(reminder_id: int):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE reminders SET sent = TRUE WHERE id = %s", (reminder_id,))
        conn.commit()


def get_pending_reminders(user_id: int) -> list[dict]:
    """Return future unsent reminders for this user."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, reminder_text, due_at FROM reminders
                   WHERE user_id = %s AND sent = FALSE
                   ORDER BY due_at""",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_user_timezone(user_id: int) -> str:
    """Get user's timezone from the users table. Falls back to 'America/Los_Angeles'."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT timezone FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            if row and row.get("timezone"):
                return row["timezone"]
    return "America/Los_Angeles"


# ── User Profiles ────────────────────────────────────────────────────────────

def upsert_user_profile(user_id: int, family_size: int = None,
                        dietary_preferences: list = None,
                        preferred_shopping_day: int = None) -> dict:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO user_profiles (user_id, family_size, dietary_preferences, preferred_shopping_day, updated_at)
                VALUES (%s, COALESCE(%s, 1), COALESCE(%s, '[]'::jsonb), %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    family_size = COALESCE(%s, user_profiles.family_size),
                    dietary_preferences = COALESCE(%s, user_profiles.dietary_preferences),
                    preferred_shopping_day = COALESCE(%s, user_profiles.preferred_shopping_day),
                    updated_at = NOW()
                RETURNING user_id, family_size, dietary_preferences, preferred_shopping_day, updated_at
                """,
                (user_id, family_size,
                 json.dumps(dietary_preferences) if dietary_preferences is not None else None,
                 preferred_shopping_day,
                 family_size,
                 json.dumps(dietary_preferences) if dietary_preferences is not None else None,
                 preferred_shopping_day),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return row


def get_user_profile(user_id: int) -> dict:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, family_size, dietary_preferences, preferred_shopping_day, updated_at "
                "FROM user_profiles WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    if row:
        return dict(row)
    return {"user_id": user_id, "family_size": 1, "dietary_preferences": [], "preferred_shopping_day": None}


# ── Stocking Rules ───────────────────────────────────────────────────────────

def upsert_stocking_rule(user_id: int, normalized_name: str, display_name: str,
                         min_quantity: int = 1):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stocking_rules (user_id, normalized_name, display_name, min_quantity)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, normalized_name) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    min_quantity = EXCLUDED.min_quantity,
                    active = TRUE
                """,
                (user_id, normalized_name, display_name, min_quantity),
            )
        conn.commit()


def remove_stocking_rule(user_id: int, normalized_name: str) -> int:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE stocking_rules SET active = FALSE WHERE user_id = %s AND normalized_name = %s AND active = TRUE",
                (user_id, normalized_name),
            )
            count = cur.rowcount
        conn.commit()
    return count


def get_stocking_rules(user_id: int) -> list[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT normalized_name, display_name, min_quantity, created_at "
                "FROM stocking_rules WHERE user_id = %s AND active = TRUE ORDER BY display_name",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


# ── Consumption Rates ────────────────────────────────────────────────────────

def get_all_purchased_items(user_id: int, min_purchases: int = 2, days: int = 365) -> list[dict]:
    """Get items with aggregated purchase dates for consumption modeling."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ri.normalized_name,
                       COUNT(DISTINCT r.purchase_date) as purchase_count,
                       ARRAY_AGG(DISTINCT r.purchase_date ORDER BY r.purchase_date) as purchase_dates,
                       MAX(r.purchase_date) as last_purchased
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                WHERE ri.user_id = %s
                  AND r.purchase_date >= CURRENT_DATE - INTERVAL '%s days'
                GROUP BY ri.normalized_name
                HAVING COUNT(DISTINCT r.purchase_date) >= %s
                """,
                (user_id, days, min_purchases),
            )
            return [dict(r) for r in cur.fetchall()]


def upsert_consumption_rate(user_id: int, normalized_name: str,
                            avg_interval_days: float, estimated_runout_date: date,
                            confidence: str, data_points: int):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO consumption_rates
                    (user_id, normalized_name, avg_interval_days, estimated_runout_date,
                     confidence, data_points, last_computed)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (user_id, normalized_name) DO UPDATE SET
                    avg_interval_days = EXCLUDED.avg_interval_days,
                    estimated_runout_date = EXCLUDED.estimated_runout_date,
                    confidence = EXCLUDED.confidence,
                    data_points = EXCLUDED.data_points,
                    last_computed = NOW()
                """,
                (user_id, normalized_name, avg_interval_days, estimated_runout_date,
                 confidence, data_points),
            )
        conn.commit()


def get_consumption_rates(user_id: int) -> list[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT normalized_name, avg_interval_days, estimated_runout_date,
                       confidence, data_points, last_computed
                FROM consumption_rates
                WHERE user_id = %s
                ORDER BY estimated_runout_date
                """,
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]


# ── Restock Notifications ────────────────────────────────────────────────────

def insert_restock_notification(user_id: int, normalized_name: str, notified_for_date: date) -> bool:
    """Insert notification dedup record. Returns True if inserted (not a duplicate)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    INSERT INTO restock_notifications (user_id, normalized_name, notified_for_date)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, normalized_name, notified_for_date),
                )
                conn.commit()
                return True
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                return False


def get_all_user_ids() -> list[int]:
    """Return all user IDs."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users")
            return [row[0] for row in cur.fetchall()]
