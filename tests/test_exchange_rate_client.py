"""Offline tests for the ECB-backed USD/CNY exchange-rate client."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import requests

from modules.exchange_rate_client import ExchangeRateClient


ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube><Cube time="2026-07-17">
    <Cube currency="USD" rate="1.1500"/>
    <Cube currency="CNY" rate="8.2800"/>
  </Cube></Cube>
</gesmes:Envelope>
"""


class FakeResponse:
    def __init__(self, content=ECB_XML, status_code=200, payload=None):
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self.payload is None:
            raise ValueError("not JSON")
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        if self.error:
            raise self.error
        return self.response


class ExchangeRateClientTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cache_path = Path(self.temp_dir.name) / "exchange_rate_cache.json"
        self.now = datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)

    def make_client(self, session=None, csfloat_session=None, ecb_session=None):
        return ExchangeRateClient(
            cache_path=self.cache_path,
            timeout=6,
            session=session,
            csfloat_session=csfloat_session,
            ecb_session=ecb_session,
            now_func=lambda: self.now,
        )

    def write_cache(self, **overrides):
        payload = {
            "rate": 7.10,
            "source": "ECB",
            "reference_date": "2026-07-16",
            "fetched_at": (self.now - timedelta(hours=1)).isoformat(),
        }
        payload.update(overrides)
        self.cache_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_default_cache_path_honors_private_data_environment(self):
        configured_dir = Path(self.temp_dir.name) / "portable-private-data"
        with patch.dict(
            "os.environ", {"CS2_RENTAL_DATA_DIR": str(configured_dir)}
        ):
            client = ExchangeRateClient(now_func=lambda: self.now)

        self.assertEqual(
            client.cache_path,
            configured_dir / "exchange_rate_cache.json",
        )

    def test_parse_derives_cny_per_usd_from_eur_cross_rates(self):
        result = ExchangeRateClient.parse_ecb_xml(ECB_XML)

        self.assertAlmostEqual(result["rate"], 7.20)
        self.assertEqual(result["reference_date"], "2026-07-17")
        self.assertEqual(result["usd_per_eur"], 1.15)
        self.assertEqual(result["cny_per_eur"], 8.28)

    def test_fresh_cache_does_not_make_network_request(self):
        self.write_cache()
        session = FakeSession(error=AssertionError("network must not be called"))

        result = self.make_client(session).get_usd_cny(7.30)

        self.assertEqual(result["rate"], 7.10)
        self.assertEqual(result["source"], "ECB")
        self.assertEqual(result["status"], "fresh_cache")
        self.assertEqual(session.calls, [])

    def test_expired_cache_prefers_csfloat_and_persists_source(self):
        self.write_cache(
            fetched_at=(self.now - timedelta(hours=13)).isoformat()
        )
        csfloat = FakeSession(FakeResponse(payload={"data": {"cny": 6.776}}))
        ecb = FakeSession(error=AssertionError("ECB must not be called"))

        result = self.make_client(
            csfloat_session=csfloat, ecb_session=ecb
        ).get_usd_cny(7.30)

        self.assertAlmostEqual(result["rate"], 6.776)
        self.assertEqual(result["source"], "CSFloat")
        self.assertEqual(result["status"], "live")
        self.assertIsNone(result["reference_date"])
        self.assertEqual(csfloat.calls[0][1], 6)
        self.assertEqual(ecb.calls, [])
        saved = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertAlmostEqual(saved["rate"], 6.776)
        self.assertEqual(saved["source"], "CSFloat")
        self.assertNotIn("failure_fetched_at", saved)

    def test_csfloat_failure_falls_back_to_ecb(self):
        csfloat = FakeSession(error=requests.Timeout("CSFloat offline"))
        ecb = FakeSession(FakeResponse())

        result = self.make_client(
            csfloat_session=csfloat, ecb_session=ecb
        ).get_usd_cny(7.30)

        self.assertAlmostEqual(result["rate"], 7.20)
        self.assertEqual(result["source"], "ECB")
        self.assertEqual(result["reference_date"], "2026-07-17")
        self.assertEqual(result["status"], "live")
        self.assertEqual(len(csfloat.calls), 1)
        self.assertEqual(len(ecb.calls), 1)
        saved = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], "ECB")

    def test_both_providers_failing_uses_expired_authoritative_cache(self):
        old_fetched_at = (self.now - timedelta(days=2)).isoformat()
        self.write_cache(
            rate=6.80,
            source="CSFloat",
            reference_date=None,
            fetched_at=old_fetched_at,
        )
        csfloat = FakeSession(error=requests.Timeout("CSFloat offline"))
        ecb = FakeSession(error=requests.Timeout("ECB offline"))

        result = self.make_client(
            csfloat_session=csfloat, ecb_session=ecb
        ).get_usd_cny(7.30)

        self.assertEqual(result["rate"], 6.80)
        self.assertEqual(result["source"], "CSFloat")
        self.assertEqual(result["fetched_at"], old_fetched_at)
        self.assertEqual(result["status"], "stale_cache_network_error")
        saved = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertIn("failure_fetched_at", saved)

    def test_first_failure_cools_down_and_uses_manual_fallback(self):
        csfloat = FakeSession(error=requests.ConnectionError("CSFloat offline"))
        ecb = FakeSession(error=requests.ConnectionError("ECB offline"))
        first = self.make_client(
            csfloat_session=csfloat, ecb_session=ecb
        ).get_usd_cny(7.36)

        self.assertEqual(first["rate"], 7.36)
        self.assertEqual(first["source"], "manual")
        self.assertEqual(first["status"], "manual_fallback_network_error")
        saved = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.assertIn("failure_fetched_at", saved)

        available_csfloat = FakeSession(
            FakeResponse(payload={"data": {"cny": 6.776}})
        )
        available_ecb = FakeSession(FakeResponse())
        second = self.make_client(
            csfloat_session=available_csfloat,
            ecb_session=available_ecb,
        ).get_usd_cny(7.36)
        self.assertEqual(second["rate"], 7.36)
        self.assertEqual(second["status"], "manual_fallback_failure_cooldown")
        self.assertEqual(available_csfloat.calls, [])
        self.assertEqual(available_ecb.calls, [])


if __name__ == "__main__":
    unittest.main()
