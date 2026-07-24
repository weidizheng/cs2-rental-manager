import unittest
from datetime import datetime, timedelta, timezone

from modules.platform_time import (
    platform_order_time_rule,
    parse_platform_datetime_utc,
)


class PlatformOrderTimeTests(unittest.TestCase):
    def setUp(self):
        self.edt = timezone(timedelta(hours=-4))

    def test_c5_uses_the_browser_local_clock(self):
        actual = parse_platform_datetime_utc(
            "2026-07-14 22:24:23", "C5GAME", local_timezone=self.edt
        )
        self.assertEqual(actual, datetime(2026, 7, 15, 2, 24, 23))

    def test_eco_and_igxe_use_beijing_page_time(self):
        expected = datetime(2026, 7, 21, 5, 19, 45)
        self.assertEqual(
            parse_platform_datetime_utc(
                "2026-07-21 13:19:45", "ECOSteam", local_timezone=self.edt
            ),
            expected,
        )
        self.assertEqual(
            parse_platform_datetime_utc(
                "2026-07-21 13:19:45", "IGXE", local_timezone=self.edt
            ),
            expected,
        )

    def test_rules_are_explicit_for_each_supported_platform(self):
        self.assertEqual(platform_order_time_rule("C5GAME"), "浏览器本地时间")
        self.assertIn("北京时间", platform_order_time_rule("ECOSteam"))
        self.assertIn("北京时间", platform_order_time_rule("IGXE"))


if __name__ == "__main__":
    unittest.main()
