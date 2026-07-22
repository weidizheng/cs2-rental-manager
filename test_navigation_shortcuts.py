import os
import inspect
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from main import (
    CS2ManagerApp,
    _csfloat_buy_order_analysis,
)
from modules.csfloat_buy_analysis import (
    csfloat_buy_increment_cents,
    csfloat_next_legal_buy_price,
)


class NavigationShortcutFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tables_keep_standard_arrow_key_behavior(self):
        source = inspect.getsource(CS2ManagerApp.eventFilter)
        self.assertNotIn("Qt.Key_Up", source)
        self.assertNotIn("Qt.Key_Down", source)
        self.assertNotIn("Qt.Key_Left", source)
        self.assertNotIn("Qt.Key_Right", source)

    def test_accessible_shortcuts_use_modifiers(self):
        source = inspect.getsource(CS2ManagerApp._install_shortcuts)
        for sequence in ("Alt+1", "Alt+2", "Alt+3", "Alt+Left", "Alt+Right", "Ctrl+F"):
            self.assertIn(sequence, source)

    def test_wasd_switches_workspaces_and_categories_without_hijacking_text_input(self):
        source = inspect.getsource(CS2ManagerApp.eventFilter)
        for key in ("Qt.Key_W", "Qt.Key_S", "Qt.Key_A", "Qt.Key_D"):
            self.assertIn(key, source)
        for widget in ("QLineEdit", "QPlainTextEdit", "QComboBox"):
            self.assertIn(widget, source)

    def test_buy_page_uses_one_sync_control_and_clickable_item_column(self):
        source = inspect.getsource(CS2ManagerApp.init_csfloat_buy_orders_tab)
        self.assertNotIn("csfloat_buy_refresh_btn", source)
        self.assertIn("setColumnCount(7)", source)
        self.assertIn("_open_csfloat_buy_item_from_cell", source)
        self.assertIn("打开 CSFloat 求购", source)

    def test_csfloat_buy_analysis_reports_tied_top_without_claiming_ownership(self):
        result = _csfloat_buy_order_analysis(
            10000,
            10000,
            [
                {"price": 10100, "sold_at": "2026-07-20T00:00:00Z"},
                {"price": 10400, "sold_at": "2026-07-19T00:00:00Z"},
            ],
        )
        self.assertTrue(result["at_top"])
        self.assertEqual(result["price_status"], "最高价位")
        self.assertEqual(result["within_2_percent"], 1)
        self.assertEqual(result["within_5_percent"], 2)
        self.assertEqual(result["purchase_signal"], "较强")

    def test_csfloat_buy_analysis_targets_next_official_price_tier(self):
        result = _csfloat_buy_order_analysis(9500, 10000, [])
        self.assertFalse(result["at_top"])
        self.assertEqual(result["gap_cents"], 500)
        self.assertEqual(result["target_price_cents"], 10100)

    def test_csfloat_official_buy_order_increment_boundaries(self):
        expected = {
            499: 1,
            500: 5,
            999: 5,
            1000: 10,
            9999: 10,
            10000: 100,
            49999: 100,
            50000: 500,
            99999: 500,
            100000: 1000,
        }
        for price, increment in expected.items():
            with self.subTest(price=price):
                self.assertEqual(csfloat_buy_increment_cents(price), increment)

    def test_csfloat_next_legal_price_handles_tier_crossings(self):
        expected = {
            499: 500,
            500: 505,
            999: 1000,
            9999: 10000,
            10000: 10100,
            49999: 50000,
            50000: 50500,
            99999: 100000,
        }
        for price, next_price in expected.items():
            with self.subTest(price=price):
                self.assertEqual(csfloat_next_legal_buy_price(price), next_price)


if __name__ == "__main__":
    unittest.main()
