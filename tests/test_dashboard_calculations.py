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

    def test_returned_order_starts_full_seven_day_cd_at_actual_return_time(self):
        rental_end = datetime(2026, 7, 23, 10, 0, 0)
        returned_at = datetime(2026, 7, 23, 22, 26, 15)
        self.assertEqual(
            _rental_lifecycle_state(rental_end, returned_at, returned_at),
            ("cooldown", returned_at + timedelta(days=7)),
        )
        self.assertEqual(
            _rental_lifecycle_state(
                rental_end,
                returned_at + timedelta(days=7),
                returned_at,
            ),
            ("available", None),
        )

    def test_only_active_orders_past_the_return_window_need_an_update(self):
        now = datetime(2026, 7, 24, 13, 0, 0)
        orders = [
            {
                "platform": "ECOSteam",
                "order_no": "overdue",
                "status": "租赁中",
                "return_deadline": "2026-07-24 20:00:00",
            },
            {
                "platform": "ECOSteam",
                "order_no": "current",
                "status": "待归还",
                "return_deadline": "2026-07-25 00:00:00",
            },
            {
                "platform": "ECOSteam",
                "order_no": "returned",
                "status": "已归还",
                "return_deadline": "2026-07-24 20:00:00",
            },
        ]
        stale = CS2ManagerApp._orders_needing_update(orders, now)
        self.assertEqual([order["order_no"] for order in stale], ["overdue"])

    def test_update_status_requires_an_explicit_deadline_and_rental_status(self):
        orders = [
            {
                "platform": "C5GAME",
                "order_no": "derived-deadline",
                "status": "租赁中",
                "rental_end_time": "2026-07-24 12:00:00",
            },
            {
                "platform": "C5GAME",
                "order_no": "pending-return",
                "status": "待归还",
                "return_deadline": "2026-07-24 12:00:00",
            },
        ]

        stale = CS2ManagerApp._orders_needing_update(orders, datetime(2099, 1, 1))

        self.assertEqual(stale, [])

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

    def test_dashboard_prioritizes_the_shortest_known_countdown(self):
        now = datetime(2026, 7, 22, 12, 0, 0)

        def record(item_id, deadline=None, unknown=False):
            return {
                "item": {"id": item_id, "name": f"Type {item_id}", "cost": 100},
                "platform": "C5GAME",
                "is_currently_rented": bool(deadline or unknown),
                "sort_deadline": deadline,
                "has_unknown_timer": unknown,
            }

        records = [
            record(1),
            record(2, now + timedelta(hours=5)),
            record(3, now + timedelta(hours=2)),
            record(4, unknown=True),
        ]
        sorted_records = _sort_dashboard_records(records)
        self.assertEqual(
            [entry["item"]["id"] for entry in sorted_records],
            [3, 2, 4, 1],
        )

    def test_dashboard_sorts_active_rentals_before_cooldowns(self):
        now = datetime(2026, 7, 22, 12, 0, 0)

        def record(item_id, priority, deadline):
            return {
                "item": {"id": item_id, "name": f"Type {item_id}", "cost": 100},
                "platform": "C5GAME",
                "is_currently_rented": priority == 0,
                "sort_deadline": deadline,
                "lifecycle_priority": priority,
            }

        records = [
            record(1, 1, now + timedelta(hours=1)),
            record(2, 0, now + timedelta(hours=5)),
            record(3, 1, now + timedelta(hours=3)),
            record(4, 0, now + timedelta(hours=2)),
        ]
        self.assertEqual(
            [entry["item"]["id"] for entry in _sort_dashboard_records(records)],
            [4, 2, 1, 3],
        )

    def test_eco_uses_return_deadline_for_existing_order_countdowns(self):
        order = {
            "platform": "ECOSteam",
            "start_time": "2026-07-22 01:20:03",
            "rental_days": 10,
            "return_deadline": "2026-08-01 19:23:43",
        }
        self.assertEqual(
            CS2ManagerApp._rental_end_datetime(order),
            datetime(2026, 7, 31, 23, 23, 43),
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

    def test_c5_transfer_reward_requires_a_verified_twelve_hour_relet_and_caps_at_five_percent(self):
        app = CS2ManagerApp.__new__(CS2ManagerApp)
        original = {
            "platform": "C5GAME",
            "order_no": "original",
            "daily_rent": 100.0,
            "rental_days": 1.0,
            "rental_end_time": "2026-07-20 12:00:00",
            "transfer_reward": 8.0,
            "transfer_reward_known": True,
            "reward_status": "待发放",
        }
        eligible_next = {
            "platform": "C5GAME",
            "order_no": "relet",
            "start_time": "2026-07-20 23:59:00",
        }
        self.assertEqual(app._order_transfer_reward(original, [original, eligible_next]), 5.0)

        outside_window = dict(eligible_next, start_time="2026-07-21 00:01:00")
        self.assertEqual(app._order_transfer_reward(original, [original, outside_window]), 0.0)

        other_platform = dict(eligible_next, platform="ECOSteam")
        self.assertEqual(app._order_transfer_reward(original, [original, other_platform]), 0.0)

        non_c5 = dict(original, platform="IGXE")
        self.assertEqual(app._order_transfer_reward(non_c5, [non_c5, eligible_next]), 0.0)

    def test_igxe_confirmed_pricing_mode_overrides_legacy_fee_settings(self):
        class DBStub:
            @staticmethod
            def get_config(_key):
                return "0.42"

        class Stub:
            db = DBStub()

            @staticmethod
            def _is_relet_order(_order, _history):
                return False

        one_click = CS2ManagerApp._order_fee_rate(
            Stub(), {"platform": "IGXE", "pricing_mode": "one_click"}, []
        )
        manual = CS2ManagerApp._order_fee_rate(
            Stub(), {"platform": "IGXE", "pricing_mode": "manual"}, []
        )
        self.assertEqual(one_click, 0.05)
        self.assertEqual(manual, 0.10)


if __name__ == "__main__":
    unittest.main()
