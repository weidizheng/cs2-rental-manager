"""Ordered, repeatable SQLite schema migrations."""

from __future__ import annotations

import sqlite3


CURRENT_SCHEMA_VERSION = 6


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_columns(
    conn: sqlite3.Connection, table: str, definitions: tuple[tuple[str, str], ...]
) -> None:
    existing = _columns(conn, table)
    for name, definition in definitions:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            existing.add(name)


def _migration_1(conn: sqlite3.Connection) -> None:
    """Bring pre-clipboard-import databases up to the legacy complete schema."""
    _add_columns(
        conn,
        "items",
        (
            ("asset_id", "TEXT DEFAULT ''"),
            ("market_hash_name", "TEXT DEFAULT ''"),
        ),
    )
    _add_columns(
        conn,
        "rental_orders",
        (
            ("daily_rent", "REAL DEFAULT 0.0"),
            ("rental_days", "REAL DEFAULT 0.0"),
            ("rental_type", "TEXT DEFAULT ''"),
            ("deposit", "REAL DEFAULT 0.0"),
            ("rental_end_time", "TEXT DEFAULT ''"),
            ("return_deadline", "TEXT DEFAULT ''"),
            ("transfer_status", "TEXT DEFAULT ''"),
            ("transfer_reward", "REAL DEFAULT 0.0"),
            ("reward_status", "TEXT DEFAULT ''"),
            ("transfer_reward_known", "INTEGER DEFAULT 0"),
        ),
    )


def _migration_2(conn: sqlite3.Connection) -> None:
    """Store money exactly as integer cents while retaining compatibility columns."""
    _add_columns(
        conn,
        "items",
        (
            ("cost_cents", "INTEGER NOT NULL DEFAULT 0"),
            ("rent_cents", "INTEGER NOT NULL DEFAULT 0"),
            ("income_cents", "INTEGER NOT NULL DEFAULT 0"),
        ),
    )
    _add_columns(
        conn,
        "rental_orders",
        (
            ("income_cents", "INTEGER NOT NULL DEFAULT 0"),
            ("daily_rent_cents", "INTEGER NOT NULL DEFAULT 0"),
            ("deposit_cents", "INTEGER NOT NULL DEFAULT 0"),
            ("transfer_reward_cents", "INTEGER NOT NULL DEFAULT 0"),
        ),
    )
    conn.execute(
        """UPDATE items SET
               cost_cents=CAST(ROUND(COALESCE(cost, 0) * 100) AS INTEGER),
               rent_cents=CAST(ROUND(COALESCE(rent, 0) * 100) AS INTEGER),
               income_cents=CAST(ROUND(COALESCE(income, 0) * 100) AS INTEGER)
           WHERE cost_cents=0 AND rent_cents=0 AND income_cents=0"""
    )
    conn.execute(
        """UPDATE rental_orders SET
               income_cents=CAST(ROUND(COALESCE(income, 0) * 100) AS INTEGER),
               daily_rent_cents=CAST(ROUND(COALESCE(daily_rent, 0) * 100) AS INTEGER),
               deposit_cents=CAST(ROUND(COALESCE(deposit, 0) * 100) AS INTEGER),
               transfer_reward_cents=CAST(ROUND(COALESCE(transfer_reward, 0) * 100) AS INTEGER)
           WHERE income_cents=0 AND daily_rent_cents=0
             AND deposit_cents=0 AND transfer_reward_cents=0"""
    )


def _migration_3(conn: sqlite3.Connection) -> None:
    """Add stable order associations and recoverable asset deletion."""
    _add_columns(
        conn,
        "items",
        (("deleted_at", "TEXT NOT NULL DEFAULT ''"),),
    )
    _add_columns(
        conn,
        "rental_orders",
        (
            ("item_id", "INTEGER DEFAULT NULL"),
            ("match_method", "TEXT NOT NULL DEFAULT ''"),
            ("match_confidence", "REAL NOT NULL DEFAULT 0.0"),
        ),
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rental_orders_item_start "
        "ON rental_orders(item_id, start_time)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_items_asset_id "
        "ON items(asset_id) WHERE asset_id <> '' AND deleted_at = ''"
    )


def _migration_4(conn: sqlite3.Connection) -> None:
    """Persist user watch lists separately from rebuildable quote caches."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS market_categories (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT ''
        )"""
    )


def _migration_5(conn: sqlite3.Connection) -> None:
    """Give every asset a portable, globally unique identity."""
    # Preserve the first occurrence of any legacy manually duplicated ID and
    # replace every other duplicate/blank value with independent random IDs.
    conn.execute(
        """UPDATE items SET asset_id=''
           WHERE asset_id<>'' AND id NOT IN (
               SELECT MIN(id) FROM items WHERE asset_id<>'' GROUP BY asset_id
           )"""
    )
    conn.execute(
        """UPDATE items SET asset_id=lower(hex(randomblob(16)))
           WHERE trim(COALESCE(asset_id, ''))=''"""
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_items_asset_id_unique "
        "ON items(asset_id) WHERE asset_id <> ''"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS market_watch_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id TEXT NOT NULL,
            identity TEXT NOT NULL,
            data_json TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(category_id) REFERENCES market_categories(id) ON DELETE CASCADE,
            UNIQUE(category_id, identity)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )"""
    )


def _migration_6(conn: sqlite3.Connection) -> None:
    """Persist an absolute cooldown deadline for newly purchased assets."""
    _add_columns(
        conn,
        "items",
        (("cooldown_until", "TEXT NOT NULL DEFAULT ''"),),
    )
    conn.execute(
        """UPDATE items
           SET cooldown_until=datetime('now', '+' || expire_hours || ' hours')
           WHERE status='CD冷却' AND cooldown_until=''
             AND expire_hours>=0 AND expire_hours<999"""
    )


MIGRATIONS = {
    1: _migration_1,
    2: _migration_2,
    3: _migration_3,
    4: _migration_4,
    5: _migration_5,
    6: _migration_6,
}


def run_migrations(conn: sqlite3.Connection) -> int:
    """Apply every missing migration in a transaction and return the new version."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {version} 高于当前程序支持的 {CURRENT_SCHEMA_VERSION}"
        )
    for target in range(version + 1, CURRENT_SCHEMA_VERSION + 1):
        migration = MIGRATIONS[target]
        try:
            migration(conn)
            conn.execute(f"PRAGMA user_version={target}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return CURRENT_SCHEMA_VERSION
