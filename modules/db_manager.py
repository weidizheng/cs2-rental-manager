import json
import logging
import os
import sqlite3
import threading

from modules.paths import get_private_data_dir

DATA_DIR = str(get_private_data_dir())
DB_PATH = os.path.join(DATA_DIR, "app.db")
ITEMS_JSON_PATH = os.path.join(DATA_DIR, "items.json")
CONFIGS_JSON_PATH = os.path.join(DATA_DIR, "configs.json")

logger = logging.getLogger("CS2Rental")

# 文件写锁，防止多线程并发写入 items.json / configs.json 造成数据损坏
_write_lock = threading.Lock()


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
        self._conn = None  # 懒加载单例连接
        self.init_db()

    def get_connection(self):
        """返回线程安全的单例数据库连接"""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")  # WAL 模式提升并发读性能
        return self._conn

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
            platform TEXT DEFAULT 'BUFF',
            status TEXT DEFAULT '在库',
            rent REAL DEFAULT 0.0,
            days INTEGER DEFAULT 0,
            income REAL DEFAULT 0.0,
            expire_hours REAL DEFAULT 999.0,
            note TEXT DEFAULT '',
            asset_id TEXT DEFAULT ''
        )
        """)

        # 尝试添加 asset_id 列（兼容已有数据库）
        try:
            cursor.execute("ALTER TABLE items ADD COLUMN asset_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 列已存在，忽略

        # 尝试添加 market_hash_name 列（兼容已有数据库）
        try:
            cursor.execute("ALTER TABLE items ADD COLUMN market_hash_name TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 列已存在，忽略

        # 2. 软件配置表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS configs (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        # 先尝试从 configs.json 加载保存过的配置
        saved_configs = self.load_configs_from_json()

        default_configs = [
            (
                "eco_partner_id",
                saved_configs.get("eco_partner_id", ""),
            ),
            ("eco_rsa_key", saved_configs.get("eco_rsa_key", "")),
            ("csqaq_token", saved_configs.get("csqaq_token", "")),
            (
                "refresh_interval",
                saved_configs.get("refresh_interval", "15"),
            ),
            (
                "c5_first_fee",
                saved_configs.get("c5_first_fee", "0.15"),
            ),
            ("c5_relet_fee", saved_configs.get("c5_relet_fee", "0.05")),
            (
                "uu_first_fee",
                saved_configs.get("uu_first_fee", "0.10"),
            ),
            ("uu_relet_fee", saved_configs.get("uu_relet_fee", "0.05")),
            (
                "igxe_first_fee",
                saved_configs.get("igxe_first_fee", "0.10"),
            ),
            (
                "igxe_relet_fee",
                saved_configs.get("igxe_relet_fee", "0.05"),
            ),
            (
                "eco_first_fee",
                saved_configs.get("eco_first_fee", "0.10"),
            ),
            (
                "eco_relet_fee",
                saved_configs.get("eco_relet_fee", "0.05"),
            ),
        ]
        for k, v in default_configs:
            cursor.execute(
                "INSERT OR REPLACE INTO configs (key, value) VALUES (?, ?)",
                (k, str(v)),
            )

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
            daily_rent REAL DEFAULT 0.0,
            rental_days REAL DEFAULT 0.0,
            deposit REAL DEFAULT 0.0,
            start_time TEXT DEFAULT '',
            return_time TEXT DEFAULT '',
            status TEXT DEFAULT '',
            raw_text TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            UNIQUE(platform, order_no)
        )
        """)

        # Migrate databases created before structured clipboard imports.
        for column, definition in (
            ("daily_rent", "REAL DEFAULT 0.0"),
            ("rental_days", "REAL DEFAULT 0.0"),
            ("deposit", "REAL DEFAULT 0.0"),
        ):
            try:
                cursor.execute(f"ALTER TABLE rental_orders ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError:
                pass

        # 3. 饰品初始化
        cursor.execute("SELECT COUNT(*) FROM items")
        if cursor.fetchone()[0] == 0:
            self.load_items_from_json(conn)

        conn.commit()

    def load_configs_from_json(self):
        """读取 configs.json 备份文件"""
        if os.path.exists(self.configs_json_path):
            try:
                with open(self.configs_json_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"读取 configs.json 失败: {e}")
        return {}

    def save_all_configs_to_json(self):
        """将最新的全局配置写回 configs.json 备份（线程安全）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM configs")
            rows = cursor.fetchall()
            config_dict = {r[0]: r[1] for r in rows}

        with _write_lock:
            try:
                with open(self.configs_json_path, "w", encoding="utf-8") as f:
                    json.dump(config_dict, f, ensure_ascii=False, indent=2)
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

            should_close = False
            if conn is None:
                conn = self.get_connection()
                should_close = True

            cursor = conn.cursor()
            for item in items:
                cursor.execute(
                    """
                INSERT INTO items (name, market_hash_name, phase, pattern, float_val, cost, platform, status, rent, days, income, expire_hours, note, asset_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        item.get("name", "未命名"),
                        item.get("market_hash_name", ""),
                        item.get("phase", "-"),
                        item.get("pattern", "-"),
                        item.get("float_val", "0.000"),
                        item.get("cost", 0.0),
                        item.get("platform", "BUFF"),
                        item.get("status", "在库"),
                        item.get("rent", 0.0),
                        item.get("days", 0),
                        item.get("income", 0.0),
                        item.get("expire_hours", 999.0),
                        item.get("note", ""),
                        item.get("asset_id", ""),
                    ),
                )
            if should_close:
                conn.commit()
                conn.close()
            return True
        except Exception as e:
            logger.error(f"读取 items.json 失败: {e}")
            return False

    def export_items_to_json(self):
        """同步写回 data/items.json（线程安全）"""
        items = self.get_all_items()
        clean_items = []
        for item in items:
            item_copy = item.copy()
            item_copy.pop("id", None)
            clean_items.append(item_copy)

        with _write_lock:
            try:
                with open(self.items_json_path, "w", encoding="utf-8") as f:
                    json.dump(clean_items, f, ensure_ascii=False, indent=2)
                return True
            except Exception as e:
                logger.error(f"写入 items.json 失败: {e}")
                return False

    def get_all_items(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, market_hash_name, phase, pattern, float_val, cost, platform, status, rent, days, income, expire_hours, note, asset_id FROM items"
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
                "cost": r[6],
                "platform": r[7],
                "status": r[8],
                "rent": r[9],
                "days": r[10],
                "income": r[11],
                "expire_hours": r[12],
                "note": r[13],
                "asset_id": r[14],
            }
            for r in rows
        ]

    def add_item(self, item):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
        INSERT INTO items (name, market_hash_name, phase, pattern, float_val, cost, platform, status, rent, days, income, expire_hours, note, asset_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                item.get("name"),
                item.get("market_hash_name", ""),
                item.get("phase", "-"),
                item.get("pattern", "-"),
                item.get("float_val", "0.000"),
                item.get("cost", 0.0),
                item.get("platform", "BUFF"),
                item.get("status", "在库"),
                item.get("rent", 0.0),
                item.get("days", 0),
                item.get("income", 0.0),
                item.get("expire_hours", 999.0),
                item.get("note", ""),
                item.get("asset_id", ""),
            ),
        )
        conn.commit()
        self.export_items_to_json()

    def update_item(self, item_id, item):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
        UPDATE items SET
            name=?, market_hash_name=?, phase=?, pattern=?, float_val=?, cost=?,
            platform=?, status=?, rent=?, days=?, income=?, expire_hours=?, note=?, asset_id=?
        WHERE id=?
        """,
            (
                item.get("name"),
                item.get("market_hash_name", ""),
                item.get("phase"),
                item.get("pattern"),
                item.get("float_val"),
                item.get("cost"),
                item.get("platform"),
                item.get("status"),
                item.get("rent"),
                item.get("days"),
                item.get("income"),
                item.get("expire_hours"),
                item.get("note"),
                item.get("asset_id", ""),
                item_id,
            ),
        )
        conn.commit()
        self.export_items_to_json()

    def delete_item(self, item_id):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM items WHERE id=?", (item_id,))
        conn.commit()
        self.export_items_to_json()

    def upsert_rental_orders(self, platform, orders):
        """Store manually read rental orders without changing inventory rows."""
        from datetime import datetime

        synced_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self.get_connection()
        cursor = conn.cursor()
        for order in orders:
            order_no = str(order.get("order_no", "")).strip()
            if not order_no:
                continue
            cursor.execute(
                """
                INSERT INTO rental_orders (
                    platform, order_no, item_name, float_val, income, daily_rent, rental_days, deposit,
                    start_time, return_time, status, raw_text, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, order_no) DO UPDATE SET
                    item_name=excluded.item_name,
                    float_val=excluded.float_val,
                    income=excluded.income,
                    daily_rent=excluded.daily_rent,
                    rental_days=excluded.rental_days,
                    deposit=excluded.deposit,
                    start_time=excluded.start_time,
                    return_time=excluded.return_time,
                    status=excluded.status,
                    raw_text=excluded.raw_text,
                    synced_at=excluded.synced_at
                """,
                (
                    platform,
                    order_no,
                    order.get("item_name", ""),
                    order.get("float_val", ""),
                    float(order.get("income", 0.0) or 0.0),
                    float(order.get("daily_rent", 0.0) or 0.0),
                    float(order.get("rental_days", 0.0) or 0.0),
                    float(order.get("deposit", 0.0) or 0.0),
                    order.get("start_time", ""),
                    order.get("return_time", ""),
                    order.get("status", ""),
                    order.get("raw_text", ""),
                    synced_at,
                ),
            )
        conn.commit()

    def get_rental_orders(self, platform=None):
        conn = self.get_connection()
        cursor = conn.cursor()
        if platform:
            cursor.execute(
                """
                SELECT platform, order_no, item_name, float_val, income, daily_rent, rental_days, deposit,
                       start_time, return_time, status, synced_at
                FROM rental_orders WHERE platform=?
                ORDER BY synced_at DESC, id DESC
                """,
                (platform,),
            )
        else:
            cursor.execute("""
                SELECT platform, order_no, item_name, float_val, income, daily_rent, rental_days, deposit,
                       start_time, return_time, status, synced_at
                FROM rental_orders ORDER BY synced_at DESC, id DESC
            """)
        columns = (
            "platform", "order_no", "item_name", "float_val", "income", "daily_rent", "rental_days", "deposit",
            "start_time", "return_time", "status", "synced_at",
        )
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_config(self, key):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM configs WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else ""

    def save_config(self, key, value):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "REPLACE INTO configs (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        conn.commit()
        # 存入 SQLite 的同时，自动写一份到 data/configs.json 备份！
        self.save_all_configs_to_json()
