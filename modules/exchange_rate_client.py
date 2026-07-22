"""USD/CNY rate client backed by CSFloat and ECB first-party endpoints.

CSFloat's own CNY conversion is preferred so displayed market prices match the
provider as closely as possible.  When it is unavailable, the European Central
Bank rates are crossed as ``CNY_per_EUR / USD_per_EUR``.  A 12-hour local cache
avoids unnecessary requests while the failure marker prevents repeated retries
during an outage affecting both providers.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

import requests

from modules.paths import get_private_path


class ExchangeRateClient:
    """Fetch and persist CSFloat's preferred USD/CNY display rate."""

    CSFLOAT_URL = "https://csfloat.com/api/v1/meta/exchange-rates"
    ECB_URL = (
        "https://www.ecb.europa.eu/stats/eurofxref/"
        "eurofxref-daily.xml"
    )
    CACHE_TTL = timedelta(hours=12)
    FAILURE_COOLDOWN = timedelta(hours=1)
    DEFAULT_MANUAL_FALLBACK = 7.20

    def __init__(
        self,
        cache_path: str | Path | None = None,
        timeout: int | float = 10,
        session: requests.Session | None = None,
        csfloat_session: requests.Session | None = None,
        ecb_session: requests.Session | None = None,
        now_func: Callable[[], datetime] | None = None,
    ):
        self.cache_path = (
            Path(cache_path)
            if cache_path is not None
            else get_private_path("exchange_rate_cache.json")
        )
        self.timeout = timeout
        shared_session = session or requests.Session()
        self.session = shared_session
        self.csfloat_session = csfloat_session or shared_session
        self.ecb_session = ecb_session or shared_session
        self._now_func = now_func or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def parse_ecb_xml(xml_data: str | bytes) -> dict[str, Any]:
        """Parse the most recent USD and CNY reference rates from ECB XML."""
        try:
            root = ElementTree.fromstring(xml_data)
        except (ElementTree.ParseError, TypeError) as exc:
            raise ValueError("invalid ECB XML") from exc

        candidates: list[tuple[str, float, float]] = []
        for element in root.iter():
            reference_date = element.attrib.get("time")
            if not reference_date:
                continue
            rates: dict[str, float] = {}
            for child in element:
                currency = child.attrib.get("currency")
                value = child.attrib.get("rate")
                if currency not in {"USD", "CNY"} or value is None:
                    continue
                try:
                    rate = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(rate) and rate > 0:
                    rates[currency] = rate
            if "USD" in rates and "CNY" in rates:
                candidates.append((reference_date, rates["USD"], rates["CNY"]))

        if not candidates:
            raise ValueError("ECB XML does not contain both USD and CNY rates")
        reference_date, usd_per_eur, cny_per_eur = max(
            candidates, key=lambda item: item[0]
        )
        rate = cny_per_eur / usd_per_eur
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("invalid derived USD/CNY rate")
        return {
            "rate": rate,
            "reference_date": reference_date,
            "usd_per_eur": usd_per_eur,
            "cny_per_eur": cny_per_eur,
        }

    def get_usd_cny(
        self,
        manual_fallback: float = DEFAULT_MANUAL_FALLBACK,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Return a live/cached first-party rate, or the manual fallback.

        Every result contains ``rate``, ``source``, ``reference_date``,
        ``fetched_at`` and ``status``.  A stale CSFloat or ECB value is
        preferred over a manual fallback after both upstream providers fail.
        """
        fallback = self._valid_rate(manual_fallback)
        if fallback is None:
            fallback = self.DEFAULT_MANUAL_FALLBACK
        now = self._utc_now()
        cache = self._load_cache()
        cached_source = cache.get("source")
        cached_rate = (
            self._valid_rate(cache.get("rate"))
            if cached_source in {"CSFloat", "ECB"}
            else None
        )
        cached_at = self._parse_datetime(cache.get("fetched_at"))

        if (
            not force_refresh
            and cached_rate is not None
            and cached_at is not None
            and now - cached_at <= self.CACHE_TTL
        ):
            return self._result_from_cache(cache, cached_rate, "fresh_cache")

        failure_at = self._parse_datetime(cache.get("failure_fetched_at"))
        if (
            not force_refresh
            and failure_at is not None
            and now - failure_at < self.FAILURE_COOLDOWN
        ):
            if cached_rate is not None:
                return self._result_from_cache(
                    cache, cached_rate, "stale_cache_failure_cooldown"
                )
            return self._manual_result(
                fallback, now, "manual_fallback_failure_cooldown"
            )

        fetched_at = self._format_datetime(now)
        try:
            parsed = self._fetch_csfloat()
        except (requests.RequestException, ValueError, AttributeError, TypeError):
            parsed = None
        if parsed is not None:
            return self._save_live_result(parsed, fetched_at)

        try:
            parsed = self._fetch_ecb()
        except (requests.RequestException, ValueError, AttributeError, TypeError):
            parsed = None
        if parsed is not None:
            return self._save_live_result(parsed, fetched_at)

        cache["failure_fetched_at"] = fetched_at
        self._write_cache(cache)
        if cached_rate is not None:
            return self._result_from_cache(
                cache, cached_rate, "stale_cache_network_error"
            )
        return self._manual_result(fallback, now, "manual_fallback_network_error")

    # A short alias makes the client convenient for callers that already know
    # the object represents USD/CNY.
    get_rate = get_usd_cny

    def _fetch_csfloat(self) -> dict[str, Any]:
        response = self.csfloat_session.get(
            self.CSFLOAT_URL, timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise ValueError("invalid CSFloat exchange-rate response")
        rate = self._valid_rate(payload["data"].get("cny"))
        if rate is None:
            raise ValueError("CSFloat response does not contain a valid CNY rate")
        return {
            "rate": rate,
            "source": "CSFloat",
            # The endpoint currently returns a currency map without a separate
            # market/reference date; fetched_at records its observed time.
            "reference_date": None,
        }

    def _fetch_ecb(self) -> dict[str, Any]:
        response = self.ecb_session.get(self.ECB_URL, timeout=self.timeout)
        response.raise_for_status()
        xml_data = getattr(response, "content", None)
        if not xml_data:
            xml_data = response.text
        parsed = self.parse_ecb_xml(xml_data)
        return {
            "rate": parsed["rate"],
            "source": "ECB",
            "reference_date": parsed["reference_date"],
            "usd_per_eur": parsed["usd_per_eur"],
            "cny_per_eur": parsed["cny_per_eur"],
        }

    def _save_live_result(
        self, parsed: dict[str, Any], fetched_at: str
    ) -> dict[str, Any]:
        new_cache = {
            "rate": parsed["rate"],
            "source": parsed["source"],
            "reference_date": parsed.get("reference_date"),
            "fetched_at": fetched_at,
        }
        for key in ("usd_per_eur", "cny_per_eur"):
            if key in parsed:
                new_cache[key] = parsed[key]
        self._write_cache(new_cache)
        return {
            "rate": parsed["rate"],
            "source": parsed["source"],
            "reference_date": parsed.get("reference_date"),
            "fetched_at": fetched_at,
            "status": "live",
        }

    def _utc_now(self) -> datetime:
        value = self._now_func()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _valid_rate(value: Any) -> float | None:
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return None
        return rate if math.isfinite(rate) and rate > 0 else None

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _load_cache(self) -> dict[str, Any]:
        try:
            with self.cache_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_cache(self, payload: dict[str, Any]) -> None:
        """Atomically replace the JSON cache; cache failures are non-fatal."""
        temp_path: Path | None = None
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.cache_path.parent,
                prefix=f".{self.cache_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, self.cache_path)
        except OSError:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _result_from_cache(
        cache: dict[str, Any], rate: float, status: str
    ) -> dict[str, Any]:
        return {
            "rate": rate,
            "source": cache.get("source"),
            "reference_date": cache.get("reference_date"),
            "fetched_at": cache.get("fetched_at"),
            "status": status,
        }

    @classmethod
    def _manual_result(
        cls, rate: float, now: datetime, status: str
    ) -> dict[str, Any]:
        return {
            "rate": rate,
            "source": "manual",
            "reference_date": None,
            "fetched_at": cls._format_datetime(now),
            "status": status,
        }
