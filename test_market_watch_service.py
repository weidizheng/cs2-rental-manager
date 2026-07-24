import unittest

from modules.market_watch_service import merge_durable_watchlist, normalize_phase, watch_identity


class MarketWatchServiceTests(unittest.TestCase):
    def test_phase_aliases_share_the_same_watch_identity(self):
        self.assertEqual(normalize_phase("红宝石"), "Ruby")
        self.assertEqual(
            watch_identity("Knife | Doppler", "P1"),
            watch_identity("Knife | Doppler", "P3"),
        )

    def test_durable_values_override_legacy_json_quotes(self):
        durable = {
            "active_category_id": "rentals",
            "categories": [{
                "id": "rentals",
                "name": "Rentals",
                "items": [{"market_hash_name": "A", "phase": "-", "csqaq_price": 20}],
            }],
        }
        legacy = {
            "categories": [{
                "id": "rentals",
                "name": "Rentals",
                "items": [{
                    "market_hash_name": "A",
                    "phase": "-",
                    "csqaq_price": 10,
                    "csfloat_min_sell_cny": 12,
                }],
            }],
        }
        merged = merge_durable_watchlist(durable, legacy)
        entry = merged["categories"][0]["items"][0]
        self.assertEqual(entry["csqaq_price"], 20)
        self.assertEqual(entry["csfloat_min_sell_cny"], 12)


if __name__ == "__main__":
    unittest.main()
