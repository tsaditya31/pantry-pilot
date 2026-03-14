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
