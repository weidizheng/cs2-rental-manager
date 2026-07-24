import json
import tempfile
import unittest
from pathlib import Path

from modules.db_manager import DBManager


class ConfigRecoveryTests(unittest.TestCase):
    def make_manager(self, root: Path) -> DBManager:
        return DBManager(
            db_path=str(root / "app.db"),
            items_json=str(root / "items.json"),
            configs_json=str(root / "configs.json"),
        )

    def test_unquoted_csfloat_key_is_repaired_and_old_keys_are_restored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "configs.json").write_text(
                "{\n"
                '  "csfloat_api_key": csf-token_123",\n'
                '  "csqaq_token": "qaq-secret",\n'
                '  "eco_partner_id": "eco-partner"\n'
                "}\n",
                encoding="utf-8",
            )

            manager = self.make_manager(root)
            try:
                self.assertEqual(manager.get_config("csfloat_api_key"), "csf-token_123")
                self.assertEqual(manager.get_config("csqaq_token"), "qaq-secret")
                self.assertEqual(manager.get_config("eco_partner_id"), "eco-partner")
                repaired = json.loads((root / "configs.json").read_text(encoding="utf-8"))
                self.assertNotEqual(repaired["csfloat_api_key"], "csf-token_123")
                self.assertTrue(repaired["csfloat_api_key"].startswith("dpapi:"))
                self.assertNotIn("qaq-secret", (root / "configs.json").read_text(encoding="utf-8"))
            finally:
                manager._conn.close()

    def test_malformed_backup_does_not_overwrite_existing_database_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "configs.json").write_text("{}", encoding="utf-8")
            manager = self.make_manager(root)
            manager.save_config("csqaq_token", "database-secret")
            manager._conn.close()

            (root / "configs.json").write_text("{not valid json", encoding="utf-8")
            reopened = self.make_manager(root)
            try:
                self.assertEqual(reopened.get_config("csqaq_token"), "database-secret")
            finally:
                reopened._conn.close()

    def test_eco_rental_fee_defaults_are_zero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self.make_manager(Path(temp_dir))
            try:
                self.assertEqual(manager.get_config("eco_first_fee"), "0")
                self.assertEqual(manager.get_config("eco_relet_fee"), "0")
            finally:
                manager._conn.close()


if __name__ == "__main__":
    unittest.main()
