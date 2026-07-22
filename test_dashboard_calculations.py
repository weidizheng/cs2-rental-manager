import unittest
from datetime import datetime, timedelta

from main import (
    CS2ManagerApp,
    _adjust_cost_by_percent,
    _build_rental_history_index,
    _is_non_earning_rental_status,
    _platform_rent_benchmark,
    _price_gap,
    _rental_term,
    _rental_lifecycle_state,
    _sort_dashboard_records,
)


class DashboardCalculationTests(unittest.TestCase):
    def test_rental_lifecycle_has_twelve_hour_relet_then_full_seven_day_cd(self):
        rental_end = datetime(2026, 7, 20, 16, 56, 8)
        cases = (
            (rental_end - timedelta(seconds=1), "rented", rental_end),
            (rental_end, "pending_relet", rental_end + timedelta(hours=12)),
            (rental_end + timedelta(hours=12), "cooldown", rental_end + timedelta(hours=12, days=7)),
            (rental_end + timedelta(hours=12, days=7), "available", None),
        )
        for now, expected_state, expected_end in cases:
            with self.subTest(now=now):
                self.assertEqual(
                    _rental_lifecycle_state(rental_end, now),
                    (expected_state, expected_end),
                )
    def test_one_percent_cost_surcharge_is_added_and_rounded_to_cents(self):
        self.assertEqual(_adjust_cost_by_percent(2121.76, 1), 2142.98)

    def test_sell_gap_uses_cost_as_the_percentage_benchmark(self):
        difference, percentage = _price_gap(2245.0, 2121.76)
        self.assertAlmostEqual(difference, 123.24)
        self.assertAlmostEqual(percentage, 5.8084, places=3)

    def test_platform_rent_uses_same_term_quote(self):
        quote = {
            "c5_short_rent": 1.99,
            "c5_long_rent": 1.71,
            "eco_min_rent": 2.0,
            "yyyp_short_rent": 1.9,
            "yyyp_long_rent": 1.71,
            "igxe_short_rent": 1.35,
            "igxe_long_rent": 1.31,
        }
        self.assertEqual(_platform_rent_benchmark("C5GAME", quote, "short")[0], 1.99)
        self.assertEqual(_platform_rent_benchmark("C5GAME", quote, "long")[0], 1.71)
        self.assertEqual(_platform_rent_benchmark("IGXE", quote, "short")[0], 1.35)
        self.assertEqual(_platform_rent_benchmark("IGXE", quote, "long")[0], 1.31)
        self.assertEqual(_platform_rent_benchmark("ECOSteam", quote, "short")[0], 2.0)
        self.assertEqual(_platform_rent_benchmark("ECOSteam", quote, "long")[0], 2.0)
        self.assertEqual(_platform_rent_benchmark("BUFF", quote, "short")[0], 0.0)

    def test_rental_term_uses_platform_specific_boundaries(self):
        self.assertEqual(_rental_term("IGXE", 14), "short")
        self.assertEqual(_rental_term("IGXE", 15), "long")
        self.assertEqual(_rental_term("C5GAME", 21), "short")
        self.assertEqual(_rental_term("C5GAME", 22), "long")
        self.assertEqual(_rental_term("ECOSteam", 21), "short")
        self.assertEqual(_rental_term("ECOSteam", 22), "long")
        self.assertEqual(_rental_term("C5GAME", 7), "unknown")

    def test_explicit_rental_term_takes_precedence_over_days(self):
        self.assertEqual(_rental_term("ECOSteam", 8, "长租"), "long")
        self.assertEqual(_rental_term("IGXE", 30, "short"), "short")

    def test_cancelled_closed_and_refunded_orders_have_no_rental_profit(self):
        for status in ("已取消", "已关闭", "已退款"):
            self.assertTrue(_is_non_earning_rental_status(status))
        for status in ("已完成", "已转交", "租赁中", ""):
            self.assertFalse(_is_non_earning_rental_status(status))

    def test_dashboard_groups_platforms_by_rented_count_then_type_and_cost(self):
        def record(item_id, platform, name, cost, rented):
            return {
                "item": {"id": item_id, "name": name, "cost": cost},
                "platform": platform,
                "is_currently_rented": rented,
            }

        records = [
            record(1, "ECOSteam", "Type C", 80, True),
            record(2, "C5GAME", "Type A", 200, True),
            record(3, "C5GAME", "Type B", 150, True),
            record(4, "C5GAME", "Type A", 100, False),
        ]
        sorted_records = _sort_dashboard_records(records)
        self.assertEqual(
            [entry["item"]["id"] for entry in sorted_records],
            [4, 2, 3, 1],
        )

    def test_refresh_collection_includes_inactive_categories_and_deduplicates(self):
        shared_a = {"name": "Shared", "market_hash_name": "Shared MHN", "phase": "P1"}
        shared_b = {"name": "Shared", "market_hash_name": "Shared MHN", "phase": "P3"}
        inactive_only = {"name": "Inactive", "market_hash_name": "Inactive MHN", "phase": "-"}

        class Stub:
            _market_categories = {
                "active": {"items": [shared_a]},
                "inactive": {"items": [shared_b, inactive_only]},
            }

            @staticmethod
            def _apply_schema_mapping(_entry):
                return None

            @staticmethod
            def _market_watch_identity(market_hash_name, phase):
                return CS2ManagerApp._market_watch_identity(market_hash_name, phase)

        unique, groups = CS2ManagerApp._collect_all_market_refresh_items(Stub())
        self.assertEqual(len(unique), 2)
        self.assertEqual(sorted(len(group) for group in groups.values()), [1, 2])

    def test_rental_history_index_preserves_truncated_float_matching(self):
        items = [
            {"id": 1, "float_val": "0.02082699"},
            {"id": 2, "float_val": "0.10000000"},
        ]
        orders = [
            {
                "order_no": "later",
                "float_val": "0.020826994",
                "start_time": "2026-02-02 12:00:00",
            },
            {
                "order_no": "other-item",
                "float_val": "0.100000001",
                "start_time": "2026-01-01 12:00:00",
            },
            {
                "order_no": "earlier",
                "float_val": "0.020826991",
                "start_time": "2026-01-02 12:00:00",
            },
            # Exactly half of the last stored decimal place remains excluded,
            # matching the original strict comparison.
            {
                "order_no": "boundary",
                "float_val": "0.020826995",
                "start_time": "2026-01-01 12:00:00",
            },
        ]

        histories = _build_rental_history_index(items, orders)

        self.assertEqual(
            [order["order_no"] for order in histories[1]],
            ["earlier", "later"],
        )
        self.assertEqual(
            [order["order_no"] for order in histories[2]],
            ["other-item"],
        )

    def test_dashboard_quote_index_reuses_first_cached_matches(self):
        exact_quote = {
            "name": "Exact display",
            "market_hash_name": "Exact MHN",
            "phase": "P1",
        }
        fallback_quote = {"name": "Fallback Name", "phase": "-"}

        class Stub:
            _market_categories = {
                "first": {"items": [fallback_quote, exact_quote]},
                "duplicate": {"items": [dict(exact_quote, source="later")]},
            }

            @staticmethod
            def _market_watch_identity(market_hash_name, phase):
                return CS2ManagerApp._market_watch_identity(market_hash_name, phase)

            @staticmethod
            def _build_market_hash_name(item):
                return item.get("market_hash_name", "")

            def _build_dashboard_market_quote_index(self):
                return CS2ManagerApp._build_dashboard_market_quote_index(self)

        stub = Stub()
        quote_index = stub._build_dashboard_market_quote_index()
        exact = CS2ManagerApp._dashboard_market_quote(
            stub,
            {"name": "Anything", "market_hash_name": "Exact MHN", "phase": "P1"},
            quote_index,
        )
        fallback = CS2ManagerApp._dashboard_market_quote(
            stub,
            {"name": " fallback name ", "market_hash_name": "Missing", "phase": "-"},
            quote_index,
        )

        self.assertIs(exact, exact_quote)
        self.assertIs(fallback, fallback_quote)

    def test_dashboard_fee_rates_are_loaded_once_per_config(self):
        class DBStub:
            def __init__(self):
                self.calls = []

            def get_config(self, key):
                self.calls.append(key)
                return "0.05"

        class Stub:
            db = DBStub()

        rates = CS2ManagerApp._dashboard_fee_rates(Stub())

        self.assertEqual(len(Stub.db.calls), 6)
        self.assertEqual(len(set(Stub.db.calls)), 6)
        self.assertEqual(rates["c5_first_fee"], 0.05)
        self.assertEqual(rates["eco_relet_fee"], 0.05)


if __name__ == "__main__":
    unittest.main()
