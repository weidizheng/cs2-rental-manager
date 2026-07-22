"""Read-only CSFloat market/account client with conservative rate limiting."""

from __future__ import annotations

import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import requests

from modules.base_client import BaseAPIClient


logger = logging.getLogger("CS2Rental")


class CSFloatClient(BaseAPIClient):
    """Fetch market, account, buy-order and recent-sale data without mutations."""

    BASE_URL = "https://csfloat.com/api/v1"
    # Server-directed cooldown is process-wide so creating a new refresh
    # worker cannot bypass a 429 from the previous refresh.
    _global_cooldown_until = 0.0
    _global_last_request_time = 0.0
    _global_cooldown_reason = ""
    _global_pacing_interval = 1.25
    _global_pacing_until = 0.0

    def __init__(self, api_key: str, timeout: int = 12):
        # CSFloat publishes per-endpoint response headers instead of one fixed
        # public quota.  The local interval is intentionally conservative and
        # is supplemented by Retry-After / remaining/reset header handling.
        super().__init__(min_interval=1.25)
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": self.api_key,
            "Accept": "application/json",
            "User-Agent": "CS2RentalManager/3.0",
        })

    @classmethod
    def reset_process_cooldown(cls):
        """Clear the in-process cooldown (primarily useful for offline tests)."""
        cls._global_cooldown_until = 0.0
        cls._global_last_request_time = 0.0
        cls._global_cooldown_reason = ""
        cls._global_pacing_interval = 1.25
        cls._global_pacing_until = 0.0

    @classmethod
    def _set_cooldown(cls, until: float, reason: str = "CSFloat 频控"):
        if until >= cls._global_cooldown_until:
            cls._global_cooldown_until = until
            cls._global_cooldown_reason = reason
        logger.warning(
            "[CSFloat] 服务端频控反馈：%s，等待约 %s 秒",
            reason,
            max(0, int(until - time.time() + 0.999)),
        )

    @classmethod
    def cooldown_remaining(cls) -> int:
        return max(0, int(cls._global_cooldown_until - time.time() + 0.999))

    @classmethod
    def cooldown_reason(cls) -> str:
        if cls.cooldown_remaining() <= 0:
            return ""
        return cls._global_cooldown_reason or "CSFloat 频控"

    @classmethod
    def effective_request_interval(cls) -> float:
        if time.time() < cls._global_pacing_until:
            return max(1.25, float(cls._global_pacing_interval or 1.25))
        return 1.25

    @classmethod
    def _rate_limited_result(cls, *, request_made: bool) -> dict[str, Any]:
        return {
            "success": False,
            "error": "rate_limited",
            "retry_after": cls.cooldown_remaining(),
            "rate_limit_source": cls.cooldown_reason(),
            "request_made": request_made,
        }

    def _wait_rate_limit(self):
        """Enforce the minimum interval across newly-created refresh clients."""
        interval = max(self.min_interval, type(self).effective_request_interval())
        elapsed = time.time() - type(self)._global_last_request_time
        if elapsed < interval:
            time.sleep(interval - elapsed)
        type(self)._global_last_request_time = time.time()
        self.last_request_time = type(self)._global_last_request_time

    @staticmethod
    def _header(response, *names):
        for name in names:
            value = response.headers.get(name)
            if value is not None:
                return value
        normalized = {
            str(key).casefold(): value
            for key, value in getattr(response, "headers", {}).items()
        }
        for name in names:
            if str(name).casefold() in normalized:
                return normalized[str(name).casefold()]
        return None

    def _observe_rate_headers(self, response):
        remaining_raw = self._header(
            response, "ratelimit-remaining", "x-ratelimit-remaining"
        )
        reset_raw = self._header(response, "ratelimit-reset", "x-ratelimit-reset")
        retry_after_raw = self._header(response, "retry-after")

        retry_at = 0.0
        try:
            retry_after = max(0.0, float(retry_after_raw or 0.0))
            retry_at = time.time() + retry_after
        except (TypeError, ValueError):
            if retry_after_raw:
                try:
                    retry_at = parsedate_to_datetime(str(retry_after_raw)).timestamp()
                except (TypeError, ValueError, OverflowError):
                    pass
        if retry_at > time.time():
            self._set_cooldown(retry_at, "CSFloat Retry-After 响应头")

        try:
            remaining = int(float(remaining_raw))
        except (TypeError, ValueError):
            remaining = None
        reset_at = 0.0
        if remaining is not None and reset_raw is not None:
            try:
                reset_value = float(reset_raw)
                if reset_value > time.time() * 100:
                    reset_value /= 1000.0
                # Providers commonly expose either seconds-from-now or a Unix timestamp.
                reset_at = reset_value if reset_value > time.time() - 60 else time.time() + reset_value
            except (TypeError, ValueError):
                pass
        if remaining is not None and reset_at > time.time():
            if remaining <= 1:
                self._set_cooldown(
                    reset_at, "CSFloat RateLimit-Remaining/Reset 响应头"
                )
            else:
                # Treat every CSFloat endpoint and every workspace as one
                # conservative request pool.  The next client instance inherits
                # this server-directed interval instead of starting a fresh burst.
                safe_interval = min(
                    60.0,
                    max(self.min_interval, (reset_at - time.time()) / remaining),
                )
                cls = type(self)
                if reset_at >= cls._global_pacing_until:
                    cls._global_pacing_interval = safe_interval
                    cls._global_pacing_until = reset_at

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform one read-only GET request and normalize errors/cooldowns."""
        if not self.api_key:
            return {"success": False, "error": "missing_api_key", "request_made": False}
        if self.cooldown_remaining() > 0:
            return self._rate_limited_result(request_made=False)

        self._wait_rate_limit()
        try:
            response = self.session.get(
                f"{self.BASE_URL}{path}", params=params, timeout=self.timeout
            )
        except requests.RequestException as exc:
            logger.warning("[CSFloat] GET %s 请求失败: %s", path, exc)
            return {
                "success": False,
                "error": "network",
                "message": str(exc),
                "request_made": True,
            }

        self._observe_rate_headers(response)
        if response.status_code == 401:
            return {"success": False, "error": "unauthorized", "request_made": True}
        if response.status_code == 403:
            return {"success": False, "error": "forbidden", "request_made": True}
        if response.status_code == 429:
            if self.cooldown_remaining() <= 0:
                self._set_cooldown(time.time() + 60, "CSFloat HTTP 429")
            return self._rate_limited_result(request_made=True)
        if not 200 <= response.status_code < 300:
            logger.warning(
                "[CSFloat] %s %s HTTP %s: %s",
                "GET",
                path,
                response.status_code,
                str(getattr(response, "text", ""))[:200],
            )
            return {
                "success": False,
                "error": f"http_{response.status_code}",
                "request_made": True,
            }

        try:
            payload = response.json()
        except ValueError:
            return {"success": False, "error": "invalid_json", "request_made": True}
        return {
            "success": True,
            "payload": payload,
            "request_made": True,
        }

    @staticmethod
    def _positive_int(value, default=0) -> int:
        try:
            return max(0, int(value or default))
        except (TypeError, ValueError):
            return max(0, int(default or 0))

    def get_account(self) -> dict[str, Any]:
        """Return the authenticated user's balance and pending balance in cents."""
        result = self._get_json("/me")
        if not result.get("success"):
            return result
        payload = result.get("payload")
        user = payload.get("user", payload) if isinstance(payload, dict) else {}
        if not isinstance(user, dict):
            user = {}
        return {
            "success": True,
            "request_made": True,
            "balance_cents": self._positive_int(user.get("balance")),
            "pending_balance_cents": self._positive_int(user.get("pending_balance")),
            "username": str(user.get("username") or ""),
            "user": user,
        }

    def get_my_buy_orders(self, page: int = 0, limit: int = 100) -> dict[str, Any]:
        """Return active buy orders belonging to the authenticated user."""
        safe_page = max(0, self._positive_int(page))
        safe_limit = max(1, min(self._positive_int(limit, 100), 100))
        result = self._get_json(
            "/me/buy-orders",
            params={"page": safe_page, "limit": safe_limit, "order": "desc"},
        )
        if not result.get("success"):
            return result
        payload = result.get("payload")
        orders = payload.get("orders", []) if isinstance(payload, dict) else []
        if not isinstance(orders, list):
            orders = []
        normalized = []
        for raw in orders:
            if not isinstance(raw, dict):
                continue
            order = dict(raw)
            order["id"] = str(raw.get("id") or "")
            order["market_hash_name"] = str(raw.get("market_hash_name") or "")
            order["price"] = self._positive_int(raw.get("price"))
            order["qty"] = self._positive_int(raw.get("qty") or raw.get("quantity"), 1)
            order["hybrid_properties"] = raw.get("hybrid_properties") or {}
            normalized.append(order)
        count = self._positive_int(
            payload.get("count") if isinstance(payload, dict) else len(normalized),
            len(normalized),
        )
        return {
            "success": True,
            "request_made": True,
            "orders": normalized,
            "count": count,
        }

    def get_recent_sales(self, market_hash_name: str) -> dict[str, Any]:
        """Return recent completed sales for one exact market hash name."""
        name = str(market_hash_name or "").strip()
        if not name:
            return {"success": False, "error": "missing_market_hash_name", "request_made": False}
        result = self._get_json(f"/history/{quote(name, safe='')}/sales")
        if not result.get("success"):
            return result
        payload = result.get("payload")
        sales = payload.get("data", []) if isinstance(payload, dict) else payload
        if not isinstance(sales, list):
            sales = []
        normalized = []
        for raw in sales:
            if not isinstance(raw, dict):
                continue
            price_cents = self._positive_int(raw.get("price"))
            if price_cents <= 0:
                continue
            sale = dict(raw)
            sale["price"] = price_cents
            sale["sold_at"] = str(raw.get("sold_at") or "")
            normalized.append(sale)
        normalized.sort(key=lambda sale: sale.get("sold_at", ""), reverse=True)
        return {
            "success": True,
            "request_made": True,
            "sales": normalized,
        }

    def get_lowest_buy_now(self, market_hash_name: str) -> dict[str, Any]:
        """Return the lowest active fixed-price listing for one exact item name."""
        if not self.api_key:
            return {"success": False, "error": "missing_api_key", "request_made": False}
        if self.cooldown_remaining() > 0:
            return self._rate_limited_result(request_made=False)

        self._wait_rate_limit()
        try:
            response = self.session.get(
                f"{self.BASE_URL}/listings",
                params={
                    "market_hash_name": market_hash_name,
                    "type": "buy_now",
                    "sort_by": "lowest_price",
                    # One sorted record is enough for the lowest quote and
                    # keeps the response body small.
                    "limit": 1,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.warning("[CSFloat] 请求失败: %s", exc)
            return {
                "success": False,
                "error": "network",
                "message": str(exc),
                "request_made": True,
            }

        self._observe_rate_headers(response)
        if response.status_code == 401:
            return {"success": False, "error": "unauthorized", "request_made": True}
        if response.status_code == 403:
            return {"success": False, "error": "forbidden", "request_made": True}
        if response.status_code == 429:
            if self.cooldown_remaining() <= 0:
                self._set_cooldown(time.time() + 60, "CSFloat HTTP 429")
            return self._rate_limited_result(request_made=True)
        if response.status_code != 200:
            logger.warning(
                "[CSFloat] HTTP %s: %s", response.status_code, response.text[:200]
            )
            return {
                "success": False,
                "error": f"http_{response.status_code}",
                "request_made": True,
            }

        try:
            payload = response.json()
        except ValueError:
            return {"success": False, "error": "invalid_json", "request_made": True}
        listings = payload.get("data", []) if isinstance(payload, dict) else payload
        if not isinstance(listings, list):
            listings = []

        valid_listings = []
        for listing in listings:
            if not isinstance(listing, dict):
                continue
            item = listing.get("item") or {}
            if listing.get("type") != "buy_now" or listing.get("state") != "listed":
                continue
            if item.get("market_hash_name") != market_hash_name:
                continue
            try:
                price_cents = int(listing.get("price") or 0)
            except (TypeError, ValueError):
                price_cents = 0
            if price_cents > 0:
                valid_listings.append((price_cents, listing))

        if not valid_listings:
            return {"success": True, "found": False, "request_made": True}
        price_cents, listing = min(valid_listings, key=lambda pair: pair[0])
        item = listing.get("item") or {}
        return {
            "success": True,
            "found": True,
            "request_made": True,
            "listing_id": str(listing.get("id") or ""),
            "price_cents": price_cents,
            "price_usd": price_cents / 100.0,
            "float_value": item.get("float_value"),
            "paint_seed": item.get("paint_seed"),
            "market_hash_name": item.get("market_hash_name", market_hash_name),
        }

    def get_highest_buy_order(self, listing_id: str, limit: int = 10) -> dict[str, Any]:
        """Return the highest active market buy order for a known listing.

        CSFloat exposes buy orders from a listing-scoped, read-only endpoint.
        The listing id is obtained from ``get_lowest_buy_now``; no purchase,
        offer, or buy-order mutation is performed here.
        """
        if not self.api_key:
            return {"success": False, "error": "missing_api_key", "request_made": False}
        if not listing_id:
            return {"success": False, "error": "missing_listing", "request_made": False}
        if self.cooldown_remaining() > 0:
            return self._rate_limited_result(request_made=False)

        self._wait_rate_limit()
        try:
            response = self.session.get(
                f"{self.BASE_URL}/listings/{listing_id}/buy-orders",
                params={"limit": max(1, min(int(limit or 10), 50))},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.warning("[CSFloat] 最高求购请求失败: %s", exc)
            return {
                "success": False,
                "error": "network",
                "message": str(exc),
                "request_made": True,
            }

        self._observe_rate_headers(response)
        if response.status_code == 401:
            return {"success": False, "error": "unauthorized", "request_made": True}
        if response.status_code == 403:
            return {"success": False, "error": "forbidden", "request_made": True}
        if response.status_code == 429:
            if self.cooldown_remaining() <= 0:
                self._set_cooldown(time.time() + 60, "CSFloat HTTP 429")
            return self._rate_limited_result(request_made=True)
        if response.status_code != 200:
            logger.warning(
                "[CSFloat] 最高求购 HTTP %s: %s", response.status_code, response.text[:200]
            )
            return {
                "success": False,
                "error": f"http_{response.status_code}",
                "request_made": True,
            }

        try:
            payload = response.json()
        except ValueError:
            return {"success": False, "error": "invalid_json", "request_made": True}
        orders = payload.get("data", []) if isinstance(payload, dict) else payload
        if not isinstance(orders, list):
            orders = []

        valid_orders = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            try:
                price_cents = int(order.get("price") or 0)
            except (TypeError, ValueError):
                price_cents = 0
            if price_cents > 0:
                valid_orders.append((price_cents, order))

        if not valid_orders:
            return {"success": True, "found": False, "request_made": True}
        price_cents, order = max(valid_orders, key=lambda pair: pair[0])
        try:
            quantity = int(order.get("qty") or order.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        return {
            "success": True,
            "found": True,
            "request_made": True,
            "price_cents": price_cents,
            "price_usd": price_cents / 100.0,
            "quantity": quantity,
            "hybrid_properties": order.get("hybrid_properties") or {},
        }
