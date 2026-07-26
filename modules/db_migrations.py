"""Ordered, repeatable SQLite schema migrations."""

from __future__ import annotations

import sqlite3
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CURRENT_SCHEMA_VERSION = 8
CANONICAL_FLOAT_DECIMAL_PLACES = 8


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


def _migration_7(conn: sqlite3.Connection) -> None:
    """Persist the user-confirmed pricing mode for IGXE imports."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rental_orders'"
    ).fetchone()
    if not table_exists:
        # A few historical minimalist databases only contained ``items``.
        # DBManager creates the complete rental table before normal migration;
        # standalone migration callers must still be able to upgrade safely.
        return
    _add_columns(
        conn,
        "rental_orders",
        (("pricing_mode", "TEXT NOT NULL DEFAULT ''"),),
    )


def _canonical_float(value) -> str:
    """Round a valid source float to the eight-place storage convention."""
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite():
            return str(value or "").strip()
        quantum = Decimal(1).scaleb(-CANONICAL_FLOAT_DECIMAL_PLACES)
        return format(
            number.quantize(quantum, rounding=ROUND_HALF_UP),
            f".{CANONICAL_FLOAT_DECIMAL_PLACES}f",
        )
    except (InvalidOperation, TypeError, ValueError):
        return str(value or "").strip()


def _asset_identity_name(name: str, market_hash_name: str) -> str:
    """Collapse presentation-only star and punctuation variants for merging."""
    text = unicodedata.normalize("NFKC", market_hash_name or name or "")
    text = text.replace("（★）", "★").replace("(★)", "★")
    text = re.sub(r"[\s★]", "", text)
    return text.casefold()


def _migration_8(conn: sqlite3.Connection) -> None:
    """Normalise stored floats and merge duplicate assets created by precision drift."""
    tables = {
        str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for table in ("items", "rental_orders"):
        if table not in tables or not {"id", "float_val"}.issubset(_columns(conn, table)):
            continue
        rows = conn.execute(f"SELECT id, float_val FROM {table}").fetchall()
        for row_id, float_value in rows:
            normalised = _canonical_float(float_value)
            if normalised != str(float_value or ""):
                conn.execute(
                    f"UPDATE {table} SET float_val=? WHERE id=?",
                    (normalised, row_id),
                )

    required_item_columns = {
        "id", "name", "market_hash_name", "float_val", "platform", "note", "deleted_at",
    }
    if "items" not in tables or not required_item_columns.issubset(_columns(conn, "items")):
        return
    rows = conn.execute(
        """SELECT id, name, market_hash_name, float_val, platform, note
           FROM items WHERE deleted_at=''"""
    ).fetchall()
    groups: dict[tuple[str, str], list[tuple]] = {}
    for row in rows:
        item_id, name, market_hash_name, float_value, _platform, _note = row
        identity_name = _asset_identity_name(name, market_hash_name)
        if identity_name and float_value:
            groups.setdefault((identity_name, str(float_value)), []).append(row)

    merged_at = datetime.now().isoformat(timespec="seconds")
    for _identity, group in groups.items():
        if len(group) < 2:
            continue
        # C5 is the requested canonical record when duplicate platform imports
        # describe the same asset; otherwise retain the oldest local row.
        target = min(
            group,
            key=lambda row: (0 if str(row[4]).upper() == "C5GAME" else 1, int(row[0])),
        )
        target_id, _name, _mhn, _float_value, _platform, target_note = target
        merged_sources = []
        for source in group:
            source_id, _sname, _smhn, _sfloat, source_platform, _source_note = source
            if source_id == target_id:
                continue
            if "rental_orders" in tables and {
                "item_id", "match_method", "match_confidence",
            }.issubset(_columns(conn, "rental_orders")):
                conn.execute(
                    """UPDATE rental_orders
                       SET item_id=?, match_method='precision_merge', match_confidence=1.0
                       WHERE item_id=?""",
                    (target_id, source_id),
                )
            conn.execute(
                """UPDATE items SET deleted_at=?,
                   note=TRIM(COALESCE(note, '') || '\n已合并到资产 #' || ? || '（磨损统一为 8 位）')
                   WHERE id=?""",
                (merged_at, target_id, source_id),
            )
            merged_sources.append(f"#{source_id} {source_platform or '未知平台'}")
        if merged_sources:
            addition = (
                f"磨损标准化合并于 {merged_at}："
                f"已合并来源 {'、'.join(merged_sources)}；"
                "各订单的平台信息仍保留在订单历史中。"
            )
            conn.execute(
                "UPDATE items SET note=TRIM(COALESCE(?, '') || '\n' || ?) WHERE id=?",
                (target_note, addition, target_id),
            )


MIGRATIONS = {
    1: _migration_1,
    2: _migration_2,
    3: _migration_3,
    4: _migration_4,
    5: _migration_5,
    6: _migration_6,
    7: _migration_7,
    8: _migration_8,
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
