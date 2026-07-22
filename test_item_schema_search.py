import unittest
from datetime import datetime

from main import CS2ManagerApp
from modules.cs2_item_schema import CS2ItemSchema, phase_hint_from_search


class ItemSchemaPhaseSearchTests(unittest.TestCase):
    def setUp(self):
        self.previous_schema = CS2ItemSchema._instance
        market_name = "★ Nomad Knife | Doppler (Factory New)"
        base = {
            "name_zh": "流浪者匕首（★） | 多普勒 (崭新出厂)",
            "market_hash_name": market_name,
            "wear_zh": "崭新出厂",
        }
        records = [
            {**base, "id": "ruby", "paint_index": "415", "phase": "Ruby"},
            {**base, "id": "sapphire", "paint_index": "416", "phase": "Sapphire"},
            {**base, "id": "black-pearl", "paint_index": "417", "phase": "Black Pearl"},
            {**base, "id": "p1", "paint_index": "418", "phase": "P1"},
        ]
        CS2ItemSchema._instance = CS2ItemSchema(
            {"nomad": records[0]}, {market_name: records[0]}, records
        )

    def tearDown(self):
        CS2ItemSchema._instance = self.previous_schema

    def test_chinese_ruby_query_returns_only_ruby_variant(self):
        results = CS2ItemSchema.search("流浪者匕首 多普勒 红宝石")
        self.assertTrue(results)
        self.assertEqual({row["phase"] for row in results}, {"Ruby"})
        self.assertEqual(results[0]["paint_index"], "415")

    def test_english_aliases_match_the_same_variant(self):
        results = CS2ItemSchema.search("Nomad Knife Doppler Ruby")
        self.assertTrue(results)
        self.assertEqual({row["phase"] for row in results}, {"Ruby"})

    def test_phase_aliases_are_canonical_for_new_watch_items(self):
        self.assertEqual(phase_hint_from_search("多普勒 蓝宝石"), "Sapphire")
        self.assertEqual(phase_hint_from_search("Gamma Doppler Emerald"), "Emerald")
        self.assertEqual(CS2ManagerApp._phase_hint_from_search("Doppler Phase 2"), "P2")

    def test_variant_lookup_does_not_collapse_shared_market_names(self):
        result = CS2ItemSchema.lookup_variant(
            "流浪者匕首（★） | 多普勒 (崭新出厂)",
            "★ Nomad Knife | Doppler (Factory New)",
            "Sapphire",
        )
        self.assertEqual(result["id"], "sapphire")
        self.assertEqual(result["paint_index"], "416")

    def test_english_query_still_renders_a_chinese_display_name(self):
        name = CS2ItemSchema.chinese_display_name(
            "",
            "★ Nomad Knife | Doppler (Factory New)",
            "Sapphire",
        )
        self.assertEqual(name, "流浪者匕首（★） | 多普勒 (崭新出厂)")

    def test_ai_asset_import_maps_to_chinese_and_sets_cooldown(self):
        app = CS2ManagerApp.__new__(CS2ManagerApp)
        record, status = app._normalize_ai_asset_item({
            "name": "Nomad Knife Doppler",
            "market_hash_name": "★ Nomad Knife | Doppler (Factory New)",
            "phase": "Sapphire",
            "pattern": "123",
            "float_val": "0.012345",
            "cost": 2000,
            "platform": "C5",
            "status": "CD冷却",
            "cooldown_hours": 48,
        })
        self.assertEqual(status, "可导入")
        self.assertEqual(record["name"], "流浪者匕首（★） | 多普勒 (崭新出厂)")
        self.assertEqual(record["platform"], "C5GAME")
        self.assertGreater(
            datetime.fromisoformat(record["cooldown_until"]), datetime.now()
        )


if __name__ == "__main__":
    unittest.main()
