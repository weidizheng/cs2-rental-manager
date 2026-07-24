import tempfile
import unittest
from pathlib import Path

from modules.asset_import import apply_asset_import_plan, plan_asset_import
from modules.db_manager import DBManager


CHINESE_NAME = "折叠刀（★） | 多普勒 (崭新出厂)"
STANDARD_MHN = "★ Flip Knife | Doppler (Factory New)"
SHORT_FLOAT = "0.02071962"
FULL_FLOAT = "0.0207196157425642"


def asset_record(
    float_val: str,
    *,
    name: str = CHINESE_NAME,
    market_hash_name: str = STANDARD_MHN,
    asset_id: str = "",
    cost: float = 2239,
) -> dict:
    return {
        "name": name,
        "market_hash_name": market_hash_name,
        "phase": "P1",
        "pattern": "-",
        "float_val": float_val,
        "cost": cost,
        "platform": "悠悠有品",
        "status": "CD冷却",
        "rent": 0,
        "days": 0,
        "income": 0,
        "expire_hours": 141,
        "cooldown_until": "2030-01-01T00:00:00",
        "note": "AI 导入",
        "asset_id": asset_id,
    }


class AssetImportPlanningTests(unittest.TestCase):
    def test_old_blank_or_chinese_mhn_merges_with_standard_name_and_full_float(self):
        for old_mhn in ("", CHINESE_NAME):
            with self.subTest(old_mhn=old_mhn):
                existing = asset_record(
                    SHORT_FLOAT,
                    market_hash_name=old_mhn,
                    asset_id="stable-old-asset",
                    cost=100,
                )
                existing["id"] = 41
                incoming = asset_record(FULL_FLOAT)

                decisions = plan_asset_import([existing], [incoming])

                self.assertEqual(len(decisions), 1)
                decision = decisions[0]
                self.assertEqual(decision.action, "merge")
                self.assertEqual(decision.existing_id, 41)
                self.assertEqual(decision.candidate_ids, ())
                self.assertIsNotNone(decision.merged_record)
                self.assertEqual(decision.merged_record["id"], 41)
                self.assertEqual(
                    decision.merged_record["asset_id"], "stable-old-asset"
                )
                self.assertEqual(decision.merged_record["float_val"], FULL_FLOAT)
                self.assertEqual(
                    decision.merged_record["market_hash_name"], STANDARD_MHN
                )

    def test_same_name_with_different_full_float_is_a_new_asset(self):
        existing = asset_record(FULL_FLOAT, asset_id="first")
        existing["id"] = 1
        incoming = asset_record("0.0207196257425642", asset_id="second")

        decisions = plan_asset_import([existing], [incoming])

        self.assertEqual([decision.action for decision in decisions], ["add"])
        self.assertIsNone(decisions[0].existing_id)

    def test_multiple_matching_existing_assets_are_left_ambiguous(self):
        first = asset_record("0.02071961", asset_id="first")
        first["id"] = 11
        second = asset_record("0.02071962", asset_id="second")
        second["id"] = 12

        decisions = plan_asset_import([first, second], [asset_record(FULL_FLOAT)])

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision.action, "ambiguous")
        self.assertIsNone(decision.existing_id)
        self.assertEqual(set(decision.candidate_ids), {11, 12})
        self.assertIsNone(decision.merged_record)

    def test_batch_duplicates_keep_the_longest_float_regardless_of_order(self):
        short = asset_record(SHORT_FLOAT, cost=100)
        full = asset_record(FULL_FLOAT, cost=2239)

        decisions = plan_asset_import([], [short, full])

        self.assertEqual(len(decisions), 2)
        self.assertEqual(
            sorted(decision.action for decision in decisions), ["add", "skip"]
        )
        add_decision = next(
            decision for decision in decisions if decision.action == "add"
        )
        self.assertEqual(add_decision.incoming["float_val"], FULL_FLOAT)


class AssetImportApplicationTests(unittest.TestCase):
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

    def test_merge_preserves_item_asset_id_and_bound_order_history(self):
        self.db.add_item(
            asset_record(
                SHORT_FLOAT,
                market_hash_name="",
                asset_id="stable-old-asset",
                cost=100,
            )
        )
        old_item = self.db.get_all_items()[0]
        self.db.upsert_rental_orders(
            "C5GAME",
            [
                {
                    "order_no": "history-1",
                    "item_name": CHINESE_NAME,
                    "float_val": SHORT_FLOAT,
                    "item_id": old_item["id"],
                    "match_method": "manual",
                    "match_confidence": 1.0,
                    "start_time": "2026-07-01 12:00:00",
                    "income": 88.8,
                }
            ],
        )

        decisions = plan_asset_import(
            self.db.get_all_items(), [asset_record(FULL_FLOAT)]
        )
        report = apply_asset_import_plan(self.db, decisions)

        self.assertEqual(
            report,
            {"added": 0, "merged": 1, "skipped": 0, "ambiguous": 0},
        )
        items = self.db.get_all_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], old_item["id"])
        self.assertEqual(items[0]["asset_id"], "stable-old-asset")
        self.assertEqual(items[0]["float_val"], FULL_FLOAT)
        self.assertEqual(items[0]["market_hash_name"], STANDARD_MHN)

        orders = self.db.get_rental_orders("C5GAME")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["order_no"], "history-1")
        self.assertEqual(orders[0]["item_id"], old_item["id"])
        self.assertEqual(orders[0]["income"], 88.8)

    def test_ambiguous_plan_does_not_write_or_merge_any_asset(self):
        self.db.add_item(asset_record("0.02071961", asset_id="first"))
        self.db.add_item(asset_record("0.02071962", asset_id="second"))
        before = self.db.get_all_items()

        decisions = plan_asset_import(before, [asset_record(FULL_FLOAT)])
        report = apply_asset_import_plan(self.db, decisions)

        self.assertEqual(report["ambiguous"], 1)
        self.assertEqual(report["added"], 0)
        self.assertEqual(report["merged"], 0)
        after = self.db.get_all_items()
        self.assertEqual(
            [(item["id"], item["asset_id"], item["float_val"]) for item in after],
            [(item["id"], item["asset_id"], item["float_val"]) for item in before],
        )

    def test_batch_duplicates_are_written_once_with_full_precision(self):
        decisions = plan_asset_import(
            [],
            [asset_record(SHORT_FLOAT), asset_record(FULL_FLOAT)],
        )

        report = apply_asset_import_plan(self.db, decisions)

        self.assertEqual(report["added"], 1)
        self.assertEqual(report["skipped"], 1)
        items = self.db.get_all_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["float_val"], FULL_FLOAT)


if __name__ == "__main__":
    unittest.main()
