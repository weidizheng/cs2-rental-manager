import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime

from modules.atomic_io import atomic_write_json
from modules.db_migrations import run_migrations
from modules.domain_models import cents_to_money, money_to_cents
from modules.paths import get_private_data_dir
from modules.rental_matching import match_order_to_items
from modules.rental_terms import classify_rental_term
from modules.secret_store import protect_secret, unprotect_secret

DATA_DIR = str(get_private_data_dir())
DB_PATH = os.path.join(DATA_DIR, "app.db")
ITEMS_JSON_PATH = os.path.join(DATA_DIR, "items.json")
CONFIGS_JSON_PATH = os.path.join(DATA_DIR, "configs.json")

logger = logging.getLogger("CS2Rental")

WATCH_PERSIST_FIELDS = (
    "key", "name", "phase", "market_hash_name", "image_url", "links",
    "schema_id", "paint_index", "csqaq_good_id", "c5_id", "yyyp_id",
    "igxe_id", "eco_id",
)
SECRET_CONFIG_KEYS = {
    "csqaq_token", "csfloat_api_key", "eco_partner_id", "eco_rsa_key",
}

# 文件写锁，防止多线程并发写入 items.json / configs.json 造成数据损坏
_write_lock = threading.RLock()


class DBManager:

    def __init__(
            self,
            db_path=DB_PATH,
            items_json=ITEMS_JSON_PATH,
            configs_json=CONFIGS_JSON_PATH,
    ):
        self.db_path = db_path
        self.items_json_path = items_json
        self.configs_json_path = configs_json
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._db_lock = threading.RLock()
        self._conn = None  # 懒加载单例连接
        self.init_db()

    def get_connection(self):
        """Return one serialized connection owned by this manager."""
        with self._db_lock:
            if self._conn is None:
                self._conn = sqlite3.connect(
                    self.db_path,
                    check_same_thread=False,
                    timeout=10,
                )
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute("PRAGMA busy_timeout=10000")
            return self._conn

    def close(self):
        """Release the persistent SQLite handle when the application exits."""
        with self._db_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def init_db(self):
        """初始化数据库表及数据/配置同步"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # 1. 创建饰品表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            market_hash_name TEXT DEFAULT '',
            phase TEXT DEFAULT '-',
            pattern TEXT DEFAULT '-',
            float_val TEXT DEFAULT '0.000',
            cost REAL DEFAULT 0.0,
            cost_cents INTEGER NOT NULL DEFAULT 0,
            platform TEXT DEFAULT 'BUFF',
            status TEXT DEFAULT '在库',
            rent REAL DEFAULT 0.0,
            rent_cents INTEGER NOT NULL DEFAULT 0,
            days INTEGER DEFAULT 0,
            income REAL DEFAULT 0.0,
            income_cents INTEGER NOT NULL DEFAULT 0,
            expire_hours REAL DEFAULT 999.0,
            cooldown_until TEXT NOT NULL DEFAULT '',
            note TEXT DEFAULT '',
            asset_id TEXT DEFAULT '',
            deleted_at TEXT NOT NULL DEFAULT ''
        )
        """)

        # 2. 软件配置表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS configs (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # 先尝试从 configs.json 加载保存过的配置
        # Existing non-empty SQLite values stay authoritative. The portable
        # backup only fills missing/blank rows, so malformed JSON can never
        # erase every API credential during startup.
        saved_configs = self.load_configs_from_json()

        for key, value in saved_configs.items():
            if not isinstance(key, str):
                continue
            cursor.execute("SELECT value FROM configs WHERE key = ?", (key,))
            existing = cursor.fetchone()
            backup_value = "" if value is None else str(value)
            if existing is None:
                cursor.execute(
                    "INSERT INTO configs (key, value) VALUES (?, ?)",
                    (key, backup_value),
                )
            elif (existing[0] is None or str(existing[0]) == "") and backup_value:
                cursor.execute(
                    "UPDATE configs SET value = ? WHERE key = ?",
                    (backup_value, key),
                )

        default_configs = [
            (
                "eco_partner_id",
                saved_configs.get("eco_partner_id", ""),
            ),
            ("eco_rsa_key", saved_configs.get("eco_rsa_key", "")),
            ("csqaq_token", saved_configs.get("csqaq_token", "")),
            ("csfloat_api_key", saved_configs.get("csfloat_api_key", "")),
            ("auto_usd_cny_rate", saved_configs.get("auto_usd_cny_rate", "1")),
            ("usd_cny_rate", saved_configs.get("usd_cny_rate", "7.20")),
            (
                "refresh_interval",
                saved_configs.get("refresh_interval", "15"),
            ),
            (
                "c5_first_fee",
                saved_configs.get("c5_first_fee", "0.15"),
            ),
            ("c5_relet_fee", saved_configs.get("c5_relet_fee", "0.15")),
            (
                "uu_first_fee",
                saved_configs.get("uu_first_fee", "0.10"),
            ),
            ("uu_relet_fee", saved_configs.get("uu_relet_fee", "0.05")),
            (
                "igxe_first_fee",
                saved_configs.get("igxe_first_fee", "0.05"),
            ),
            (
                "igxe_relet_fee",
                saved_configs.get("igxe_relet_fee", "0.05"),
            ),
            (
                "eco_first_fee",
                saved_configs.get("eco_first_fee", "0"),
            ),
            (
                "eco_relet_fee",
                saved_configs.get("eco_relet_fee", "0"),
            ),
        ]
        for k, v in default_configs:
            cursor.execute(
                "INSERT OR IGNORE INTO configs (key, value) VALUES (?, ?)",
                (k, str(v)),
            )

        # Existing plaintext credentials are transparently upgraded to the
        # current Windows account's DPAPI protection. API callers continue to
        # receive plaintext through ``get_config``.
        credentials_upgraded = False
        for secret_key in SECRET_CONFIG_KEYS:
            cursor.execute("SELECT value FROM configs WHERE key=?", (secret_key,))
            row = cursor.fetchone()
            if row and str(row[0] or "") and not str(row[0]).startswith("dpapi:"):
                cursor.execute(
                    "UPDATE configs SET value=? WHERE key=?",
                    (protect_secret(str(row[0])), secret_key),
                )
                credentials_upgraded = True

        # Platform rental orders are stored separately from inventory.  A web
        # sync must not change an asset's status until matching is reviewed.
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rental_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            order_no TEXT NOT NULL,
            item_name TEXT DEFAULT '',
            float_val TEXT DEFAULT '',
            income REAL DEFAULT 0.0,
            income_cents INTEGER NOT NULL DEFAULT 0,
            daily_rent REAL DEFAULT 0.0,
            daily_rent_cents INTEGER NOT NULL DEFAULT 0,
            rental_days REAL DEFAULT 0.0,
            rental_type TEXT DEFAULT '',
            deposit REAL DEFAULT 0.0,
            deposit_cents INTEGER NOT NULL DEFAULT 0,
            start_time TEXT DEFAULT '',
            return_time TEXT DEFAULT '',
            rental_end_time TEXT DEFAULT '',
            return_deadline TEXT DEFAULT '',
            transfer_status TEXT DEFAULT '',
            status TEXT DEFAULT '',
            raw_text TEXT DEFAULT '',
            transfer_reward REAL DEFAULT 0.0,
            transfer_reward_cents INTEGER NOT NULL DEFAULT 0,
            reward_status TEXT DEFAULT '',
            transfer_reward_known INTEGER DEFAULT 0,
            item_id INTEGER DEFAULT NULL,
            match_method TEXT NOT NULL DEFAULT '',
            match_confidence REAL NOT NULL DEFAULT 0.0,
            synced_at TEXT NOT NULL,
            UNIQUE(platform, order_no),
            FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE SET NULL
        )
        """)

        schema_version = run_migrations(conn)
        logger.info("SQLite schema version: %s", schema_version)

        # Populate the new field for historical imports. Explicit labels in the
        # retained order text win; otherwise use the confirmed platform ranges.
        cursor.execute(
            "SELECT id, platform, rental_days, raw_text, rental_type FROM rental_orders"
        )
        for order_id, platform, rental_days, raw_text, rental_type in cursor.fetchall():
            classified_type = classify_rental_term(
                platform, rental_days, raw_text, rental_type
            )
            if classified_type != str(rental_type or ""):
                cursor.execute(
                    "UPDATE rental_orders SET rental_type=? WHERE id=?",
                    (classified_type, order_id),
                )

        # 3. 饰品初始化
        cursor.execute("SELECT COUNT(*) FROM items")
        if cursor.fetchone()[0] == 0:
            self.load_items_from_json(conn)

        # Link legacy orders only when the match is unique. Ambiguous rows stay
        # unlinked until the user confirms them in the import/history workflow.
        cursor.execute(
            "SELECT id, name, float_val, asset_id FROM items WHERE deleted_at=''"
        )
        inventory = [
            {"id": row[0], "name": row[1], "float_val": row[2], "asset_id": row[3]}
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """SELECT id, item_name, float_val FROM rental_orders
               WHERE item_id IS NULL"""
        )
        for order_id, item_name, float_val in cursor.fetchall():
            match = match_order_to_items(
                {"item_name": item_name, "float_val": float_val}, inventory
            )
            if match["item_id"] is not None:
                cursor.execute(
                    """UPDATE rental_orders
                       SET item_id=?, match_method=?, match_confidence=?
                       WHERE id=?""",
                    (
                        match["item_id"], match["method"],
                        match["confidence"], order_id,
                    ),
                )

        conn.commit()
        if credentials_upgraded:
            self.save_all_configs_to_json()

    def load_configs_from_json(self):
        """读取 configs.json 备份文件"""
        if os.path.exists(self.configs_json_path):
            try:
                with open(self.configs_json_path, "r", encoding="utf-8") as f:
                    raw = f.read()
                loaded = json.loads(raw)
                return loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError as exc:
                # A common manual-edit mistake is pasting the CSFloat key as a
                # bare token instead of a quoted JSON string. Recover only this
                # narrowly defined form; never guess arbitrary damaged JSON.
                repaired = re.sub(
                    r'((?:\{|,)\s*"csfloat_api_key"\s*:\s*)'
                    r'([A-Za-z0-9._~+/=\-]+)"?(\s*[,}])',
                    r'\1"\2"\3',
                    raw,
                    count=1,
                )
                if repaired != raw:
                    try:
                        loaded = json.loads(repaired)
                    except json.JSONDecodeError:
                        loaded = None
                    if isinstance(loaded, dict):
                        self._write_config_dict(loaded)
                        logger.warning(
                            "configs.json 中未加引号的 CSFloat API Key 已自动修复"
                        )
                        return loaded
                logger.warning(
                    "读取 configs.json 失败: JSON 第 %s 行，第 %s 列",
                    exc.lineno,
                    exc.colno,
                )
            except Exception as e:
                logger.warning(f"读取 configs.json 失败: {e}")
        return {}

    def _write_config_dict(self, config_dict):
        """Atomically replace configs.json without leaving partial JSON."""
        atomic_write_json(self.configs_json_path, config_dict)

    def save_all_configs_to_json(self):
        """将最新的全局配置写回 configs.json 备份（线程安全）"""
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM configs")
            rows = cursor.fetchall()
            config_dict = {r[0]: r[1] for r in rows}

        with _write_lock:
            try:
                self._write_config_dict(config_dict)
                logger.info(f"配置已同步至 {self.configs_json_path}")
            except Exception as e:
                logger.error(f"保存 configs.json 失败: {e}")

    def load_items_from_json(self, conn=None):
        """从 data/items.json 导入饰品数据"""
        if not os.path.exists(self.items_json_path):
            return False
        try:
            with open(self.items_json_path, "r", encoding="utf-8") as f:
                items = json.load(f)

            should_commit = False
            if conn is None:
                conn = self.get_connection()
                should_commit = True

            cursor = conn.cursor()
            for item in items:
                cursor.execute(
                    """
                INSERT INTO items (
                    name, market_hash_name, phase, pattern, float_val,
                    cost, cost_cents, platform, status, rent, rent_cents,
                    days, income, income_cents, expire_hours, cooldown_until,
                    note, asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        item.get("name", "未命名"),
                        item.get("market_hash_name", ""),
                        item.get("phase", "-"),
                        item.get("pattern", "-"),
                        item.get("float_val", "0.000"),
                        item.get("cost", 0.0),
                        money_to_cents(item.get("cost", 0.0)),
                        item.get("platform", "BUFF"),
                        item.get("status", "在库"),
                        item.get("rent", 0.0),
                        money_to_cents(item.get("rent", 0.0)),
                        item.get("days", 0),
                        item.get("income", 0.0),
                        money_to_cents(item.get("income", 0.0)),
                        item.get("expire_hours", 999.0),
                        item.get("cooldown_until", ""),
                        item.get("note", ""),
                        item.get("asset_id") or uuid.uuid4().hex,
                    ),
                )
            if should_commit:
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"读取 items.json 失败: {e}")
            return False

    def export_items_to_json(self):
        """Write the portable compatibility snapshot atomically."""
        items = self.get_all_items()
        clean_items = []
        for item in items:
            item_copy = item.copy()
            item_copy.pop("id", None)
            clean_items.append(item_copy)

        with _write_lock:
            try:
                atomic_write_json(self.items_json_path, clean_items)
                return True
            except Exception as e:
                logger.error(f"写入 items.json 失败: {e}")
                return False

    def get_all_items(self):
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, name, market_hash_name, phase, pattern, float_val,
                          cost_cents, platform, status, rent_cents, days,
                          income_cents, expire_hours, cooldown_until, note, asset_id
                   FROM items WHERE deleted_at=''"""
            )
            rows = cursor.fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "market_hash_name": r[2] or "",
                "phase": r[3],
                "pattern": r[4],
                "float_val": r[5],
                "cost": cents_to_money(r[6]),
                "platform": r[7],
                "status": r[8],
                "rent": cents_to_money(r[9]),
                "days": r[10],
                "income": cents_to_money(r[11]),
                "expire_hours": r[12],
                "cooldown_until": r[13] or "",
                "note": r[14],
                "asset_id": r[15],
            }
            for r in rows
        ]

    def add_item(self, item):
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
            INSERT INTO items (
                name, market_hash_name, phase, pattern, float_val,
                cost, cost_cents, platform, status, rent, rent_cents,
                days, income, income_cents, expire_hours, cooldown_until,
                note, asset_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    item.get("name"),
                    item.get("market_hash_name", ""),
                    item.get("phase", "-"),
                    item.get("pattern", "-"),
                    item.get("float_val", "0.000"),
                    item.get("cost", 0.0),
                    money_to_cents(item.get("cost", 0.0)),
                    item.get("platform", "BUFF"),
                    item.get("status", "在库"),
                    item.get("rent", 0.0),
                    money_to_cents(item.get("rent", 0.0)),
                    item.get("days", 0),
                    item.get("income", 0.0),
                    money_to_cents(item.get("income", 0.0)),
                    item.get("expire_hours", 999.0),
                    item.get("cooldown_until", ""),
                    item.get("note", ""),
                    item.get("asset_id") or uuid.uuid4().hex,
                ),
            )
            conn.commit()
        self.export_items_to_json()

    def update_item(self, item_id, item):
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
            UPDATE items SET
                name=?, market_hash_name=?, phase=?, pattern=?, float_val=?,
                cost=?, cost_cents=?, platform=?, status=?, rent=?, rent_cents=?,
                days=?, income=?, income_cents=?, expire_hours=?, cooldown_until=?, note=?,
                asset_id=COALESCE(NULLIF(?, ''), asset_id)
            WHERE id=? AND deleted_at=''
            """,
                (
                    item.get("name"),
                    item.get("market_hash_name", ""),
                    item.get("phase"),
                    item.get("pattern"),
                    item.get("float_val"),
                    item.get("cost"),
                    money_to_cents(item.get("cost", 0.0)),
                    item.get("platform"),
                    item.get("status"),
                    item.get("rent"),
                    money_to_cents(item.get("rent", 0.0)),
                    item.get("days"),
                    item.get("income"),
                    money_to_cents(item.get("income", 0.0)),
                    item.get("expire_hours"),
                    item.get("cooldown_until", ""),
                    item.get("note"),
                    item.get("asset_id", ""),
                    item_id,
                ),
            )
            conn.commit()
        self.export_items_to_json()

    def delete_item(self, item_id):
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE items SET deleted_at=? WHERE id=? AND deleted_at=''",
                (datetime.now().isoformat(timespec="seconds"), item_id),
            )
            conn.commit()
        self.export_items_to_json()

    def restore_item(self, item_id):
        with self._db_lock:
            conn = self.get_connection()
            conn.execute("UPDATE items SET deleted_at='' WHERE id=?", (item_id,))
            conn.commit()
        self.export_items_to_json()

    def upsert_rental_orders(self, platform, orders, *, commit=True):
        """Store manually read rental orders without changing inventory rows."""
        synced_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        inventory = self.get_all_items()
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            for order in orders:
                order_no = str(order.get("order_no", "")).strip()
                if not order_no:
                    continue
                association = match_order_to_items(order, inventory)
                income = float(order.get("income", 0.0) or 0.0)
                daily_rent = float(order.get("daily_rent", 0.0) or 0.0)
                deposit = float(order.get("deposit", 0.0) or 0.0)
                transfer_reward = float(order.get("transfer_reward", 0.0) or 0.0)
                cursor.execute(
                """
                INSERT INTO rental_orders (
                    platform, order_no, item_name, float_val,
                    income, income_cents, daily_rent, daily_rent_cents,
                    rental_days, rental_type, deposit, deposit_cents,
                    start_time, return_time, rental_end_time, return_deadline, transfer_status,
                    status, raw_text, transfer_reward, transfer_reward_cents,
                    reward_status, transfer_reward_known,
                    item_id, match_method, match_confidence, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, order_no) DO UPDATE SET
                    item_name=excluded.item_name,
                    float_val=excluded.float_val,
                    income=excluded.income,
                    income_cents=excluded.income_cents,
                    daily_rent=excluded.daily_rent,
                    daily_rent_cents=excluded.daily_rent_cents,
                    rental_days=excluded.rental_days,
                    rental_type=excluded.rental_type,
                    deposit=excluded.deposit,
                    deposit_cents=excluded.deposit_cents,
                    start_time=excluded.start_time,
                    return_time=excluded.return_time,
                    rental_end_time=CASE WHEN excluded.rental_end_time!=''
                        THEN excluded.rental_end_time ELSE rental_orders.rental_end_time END,
                    return_deadline=CASE WHEN excluded.return_deadline!=''
                        THEN excluded.return_deadline ELSE rental_orders.return_deadline END,
                    transfer_status=CASE WHEN excluded.transfer_status!=''
                        THEN excluded.transfer_status ELSE rental_orders.transfer_status END,
                    status=excluded.status,
                    raw_text=excluded.raw_text,
                    transfer_reward=CASE WHEN excluded.transfer_reward_known=1
                        THEN excluded.transfer_reward ELSE rental_orders.transfer_reward END,
                    transfer_reward_cents=CASE WHEN excluded.transfer_reward_known=1
                        THEN excluded.transfer_reward_cents ELSE rental_orders.transfer_reward_cents END,
                    reward_status=CASE WHEN excluded.transfer_reward_known=1
                        THEN excluded.reward_status ELSE rental_orders.reward_status END,
                    transfer_reward_known=MAX(rental_orders.transfer_reward_known, excluded.transfer_reward_known),
                    item_id=COALESCE(excluded.item_id, rental_orders.item_id),
                    match_method=CASE WHEN excluded.item_id IS NOT NULL
                        THEN excluded.match_method ELSE rental_orders.match_method END,
                    match_confidence=CASE WHEN excluded.item_id IS NOT NULL
                        THEN excluded.match_confidence ELSE rental_orders.match_confidence END,
                    synced_at=excluded.synced_at
                WHERE excluded.synced_at >= rental_orders.synced_at
                """,
                (
                    platform,
                    order_no,
                    order.get("item_name", ""),
                    order.get("float_val", ""),
                    income,
                    money_to_cents(income),
                    daily_rent,
                    money_to_cents(daily_rent),
                    float(order.get("rental_days", 0.0) or 0.0),
                    classify_rental_term(
                        platform,
                        order.get("rental_days", 0.0),
                        order.get("raw_text", ""),
                        order.get("rental_type", ""),
                    ),
                    deposit,
                    money_to_cents(deposit),
                    order.get("start_time", ""),
                    order.get("return_time", ""),
                    order.get("rental_end_time", order.get("return_time", "")),
                    order.get("return_deadline", ""),
                    order.get("transfer_status", ""),
                    order.get("status", ""),
                    order.get("raw_text", ""),
                    transfer_reward,
                    money_to_cents(transfer_reward),
                    order.get("reward_status", ""),
                    1 if order.get("transfer_reward_known", False) else 0,
                    association["item_id"],
                    association["method"],
                    association["confidence"],
                    str(order.get("synced_at") or synced_at),
                ),
            )
            if commit:
                conn.commit()

    def get_rental_orders(self, platform=None):
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            if platform:
                cursor.execute(
                """
                SELECT platform, order_no, item_name, float_val,
                       income_cents, daily_rent_cents, rental_days, rental_type, deposit_cents,
                       start_time, return_time, rental_end_time, return_deadline, transfer_status,
                       status, raw_text, transfer_reward_cents, reward_status,
                       transfer_reward_known, item_id, match_method, match_confidence, synced_at
                FROM rental_orders WHERE platform=?
                ORDER BY synced_at DESC, id DESC
                """,
                (platform,),
            )
            else:
                cursor.execute("""
                SELECT platform, order_no, item_name, float_val,
                       income_cents, daily_rent_cents, rental_days, rental_type, deposit_cents,
                       start_time, return_time, rental_end_time, return_deadline, transfer_status,
                       status, raw_text, transfer_reward_cents, reward_status,
                       transfer_reward_known, item_id, match_method, match_confidence, synced_at
                FROM rental_orders ORDER BY synced_at DESC, id DESC
            """)
            rows = cursor.fetchall()
        columns = (
            "platform", "order_no", "item_name", "float_val", "income_cents", "daily_rent_cents", "rental_days", "rental_type", "deposit_cents",
            "start_time", "return_time", "rental_end_time", "return_deadline", "transfer_status",
            "status", "raw_text", "transfer_reward_cents", "reward_status",
            "transfer_reward_known", "item_id", "match_method", "match_confidence", "synced_at",
        )
        orders = [dict(zip(columns, row)) for row in rows]
        # Keep reads compatible with rows imported before ``rental_type`` was
        # introduced, including databases restored after this instance started.
        # The explicit stored/page value wins; confirmed platform day ranges are
        # only a fallback.
        for order in orders:
            order["income"] = cents_to_money(order.pop("income_cents", 0))
            order["daily_rent"] = cents_to_money(order.pop("daily_rent_cents", 0))
            order["deposit"] = cents_to_money(order.pop("deposit_cents", 0))
            order["transfer_reward"] = cents_to_money(
                order.pop("transfer_reward_cents", 0)
            )
            order["rental_type"] = classify_rental_term(
                order.get("platform"),
                order.get("rental_days"),
                order.get("raw_text"),
                order.get("rental_type"),
            )
        return orders

    def associate_rental_order(self, platform, order_no, item_id):
        """Persist a user-confirmed order association."""
        with self._db_lock:
            conn = self.get_connection()
            exists = conn.execute(
                "SELECT 1 FROM items WHERE id=? AND deleted_at=''", (item_id,)
            ).fetchone()
            if not exists:
                raise ValueError("选择的资产不存在或已删除")
            conn.execute(
                """UPDATE rental_orders
                   SET item_id=?, match_method='manual', match_confidence=1.0
                   WHERE platform=? AND order_no=?""",
                (item_id, platform, order_no),
            )
            conn.commit()

    def get_config(self, key):
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM configs WHERE key = ?", (key,))
            row = cursor.fetchone()
        value = row[0] if row else ""
        return unprotect_secret(value) if key in SECRET_CONFIG_KEYS else value

    def save_config(self, key, value):
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO configs (key, value) VALUES (?, ?)",
                (key, protect_secret(str(value)) if key in SECRET_CONFIG_KEYS else str(value)),
            )
            conn.commit()
        # 存入 SQLite 的同时，自动写一份到 data/configs.json 备份！
        self.save_all_configs_to_json()

    def save_configs(self, values, *, commit=True, write_backup=True):
        """Persist several settings in one transaction and one JSON backup."""
        if not isinstance(values, dict) or not values:
            return
        with self._db_lock:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.executemany(
                "REPLACE INTO configs (key, value) VALUES (?, ?)",
                [
                    (
                        str(key),
                        protect_secret(str(value)) if str(key) in SECRET_CONFIG_KEYS else str(value),
                    )
                    for key, value in values.items()
                ],
            )
            if commit:
                conn.commit()
        if write_backup:
            self.save_all_configs_to_json()

    @staticmethod
    def _watch_identity(item):
        key = str(item.get("key") or "").strip()
        if key:
            return key.casefold()
        name = str(item.get("market_hash_name") or item.get("name") or "").strip()
        phase = str(item.get("phase") or "-").strip()
        return f"{name}|{phase}".casefold()

    def save_market_watchlist(self, cache, *, manage_transaction=True):
        """Persist user-owned watch identities separately from quote cache data."""
        if not isinstance(cache, dict):
            return
        categories = cache.get("categories")
        if not isinstance(categories, list):
            return
        active = str(cache.get("active_category_id") or "")
        with self._db_lock:
            conn = self.get_connection()
            try:
                if manage_transaction:
                    conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM market_watch_items")
                conn.execute("DELETE FROM market_categories")
                now = datetime.now().isoformat(timespec="seconds")
                for category_position, category in enumerate(categories):
                    if not isinstance(category, dict):
                        continue
                    category_id = str(category.get("id") or f"category_{category_position + 1}")
                    category_name = str(category.get("name") or f"分类 {category_position + 1}")
                    conn.execute(
                        """INSERT INTO market_categories(id, name, position, updated_at)
                           VALUES (?, ?, ?, ?)""",
                        (category_id, category_name, category_position, now),
                    )
                    items = category.get("items")
                    if not isinstance(items, list):
                        continue
                    for item_position, item in enumerate(items):
                        if not isinstance(item, dict):
                            continue
                        identity = self._watch_identity(item)
                        if not identity:
                            continue
                        portable = {
                            key: item[key] for key in WATCH_PERSIST_FIELDS if key in item
                        }
                        conn.execute(
                            """INSERT INTO market_watch_items(
                                   category_id, identity, data_json, position
                               ) VALUES (?, ?, ?, ?)""",
                            (
                                category_id,
                                identity,
                                json.dumps(portable, ensure_ascii=False, separators=(",", ":")),
                                item_position,
                            ),
                        )
                conn.execute(
                    "REPLACE INTO app_state(key, value) VALUES ('active_market_category', ?)",
                    (active,),
                )
                if manage_transaction:
                    conn.commit()
            except Exception:
                if manage_transaction:
                    conn.rollback()
                raise

    def merge_sync_data(self, grouped_orders, configs, watchlist):
        """Apply all durable imported user data in one SQLite transaction."""
        with self._db_lock:
            conn = self.get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for platform_name, platform_orders in grouped_orders.items():
                    self.upsert_rental_orders(
                        platform_name, platform_orders, commit=False
                    )
                self.save_configs(
                    configs, commit=False, write_backup=False
                )
                self.save_market_watchlist(
                    watchlist, manage_transaction=False
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.save_all_configs_to_json()

    def load_market_watchlist(self):
        """Load the durable watch-list projection, without ephemeral prices."""
        with self._db_lock:
            conn = self.get_connection()
            categories = []
            for category_id, name in conn.execute(
                "SELECT id, name FROM market_categories ORDER BY position, rowid"
            ):
                items = []
                for (data_json,) in conn.execute(
                    """SELECT data_json FROM market_watch_items
                       WHERE category_id=? ORDER BY position, id""",
                    (category_id,),
                ):
                    try:
                        item = json.loads(data_json)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(item, dict):
                        items.append(item)
                categories.append({"id": category_id, "name": name, "items": items})
            row = conn.execute(
                "SELECT value FROM app_state WHERE key='active_market_category'"
            ).fetchone()
        return {
            "format": "market_categories_v1",
            "active_category_id": row[0] if row else "",
            "categories": categories,
        }
