import tempfile
import unittest
from pathlib import Path

from modules.eco_client import ECOClient
from modules.eco_market_cache import ECOMarketCache


class ECOSelectiveCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache = ECOMarketCache(
            "partner-test", Path(self.temp_dir.name) / "eco.db"
        )
        self.cache.replace_snapshot([
            {"HashName": "Wanted", "StyleName": "", "Price": 100, "RentGoodsBottomPrice": 1.2},
            {"HashName": "Wanted", "StyleName": "Phase2", "Price": 120, "RentGoodsBottomPrice": 0},
            {"HashName": "Other", "StyleName": "", "Price": 200, "RentGoodsBottomPrice": 2.4},
        ], return_snapshot=False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_selective_read_keeps_requested_phases_and_excludes_other_names(self):
        snapshot = self.cache.load_snapshot_for_hash_names(["Wanted"])
        self.assertEqual(set(snapshot), {("Wanted", ""), ("Wanted", "phase2")})
        self.assertNotIn(("Other", ""), snapshot)

    def test_fresh_client_path_does_not_load_the_full_snapshot(self):
        client = ECOClient("partner-test")
        client.market_cache = self.cache
        client.market_cache.load_snapshot = lambda: (_ for _ in ()).throw(
            AssertionError("full snapshot should not be loaded")
        )
        snapshot = client.get_prices_for_hash_names(["Wanted"])
        self.assertEqual(set(snapshot), {("Wanted", ""), ("Wanted", "phase2")})
        self.assertEqual(client.last_price_source, "cache")


if __name__ == "__main__":
    unittest.main()
