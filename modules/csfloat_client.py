"""Read-only CSFloat market client with conservative rate limiting."""

from __future__ import annotations

import logging
import json
import time
from email.utils import parsedate_to_datetime
from typing import Any
import requests

from modules.base_client import BaseAPIClient
from modules.paths import get_private_path


logger = logging.getLogger("CS2Rental")


class CSFloatClient(BaseAPIClient):
    """Fetch read-only CSFloat market quotes without account mutations."""

    BASE_URL = "https://csfloat.com/api/v1"
    # Server-directed cooldown is process-wide so creating a new refresh
    # worker cannot bypass a 429 from the previous refresh.
    _global_cooldown_until = 0.0
    _global_last_request_time = 0.0
    _global_cooldown_reason = ""
    _global_pacing_interval = 1.25
    _global_pacing_until = 0.0
    _global_request_count = 0
    _global_rate_limit = 0
    _global_rate_remaining = -1
    _global_rate_reset_at = 0.0
    _endpoint_429_streaks: dict[str, int] = {}
    _persisted_cooldown_loaded = False
    _persist_cooldown_enabled = True
    # Complete a refresh in a short burst when quota is healthy, but retain a
    # small safety reserve so background work stops before the server returns
    # another 429.  Every workspace still shares this one process-wide pool.
    RATE_LIMIT_RESERVE = 10
    FALLBACK_429_BACKOFF_SECONDS = (60, 120, 300, 600, 1200, 1800)

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
        type(self)._load_persisted_cooldown()

    @classmethod
    def reset_process_cooldown(cls):
        """Clear the in-process cooldown (primarily useful for offline tests)."""
        cls._global_cooldown_until = 0.0
        cls._global_last_request_time = 0.0
        cls._global_cooldown_reason = ""
        cls._global_pacing_interval = 1.25
        cls._global_pacing_until = 0.0
        cls._global_request_count = 0
        cls._global_rate_limit = 0
        cls._global_rate_remaining = -1
        cls._global_rate_reset_at = 0.0
        cls._endpoint_429_streaks = {}
        # Offline tests use this reset and must never read/write a user's
        # persisted runtime cooldown file.
        cls._persisted_cooldown_loaded = True
        cls._persist_cooldown_enabled = False

    @classmethod
    def _cooldown_state_path(cls):
        return get_private_path("csfloat_cooldown.json")

    @classmethod
    def _load_persisted_cooldown(cls):
        if cls._persisted_cooldown_loaded:
            return
        cls._persisted_cooldown_loaded = True
        try:
            payload = json.loads(cls._cooldown_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        try:
            until = float(payload.get("cooldown_until") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return
        # Ignore malformed or implausibly distant state instead of locking the
        # application indefinitely because of a damaged local file.
        now = time.time()
        if now < until <= now + 24 * 60 * 60:
            cls._global_cooldown_until = until
            cls._global_cooldown_reason = str(
                payload.get("reason") or "CSFloat 服务端频控（重启前）"
            )
        streaks = payload.get("endpoint_429_streaks") or {}
        if isinstance(streaks, dict):
            restored_streaks = {}
            for key, value in streaks.items():
                try:
                    streak = max(0, min(int(value), 100))
                except (TypeError, ValueError):
                    continue
                if str(key):
                    restored_streaks[str(key)] = streak
            cls._endpoint_429_streaks = restored_streaks

    @classmethod
    def _persist_cooldown(cls):
        if not cls._persist_cooldown_enabled:
            return
        path = cls._cooldown_state_path()
        temporary = path.with_suffix(path.suffix + ".tmp")
        payload = {
            "cooldown_until": cls._global_cooldown_until,
            "reason": cls._global_cooldown_reason,
            "endpoint_429_streaks": cls._endpoint_429_streaks,
            "saved_at": time.time(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(path)
        except OSError as exc:
            logger.warning("[CSFloat] 保存频控状态失败: %s", exc)

    @staticmethod
    def _endpoint_bucket(path: str) -> str:
        normalized = str(path or "/unknown")
        if normalized.startswith("/listings/") and normalized.endswith("/buy-orders"):
            return "/listings/{id}/buy-orders"
        return normalized

    @classmethod
    def _handle_http_429(cls, path: str):
        bucket = cls._endpoint_bucket(path)
        streak = cls._endpoint_429_streaks.get(bucket, 0) + 1
        cls._endpoint_429_streaks[bucket] = streak
        if cls.cooldown_remaining() <= 0:
            delay_index = min(streak - 1, len(cls.FALLBACK_429_BACKOFF_SECONDS) - 1)
            delay = cls.FALLBACK_429_BACKOFF_SECONDS[delay_index]
            cls._set_cooldown(
                time.time() + delay,
                f"CSFloat HTTP 429（{bucket}，连续 {streak} 次）",
            )
        else:
            cls._persist_cooldown()

    @classmethod
    def _mark_endpoint_success(cls, path: str):
        bucket = cls._endpoint_bucket(path)
        if cls._endpoint_429_streaks.pop(bucket, None) is not None:
            cls._persist_cooldown()

    @classmethod
    def _set_cooldown(cls, until: float, reason: str = "CSFloat 频控"):
        if until >= cls._global_cooldown_until:
            cls._global_cooldown_until = until
            cls._global_cooldown_reason = reason
            cls._persist_cooldown()
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
    def request_count(cls) -> int:
        """Return the number of CSFloat requests started in this process."""
        return int(cls._global_request_count)

    @classmethod
    def rate_limit_snapshot(cls) -> dict[str, float | int]:
        """Expose the latest safe response-header quota snapshot to the scheduler."""
        return {
            "limit": int(cls._global_rate_limit),
            "remaining": int(cls._global_rate_remaining),
            "reset_at": float(cls._global_rate_reset_at),
        }

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
        type(self)._global_request_count += 1
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

    def _observe_rate_headers(self, response, path: str = ""):
        endpoint = f"（{path}）" if path else ""
        remaining_raw = self._header(
            response, "ratelimit-remaining", "x-ratelimit-remaining"
        )
        limit_raw = self._header(response, "ratelimit-limit", "x-ratelimit-limit")
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
            self._set_cooldown(
                retry_at, f"CSFloat Retry-After 响应头{endpoint}"
            )

        try:
            remaining = int(float(remaining_raw))
        except (TypeError, ValueError):
            remaining = None
        try:
            rate_limit = int(float(limit_raw))
        except (TypeError, ValueError):
            rate_limit = None
        reset_at = 0.0
        if reset_raw is not None:
            try:
                reset_value = float(reset_raw)
                if reset_value > time.time() * 100:
                    reset_value /= 1000.0
                # Providers commonly expose either seconds-from-now or a Unix timestamp.
                reset_at = reset_value if reset_value > time.time() - 60 else time.time() + reset_value
            except (TypeError, ValueError):
                pass
        if response.status_code == 429 and reset_at > time.time():
            self._set_cooldown(
                reset_at, f"CSFloat RateLimit-Reset 响应头{endpoint}"
            )
        if remaining is not None and reset_at > time.time():
            cls = type(self)
            cls._global_rate_remaining = remaining
            cls._global_rate_reset_at = reset_at
            if rate_limit is not None:
                cls._global_rate_limit = rate_limit
            if remaining <= self.RATE_LIMIT_RESERVE:
                self._set_cooldown(
                    reset_at,
                    f"CSFloat 额度保留线（剩余 {remaining}）{endpoint}",
                )
            else:
                # Do not spread healthy quota evenly across the whole reset
                # window.  That made one user-visible refresh take many
                # minutes.  Finish the ordered batch at the base pace, then
                # wait between complete cycles instead.
                if reset_at >= cls._global_pacing_until:
                    cls._global_pacing_interval = self.min_interval
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

        self._observe_rate_headers(response, path)
        if response.status_code == 401:
            return {"success": False, "error": "unauthorized", "request_made": True}
        if response.status_code == 403:
            return {"success": False, "error": "forbidden", "request_made": True}
        if response.status_code == 429:
            self._handle_http_429(path)
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
        self._mark_endpoint_success(path)
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

    def get_lowest_buy_now(self, market_hash_name: str) -> dict[str, Any]:
        """Return the lowest active fixed-price listing for one exact item name."""
        result = self._get_json(
            "/listings",
            params={
                "market_hash_name": market_hash_name,
                "type": "buy_now",
                "sort_by": "lowest_price",
                # One sorted record is enough for the lowest quote and keeps
                # the response body small.
                "limit": 1,
            },
        )
        if not result.get("success"):
            return result
        payload = result.get("payload")
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
        if not listing_id:
            return {"success": False, "error": "missing_listing", "request_made": False}
        result = self._get_json(
            f"/listings/{listing_id}/buy-orders",
            params={"limit": max(1, min(int(limit or 10), 50))},
        )
        if not result.get("success"):
            return result
        payload = result.get("payload")
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
