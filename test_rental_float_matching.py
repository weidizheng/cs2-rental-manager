import unittest

from modules.rental_matching import (
    MIN_FLOAT_MATCH_DECIMAL_PLACES,
    float_match_precision,
    float_precision,
    rental_float_matches,
)


class RentalFloatMatchingTests(unittest.TestCase):
    def test_reports_source_precision_for_candidate_ranking(self):
        values = ["0.02071962", "0.0207196157425642", "0.020719615"]
        self.assertEqual([float_precision(value) for value in values], [8, 16, 9])
        self.assertEqual(
            max(values, key=float_precision),
            "0.0207196157425642",
        )

    def test_matches_a_more_precise_value_by_rounding(self):
        short = "0.02071962"
        long = "0.0207196157425642"
        self.assertEqual(float_match_precision(short, long), 8)
        self.assertEqual(float_match_precision(long, short), 8)
        self.assertTrue(rental_float_matches(short, long))

    def test_matches_a_more_precise_value_by_truncation(self):
        short = "0.34055593"
        long = "0.34055593609809875"
        self.assertEqual(float_match_precision(short, long), 8)
        self.assertEqual(float_match_precision(long, short), 8)

    def test_rejects_values_that_disagree_at_the_shorter_precision(self):
        self.assertIsNone(
            float_match_precision("0.34055593", "0.34055493609809875")
        )
        self.assertFalse(
            rental_float_matches("0.34055593", "0.34055493609809875")
        )

    def test_asset_import_matching_rejects_low_precision_values(self):
        self.assertEqual(MIN_FLOAT_MATCH_DECIMAL_PLACES, 6)
        self.assertIsNone(float_match_precision("0.35", "0.3500000001"))
        self.assertIsNone(float_match_precision("0.35", "0.35"))

    def test_minimum_precision_can_be_explicitly_overridden(self):
        self.assertEqual(
            float_match_precision("0.35", "0.3500000001", min_decimal_places=2),
            2,
        )

    def test_invalid_and_non_finite_values_do_not_match(self):
        for value in (None, "", "not-a-number", "NaN", "Infinity"):
            with self.subTest(value=value):
                self.assertEqual(float_precision(value), -1)
                self.assertIsNone(float_match_precision(value, "0.123456"))


if __name__ == "__main__":
    unittest.main()
