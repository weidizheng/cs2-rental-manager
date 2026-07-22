import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.cloud_sync import (
    SYNC_FILENAME,
    export_sync_bundle,
    import_sync_bundle,
    load_sync_bundle,
)
from modules.db_manager import DBManager


class CloudSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.source_db = DBManager(
            db_path=str(root / "source.db"),
            items_json=str(root / "source-items.json"),
            configs_json=str(root / "source-configs.json"),
        )
        self.target_db = DBManager(
            db_path=str(root / "target.db"),
            items_json=str(root / "target-items.json"),
            configs_json=str(root / "target-configs.json"),
        )
        self.bundle_path = root / "sync.cs2sync"

    def tearDown(self):
        for db in (self.source_db, self.target_db):
            if db._conn is not None:
                db._conn.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _remote_watchlist():
        return {
            "format": "market_categories_v1",
            "active_category_id": "rentals",
            "categories": [{
                "id": "rentals",
                "name": "出租品",
                "items": [{
                    "key": "remote-item|P2",
                    "name": "远端观察品",
                    "market_hash_name": "Remote Item (Factory New)",
                    "phase": "P2",
                    "links": {"csqaq": "https://example.invalid/remote"},
                    "csqaq_price": 999.0,
                }],
            }],
        }

    def test_bundle_encrypts_secrets_and_requires_the_same_password(self):
        secret = "secret-csqaq-token-value"
        self.source_db.save_configs({"csqaq_token": secret})
        self.source_db.upsert_rental_orders("C5", [{
            "order_no": "ORDER-SECRET-123",
            "item_name": "敏感订单饰品",
        }])
        with patch("modules.cloud_sync.MarketCache.load", return_value=self._remote_watchlist()):
            result = export_sync_bundle(
                self.source_db, "correct horse battery", self.bundle_path
            )

        raw_bundle = self.bundle_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw_bundle)
        self.assertNotIn("ORDER-SECRET-123", raw_bundle)
        self.assertNotIn("远端观察品", raw_bundle)
        self.assertEqual(result["orders"], 1)
        self.assertEqual(result["watch_items"], 1)

        payload = load_sync_bundle(self.bundle_path, "correct horse battery")
        self.assertEqual(payload["data"]["api_config"]["csqaq_token"], secret)
        self.assertEqual(payload["data"]["rental_orders"][0]["order_no"], "ORDER-SECRET-123")
        with self.assertRaisesRegex(ValueError, "口令错误|文件已被修改"):
            load_sync_bundle(self.bundle_path, "definitely-wrong-password")

    def test_outbox_keeps_only_the_latest_generated_bundle(self):
        outbox = Path(self.temp_dir.name) / "outbox"
        outbox.mkdir()
        (outbox / "older-one.cs2sync").write_text("old", encoding="utf-8")
        (outbox / "older-two.cs2sync").write_text("old", encoding="utf-8")
        unrelated = outbox / "upload-notes.txt"
        unrelated.write_text("keep", encoding="utf-8")

        with (
            patch("modules.cloud_sync.get_sync_outbox_directory", return_value=outbox),
            patch("modules.cloud_sync.MarketCache.load", return_value={}),
        ):
            result = export_sync_bundle(self.source_db, "shared-password")

        bundles = sorted(path.name for path in outbox.glob("*.cs2sync"))
        self.assertEqual(bundles, [SYNC_FILENAME])
        self.assertEqual(result["removed_old_bundles"], 2)
        self.assertTrue(unrelated.exists())

    def test_failed_export_does_not_remove_an_existing_bundle(self):
        outbox = Path(self.temp_dir.name) / "outbox"
        outbox.mkdir()
        existing = outbox / "existing.cs2sync"
        existing.write_text("old", encoding="utf-8")

        with (
            patch("modules.cloud_sync.get_sync_outbox_directory", return_value=outbox),
            patch("modules.cloud_sync.MarketCache.load", return_value={}),
            patch("modules.cloud_sync._atomic_write_json", side_effect=OSError("disk full")),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            export_sync_bundle(self.source_db, "shared-password")

        self.assertTrue(existing.exists())

    def test_import_merges_orders_watchlist_and_api_config(self):
        self.source_db.save_configs({"csqaq_token": "remote-token-value"})
        self.source_db.upsert_rental_orders("C5", [{
            "order_no": "C5-10001",
            "item_name": "远端订单饰品",
            "status": "租赁中",
        }])
        remote_cache = self._remote_watchlist()
        with patch("modules.cloud_sync.MarketCache.load", return_value=remote_cache):
            export_sync_bundle(self.source_db, "shared-password", self.bundle_path)

        local_cache = {
            "format": "market_categories_v1",
            "active_category_id": "rentals",
            "categories": [{
                "id": "rentals",
                "name": "出租品",
                "items": [
                    {
                        "key": "remote-item|P2",
                        "name": "本机旧名称",
                        "market_hash_name": "Remote Item (Factory New)",
                        "phase": "P2",
                        "csqaq_price": 123.45,
                    },
                    {
                        "key": "local-only|-",
                        "name": "仅本机收藏",
                        "phase": "-",
                        "csqaq_price": 88.0,
                    },
                ],
            }],
        }
        backup_directory = Path(self.temp_dir.name) / "backups"
        backup_directory.mkdir()
        for index in range(4):
            old_backup = backup_directory / f"old-{index}.cs2sync"
            old_backup.write_text("old", encoding="utf-8")
            os.utime(old_backup, (index + 1, index + 1))
        with (
            patch("modules.cloud_sync.MarketCache.load", return_value=local_cache),
            patch("modules.cloud_sync.MarketCache.save") as save_cache,
            patch("modules.cloud_sync.get_sync_directory", return_value=Path(self.temp_dir.name)),
        ):
            result = import_sync_bundle(
                self.target_db, self.bundle_path, "shared-password"
            )

        self.assertEqual(self.target_db.get_config("csqaq_token"), "remote-token-value")
        self.assertEqual(self.target_db.get_rental_orders()[0]["order_no"], "C5-10001")
        merged = save_cache.call_args.args[0]
        merged_items = merged["categories"][0]["items"]
        self.assertEqual(len(merged_items), 2)
        synced_item = next(item for item in merged_items if item["key"] == "remote-item|P2")
        self.assertEqual(synced_item["csqaq_price"], 123.45)
        self.assertEqual(
            synced_item["links"]["csqaq"], "https://example.invalid/remote"
        )
        self.assertEqual(result["orders"], 1)
        self.assertTrue(os.path.exists(result["backup_path"]))
        self.assertEqual(len(list(backup_directory.glob("*.cs2sync"))), 3)
        self.assertEqual(result["removed_old_backups"], 2)

    def test_order_association_uses_portable_asset_id_not_local_row_id(self):
        def asset(name, asset_id):
            return {
                "name": name,
                "market_hash_name": name,
                "float_val": "0.123456",
                "cost": 1,
                "platform": "C5GAME",
                "status": "在库",
                "rent": 0,
                "days": 0,
                "income": 0,
                "expire_hours": 999,
                "asset_id": asset_id,
            }

        self.source_db.add_item(asset("Source", "portable-asset-1"))
        source_item = self.source_db.get_all_items()[0]
        self.source_db.upsert_rental_orders(
            "C5GAME",
            [{
                "order_no": "portable-order",
                "float_val": "0.123456",
                "item_id": source_item["id"],
                "match_method": "manual",
            }],
        )

        self.target_db.add_item(asset("Decoy", "decoy-asset"))
        self.target_db.add_item(asset("Target", "portable-asset-1"))
        target_item = next(
            item for item in self.target_db.get_all_items()
            if item["asset_id"] == "portable-asset-1"
        )
        self.assertNotEqual(source_item["id"], target_item["id"])

        with patch("modules.cloud_sync.MarketCache.load", return_value={}):
            export_sync_bundle(self.source_db, "shared-password", self.bundle_path)
        payload_order = load_sync_bundle(
            self.bundle_path, "shared-password"
        )["data"]["rental_orders"][0]
        self.assertNotIn("item_id", payload_order)
        self.assertEqual(payload_order["asset_id"], "portable-asset-1")

        with (
            patch("modules.cloud_sync.MarketCache.load", return_value={}),
            patch("modules.cloud_sync.MarketCache.save"),
            patch(
                "modules.cloud_sync.get_sync_directory",
                return_value=Path(self.temp_dir.name),
            ),
        ):
            import_sync_bundle(self.target_db, self.bundle_path, "shared-password")
        imported = next(
            order for order in self.target_db.get_rental_orders()
            if order["order_no"] == "portable-order"
        )
        self.assertEqual(imported["item_id"], target_item["id"])
        self.assertEqual(imported["match_method"], "asset_id")


if __name__ == "__main__":
    unittest.main()
