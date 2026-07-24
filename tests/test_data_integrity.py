import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from main import ItemEditDialog, _build_rental_history_index
from modules.db_manager import DBManager
from modules.db_migrations import CURRENT_SCHEMA_VERSION, run_migrations


class DataIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = DBManager(
            db_path=str(root / "app.db"),
            items_json=str(root / "items.json"),
            configs_json=str(root / "configs.json"),
        )

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    @staticmethod
    def item(name, float_val, asset_id=""):
        return {
            "name": name,
            "market_hash_name": name,
            "phase": "-",
            "pattern": "-",
            "float_val": float_val,
            "cost": 12.34,
            "platform": "C5GAME",
            "status": "在库",
            "rent": 1.23,
            "days": 0,
            "income": 0,
            "expire_hours": 999,
            "note": "",
            "asset_id": asset_id,
        }

    def test_schema_money_soft_delete_and_restore(self):
        self.assertEqual(
            self.db.get_connection().execute("PRAGMA user_version").fetchone()[0],
            CURRENT_SCHEMA_VERSION,
        )
        self.db.add_item(self.item("A", "0.123", "asset-1"))
        row = self.db.get_all_items()[0]
        self.assertEqual(row["cost"], 12.34)
        stored = self.db.get_connection().execute(
            "SELECT cost_cents, rent_cents FROM items WHERE id=?", (row["id"],)
        ).fetchone()
        self.assertEqual(stored, (1234, 123))
        self.db.delete_item(row["id"])
        self.assertEqual(self.db.get_all_items(), [])
        self.db.restore_item(row["id"])
        self.assertEqual(self.db.get_all_items()[0]["asset_id"], "asset-1")

    def test_new_purchase_cooldown_deadline_is_persisted(self):
        deadline = (datetime.now() + timedelta(hours=36)).isoformat(timespec="seconds")
        item = self.item("CD item", "0.234", "asset-cd")
        item.update({
            "status": "CD冷却",
            "expire_hours": 36,
            "cooldown_until": deadline,
        })
        self.db.add_item(item)
        stored = self.db.get_all_items()[0]
        self.assertEqual(stored["cooldown_until"], deadline)
        columns = {
            row[1] for row in self.db.get_connection().execute("PRAGMA table_info(items)")
        }
        self.assertIn("cooldown_until", columns)

    def test_v5_database_migrates_legacy_cooldown_hours(self):
        legacy_path = Path(self.temp.name) / "legacy-v5.db"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            """CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT '在库',
                expire_hours REAL NOT NULL DEFAULT 999
            )"""
        )
        connection.execute(
            "INSERT INTO items(status, expire_hours) VALUES ('CD冷却', 24)"
        )
        connection.execute("PRAGMA user_version=5")
        connection.commit()
        run_migrations(connection)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        deadline = connection.execute(
            "SELECT cooldown_until FROM items"
        ).fetchone()[0]
        connection.close()
        self.assertEqual(version, 7)
        self.assertTrue(deadline)

    def test_blank_asset_ids_are_generated_and_preserved(self):
        self.db.add_item(self.item("A", "0.111"))
        item = self.db.get_all_items()[0]
        self.assertEqual(len(item["asset_id"]), 32)
        updated = dict(item)
        updated["asset_id"] = ""
        updated["name"] = "A edited"
        self.db.update_item(item["id"], updated)
        self.assertEqual(self.db.get_all_items()[0]["asset_id"], item["asset_id"])

    def test_ambiguous_float_is_not_counted_twice(self):
        self.db.add_item(self.item("A", "0.123456"))
        self.db.add_item(self.item("B", "0.123456"))
        self.db.upsert_rental_orders(
            "C5GAME",
            [{"order_no": "order-1", "float_val": "0.123456", "income": 10}],
        )
        order = self.db.get_rental_orders()[0]
        self.assertIsNone(order["item_id"])
        self.assertEqual(order["match_method"], "ambiguous_float")
        histories = _build_rental_history_index(self.db.get_all_items(), [order])
        self.assertTrue(all(not history for history in histories.values()))

    def test_manual_association_is_stable(self):
        self.db.add_item(self.item("A", "0.123456"))
        self.db.add_item(self.item("B", "0.123456"))
        target = self.db.get_all_items()[1]
        self.db.upsert_rental_orders(
            "C5GAME",
            [{
                "order_no": "order-2",
                "float_val": "0.123456",
                "item_id": target["id"],
                "match_method": "manual",
                "match_confidence": 1,
            }],
        )
        order = self.db.get_rental_orders()[0]
        self.assertEqual(order["item_id"], target["id"])
        histories = _build_rental_history_index(self.db.get_all_items(), [order])
        self.assertEqual(len(histories[target["id"]]), 1)

    def test_watchlist_is_durable_and_sync_transaction_rolls_back(self):
        watchlist = {
            "format": "market_categories_v1",
            "active_category_id": "rentals",
            "categories": [{
                "id": "rentals",
                "name": "出租品",
                "items": [{"name": "A", "market_hash_name": "A", "phase": "-"}],
            }],
        }
        self.db.save_market_watchlist(watchlist)
        self.assertEqual(
            self.db.load_market_watchlist()["categories"][0]["items"][0]["name"],
            "A",
        )
        original = self.db.save_market_watchlist

        def fail(_cache, *, manage_transaction=True):
            if not manage_transaction:
                raise RuntimeError("probe")
            return original(_cache, manage_transaction=manage_transaction)

        with patch.object(self.db, "save_market_watchlist", side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.db.merge_sync_data(
                    {"C5GAME": [{"order_no": "rolled-back"}]}, {}, watchlist
                )
        self.assertFalse(
            any(order["order_no"] == "rolled-back" for order in self.db.get_rental_orders())
        )

    def test_watchlist_keeps_cached_quotes_in_sqlite(self):
        watchlist = {
            "format": "market_categories_v1",
            "active_category_id": "rentals",
            "categories": [{
                "id": "rentals",
                "name": "Rentals",
                "items": [{
                    "key": "A|-",
                    "name": "A",
                    "market_hash_name": "A",
                    "phase": "-",
                    "csqaq_min_sell_price": 123.45,
                    "csfloat_min_sell_cny": 120.01,
                    "detail": {"name_zh": "A"},
                }],
            }],
        }
        self.db.save_market_watchlist(watchlist)
        saved = self.db.load_market_watchlist()["categories"][0]["items"][0]
        self.assertEqual(saved["csqaq_min_sell_price"], 123.45)
        self.assertEqual(saved["csfloat_min_sell_cny"], 120.01)
        self.assertEqual(saved["detail"], {"name_zh": "A"})


class ItemDialogValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_invalid_number_stays_open_and_asset_id_is_preserved(self):
        dialog = ItemEditDialog({
            "name": "A",
            "float_val": "0.1",
            "cost": 1,
            "rent": 0,
            "days": 0,
            "income": 0,
            "expire_hours": 999,
            "asset_id": "stable-id",
        })
        dialog.cost_in.setText("not-a-number")
        dialog._accept_if_valid()
        self.assertEqual(dialog.result(), QDialog.Rejected)
        self.assertFalse(dialog.validation_label.isHidden())
        dialog.cost_in.setText("12.34")
        self.assertEqual(dialog._validated_data()["asset_id"], "stable-id")

    def test_switching_to_cooldown_creates_an_absolute_deadline(self):
        dialog = ItemEditDialog({
            "name": "A",
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "float_val": "0.2",
            "cost": 100,
            "status": "在库",
            "expire_hours": 999,
        })
        dialog.status_box.setCurrentText("CD冷却")
        self.assertEqual(dialog.expire_in.text(), "168")
        record = dialog._validated_data()
        deadline = datetime.fromisoformat(record["cooldown_until"])
        self.assertGreater(deadline, datetime.now() + timedelta(hours=167))


if __name__ == "__main__":
    unittest.main()
