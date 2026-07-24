import os
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox

from main import CS2ManagerApp


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
        self.assertIn("_queue_navigation", source)
        self.assertIn("_step_dashboard_category", source)
        self.assertIn("QTimer.singleShot", inspect.getsource(CS2ManagerApp._queue_navigation))

    def test_dashboard_category_step_wraps_through_the_status_filter(self):
        category_box = QComboBox()
        category_box.addItems(["all", "rented", "cooldown"])
        fake = SimpleNamespace(status_filter_box=category_box)

        self.assertTrue(CS2ManagerApp._step_dashboard_category(fake, 1))
        self.assertEqual(category_box.currentText(), "rented")
        self.assertTrue(CS2ManagerApp._step_dashboard_category(fake, -1))
        self.assertEqual(category_box.currentText(), "all")
        self.assertTrue(CS2ManagerApp._step_dashboard_category(fake, -1))
        self.assertEqual(category_box.currentText(), "cooldown")

    def test_background_refresh_defers_rendering_hidden_tables(self):
        market_source = inspect.getsource(CS2ManagerApp._on_market_refresh_finished)
        switch_source = inspect.getsource(CS2ManagerApp.switch_page)
        self.assertIn("_market_table_render_pending", market_source)
        self.assertIn("_market_table_render_pending", switch_source)

    def test_personal_csfloat_buy_workspace_and_requests_are_removed(self):
        source = inspect.getsource(CS2ManagerApp.init_ui)
        self.assertNotIn("CSF 求购", source)
        self.assertFalse(hasattr(CS2ManagerApp, "init_csfloat_buy_orders_tab"))
        self.assertFalse(hasattr(CS2ManagerApp, "_refresh_csfloat_buy_orders"))

    def test_all_workspace_refresh_entries_use_the_global_dispatcher(self):
        switch_source = inspect.getsource(CS2ManagerApp.switch_page)
        key_source = inspect.getsource(CS2ManagerApp.eventFilter)
        market_source = inspect.getsource(CS2ManagerApp.init_market_tab)
        self.assertNotIn("_refresh_csfloat_buy_orders", switch_source)
        self.assertIn("_request_global_sync_now", key_source)
        self.assertIn("_request_global_sync_now", market_source)

    def test_global_sync_runs_market_categories_then_dashboard(self):
        source = inspect.getsource(CS2ManagerApp._run_rolling_market_refresh)
        market_position = source.index("_collect_market_refresh_batches")
        dashboard_position = source.index("_complete_global_sync_cycle")
        self.assertLess(market_position, dashboard_position)
        self.assertNotIn("_refresh_cycle_csfloat_buy_orders", source)

        cleanup_source = inspect.getsource(CS2ManagerApp._cleanup_market_refresh_thread)
        self.assertIn("QTimer.singleShot(0, self._run_rolling_market_refresh)", cleanup_source)

    def test_market_batches_preserve_category_order_and_deduplicate(self):
        first = {"market_hash_name": "item-a", "phase": "-"}
        duplicate = {"market_hash_name": "item-a", "phase": "-"}
        second = {"market_hash_name": "item-b", "phase": "P2"}
        fake = SimpleNamespace(
            _market_categories={
                "first": {"name": "分类一", "items": [first]},
                "second": {"name": "分类二", "items": [duplicate, second]},
            },
            _apply_schema_mapping=lambda _entry: None,
            _market_watch_identity=lambda name, phase: f"{name}|{phase}",
        )

        batches = CS2ManagerApp._collect_market_refresh_batches(fake)

        self.assertEqual(batches, [
            ("分类一", ["item-a|-"]),
            ("分类二", ["item-b|P2"]),
        ])

    def test_cycle_completion_updates_dashboard_then_waits_for_next_round(self):
        source = inspect.getsource(CS2ManagerApp._complete_global_sync_cycle)
        self.assertIn("self.load_data()", source)
        self.assertIn("_next_global_sync_delay", source)
        self.assertIn("大盘 → 总览", source)

    def test_inventory_assets_are_mirrored_into_rentals_market_category(self):
        fake = SimpleNamespace(
            _market_refresh_thread=None,
            _market_categories={"rentals": {"name": "出租品", "items": []}},
            _active_market_category_id="rentals",
            _market_tracked_items=[],
            db=SimpleNamespace(get_all_items=lambda: [{
                "name": "运动手套（★） | 赤色迫风 (久经沙场)",
                "market_hash_name": "Sport Gloves | Slingshot (Field-Tested)",
                "phase": "-",
            }]),
            _ensure_market_categories=lambda: None,
            _build_market_hash_name=lambda item: item["market_hash_name"],
            _apply_schema_mapping=lambda _entry: None,
            _market_watch_identity=lambda name, phase: f"{name}|{phase}",
        )

        changed = CS2ManagerApp._sync_inventory_market_watchlist(fake)

        self.assertTrue(changed)
        self.assertEqual(len(fake._market_tracked_items), 1)
        self.assertEqual(
            fake._market_tracked_items[0]["market_hash_name"],
            "Sport Gloves | Slingshot (Field-Tested)",
        )

    def test_next_cycle_delay_uses_remaining_complete_rounds(self):
        now = 1_000.0
        snapshot = {"limit": 200, "remaining": 161, "reset_at": now + 1_920}
        self.assertEqual(
            CS2ManagerApp._next_global_sync_delay(67, snapshot, now=now),
            640,
        )

        insufficient = {"limit": 200, "remaining": 70, "reset_at": now + 1_920}
        self.assertEqual(
            CS2ManagerApp._next_global_sync_delay(67, insufficient, now=now),
            1_921,
        )

        self.assertEqual(
            CS2ManagerApp._next_global_sync_delay(67, {}, now=now),
            600,
        )

    def test_settings_is_the_third_page_and_does_not_request_sync(self):
        fake = SimpleNamespace(
            tabs=MagicMock(),
            navigation_buttons=[],
            _request_global_sync_now=MagicMock(),
        )

        CS2ManagerApp.switch_page(fake, 2, request_sync=False)

        fake.tabs.setCurrentIndex.assert_called_once_with(2)
        fake._request_global_sync_now.assert_not_called()


if __name__ == "__main__":
    unittest.main()
