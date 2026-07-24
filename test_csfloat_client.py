"""Offline tests for the read-only CSFloat integration."""

import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.csfloat_client import CSFloatClient
from modules.workers import (
    CSFLOAT_MAX_REQUESTS_PER_REFRESH,
    MarketRefreshWorker,
    csfloat_buy_quote_is_fresh,
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
        self.assertIn("Retry-After", result["rate_limit_source"])

    def test_429_feedback_identifies_the_endpoint(self):
        client = CSFloatClient("test-key")
        client.session = FakeSession(FakeResponse([], status_code=429))

        result = client.get_highest_buy_order("listing-123")

        self.assertEqual(result["error"], "rate_limited")
        self.assertIn("/listings/{id}/buy-orders", result["rate_limit_source"])

    def test_repeated_headerless_429_uses_endpoint_exponential_backoff(self):
        with (
            patch("modules.csfloat_client.time.time", return_value=1_000.0),
            patch("modules.csfloat_client.time.sleep"),
        ):
            first = CSFloatClient("test-key")
            first.session = FakeSession(FakeResponse([], status_code=429))
            result = first.get_lowest_buy_now("AK-47 | Redline (Field-Tested)")
        self.assertEqual(result["retry_after"], 60)
        self.assertIn("/listings", result["rate_limit_source"])
        self.assertIn("连续 1 次", result["rate_limit_source"])

        with (
            patch("modules.csfloat_client.time.time", return_value=1_061.0),
            patch("modules.csfloat_client.time.sleep"),
        ):
            second = CSFloatClient("test-key")
            second.session = FakeSession(FakeResponse([], status_code=429))
            result = second.get_lowest_buy_now("M4A4 | Poseidon (Factory New)")
        self.assertEqual(result["retry_after"], 120)
        self.assertIn("连续 2 次", result["rate_limit_source"])

        with (
            patch("modules.csfloat_client.time.time", return_value=1_182.0),
            patch("modules.csfloat_client.time.sleep"),
        ):
            recovered = CSFloatClient("test-key")
            recovered.session = FakeSession(FakeResponse([]))
            self.assertTrue(
                recovered.get_lowest_buy_now("M4A4 | Poseidon (Factory New)")["success"]
            )
        self.assertNotIn("/listings", CSFloatClient._endpoint_429_streaks)

    def test_persisted_cooldown_survives_a_simulated_restart(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            state_path = Path(temporary_dir) / "csfloat_cooldown.json"
            with (
                patch.object(CSFloatClient, "_cooldown_state_path", return_value=state_path),
                patch("modules.csfloat_client.time.time", return_value=2_000.0),
            ):
                CSFloatClient._persist_cooldown_enabled = True
                CSFloatClient._handle_http_429("/listings")

            CSFloatClient._global_cooldown_until = 0.0
            CSFloatClient._global_cooldown_reason = ""
            CSFloatClient._endpoint_429_streaks = {}
            CSFloatClient._persisted_cooldown_loaded = False
            with (
                patch.object(CSFloatClient, "_cooldown_state_path", return_value=state_path),
                patch("modules.csfloat_client.time.time", return_value=2_001.0),
            ):
                CSFloatClient("test-key")
                self.assertEqual(CSFloatClient.cooldown_remaining(), 59)
                self.assertEqual(CSFloatClient._endpoint_429_streaks["/listings"], 1)

    def test_healthy_rate_headers_keep_the_fast_shared_base_interval(self):
        client = CSFloatClient("test-key")
        client._observe_rate_headers(FakeResponse([], headers={
            "ratelimit-remaining": "20",
            "ratelimit-reset": str(time.time() + 50),
        }))
        self.assertEqual(CSFloatClient.effective_request_interval(), 1.25)
        another_client = CSFloatClient("test-key")
        self.assertEqual(
            another_client.effective_request_interval(),
            client.effective_request_interval(),
        )

    def test_rate_snapshot_and_request_count_are_available_to_scheduler(self):
        reset_at = time.time() + 1_920
        client = CSFloatClient("test-key")
        client.session = FakeSession(FakeResponse([], headers={
            "x-ratelimit-limit": "200",
            "x-ratelimit-remaining": "161",
            "x-ratelimit-reset": str(reset_at),
        }))

        client.get_lowest_buy_now("M4A4 | Poseidon (Factory New)")
        snapshot = CSFloatClient.rate_limit_snapshot()

        self.assertEqual(CSFloatClient.request_count(), 1)
        self.assertEqual(snapshot["limit"], 200)
        self.assertEqual(snapshot["remaining"], 161)
        self.assertAlmostEqual(snapshot["reset_at"], reset_at, delta=0.01)

    def test_rate_header_reserve_stops_every_workspace_before_429(self):
        client = CSFloatClient("test-key")
        client._observe_rate_headers(FakeResponse([], headers={
            "ratelimit-remaining": str(CSFloatClient.RATE_LIMIT_RESERVE),
            "ratelimit-reset": str(time.time() + 50),
        }), "/listings")

        another_client = CSFloatClient("test-key")
        another_client.session = FakeSession(FakeResponse([]))
        result = another_client.get_lowest_buy_now("M4A4 | Poseidon (Factory New)")

        self.assertEqual(result["error"], "rate_limited")
        self.assertFalse(result["request_made"])
        self.assertEqual(another_client.session.calls, [])
        self.assertIn("额度保留线", result["rate_limit_source"])

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

    def test_cache_requires_same_name_and_ttl(self):
        now = time.time()
        entry = {"csfloat_fetched_at": now, "csfloat_query_mhn": "name-a"}
        self.assertTrue(csfloat_quote_is_fresh(entry, "name-a", now=now + 10))
        self.assertFalse(csfloat_quote_is_fresh(entry, "name-b", now=now + 10))
        self.assertFalse(csfloat_quote_is_fresh(entry, "name-a", now=now + 601))

    def test_highest_buy_quote_has_an_independent_thirty_minute_cache(self):
        now = time.time()
        entry = {
            "csfloat_buy_fetched_at": now,
            "csfloat_buy_query_mhn": "name-a",
        }
        self.assertTrue(csfloat_buy_quote_is_fresh(entry, "name-a", now=now + 1_799))
        self.assertFalse(csfloat_buy_quote_is_fresh(entry, "name-a", now=now + 1_801))
        self.assertFalse(csfloat_buy_quote_is_fresh(entry, "name-b", now=now + 10))

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

    def test_worker_reuses_fresh_highest_buy_when_listing_quote_is_stale(self):
        class ListingOnlyClient:
            def __init__(self, _api_key):
                pass

            def get_lowest_buy_now(self, _market_hash_name):
                return {
                    "success": True, "found": True, "request_made": True,
                    "listing_id": "new-listing", "price_cents": 21000,
                    "price_usd": 210.0, "float_value": 0.2, "paint_seed": 1,
                }

            def get_highest_buy_order(self, _listing_id):
                raise AssertionError("fresh thirty-minute buy cache must be reused")

        name = "item"
        entry = {
            "name": name,
            "market_hash_name": name,
            "csfloat_buy_fetched_at": time.time(),
            "csfloat_buy_query_mhn": name,
            "csfloat_highest_buy_price_cents": 15000,
            "csfloat_highest_buy_cny": 1080.0,
            "csfloat_buy_status": "ok",
        }
        worker = MarketRefreshWorker()
        with patch("modules.workers.CSFloatClient", ListingOnlyClient):
            worker.refresh_all(
                "", "", "", [entry], lambda value: value["market_hash_name"],
                csfloat_api_key="test-key",
            )

        self.assertEqual(entry["csfloat_price_cents"], 21000)
        self.assertEqual(entry["csfloat_highest_buy_price_cents"], 15000)

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
