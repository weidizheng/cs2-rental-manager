"""Persistent, phase-aware cache for ECO's full market-price snapshot."""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from modules.paths import get_private_path


CACHE_TTL_SECONDS = 10 * 60
DB_PATH = get_private_path("eco_market_cache.db")


def normalize_style(value: str | None) -> str:
    """Normalize ECO StyleName and UI phase values to a comparable key."""
    style = re.sub(r"[^a-z0-9]+", "", (value or "").lower())
    return {
        "p1": "phase1",
        "p2": "phase2",
        "p3": "phase3",
        "p4": "phase4",
    }.get(style, style)


def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


class ECOMarketCache:
    """SQLite snapshot cache scoped to one ECO partner account."""

    def __init__(self, partner_id: str, db_path: Path = DB_PATH):
        self.partner_id = partner_id
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _session(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eco_cache_meta (
                    partner_id TEXT PRIMARY KEY,
                    fetched_at INTEGER NOT NULL,
                    item_count INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS eco_prices (
                    partner_id TEXT NOT NULL,
                    hash_name TEXT NOT NULL,
                    style_key TEXT NOT NULL,
                    style_name TEXT NOT NULL,
                    sell_price REAL NOT NULL,
                    rent_price REAL NOT NULL,
                    PRIMARY KEY (partner_id, hash_name, style_key)
                )
                """
            )

    def status(self) -> dict[str, int] | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT fetched_at, item_count FROM eco_cache_meta WHERE partner_id = ?",
                (self.partner_id,),
            ).fetchone()
        if row is None:
            return None
        return {"fetched_at": row["fetched_at"], "item_count": row["item_count"]}

    def is_fresh(self, ttl_seconds: int = CACHE_TTL_SECONDS) -> bool:
        status = self.status()
        return bool(status and time.time() - status["fetched_at"] <= ttl_seconds)

    def load_snapshot(self) -> dict[tuple[str, str], dict[str, Any]]:
        with self._session() as conn:
            rows = conn.execute(
                """
                SELECT hash_name, style_key, style_name, sell_price, rent_price
                FROM eco_prices WHERE partner_id = ?
                """,
                (self.partner_id,),
            ).fetchall()
        return {
            (row["hash_name"], row["style_key"]): {
                "eco_sell_price": row["sell_price"],
                "eco_rent_price": row["rent_price"],
                "style_name": row["style_name"],
            }
            for row in rows
        }

    def load_snapshot_for_hash_names(
        self, hash_names: list[str]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Load only watched market names while preserving all phase rows."""
        names = list(dict.fromkeys(str(name).strip() for name in hash_names if str(name).strip()))
        if not names:
            return {}

        rows = []
        # Stay well below SQLite's common 999-variable limit.
        for start in range(0, len(names), 400):
            chunk = names[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            with self._session() as conn:
                rows.extend(conn.execute(
                    f"""
                    SELECT hash_name, style_key, style_name, sell_price, rent_price
                    FROM eco_prices
                    WHERE partner_id = ? AND hash_name IN ({placeholders})
                    """,
                    (self.partner_id, *chunk),
                ).fetchall())
        return {
            (row["hash_name"], row["style_key"]): {
                "eco_sell_price": row["sell_price"],
                "eco_rent_price": row["rent_price"],
                "style_name": row["style_name"],
            }
            for row in rows
        }

    def replace_snapshot(
        self,
        items: list[dict[str, Any]],
        return_snapshot: bool = True,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Atomically replace the prior full snapshot with a newly fetched one."""
        rows: dict[tuple[str, str], tuple[str, str, str, float, float]] = {}
        for item in items:
            hash_name = item.get("HashName")
            if not hash_name:
                continue
            style_name = str(item.get("StyleName") or "")
            style_key = normalize_style(style_name)
            rows[(hash_name, style_key)] = (
                hash_name,
                style_key,
                style_name,
                _to_float(item.get("Price")),
                _to_float(item.get("RentGoodsBottomPrice")),
            )

        fetched_at = int(time.time())
        with self._session() as conn:
            conn.execute("DELETE FROM eco_prices WHERE partner_id = ?", (self.partner_id,))
            conn.executemany(
                """
                INSERT INTO eco_prices
                (partner_id, hash_name, style_key, style_name, sell_price, rent_price)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(self.partner_id, *row) for row in rows.values()],
            )
            conn.execute(
                """
                INSERT INTO eco_cache_meta (partner_id, fetched_at, item_count)
                VALUES (?, ?, ?)
                ON CONFLICT(partner_id) DO UPDATE SET
                    fetched_at=excluded.fetched_at,
                    item_count=excluded.item_count
                """,
                (self.partner_id, fetched_at, len(rows)),
            )
        return self.load_snapshot() if return_snapshot else {}

    @staticmethod
    def find_price(
        snapshot: dict[tuple[str, str], dict[str, Any]],
        hash_name: str,
        phase: str = "",
    ) -> dict[str, Any]:
        style_key = normalize_style(phase)
        base_item = snapshot.get((hash_name, ""))
        if style_key:
            phase_item = snapshot.get((hash_name, style_key))
            if phase_item:
                # ECO can return phase-specific selling prices while leaving
                # the phase-specific rental field at zero.  Keep the precise
                # phase selling price, but use the unphased rental quote when
                # it is the only available rental value.
                if _to_float(phase_item.get("eco_rent_price")) <= 0 and base_item:
                    base_rent = _to_float(base_item.get("eco_rent_price"))
                    if base_rent > 0:
                        merged = dict(phase_item)
                        merged["eco_rent_price"] = base_rent
                        merged["rent_price_source"] = "base_fallback"
                        return merged
                return phase_item

        if base_item:
            return base_item

        candidates = [item for (name, _), item in snapshot.items() if name == hash_name]
        return candidates[0] if len(candidates) == 1 else {}
