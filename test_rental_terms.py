import os
import sqlite3
import tempfile
import unittest

from modules.db_manager import DBManager
from modules.rental_order_parsers import (
    parse_c5_detail_clipboard,
    parse_eco_clipboard,
    parse_igxe_clipboard,
)
from modules.rental_terms import classify_rental_term


class RentalTermRuleTests(unittest.TestCase):
    def test_c5_detail_preserves_return_and_transfer_state(self):
        text = """订单号：1548041906146594816
订单状态: 已归还
折叠刀（★） | 多普勒 (崭新出厂)
磨损度：0.0207196157425642
租赁价格：￥2.5/天*15天 = 37.5元
租期时长：15天 （最多可租45天）
下单时间：2026-07-05 16:51:54
租赁到期：2026-07-20 16:56:08
归还截至：2026-07-21 07:05:22
转租状态：未转租
转租奖励：最高奖励 ￥1.87
"""
        order = parse_c5_detail_clipboard(text)[0]
        self.assertEqual(order["status"], "已归还")
        self.assertEqual(order["rental_end_time"], "2026-07-20 16:56:08")
        self.assertEqual(order["return_deadline"], "2026-07-21 07:05:22")
        self.assertEqual(order["transfer_status"], "未转租")
        self.assertFalse(order["transfer_reward_known"])

    def test_confirmed_platform_day_boundaries(self):
        cases = (
            ("IGXE", 1, "short"),
            ("IGXE", 14, "short"),
            ("IGXE", 15, "long"),
            ("IGXE", 60, "long"),
            ("IGXE", 61, "unknown"),
            ("ECOSteam", 1, "short"),
            ("ECOSteam", 21, "short"),
            ("ECOSteam", 22, "long"),
            ("ECOSteam", 45, "long"),
            ("ECOSteam", 46, "unknown"),
            ("C5GAME", 7, "unknown"),
            ("C5GAME", 8, "short"),
            ("C5GAME", 21, "short"),
            ("C5GAME", 22, "long"),
            ("C5GAME", 45, "long"),
            ("C5GAME", 46, "unknown"),
        )
        for platform, days, expected in cases:
            with self.subTest(platform=platform, days=days):
                self.assertEqual(classify_rental_term(platform, days), expected)

    def test_explicit_page_label_wins_over_day_range(self):
        self.assertEqual(
            classify_rental_term("C5GAME", 22, "租期类型：短租"),
            "short",
        )
        self.assertEqual(
            classify_rental_term("ECOSteam", 8, explicit_term="长租"),
            "long",
        )

    def test_eco_parser_preserves_explicit_term(self):
        text = """2026-07-18 10:00:00 订单编号：123456789
[测试饰品](https://example.test/item)
磨损：0.123456789
￥1.25/天 ×22（长租）
含押金 ￥1000.00
2026年08月09日 22:00:00 前归还
租赁中
"""
        orders = parse_eco_clipboard(text)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["rental_days"], 22.0)
        self.assertEqual(orders[0]["rental_type"], "long")

    def test_igxe_parser_falls_back_to_confirmed_days(self):
        text = """订单类型：出租
[测试饰品](https://www.igxe.cn/lease/trade/730/123456789)
磨损 0.123456789
创建时间：2026-07-18 10:00:00
租赁到期时间：2026-08-02 10:00:00
归还截止时间：2026-08-03 04:00:00
租赁价格 ￥1.50/天
出租天数：**15天**
饰品押金 ￥1000.00
订单金额 ￥22.50
"""
        orders = parse_igxe_clipboard(text)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["rental_days"], 15.0)
        self.assertEqual(orders[0]["rental_type"], "long")


class RentalTermStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        base = self.temp_dir.name
        self.db = DBManager(
            db_path=os.path.join(base, "app.db"),
            items_json=os.path.join(base, "items.json"),
            configs_json=os.path.join(base, "configs.json"),
        )

    def tearDown(self):
        if self.db._conn is not None:
            self.db._conn.close()
        self.temp_dir.cleanup()

    def test_upsert_classifies_and_persists_rental_type(self):
        self.db.upsert_rental_orders(
            "C5GAME",
            [{"order_no": "C5-1", "rental_days": 22, "raw_text": ""}],
        )
        order = self.db.get_rental_orders()[0]
        self.assertEqual(order["rental_type"], "long")

        stored = self.db.get_connection().execute(
            "SELECT rental_type FROM rental_orders WHERE order_no='C5-1'"
        ).fetchone()[0]
        self.assertEqual(stored, "long")

    def test_upsert_persists_rental_lifecycle_fields(self):
        self.db.upsert_rental_orders(
            "C5GAME",
            [{
                "order_no": "C5-returned",
                "rental_end_time": "2026-07-20 16:56:08",
                "return_deadline": "2026-07-21 07:05:22",
                "transfer_status": "未转租",
                "status": "已归还",
            }],
        )
        order = self.db.get_rental_orders()[0]
        self.assertEqual(order["rental_end_time"], "2026-07-20 16:56:08")
        self.assertEqual(order["return_deadline"], "2026-07-21 07:05:22")
        self.assertEqual(order["transfer_status"], "未转租")
        self.assertEqual(order["status"], "已归还")

    def test_read_classifies_a_legacy_blank_value(self):
        connection = self.db.get_connection()
        connection.execute(
            """
            INSERT INTO rental_orders (
                platform, order_no, rental_days, rental_type, synced_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("IGXE", "IGXE-legacy", 15, "", "2026-07-18 10:00:00"),
        )
        connection.commit()

        order = self.db.get_rental_orders()[0]
        self.assertEqual(order["rental_type"], "long")

    def test_schema_migration_adds_rental_type_to_legacy_table(self):
        if self.db._conn is not None:
            self.db._conn.close()
            self.db._conn = None
        os.remove(self.db.db_path)
        legacy = sqlite3.connect(self.db.db_path)
        legacy.execute(
            """
            CREATE TABLE rental_orders (
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
                transfer_reward REAL DEFAULT 0.0,
                reward_status TEXT DEFAULT '',
                transfer_reward_known INTEGER DEFAULT 0,
                synced_at TEXT NOT NULL,
                UNIQUE(platform, order_no)
            )
            """
        )
        legacy.execute(
            """
            INSERT INTO rental_orders (
                platform, order_no, rental_days, synced_at
            ) VALUES ('ECOSteam', 'ECO-legacy', 21, '2026-07-18 10:00:00')
            """
        )
        legacy.commit()
        legacy.close()

        self.db.init_db()
        columns = {
            row[1]
            for row in self.db.get_connection().execute(
                "PRAGMA table_info(rental_orders)"
            ).fetchall()
        }
        self.assertIn("rental_type", columns)
        self.assertEqual(self.db.get_rental_orders()[0]["rental_type"], "short")


if __name__ == "__main__":
    unittest.main()
