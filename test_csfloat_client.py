"""Offline tests for the read-only CSFloat integration."""

import time
import unittest
from unittest.mock import patch

from modules.csfloat_client import CSFloatClient
from modules.workers import (
    CSFLOAT_MAX_REQUESTS_PER_REFRESH,
    MarketRefreshWorker,
    csfloat_cny_display_price,
    csfloat_quote_is_fresh,
)


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return self.response


class CSFloatClientTests(unittest.TestCase):
    def setUp(self):
        CSFloatClient.reset_process_cooldown()

    def test_lowest_quote_excludes_auction_and_uses_exact_name(self):
        market_hash_name = "★ Butterfly Knife | Gamma Doppler (Factory New)"
        payload = [
            {
                "id": "auction-cheaper",
                "type": "auction",
                "state": "listed",
                "price": 100,
                "item": {"market_hash_name": market_hash_name},
            },
            {
                "id": "wrong-item",
                "type": "buy_now",
                "state": "listed",
                "price": 200,
                "item": {"market_hash_name": "AK-47 | Redline (Field-Tested)"},
            },
            {
                "id": "fixed-price",
                "type": "buy_now",
                "state": "listed",
                "price": 12345,
                "item": {
                    "market_hash_name": market_hash_name,
                    "float_value": 0.0123,
                    "paint_seed": 456,
                },
            },
        ]
        client = CSFloatClient("test-key")
        self.assertEqual(client.session.headers["Authorization"], "test-key")
        client.session = FakeSession(FakeResponse(payload))

        result = client.get_lowest_buy_now(market_hash_name)

        self.assertTrue(result["success"])
        self.assertTrue(result["found"])
        self.assertEqual(result["listing_id"], "fixed-price")
        self.assertEqual(result["price_usd"], 123.45)
        params = client.session.calls[0][1]
        self.assertEqual(params["type"], "buy_now")
        self.assertEqual(params["sort_by"], "lowest_price")
        self.assertEqual(params["market_hash_name"], market_hash_name)
        self.assertEqual(params["limit"], 1)

    def test_rate_headers_stop_the_next_request_locally(self):
        response = FakeResponse(
            [],
            headers={
                "ratelimit-remaining": "0",
                "ratelimit-reset": str(time.time() + 30),
            },
        )
        client = CSFloatClient("test-key")
        client.session = FakeSession(response)

        first = client.get_lowest_buy_now("AK-47 | Redline (Field-Tested)")
        second = client.get_lowest_buy_now("M4A4 | Poseidon (Factory New)")

        self.assertTrue(first["success"])
        self.assertEqual(second["error"], "rate_limited")
        self.assertEqual(len(client.session.calls), 1)

    def test_429_retry_after_is_shared_by_the_next_client(self):
        first_client = CSFloatClient("test-key")
        first_client.session = FakeSession(
            FakeResponse([], status_code=429, headers={"Retry-After": "30"})
        )
        result = first_client.get_lowest_buy_now("AK-47 | Redline (Field-Tested)")
        self.assertEqual(result["error"], "rate_limited")
        self.assertTrue(result["request_made"])

        second_client = CSFloatClient("test-key")
        second_client.session = FakeSession(FakeResponse([]))
        result = second_client.get_lowest_buy_now("M4A4 | Poseidon (Factory New)")
        self.assertEqual(result["error"], "rate_limited")
        self.assertFalse(result["request_made"])
        self.assertEqual(second_client.session.calls, [])

    def test_wrapped_data_response_is_supported(self):
        name = "M4A4 | Poseidon (Factory New)"
        listing = {
            "id": "wrapped",
            "type": "buy_now",
            "state": "listed",
            "price": 260000,
            "item": {"market_hash_name": name},
        }
        client = CSFloatClient("test-key")
        client.session = FakeSession(FakeResponse({"data": [listing]}))
        result = client.get_lowest_buy_now(name)
        self.assertEqual(result["price_usd"], 2600.0)

    def test_highest_buy_order_uses_the_largest_price_not_response_order(self):
        client = CSFloatClient("test-key")
        client.session = FakeSession(FakeResponse([
            {"price": 31900, "qty": 1, "hybrid_properties": {}},
            {"price": 32500, "qty": 2, "hybrid_properties": {"float": {"max": 0.2}}},
            {"price": 29400, "qty": 1},
        ]))

        result = client.get_highest_buy_order("listing-123")

        self.assertTrue(result["success"])
        self.assertTrue(result["found"])
        self.assertEqual(result["price_cents"], 32500)
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["hybrid_properties"], {"float": {"max": 0.2}})
        url, params, _timeout = client.session.calls[0]
        self.assertTrue(url.endswith("/listings/listing-123/buy-orders"))
        self.assertEqual(params, {"limit": 10})

    def test_missing_key_never_sends_a_request(self):
        client = CSFloatClient("")
        client.session = FakeSession(FakeResponse([]))
        result = client.get_lowest_buy_now("M4A4 | Poseidon (Factory New)")
        self.assertEqual(result["error"], "missing_api_key")
        self.assertFalse(result["request_made"])
        self.assertEqual(client.session.calls, [])

    def test_client_exposes_no_buy_order_write_methods(self):
        client = CSFloatClient("test-key")
        self.assertFalse(hasattr(client, "create_buy_order"))
        self.assertFalse(hasattr(client, "delete_buy_order"))

    def test_account_and_my_buy_orders_are_normalized(self):
        client = CSFloatClient("test-key")
        client.session = FakeSession(FakeResponse({
            "user": {
                "username": "tester",
                "balance": 12345,
                "pending_balance": 678,
            }
        }))
        account = client.get_account()
        self.assertTrue(account["success"])
        self.assertEqual(account["balance_cents"], 12345)
        self.assertEqual(account["pending_balance_cents"], 678)
        self.assertEqual(account["username"], "tester")

        client.session = FakeSession(FakeResponse({
            "count": 1,
            "orders": [{
                "id": 987,
                "market_hash_name": "AK-47 | Redline (Field-Tested)",
                "price": "2500",
                "qty": "2",
                "hybrid_properties": {"paint_index": 12},
            }],
        }))
        orders = client.get_my_buy_orders()
        self.assertTrue(orders["success"])
        self.assertEqual(orders["count"], 1)
        self.assertEqual(orders["orders"][0]["id"], "987")
        self.assertEqual(orders["orders"][0]["price"], 2500)
        self.assertEqual(orders["orders"][0]["qty"], 2)

    def test_recent_sales_filters_invalid_prices_and_sorts_newest_first(self):
        client = CSFloatClient("test-key")
        client.session = FakeSession(FakeResponse([
            {"price": 1200, "sold_at": "2026-01-01T00:00:00Z"},
            {"price": 0, "sold_at": "2026-01-03T00:00:00Z"},
            {"price": 1250, "sold_at": "2026-01-02T00:00:00Z"},
        ]))
        result = client.get_recent_sales("AK-47 | Redline (Field-Tested)")
        self.assertTrue(result["success"])
        self.assertEqual([sale["price"] for sale in result["sales"]], [1250, 1200])
        self.assertIn("AK-47%20%7C%20Redline", client.session.calls[0][0])

    def test_cache_requires_same_name_and_ttl(self):
        now = time.time()
        entry = {"csfloat_fetched_at": now, "csfloat_query_mhn": "name-a"}
        self.assertTrue(csfloat_quote_is_fresh(entry, "name-a", now=now + 10))
        self.assertFalse(csfloat_quote_is_fresh(entry, "name-b", now=now + 10))
        self.assertFalse(csfloat_quote_is_fresh(entry, "name-a", now=now + 601))

    def test_cny_display_rounds_up_to_match_csfloat_frontend(self):
        self.assertEqual(csfloat_cny_display_price(1, 6.776), 0.07)
        self.assertEqual(csfloat_cny_display_price(100, 6.776), 6.78)

    def test_worker_caps_each_refresh_and_defers_remaining_rows(self):
        class FakeClient:
            calls = []

            def __init__(self, _api_key):
                pass

            def get_lowest_buy_now(self, market_hash_name):
                self.calls.append(market_hash_name)
                return {
                    "success": True,
                    "found": False,
                    "request_made": True,
                }

        entries = [
            {"name": f"item-{index}", "market_hash_name": f"item-{index}"}
            for index in range(CSFLOAT_MAX_REQUESTS_PER_REFRESH + 5)
        ]
        worker = MarketRefreshWorker()
        with patch("modules.workers.CSFloatClient", FakeClient):
            worker.refresh_all(
                "", "", "", entries,
                lambda entry: entry["market_hash_name"],
                csfloat_api_key="test-key",
            )

        self.assertEqual(len(FakeClient.calls), CSFLOAT_MAX_REQUESTS_PER_REFRESH)
        deferred = [entry for entry in entries if entry.get("csfloat_status") == "deferred"]
        self.assertEqual(len(deferred), 5)

    def test_worker_reuses_fresh_quote_and_recalculates_exchange_rate(self):
        class NoNetworkClient:
            def __init__(self, _api_key):
                pass

            def get_lowest_buy_now(self, _market_hash_name):
                raise AssertionError("fresh cache must not call CSFloat")

        name = "M4A4 | Poseidon (Factory New)"
        entry = {
            "name": name,
            "market_hash_name": name,
            "csfloat_query_mhn": name,
            "csfloat_fetched_at": time.time(),
            "csfloat_min_sell_usd": 100.0,
            "csfloat_min_sell_cny": 720.0,
            "csfloat_status": "ok",
        }
        worker = MarketRefreshWorker()
        with patch("modules.workers.CSFloatClient", NoNetworkClient):
            worker.refresh_all(
                "", "", "", [entry],
                lambda value: value["market_hash_name"],
                csfloat_api_key="test-key",
                usd_cny_rate=7.35,
            )

        self.assertEqual(entry["csfloat_min_sell_cny"], 735.0)
        self.assertIn("缓存 1 条", worker.csfloat_status_text)

    def test_worker_refreshes_a_buy_order_with_the_listing_quote(self):
        class FakeClient:
            def __init__(self, _api_key):
                pass

            def get_lowest_buy_now(self, _market_hash_name):
                return {
                    "success": True, "found": True, "request_made": True,
                    "listing_id": "listing-1", "price_cents": 20000,
                    "price_usd": 200.0, "float_value": 0.2, "paint_seed": 1,
                }

            def get_highest_buy_order(self, listing_id):
                assert listing_id == "listing-1"
                return {
                    "success": True, "found": True, "request_made": True,
                    "price_cents": 15000, "price_usd": 150.0, "quantity": 2,
                    "hybrid_properties": {"float": {"max": 0.3}},
                }

        entry = {"name": "item", "market_hash_name": "item"}
        worker = MarketRefreshWorker()
        with patch("modules.workers.CSFloatClient", FakeClient):
            worker.refresh_all(
                "", "", "", [entry], lambda value: value["market_hash_name"],
                csfloat_api_key="test-key", force_csfloat=True,
            )

        self.assertEqual(entry["csfloat_highest_buy_price_cents"], 15000)
        self.assertEqual(entry["csfloat_highest_buy_cny"], 1080.0)
        self.assertEqual(entry["csfloat_highest_buy_qty"], 2)

    def test_worker_prefers_csfloat_official_exchange_rate(self):
        class NoNetworkQuoteClient:
            def __init__(self, _api_key):
                pass

            def get_lowest_buy_now(self, _market_hash_name):
                raise AssertionError("fresh quote cache must not query listings")

        class OfficialRateClient:
            def get_usd_cny(self, _fallback):
                return {
                    "rate": 6.776,
                    "source": "CSFloat",
                    "reference_date": None,
                    "status": "live",
                }

        name = "M4A4 | Poseidon (Factory New)"
        entry = {
            "name": name,
            "market_hash_name": name,
            "csfloat_query_mhn": name,
            "csfloat_fetched_at": time.time(),
            "csfloat_price_cents": 12345,
            "csfloat_min_sell_usd": 123.45,
            "csfloat_status": "ok",
        }
        worker = MarketRefreshWorker()
        with (
            patch("modules.workers.CSFloatClient", NoNetworkQuoteClient),
            patch("modules.workers.ExchangeRateClient", OfficialRateClient),
        ):
            worker.refresh_all(
                "", "", "", [entry],
                lambda value: value["market_hash_name"],
                csfloat_api_key="test-key",
                usd_cny_rate=7.35,
                auto_usd_cny_rate=True,
            )

        self.assertEqual(entry["csfloat_fx_source"], "csfloat")
        self.assertEqual(entry["csfloat_fx_rate"], 6.776)
        self.assertEqual(
            entry["csfloat_min_sell_cny"],
            csfloat_cny_display_price(12345, 6.776),
        )
        self.assertIn("官网汇率 6.7760", worker.csfloat_status_text)

    def test_worker_stops_after_protocol_error(self):
        class BrokenClient:
            calls = 0

            def __init__(self, _api_key):
                pass

            def get_lowest_buy_now(self, _market_hash_name):
                type(self).calls += 1
                return {
                    "success": False,
                    "error": "invalid_json",
                    "request_made": True,
                }

        entries = [
            {"name": "one", "market_hash_name": "one"},
            {"name": "two", "market_hash_name": "two"},
        ]
        worker = MarketRefreshWorker()
        with patch("modules.workers.CSFloatClient", BrokenClient):
            worker.refresh_all(
                "", "", "", entries,
                lambda entry: entry["market_hash_name"],
                csfloat_api_key="test-key",
            )

        self.assertEqual(BrokenClient.calls, 1)
        self.assertEqual(entries[1]["csfloat_status"], "skipped_invalid_json")


if __name__ == "__main__":
    unittest.main()
