import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from modules.market_table_model import MarketTableModel


class MarketTableModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_model(self):
        return MarketTableModel(
            thumbnail_provider=lambda _entry: QPixmap(),
            updated_text=lambda entry: entry.get("updated", "never"),
        )

    def test_filter_keeps_entry_identity_for_actions(self):
        first = {"name": "AK", "market_hash_name": "AK-47 | Redline", "phase": "-"}
        second = {"name": "M4", "market_hash_name": "M4A1-S | Printstream", "phase": "-"}
        model = self.make_model()
        model.set_entries([first, second])
        model.set_filter("printstream")

        self.assertEqual(model.rowCount(), 1)
        self.assertIs(model.entry_at(model.index(0, 0)), second)

    def test_sorts_numeric_quote_columns(self):
        lower = {"name": "A", "market_hash_name": "A", "phase": "-", "csqaq_price": 10}
        higher = {"name": "B", "market_hash_name": "B", "phase": "-", "csqaq_price": 20}
        model = self.make_model()
        model.set_entries([higher, lower])
        model.sort(2, Qt.AscendingOrder)

        self.assertIs(model.entry_at(model.index(0, 0)), lower)

    def test_does_not_display_a_quote_cached_for_a_different_name(self):
        model = self.make_model()
        model.set_entries([{
            "name": "A",
            "market_hash_name": "A",
            "phase": "-",
            "csfloat_query_mhn": "old-name",
            "csfloat_min_sell_cny": 99.99,
        }])

        displayed = model.data(model.index(0, 3), Qt.DisplayRole)
        self.assertIn("名称已变更", displayed)
        self.assertNotIn("99.99", displayed)


if __name__ == "__main__":
    unittest.main()
