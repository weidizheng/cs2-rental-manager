import json
import os
import sys
import logging
import time
import re
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from datetime import datetime, timedelta
from urllib.parse import quote
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView,
    QTabWidget, QFormLayout, QGroupBox, QMessageBox,
    QAbstractItemView, QDialog, QSplitter, QScrollArea,
    QCheckBox, QFrame,
    QMenu, QInputDialog,
)
from PySide6.QtCore import Qt, QTimer, QThread, QUrl, QTime
from PySide6.QtGui import QFont, QColor, QPixmap, QIcon, QPainter, QLinearGradient, QBrush, QDesktopServices

from modules.db_manager import DBManager
from modules.workers import ApiWorker, MarketRefreshWorker
from modules.logger import logger
from modules.image_cache import ImageCache, MarketCache
from modules.cs2_item_schema import CS2ItemSchema
from modules.rental_order_parsers import parse_rental_clipboard


ORDER_PAGE_URLS = {
    "c5": ("C5", "https://www.c5game.com/user/rent?actag=2"),
    "eco": ("ECO", "https://www.ecosteam.cn/html/person/rentrecordlist.html"),
    "igxe": ("IGXE", "https://www.igxe.cn/lease/seller-order-list"),
}


class ItemEditDialog(QDialog):
    """可视化修改与新增饰品弹窗"""

    def __init__(self, item_data=None, parent=None):
        super().__init__(parent)
        self.item_data = item_data or {}
        self.setWindowTitle("修改饰品数据" if item_data else "手动新增饰品")
        self.resize(400, 580)

        layout = QFormLayout(self)
        self.name_in = QLineEdit(self.item_data.get("name", ""))
        self.name_in.textChanged.connect(self._auto_build_mhn)

        self.mhn_in = QLineEdit(self.item_data.get("market_hash_name", ""))
        self.mhn_in.setPlaceholderText("英文 market_hash_name，如: ★ Bayonet | Doppler (Factory New)")

        self.phase_in = QComboBox()
        self.phase_in.addItems(["-", "P1", "P2", "P3", "P4", "Ruby", "Sapphire", "Emerald"])
        self.phase_in.setCurrentText(self.item_data.get("phase", "-"))
        self.phase_in.currentTextChanged.connect(self._auto_build_mhn)

        self.pattern_in = QLineEdit(str(self.item_data.get("pattern", "-")))
        self.float_in = QLineEdit(str(self.item_data.get("float_val", "0.000")))
        self.cost_in = QLineEdit(str(self.item_data.get("cost", "0.00")))

        self.platform_box = QComboBox()
        self.platform_box.addItems(["BUFF", "C5GAME", "ECOSteam", "悠悠有品", "IGXE"])
        self.platform_box.setCurrentText(self.item_data.get("platform", "C5GAME"))

        self.status_box = QComboBox()
        self.status_box.addItems(["在库", "已出租", "CD冷却"])
        self.status_box.setCurrentText(self.item_data.get("status", "在库"))

        self.rent_in = QLineEdit(str(self.item_data.get("rent", "0.00")))
        self.days_in = QLineEdit(str(self.item_data.get("days", "0")))
        self.expire_in = QLineEdit(str(self.item_data.get("expire_hours", "999.0")))
        self.income_in = QLineEdit(str(self.item_data.get("income", "0.00")))

        layout.addRow("饰品完整名称:", self.name_in)
        layout.addRow("英文 MarketHashName:", self.mhn_in)
        layout.addRow("多普勒相位:", self.phase_in)
        layout.addRow("图案模板 (Pattern):", self.pattern_in)
        layout.addRow("磨损度 (Float):", self.float_in)
        layout.addRow("买入成本 (元):", self.cost_in)
        layout.addRow("所属平台:", self.platform_box)
        layout.addRow("当前状态:", self.status_box)
        layout.addRow("日租金 (元/天):", self.rent_in)
        layout.addRow("累计出租天数:", self.days_in)
        layout.addRow("到期剩余小时数:", self.expire_in)
        layout.addRow("累计已收收益 (元):", self.income_in)

        save_btn = QPushButton("💾 保存并自动同步至 JSON")
        save_btn.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; padding: 8px; border-radius: 6px;")
        save_btn.clicked.connect(self.accept)
        layout.addRow(save_btn)

    def _auto_build_mhn(self):
        """根据中文名称和相位自动构建英文 market_hash_name"""
        name = self.name_in.text().strip()
        phase = self.phase_in.currentText()
        if not name or self.mhn_in.text().strip():
            return

        mapped_item = CS2ItemSchema.lookup(name)
        if mapped_item:
            self.mhn_in.setText(mapped_item["market_hash_name"])
            return

        # 如果用户已手动输入 mhn，不再自动覆盖
        if self.item_data.get("market_hash_name") and self.mhn_in.text() == self.item_data.get("market_hash_name"):
            pass

        exterior_map = {
            "崭新出厂": "Factory New",
            "略有磨损": "Minimal Wear",
            "久经沙场": "Field-Tested",
            "破损不堪": "Well-Worn",
            "战痕累累": "Battle-Scarred",
        }
        weapon_map = {
            "折叠刀": "Bayonet",
            "M9 刺刀": "M9 Bayonet",
            "刺刀": "Bayonet",
            "爪子刀": "Karambit",
            "蝴蝶刀": "Butterfly Knife",
            "猎杀者匕首": "Huntsman Knife",
            "暗影双匕": "Shadow Daggers",
            "弯刀": "Falchion Knife",
            "鲍伊猎刀": "Bowie Knife",
            "短剑": "Stiletto Knife",
            "熊刀": "Ursus Knife",
            "锯齿爪刀": "Navaja Knife",
            "海豹短刀": "Classic Knife",
            "骷髅匕首": "Skeleton Knife",
            "求生匕首": "Survival Knife",
            "流浪者匕首": "Nomad Knife",
            "系绳匕首": "Paracord Knife",
            "专业手套": "Specialist Gloves",
            "运动手套": "Sport Gloves",
            "驾驶手套": "Driver Gloves",
            "摩托手套": "Moto Gloves",
            "血猎手套": "Bloodhound Gloves",
            "手部束带": "Hand Wraps",
            "九头蛇手套": "Hydra Gloves",
            "狂牙手套": "Broken Fang Gloves",
        }
        skin_map = {
            "多普勒": "Doppler",
            "狂澜": "Crimson Web",
            "紫罗兰珠绣": "Vice",
            "繁花似锦": "Boom!",
        }

        import re
        match = re.match(r"(.*?)（★）\s*\|\s*(.*?)\s*\((.*?)\)", name)
        if not match:
            match = re.match(r"★\s*(.*?)\s*\|\s*(.*?)\s*\((.*?)\)", name)

        if match:
            weapon_zh = match.group(1).strip()
            skin_zh = match.group(2).strip()
            exterior_zh = match.group(3).strip()

            weapon_en = weapon_map.get(weapon_zh, weapon_zh)
            skin_en = skin_map.get(skin_zh, skin_zh)
            exterior_en = exterior_map.get(exterior_zh, exterior_zh)

            mhn = f"★ {weapon_en} | {skin_en} ({exterior_en})"

            if skin_en == "Doppler" and phase and phase != "-":
                phase_map = {
                    "P1": "Phase 1", "P2": "Phase 2", "P3": "Phase 3", "P4": "Phase 4",
                    "Ruby": "Ruby", "Sapphire": "Sapphire", "Emerald": "Emerald",
                }
                phase_en = phase_map.get(phase, phase)
                mhn = f"★ {weapon_en} | {skin_en} ({exterior_en}) - {phase_en}"

            self.mhn_in.setText(mhn)

    def get_data(self):
        return {
            "name": self.name_in.text().strip() or "未命名饰品",
            "market_hash_name": self.mhn_in.text().strip(),
            "phase": self.phase_in.currentText(),
            "pattern": self.pattern_in.text().strip() or "-",
            "float_val": self.float_in.text().strip() or "0.000",
            "cost": float(self.cost_in.text() or 0.0),
            "platform": self.platform_box.currentText(),
            "status": self.status_box.currentText(),
            "rent": float(self.rent_in.text() or 0.0),
            "days": int(self.days_in.text() or 0),
            "expire_hours": float(self.expire_in.text() or 999.0),
            "income": float(self.income_in.text() or 0.0),
            # Notes are no longer edited in the UI, but legacy data remains
            # intact so an ordinary asset edit never discards user records.
            "note": self.item_data.get("note", "")
        }


def _rental_float_key(value) -> str:
    try:
        return f"{float(str(value).strip()):.12f}"
    except (TypeError, ValueError):
        return ""


def _rental_float_matches(asset_value, order_value) -> bool:
    """Match a full platform float to an asset float that may have been truncated."""
    try:
        asset_text = str(asset_value).strip()
        asset_float = Decimal(asset_text)
        order_float = Decimal(str(order_value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return False

    if asset_float == order_float:
        return True
    # Early versions of the app stored user-entered floats such as 0.02082699
    # while platform order pages expose the complete value.  Use half of the
    # last entered decimal place as the maximum matching tolerance.
    decimal_places = len(asset_text.partition(".")[2])
    if decimal_places <= 0:
        return False
    tolerance = Decimal("0.5") * (Decimal(10) ** -decimal_places)
    return abs(asset_float - order_float) < tolerance


def _parse_rental_datetime(value) -> datetime:
    try:
        normalized = " ".join(str(value).split())
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.min


def _money_text(value) -> str:
    """Format money with financial half-up rounding instead of float bankers rounding."""
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0.00")
    return f"¥ {amount:.2f}"


class RentalHistoryDialog(QDialog):
    """Shows all manually imported orders for one physical float value."""

    def __init__(self, item_name, float_value, orders, parent=None):
        super().__init__(parent)
        self.orders = sorted(orders, key=lambda order: _parse_rental_datetime(order.get("start_time")))
        self.setWindowTitle("出租订单历史")
        self.resize(920, 430)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{item_name}  ·  磨损 {float_value}"))
        layout.addWidget(QLabel("双击订单查看完整日期、收入和同步来源。"))

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "平台", "状态", "出租时间", "租赁到期", "租期", "日租（原价）", "订单金额", "净收入",
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for index, order in enumerate(self.orders):
            self.table.insertRow(index)
            income = float(order.get("income", 0.0) or 0.0)
            rental_days = float(order.get("rental_days", 0.0) or 0.0)
            daily = float(order.get("daily_rent", 0.0) or 0.0)
            if daily <= 0 and rental_days > 0:
                daily = income / rental_days
            values = [
                order.get("platform", ""), order.get("status", ""),
                order.get("start_time", ""), order.get("return_time", ""),
                f"{rental_days:g} 天" if rental_days > 0 else "—",
                _money_text(daily) if daily > 0 else "—",
                _money_text(income), _money_text(order.get("net_income", income) or 0.0),
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
        self.table.doubleClicked.connect(self._show_order_detail)
        layout.addWidget(self.table)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def _show_order_detail(self, index):
        if not index.isValid() or index.row() >= len(self.orders):
            return
        order = self.orders[index.row()]
        QMessageBox.information(
            self,
            "订单详情",
            "\n".join([
                f"平台：{order.get('platform', '')}",
                f"订单号：{order.get('order_no', '')}",
                f"状态：{order.get('status', '')}",
                f"出租：{order.get('start_time', '')}",
                f"租赁到期：{order.get('return_time', '')}",
                f"租期：{float(order.get('rental_days', 0.0) or 0.0):g} 天",
                f"日租（原价）：{_money_text(order.get('daily_rent', 0.0) or 0.0)}",
                f"订单金额：{_money_text(order.get('income', 0.0) or 0.0)}",
                f"净收入：{_money_text(order.get('net_income', order.get('income', 0.0)) or 0.0)}",
            ]),
        )


class RentalImportPreviewDialog(QDialog):
    """Show parsed clipboard orders before the user allows a database write."""

    def __init__(self, platform, orders, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.orders = orders
        self.setWindowTitle("确认导入出租订单")
        self.resize(1240, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"已识别为 {platform}，共解析到 {len(orders)} 条订单。请核对后再确认写入本地记录。"
        ))

        self.table = QTableWidget()
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "平台", "订单号", "饰品", "磨损", "出租时间", "归还时间",
            "租期", "日租（原价）", "订单金额", "状态",
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("alternate-background-color: #1e1e2e;")
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for column in (0, 1, 3, 4, 5, 6, 7, 8, 9):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

        for row, order in enumerate(orders):
            self.table.insertRow(row)
            income = float(order.get("income", 0.0) or 0.0)
            daily_rent = float(order.get("daily_rent", 0.0) or 0.0)
            rental_days = float(order.get("rental_days", 0.0) or 0.0)
            values = [
                platform, order.get("order_no", ""), order.get("item_name", ""),
                order.get("float_val", ""), order.get("start_time", ""), order.get("return_time", ""),
                f"{rental_days:g} 天" if rental_days > 0 else "—",
                _money_text(daily_rent) if daily_rent > 0 else "—",
                _money_text(income), order.get("status", "") or "—",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        confirm_button = QPushButton("确认导入")
        confirm_button.setObjectName("successBtn")
        confirm_button.clicked.connect(self.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(confirm_button)
        layout.addLayout(buttons)


class CS2ManagerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DBManager()
        self.setWindowTitle("CS2 饰品出租管理终端 v3.0 (多平台聚合)")
        self.resize(1400, 800)

        # 保存活跃线程引用，防止被 GC
        self._active_threads = []

        # 市场行情刷新专用线程/Worker（单线程顺序队列）
        self._market_refresh_thread = None
        self._market_refresh_worker = None
        self._market_auto_refresh_deadline = 0.0
        self._market_auto_refresh_enabled = False
        self._dashboard_rental_rows = {}

        # 当前市场详情页选中的物品标识 (name|phase)
        self._current_market_item_key = ""
        # 市场页数据列表: [{name, phase, market_hash_name, ...}]
        self._market_tracked_items = []

        self.apply_theme()
        self.init_ui()

        self.market_countdown_timer = QTimer(self)
        self.market_countdown_timer.timeout.connect(self._update_market_auto_refresh_countdown)
        self.market_auto_refresh_timer = QTimer(self)
        self.market_auto_refresh_timer.setSingleShot(True)
        self.market_auto_refresh_timer.timeout.connect(self._run_scheduled_market_refresh)

        # Keep rental end-time displays live without reloading data or making API calls.
        self.rental_countdown_timer = QTimer(self)
        self.rental_countdown_timer.timeout.connect(self._update_dashboard_rental_countdowns)
        self.rental_countdown_timer.start(1000)

        # Relative market-update labels are local UI state and never trigger API calls.
        self.market_relative_time_timer = QTimer(self)
        self.market_relative_time_timer.timeout.connect(self._update_market_relative_times)
        self.market_relative_time_timer.start(60 * 1000)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_auto_refresh)
        self.update_timer_interval()

        self.load_data()
        # Market refresh is enabled by default for every application start.
        self._market_auto_refresh_enabled = True
        self._schedule_next_market_auto_refresh()

    def apply_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e2e; }
            QLabel { color: #cdd6f4; font-size: 13px; }
            QLabel#titleLabel { font-size: 18px; font-weight: bold; color: #89b4fa; }
            QLabel#cardTitle { color: #a6adc8; font-size: 11px; }
            QLabel#cardValue { font-size: 20px; font-weight: bold; }
            QLabel#sectionTitle { font-size: 15px; font-weight: bold; color: #89b4fa; padding: 4px 0; }
            QPushButton { background-color: #313244; color: #cdd6f4; border-radius: 6px; padding: 7px 16px; font-weight: bold; border: none; }
            QPushButton:hover { background-color: #45475a; }
            QPushButton:pressed { background-color: #585b70; }
            QPushButton#primaryBtn { background-color: #89b4fa; color: #11111b; }
            QPushButton#primaryBtn:hover { background-color: #74c7ec; }
            QPushButton#dangerBtn { background-color: #f38ba8; color: #11111b; }
            QPushButton#dangerBtn:hover { background-color: #eba0ac; }
            QPushButton#successBtn { background-color: #a6e3a1; color: #11111b; }
            QPushButton:disabled { background-color: #585b70; color: #6c7086; }
            QTableWidget { background-color: #181825; color: #cdd6f4; gridline-color: #313244; border-radius: 8px; border: 1px solid #313244; }
            QHeaderView::section { background-color: #313244; color: #cdd6f4; padding: 8px; border: none; font-weight: bold; }
            QComboBox, QLineEdit { background-color: #313244; color: #cdd6f4; padding: 6px; border: 1px solid #45475a; border-radius: 4px; }
            QComboBox:hover, QLineEdit:hover { border: 1px solid #89b4fa; }
            QComboBox::drop-down { border: none; }
            QComboBox::down-arrow { image: none; }
            QTabWidget::pane { border: 1px solid #313244; border-radius: 8px; background: #1e1e2e; }
            QTabBar::tab { background: #313244; color: #cdd6f4; padding: 10px 24px; font-weight: bold; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
            QTabBar::tab:selected { background: #89b4fa; color: #11111b; }
            QTabBar::tab:hover:!selected { background: #45475a; }
            QGroupBox { color: #89b4fa; font-weight: bold; border: 1px solid #45475a; border-radius: 8px; margin-top: 12px; padding-top: 16px; font-size: 13px; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
            QScrollArea { border: none; background: transparent; }
            QCheckBox { color: #cdd6f4; spacing: 6px; }
            QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px; border: 2px solid #585b70; }
            QCheckBox::indicator:checked { background-color: #89b4fa; border-color: #89b4fa; }
            QFrame#cardFrame { background-color: #313244; border-radius: 10px; padding: 16px; }
            QFrame#detailFrame { background-color: #181825; border-radius: 8px; border: 1px solid #313244; padding: 12px; }
        """)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.tab_dashboard = QWidget()
        self.init_dashboard_tab()
        self.tabs.addTab(self.tab_dashboard, "📊 资产与出租管理")

        self.tab_market = QWidget()
        self.init_market_tab()
        self.tabs.addTab(self.tab_market, "🔍 一览式大盘行情")

        self.tab_settings = QWidget()
        self.init_settings_tab()
        self.tabs.addTab(self.tab_settings, "⚙️ 系统与费率设置")

    # ═══════════════════════════════════════════
    # Tab 1: 资产仪表盘 (Beautified)
    # ═══════════════════════════════════════════

    def init_dashboard_tab(self):
        layout = QVBoxLayout(self.tab_dashboard)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 顶部标题栏 ──
        header = QHBoxLayout()
        title = QLabel("📈 资产总览")
        title.setObjectName("titleLabel")
        header.addWidget(title)
        header.addStretch()
        self.lbl_last_update = QLabel("最后更新: --")
        self.lbl_last_update.setStyleSheet("color: #a6adc8; font-size: 12px;")
        header.addWidget(self.lbl_last_update)
        layout.addLayout(header)

        # ── 四张统计卡片 ──
        card_layout = QHBoxLayout()
        card_layout.setSpacing(12)
        self.card_cost = self.create_card("💰 买入总资产", "¥ 0.00", "#89b4fa")
        self.card_income = self.create_card("📥 当前每日净收益", "¥ 0.00", "#a6e3a1")
        self.card_rented = self.create_card("📦 在租件数", "0 / 0 件", "#f9e2af")
        self.card_rate = self.create_card("📈 在租年化（总资产）", "0.0%", "#f38ba8")
        for c in [self.card_cost, self.card_income, self.card_rented, self.card_rate]:
            card_layout.addWidget(c)
        layout.addLayout(card_layout)

        platform_sync = QGroupBox("平台订单获取方法")
        platform_layout = QVBoxLayout(platform_sync)
        browser_layout = QHBoxLayout()
        open_c5_btn = QPushButton("🌐 打开 C5 订单页")
        open_c5_btn.setObjectName("primaryBtn")
        open_c5_btn.clicked.connect(lambda: self._open_default_browser_order_page("c5"))
        open_eco_btn = QPushButton("🌐 打开 ECO 订单页")
        open_eco_btn.setObjectName("primaryBtn")
        open_eco_btn.clicked.connect(lambda: self._open_default_browser_order_page("eco"))
        open_igxe_btn = QPushButton("🌐 打开 IGXE 订单页")
        open_igxe_btn.setObjectName("primaryBtn")
        open_igxe_btn.clicked.connect(lambda: self._open_default_browser_order_page("igxe"))

        self.clipboard_import_btn = QPushButton("📋 从剪贴板导入订单")
        self.clipboard_import_btn.setObjectName("successBtn")
        self.clipboard_import_btn.setToolTip("复制 C5、ECO 或 IGXE 订单页文本后点击，程序会自动识别平台。")
        self.clipboard_import_btn.clicked.connect(self._import_rental_orders_from_clipboard)
        browser_layout.addWidget(open_c5_btn)
        browser_layout.addWidget(open_eco_btn)
        browser_layout.addWidget(open_igxe_btn)
        browser_layout.addWidget(self.clipboard_import_btn)
        browser_layout.addStretch()
        platform_layout.addLayout(browser_layout)
        layout.addWidget(platform_sync)

        # ── 工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        add_btn = QPushButton("➕ 新增饰品")
        add_btn.clicked.connect(self.add_item)

        edit_btn = QPushButton("✏️ 修改选中")
        edit_btn.setObjectName("primaryBtn")
        edit_btn.clicked.connect(self.edit_selected_item)

        history_btn = QPushButton("📜 订单历史")
        history_btn.clicked.connect(self.show_selected_rental_history)

        del_btn = QPushButton("🗑️ 删除")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self.delete_selected_item)

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.clicked.connect(self.load_data)

        self.filter_box = QComboBox()
        self.filter_box.addItems(["全部平台", "C5GAME", "ECOSteam", "悠悠有品", "IGXE", "BUFF"])
        self.filter_box.currentTextChanged.connect(self.load_data)
        self.filter_box.setFixedWidth(120)

        toolbar.addWidget(add_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(history_btn)
        toolbar.addWidget(del_btn)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("筛选:"))
        toolbar.addWidget(self.filter_box)
        layout.addLayout(toolbar)

        # ── 饰品表格 ──
        self.table = QTableWidget()
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels([
            "ID", "饰品名称", "相位", "磨损度", "成本(元)", "平台",
            "状态 / 倒计时", "租期天数", "日租（净）", "本单净收入", "累计净收益",
        ])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 44)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        for column in range(2, 11):
            if column == 1:
                continue
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("alternate-background-color: #1e1e2e;")
        self.table.doubleClicked.connect(self.show_selected_rental_history)

        layout.addWidget(self.table)

    def create_card(self, title, val, color):
        """创建美化后的统计卡片"""
        w = QWidget()
        w.setObjectName("cardFrame")
        w.setMinimumHeight(100)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)

        t = QLabel(title)
        t.setObjectName("cardTitle")
        v = QLabel(val)
        v.setObjectName("cardValue")
        v.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: bold;")

        lay.addWidget(t)
        lay.addWidget(v)
        return w

    # ═══════════════════════════════════════════
    # Tab 2: 一览式大盘行情 (Refactored)
    # ═══════════════════════════════════════════

    def init_orders_tab(self):
        layout = QVBoxLayout(self.tab_orders)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("📋 平台出租订单")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        description = QLabel(
            "订单由你手动点击同步；C5 当前已接入。同步结果不会自动改写库存饰品状态。"
        )
        description.setStyleSheet("color: #a6adc8;")
        layout.addWidget(description)

        self.order_table = QTableWidget()
        self.order_table.setColumnCount(9)
        self.order_table.setHorizontalHeaderLabels([
            "平台", "订单号", "饰品", "磨损", "实际收入", "起租时间",
            "归还时间", "状态", "最后同步",
        ])
        self.order_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.order_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.order_table.setAlternatingRowColors(True)
        self.order_table.setStyleSheet("alternate-background-color: #1e1e2e;")
        header = self.order_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for column in (0, 1, 3, 4, 5, 6, 7, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.order_table)
        self._refresh_orders_table()

    def _refresh_orders_table(self):
        if not hasattr(self, "order_table"):
            return
        orders = self.db.get_rental_orders()
        self.order_table.setRowCount(0)
        for row_index, order in enumerate(orders):
            self.order_table.insertRow(row_index)
            values = [
                order["platform"], order["order_no"], order["item_name"],
                order["float_val"], f"¥ {order['income']:.2f}",
                order["start_time"], order["return_time"], order["status"],
                order["synced_at"],
            ]
            for column, value in enumerate(values):
                self.order_table.setItem(row_index, column, QTableWidgetItem(str(value)))

    def _open_default_browser_order_page(self, platform):
        """Open an order page in the user's default browser and its existing session."""
        _platform_name, url = ORDER_PAGE_URLS.get(platform, ("", ""))
        if not url:
            return
        QDesktopServices.openUrl(QUrl(url))

    def _import_rental_orders_from_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if not text:
            QMessageBox.information(self, "剪贴板导入", "剪贴板为空。请先从 C5、ECO 或 IGXE 订单页复制文本。")
            return
        try:
            platform, orders = parse_rental_clipboard(text)
        except ValueError as exc:
            QMessageBox.warning(self, "剪贴板导入", str(exc))
            return
        if not orders:
            QMessageBox.warning(self, "剪贴板导入", f"已识别 {platform}，但未解析到有效订单。")
            return
        preview = RentalImportPreviewDialog(platform, orders, self)
        if preview.exec() != QDialog.Accepted:
            return
        self.db.upsert_rental_orders(platform, orders)
        self.load_data()
        QMessageBox.information(
            self,
            "剪贴板导入完成",
            f"已导入 {len(orders)} 条 {platform} 订单。\n重复订单会按订单号更新，不会重复累计收益。",
        )

    def init_market_tab(self):
        layout = QVBoxLayout(self.tab_market)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 顶部标题栏 ──
        header = QHBoxLayout()
        title = QLabel("📊 一览式大盘行情")
        title.setObjectName("titleLabel")
        header.addWidget(title)
        header.addStretch()
        self.lbl_market_update = QLabel("最后更新: --")
        self.lbl_market_update.setStyleSheet("color: #a6adc8; font-size: 12px;")
        header.addWidget(self.lbl_market_update)
        layout.addLayout(header)

        # ── 搜索栏 ──
        search_layout = QHBoxLayout()
        self.market_input = QLineEdit()
        self.market_input.setPlaceholderText("输入饰品名称搜索并添加到大盘 (例如: 折叠刀 | 多普勒)")
        self.market_search_btn = QPushButton("🔍 搜索并添加")
        self.market_search_btn.setObjectName("primaryBtn")
        self.market_search_btn.clicked.connect(self.query_csqaq_market)
        search_layout.addWidget(self.market_input, 1)
        search_layout.addWidget(self.market_search_btn)

        refresh_market_btn = QPushButton("🔄 刷新行情")
        refresh_market_btn.setObjectName("successBtn")
        refresh_market_btn.clicked.connect(self._refresh_all_market_data)
        search_layout.addWidget(refresh_market_btn)

        force_eco_btn = QPushButton("⏱ 开启自动刷新（10:00）")
        force_eco_btn.setObjectName("primaryBtn")
        force_eco_btn.setToolTip("每 10 分钟自动刷新 CSQAQ 与 ECO 行情；再次点击可停止")
        force_eco_btn.clicked.connect(self._toggle_market_auto_refresh)
        self.market_auto_refresh_btn = force_eco_btn
        search_layout.addWidget(force_eco_btn)

        self.market_remove_btn = QPushButton("🗑 删除选中")
        self.market_remove_btn.setObjectName("dangerBtn")
        self.market_remove_btn.clicked.connect(self._remove_selected_market_items)
        search_layout.addWidget(self.market_remove_btn)

        layout.addLayout(search_layout)

        # ── 一览式大盘表格 ──
        # 列: 图片(48x48) | 饰品名称+Phase | CSQAQ最低售价 | ECO最低日租 | IGXE最低日租 | 更新时间 | 展开明细
        self.market_table = QTableWidget()
        self.market_table.setColumnCount(8)
        self.market_table.setHorizontalHeaderLabels([
            "图片", "饰品名称 / Phase", "CSQAQ 最低售价",
            "ECO 最低日租", "IGXE 最低日租", "更新时间", "操作"
        ])
        hdr = self.market_table.horizontalHeader()
        self.market_table.setHorizontalHeaderLabels([
            "图片", "饰品名称 / Phase", "CSQAQ 最低售价", "ECO 最低日租",
            "C5 租金（短 / 长）", "悠悠 租金（短 / 长）",
            "IGXE 租金（短 / 长）", "更新时间",
        ])
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.Fixed)
        self.market_table.setColumnWidth(0, 60)
        self.market_table.setColumnWidth(6, 100)
        self.market_table.verticalHeader().setDefaultSectionSize(56)
        self.market_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.market_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.market_table.setAlternatingRowColors(True)
        self.market_table.setStyleSheet("alternate-background-color: #1e1e2e;")
        for column in range(2, 8):
            hdr.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.market_table.verticalHeader().setDefaultSectionSize(62)
        self.market_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.market_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.market_table.customContextMenuRequested.connect(self._show_market_context_menu)
        # 双击单行刷新该饰品行情
        self.market_table.doubleClicked.connect(self._on_market_table_double_click)
        layout.addWidget(self.market_table)

        # ── 明细面板 (默认隐藏) ──
        self.detail_frame = QFrame()
        self.detail_frame.setObjectName("detailFrame")
        self.detail_frame.setVisible(False)
        detail_layout = QVBoxLayout(self.detail_frame)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        detail_layout.setSpacing(8)

        # 明细标题
        self.detail_title = QLabel("📋 展开明细")
        self.detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(self.detail_title)

        # 明细内容区域（滚动）
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setMaximumHeight(300)
        self.detail_content = QWidget()
        self.detail_content_layout = QVBoxLayout(self.detail_content)
        self.detail_content_layout.setSpacing(6)
        detail_scroll.setWidget(self.detail_content)
        detail_layout.addWidget(detail_scroll)

        # 关闭按钮
        close_detail_btn = QPushButton("✕ 关闭明细")
        close_detail_btn.setObjectName("dangerBtn")
        close_detail_btn.clicked.connect(lambda: self.detail_frame.setVisible(False))
        detail_layout.addWidget(close_detail_btn)

        layout.addWidget(self.detail_frame)

        # ── 初始加载 ──
        self._init_market_from_cache()

    def _init_market_from_cache(self):
        """冷启动时从 market_cache.json 加载缓存数据并渲染大盘，绝不发送网络请求"""
        cached = MarketCache.load()
        if cached:
            logger.info(f"[大盘] 从缓存加载 {len(cached)} 条行情数据")
            self._market_tracked_items = list(cached.values())
            for entry in self._market_tracked_items:
                self._apply_schema_mapping(entry)
            if self._deduplicate_market_tracked_items():
                self._save_market_cache()
            self._populate_market_table()
            self.lbl_market_update.setText(f"最后更新: {QTime.currentTime().toString('HH:mm:ss')} (缓存)")
        else:
            # 无缓存时从第一页库存饰品自动同步到大盘
            self._refresh_market_tracked_list()
            self.lbl_market_update.setText("最后更新: -- (来自库存)")

    def _refresh_market_tracked_list(self):
        """从数据库加载库存物品，按 (名称 + 相位) 去重后填充到大盘表格"""
        db_items = self.db.get_all_items()
        seen = set()
        self._market_tracked_items = []
        for item in db_items:
            name = item["name"]
            phase = item.get("phase", "-")
            key = f"{name}|{phase}"
            if key in seen:
                continue
            seen.add(key)
            market_hash_name = self._build_market_hash_name(item)
            entry = {
                "key": key,
                "name": name,
                "phase": phase,
                "market_hash_name": market_hash_name,
                "csqaq_price": 0.0,
                "eco_min_rent": 0.0,
                "igxe_min_rent": 0.0,
                "updated_at": "",
                "detail": {},
            }
            self._market_tracked_items.append(entry)
            self._apply_schema_mapping(entry)
        self._deduplicate_market_tracked_items()
        self._populate_market_table()

    @staticmethod
    def _apply_schema_mapping(entry: dict):
        """Fill standard Steam market name and image URL from the local schema."""
        mapped_item = CS2ItemSchema.lookup(entry.get("name", ""))
        if mapped_item:
            entry["market_hash_name"] = mapped_item["market_hash_name"]
            entry["image_url"] = mapped_item.get("image", "")
            entry["schema_id"] = mapped_item["id"]

    def _deduplicate_market_tracked_items(self) -> bool:
        """Merge P1/P3 Doppler rows only in the market watch list.

        Inventory rows remain separate: their float values identify different
        assets.  ECO supplies one usable rental quote for these two phases,
        so displaying them twice adds no market information.
        """
        merged: list[dict] = []
        group_index: dict[str, int] = {}
        changed = False

        for entry in self._market_tracked_items:
            phase = str(entry.get("phase", "-")).upper().replace(" ", "")
            market_hash_name = entry.get("market_hash_name", entry.get("name", ""))
            if phase in {"P1", "P3", "P1/P3"}:
                group_key = f"{market_hash_name}|P1/P3"
            else:
                group_key = entry.get("key", f"{market_hash_name}|{phase}")

            existing_index = group_index.get(group_key)
            if existing_index is None:
                if group_key.endswith("|P1/P3"):
                    entry["key"] = group_key
                    entry["phase"] = "P1 / P3"
                    changed = changed or phase != "P1/P3"
                group_index[group_key] = len(merged)
                merged.append(entry)
                continue

            changed = True
            existing = merged[existing_index]
            # Preserve an available lowest quote if the first cached entry was
            # empty, while retaining its image/details where available.
            for field in ("csqaq_price", "eco_min_rent", "igxe_min_rent"):
                current = float(existing.get(field, 0.0) or 0.0)
                incoming = float(entry.get(field, 0.0) or 0.0)
                if current <= 0 < incoming:
                    existing[field] = incoming

        if changed:
            self._market_tracked_items = merged
            logger.info("[大盘] 已合并 P1/P3 重复行情项，剩余 %s 条", len(merged))
        return changed

    def _build_market_hash_name(self, item: dict) -> str:
        """根据饰品数据构建英文 market_hash_name，优先使用已存储的"""
        mapped_item = CS2ItemSchema.lookup(item.get("name", ""))
        if mapped_item:
            return mapped_item["market_hash_name"]

        stored_mhn = item.get("market_hash_name", "")
        if stored_mhn and stored_mhn != item.get("name", ""):
            return stored_mhn

        name = item.get("name", "")

        exterior_map = {
            "崭新出厂": "Factory New",
            "略有磨损": "Minimal Wear",
            "久经沙场": "Field-Tested",
            "破损不堪": "Well-Worn",
            "战痕累累": "Battle-Scarred",
        }

        weapon_map = {
            "折叠刀": "Flip Knife",
            "M9 刺刀": "M9 Bayonet",
            "刺刀": "Bayonet",
            "爪子刀": "Karambit",
            "蝴蝶刀": "Butterfly Knife",
            "猎杀者匕首": "Huntsman Knife",
            "暗影双匕": "Shadow Daggers",
            "弯刀": "Falchion Knife",
            "鲍伊猎刀": "Bowie Knife",
            "短剑": "Stiletto Knife",
            "熊刀": "Ursus Knife",
            "锯齿爪刀": "Navaja Knife",
            "海豹短刀": "Classic Knife",
            "骷髅匕首": "Skeleton Knife",
            "求生匕首": "Survival Knife",
            "流浪者匕首": "Nomad Knife",
            "系绳匕首": "Paracord Knife",
            "专业手套": "Specialist Gloves",
            "运动手套": "Sport Gloves",
            "驾驶手套": "Driver Gloves",
            "摩托手套": "Moto Gloves",
            "血猎手套": "Bloodhound Gloves",
            "手部束带": "Hand Wraps",
            "九头蛇手套": "Hydra Gloves",
            "狂牙手套": "Broken Fang Gloves",
        }

        skin_map = {
            "多普勒": "Doppler",
            "狂澜": "Crimson Web",
            "紫罗兰珠绣": "Vice",
            "繁花似锦": "Boom!",
        }

        import re
        # 手套格式: "专业手套（★） | 狂澜 (久经沙场)"
        match = re.match(r"(.*?)（★）\s*\|\s*(.*?)\s*\((.*?)\)", name)
        if match:
            weapon_zh = match.group(1).strip()
            skin_zh = match.group(2).strip()
            exterior_zh = match.group(3).strip()

            weapon_en = weapon_map.get(weapon_zh, weapon_zh)
            skin_en = skin_map.get(skin_zh, skin_zh)
            exterior_en = exterior_map.get(exterior_zh, exterior_zh)

            # ECO 返回的手套 HashName 也带 ★ 前缀
            return f"★ {weapon_en} | {skin_en} ({exterior_en})"

        # 刀具格式: "★ 折叠刀 | 多普勒 (崭新出厂)"
        match = re.match(r"★\s*(.*?)\s*\|\s*(.*?)\s*\((.*?)\)", name)
        if match:
            weapon_zh = match.group(1).strip()
            skin_zh = match.group(2).strip()
            exterior_zh = match.group(3).strip()

            weapon_en = weapon_map.get(weapon_zh, weapon_zh)
            skin_en = skin_map.get(skin_zh, skin_zh)
            exterior_en = exterior_map.get(exterior_zh, exterior_zh)

            return f"★ {weapon_en} | {skin_en} ({exterior_en})"

        return name

    @staticmethod
    def _market_number(value):
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _market_price_text(self, value, unavailable_reason, suffix=""):
        amount = self._market_number(value)
        return f"¥ {amount:.2f}{suffix}" if amount > 0 else f"暂无（{unavailable_reason}）"

    def _market_rent_text(self, short_value, long_value, unavailable_reason):
        short_rent = self._market_number(short_value)
        long_rent = self._market_number(long_value)
        if short_rent <= 0 and long_rent <= 0:
            return f"暂无（{unavailable_reason}）"
        short_text = f"短 ¥ {short_rent:.2f}" if short_rent > 0 else "短 —"
        long_text = f"长 ¥ {long_rent:.2f}" if long_rent > 0 else "长 —"
        return f"{short_text}\n{long_text}/天"

    @staticmethod
    def _market_updated_datetime(value):
        """Read current ISO timestamps and the legacy HH:MM:SS cache format."""
        if not value:
            return None
        text = str(value).strip()
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
        try:
            clock = datetime.strptime(text, "%H:%M:%S").time()
            return datetime.combine(datetime.now().date(), clock)
        except ValueError:
            return None

    def _market_updated_text(self, entry):
        updated_at = self._market_updated_datetime(entry.get("updated_at"))
        if updated_at is None:
            return "暂无更新记录"
        elapsed_seconds = max(0, (datetime.now() - updated_at).total_seconds())
        elapsed_minutes = int(elapsed_seconds // 60)
        return f"{elapsed_minutes} 分钟前更新成功"

    def _update_market_relative_times(self):
        """Refresh the display-only rightmost market column every minute."""
        for row, entry in enumerate(self._market_tracked_items):
            updated_item = self.market_table.item(row, 7)
            if updated_item is not None:
                updated_item.setText(self._market_updated_text(entry))

    @staticmethod
    def _market_link_platform(column):
        return {
            1: "csqaq",
            2: "csqaq",
            3: "eco",
            4: "c5",
            5: "yyyp",
            6: "igxe",
        }.get(column, "")

    def _default_market_link(self, entry, platform):
        detail = entry.get("detail", {})
        name = detail.get("name_zh") or entry.get("name", "")
        if platform == "csqaq":
            good_id = entry.get("csqaq_good_id") or detail.get("csqaq_good_id") or detail.get("good_id")
            return f"https://csqaq.com/goods/{good_id}" if good_id else ""
        if platform == "c5":
            item_id = entry.get("c5_id") or detail.get("c5_id")
            if item_id:
                return f"https://www.c5game.com/csgo/{item_id}/"
            return f"https://www.c5game.com/csgo?marketKeyword={quote(name)}" if name else ""
        if platform == "igxe":
            item_id = entry.get("igxe_id") or detail.get("igxe_id")
            if item_id:
                return f"https://www.igxe.cn/product/730/{item_id}?cur_page=6&sort_rule=1"
            return f"https://www.igxe.cn/market/csgo?keyword={quote(name)}" if name else ""
        if platform == "eco":
            item_id = entry.get("eco_id") or detail.get("eco_id")
            if item_id:
                return f"https://www.ecosteam.cn/goods/730-{item_id}-1-laypageRent-0-1.html"
        if platform == "yyyp":
            item_id = entry.get("yyyp_id") or detail.get("yyyp_id")
            if item_id:
                return (
                    "https://www.youpin898.com/market/goods-list"
                    f"?listType=30&templateId={item_id}&gameId=730"
                )
        return ""

    def _market_link(self, entry, platform):
        return (entry.get("links", {}) or {}).get(platform) or self._default_market_link(entry, platform)

    def _open_market_link(self, row, column):
        if row < 0 or row >= len(self._market_tracked_items):
            return
        platform = self._market_link_platform(column)
        if not platform:
            return
        entry = self._market_tracked_items[row]
        url = self._market_link(entry, platform)
        if not url:
            QMessageBox.information(
                self,
                "未设置链接",
                "该平台没有稳定的自动跳转地址。请右键该价格单元格，选择“设置自定义链接”。",
            )
            return
        QDesktopServices.openUrl(QUrl(url))

    def _show_market_context_menu(self, position):
        index = self.market_table.indexAt(position)
        if not index.isValid() or index.row() >= len(self._market_tracked_items):
            return
        platform = self._market_link_platform(index.column())
        if not platform:
            return
        entry = self._market_tracked_items[index.row()]
        menu = QMenu(self)
        open_action = menu.addAction("打开链接")
        edit_action = menu.addAction("设置自定义链接")
        clear_action = menu.addAction("清除自定义链接")
        chosen = menu.exec(self.market_table.viewport().mapToGlobal(position))
        if chosen == open_action:
            self._open_market_link(index.row(), index.column())
        elif chosen == edit_action:
            current = self._market_link(entry, platform)
            url, accepted = QInputDialog.getText(
                self, "设置平台链接", "网页链接（留空可取消自定义）：", text=current
            )
            if accepted:
                entry.setdefault("links", {})[platform] = url.strip()
                self._save_market_cache()
        elif chosen == clear_action:
            links = entry.get("links", {})
            if platform in links:
                links.pop(platform)
                self._save_market_cache()

    def _remove_selected_market_items(self):
        rows = sorted({index.row() for index in self.market_table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "大盘编辑", "请先选择要从大盘移除的饰品（可按 Ctrl 或 Shift 多选）。")
            return
        names = [self._market_tracked_items[row].get("name", "") for row in rows]
        answer = QMessageBox.question(
            self,
            "确认移除",
            f"从大盘移除 {len(rows)} 个饰品？这不会删除资产库存。\n\n" + "\n".join(names[:8]),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        for row in rows:
            self._market_tracked_items.pop(row)
        self._populate_market_table()
        self._save_market_cache()

    def _populate_market_table(self):
        """Render cached CSQAQ aggregate quotes and ECO rental prices only."""
        self.market_table.setRowCount(0)
        for i, entry in enumerate(self._market_tracked_items):
            self.market_table.insertRow(i)
            self.market_table.setRowHeight(i, 62)

            image_label = QLabel()
            image_label.setFixedSize(48, 48)
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setStyleSheet("background-color: #181825; border-radius: 4px;")
            local_img = ImageCache.get_local_path(entry.get("market_hash_name", entry["name"]))
            if os.path.exists(local_img):
                pixmap = QPixmap(local_img)
                if not pixmap.isNull():
                    image_label.setPixmap(pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                image_label.setText("图片")
            self.market_table.setCellWidget(i, 0, image_label)

            display_name = entry["name"]
            if entry.get("phase") and entry["phase"] != "-":
                display_name += f"  [{entry['phase']}]"
            name_item = QTableWidgetItem(display_name)
            name_item.setToolTip(entry.get("market_hash_name", entry["name"]))
            self.market_table.setItem(i, 1, name_item)

            lowest = self._market_number(entry.get("csqaq_min_sell_price", entry.get("csqaq_price")))
            platform = entry.get("csqaq_min_sell_platform", "")
            lowest_text = self._market_price_text(lowest, "CSQAQ 未提供")
            if lowest > 0 and platform:
                lowest_text += f"\n· {platform}"
            csqaq_item = QTableWidgetItem(lowest_text)
            csqaq_item.setForeground(QColor("#a6e3a1") if lowest > 0 else QColor("#6c7086"))
            self.market_table.setItem(i, 2, csqaq_item)

            eco_rent = self._market_number(entry.get("eco_min_rent"))
            eco_item = QTableWidgetItem(self._market_price_text(eco_rent, "ECO 无租金", "/天"))
            eco_item.setForeground(QColor("#89b4fa") if eco_rent > 0 else QColor("#6c7086"))
            self.market_table.setItem(i, 3, eco_item)

            rent_columns = (
                (4, "c5_short_rent", "c5_long_rent", "CSQAQ 未提供 C5 报价", "#f9e2af"),
                (5, "yyyp_short_rent", "yyyp_long_rent", "CSQAQ 未提供悠悠报价", "#94e2d5"),
                (6, "igxe_short_rent", "igxe_long_rent", "CSQAQ 未提供 IGXE 报价", "#cba6f7"),
            )
            for column, short_key, long_key, unavailable, color in rent_columns:
                rent_text = self._market_rent_text(entry.get(short_key), entry.get(long_key), unavailable)
                rent_item = QTableWidgetItem(rent_text)
                available = self._market_number(entry.get(short_key)) > 0 or self._market_number(entry.get(long_key)) > 0
                rent_item.setForeground(QColor(color) if available else QColor("#6c7086"))
                self.market_table.setItem(i, column, rent_item)

            self.market_table.setItem(
                i, 7, QTableWidgetItem(self._market_updated_text(entry))
            )
        return
        """填充一览式大盘表格"""
        self.market_table.setRowCount(0)
        for i, entry in enumerate(self._market_tracked_items):
            self.market_table.insertRow(i)
            self.market_table.setRowHeight(i, 56)

            # 列0: 图片 (48x48)
            img_label = QLabel()
            img_label.setFixedSize(48, 48)
            img_label.setAlignment(Qt.AlignCenter)
            img_label.setStyleSheet("background-color: #181825; border-radius: 4px;")
            # 尝试加载本地缓存图片
            local_img = ImageCache.get_local_path(entry.get("market_hash_name", entry["name"]))
            if os.path.exists(local_img):
                pix = QPixmap(local_img)
                if not pix.isNull():
                    img_label.setPixmap(pix.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                img_label.setText("🖼️")
            self.market_table.setCellWidget(i, 0, img_label)

            # 列1: 饰品名称 + Phase
            display_name = entry["name"]
            if entry.get("phase") and entry["phase"] != "-":
                display_name += f"  [{entry['phase']}]"
            name_item = QTableWidgetItem(display_name)
            name_item.setToolTip(entry.get("market_hash_name", entry["name"]))
            self.market_table.setItem(i, 1, name_item)

            # 列2: CSQAQ 最低售价
            csqaq_price = entry.get("csqaq_price", 0.0)
            csqaq_text = f"¥ {csqaq_price:.2f}" if csqaq_price > 0 else "查询中..."
            csqaq_item = QTableWidgetItem(csqaq_text)
            if csqaq_price > 0:
                csqaq_item.setForeground(QColor("#a6e3a1"))
            self.market_table.setItem(i, 2, csqaq_item)

            # 列3: ECO 最低日租
            eco_rent = entry.get("eco_min_rent", 0.0)
            eco_text = f"¥ {eco_rent:.2f}/天" if eco_rent > 0 else "查询中..."
            eco_item = QTableWidgetItem(eco_text)
            if eco_rent > 0:
                eco_item.setForeground(QColor("#89b4fa"))
            self.market_table.setItem(i, 3, eco_item)

            # 列4: IGXE 最低日租
            igxe_rent = entry.get("igxe_min_rent", 0.0)
            igxe_text = f"¥ {igxe_rent:.2f}/天" if igxe_rent > 0 else "查询中..."
            igxe_item = QTableWidgetItem(igxe_text)
            if igxe_rent > 0:
                igxe_item.setForeground(QColor("#cba6f7"))
            self.market_table.setItem(i, 4, igxe_item)

            # 列5: 更新时间
            updated = entry.get("updated_at", "")
            if not updated:
                updated = "--"
            self.market_table.setItem(i, 5, QTableWidgetItem(updated))

            # 列6: 展开明细按钮
            expand_btn = QPushButton("📋 展开")
            expand_btn.setStyleSheet("background-color: #45475a; color: #cdd6f4; padding: 4px 8px; font-size: 11px;")
            row_index = i  # 闭包捕获
            expand_btn.clicked.connect(lambda checked, r=row_index: self._toggle_detail(r))
            self.market_table.setCellWidget(i, 6, expand_btn)

    def _toggle_detail(self, row: int):
        """展开/收起指定行的明细面板"""
        if row < 0 or row >= len(self._market_tracked_items):
            return
        entry = self._market_tracked_items[row]
        self._current_market_item_key = entry["key"]

        # 填充明细内容
        self._populate_detail_panel(entry)
        self.detail_frame.setVisible(True)

    def _populate_detail_panel(self, entry: dict):
        """填充明细面板内容"""
        # 清除旧内容
        while self.detail_content_layout.count():
            child = self.detail_content_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        name = entry["name"]
        phase = entry.get("phase", "-")
        display_name = f"{name}  [{phase}]" if phase and phase != "-" else name
        self.detail_title.setText(f"📋 展开明细 — {display_name}")

        detail = entry.get("detail", {})

        # ── 全网售价对比 ──
        price_group = QGroupBox("🏪 全网在售底价对比")
        price_form = QFormLayout(price_group)
        platforms = [
            ("🟢 BUFF 在售价", detail.get("buff_price", 0.0)),
            ("🔵 悠悠有品 在售价", detail.get("yy_price", 0.0)),
            ("💧 Steam 在售价", detail.get("steam_price", 0.0)),
            ("⚡ 全网最低价", detail.get("min_sell_price", 0.0)),
        ]
        for label, price in platforms:
            text = f"¥ {price:.2f}" if price and price > 0 else "无数据"
            price_form.addRow(f"{label}:", QLabel(text))
        self.detail_content_layout.addWidget(price_group)

        # ── ECO 出租挂单明细 ──
        eco_listings = detail.get("eco_listings", [])
        eco_group = QGroupBox(f"🟢 ECO 出租挂单明细 ({len(eco_listings)} 条)")
        eco_layout = QVBoxLayout(eco_group)
        if eco_listings:
            eco_table = QTableWidget()
            eco_table.setColumnCount(4)
            eco_table.setHorizontalHeaderLabels(["磨损度", "Float 值", "日租金", "押金"])
            eco_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            eco_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            eco_table.setMaximumHeight(150)
            eco_table.setRowCount(0)
            for i, listing in enumerate(eco_listings):
                eco_table.insertRow(i)
                eco_table.setItem(i, 0, QTableWidgetItem(listing.get("wear", "-")))
                eco_table.setItem(i, 1, QTableWidgetItem(listing.get("float_val", "-")))
                eco_table.setItem(i, 2, QTableWidgetItem(f"¥ {listing['rent']:.2f}"))
                eco_table.setItem(i, 3, QTableWidgetItem(f"¥ {listing['deposit']:.2f}"))
            eco_layout.addWidget(eco_table)
        else:
            eco_layout.addWidget(QLabel("暂无 ECO 出租挂单数据"))
        self.detail_content_layout.addWidget(eco_group)

        # ── IGXE 出租挂单明细 ──
        igxe_listings = detail.get("igxe_listings", [])
        igxe_group = QGroupBox(f"🟣 IGXE 出租挂单明细 ({len(igxe_listings)} 条)")
        igxe_layout = QVBoxLayout(igxe_group)
        if igxe_listings:
            igxe_table = QTableWidget()
            igxe_table.setColumnCount(4)
            igxe_table.setHorizontalHeaderLabels(["磨损度", "Float 值", "日租金", "押金"])
            igxe_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            igxe_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            igxe_table.setMaximumHeight(150)
            igxe_table.setRowCount(0)
            for i, listing in enumerate(igxe_listings):
                igxe_table.insertRow(i)
                igxe_table.setItem(i, 0, QTableWidgetItem(listing.get("wear", "-")))
                igxe_table.setItem(i, 1, QTableWidgetItem(listing.get("float_val", "-")))
                igxe_table.setItem(i, 2, QTableWidgetItem(f"¥ {listing['rent']:.2f}"))
                igxe_table.setItem(i, 3, QTableWidgetItem(f"¥ {listing['deposit']:.2f}"))
            igxe_layout.addWidget(igxe_table)
        else:
            igxe_layout.addWidget(QLabel("暂无 IGXE 出租挂单数据"))
        self.detail_content_layout.addWidget(igxe_group)

        self.detail_content_layout.addStretch()

    # ── 刷新全部行情（顺序队列） ──

    def _set_market_auto_refresh_button_text(self, text):
        """Update the market-page auto-refresh control."""
        if getattr(self, "market_auto_refresh_btn", None) is not None:
            self.market_auto_refresh_btn.setText(text)

    def _schedule_next_market_auto_refresh(self):
        """Start one ten-minute interval while keeping recurring mode enabled."""
        self._market_auto_refresh_deadline = time.monotonic() + 10 * 60
        self.market_auto_refresh_timer.start(10 * 60 * 1000)
        if not self.market_countdown_timer.isActive():
            self.market_countdown_timer.start(1000)
        self._update_market_auto_refresh_countdown()

    def _toggle_market_auto_refresh(self):
        if self._market_auto_refresh_enabled:
            self._market_auto_refresh_enabled = False
            self.market_auto_refresh_timer.stop()
            self.market_countdown_timer.stop()
            self._market_auto_refresh_deadline = 0.0
            self._set_market_auto_refresh_button_text("⏱ 开启自动刷新（10:00）")
            self.lbl_market_update.setText("已取消自动刷新倒计时")
            return

        self._market_auto_refresh_enabled = True
        self._schedule_next_market_auto_refresh()

    def _update_market_auto_refresh_countdown(self):
        if not self._market_auto_refresh_enabled:
            return
        remaining = max(0, int(self._market_auto_refresh_deadline - time.monotonic() + 0.999))
        minutes, seconds = divmod(remaining, 60)
        self._set_market_auto_refresh_button_text(
            f"⏱ 自动刷新 {minutes:02d}:{seconds:02d}"
        )

    def _run_scheduled_market_refresh(self):
        if not self._market_auto_refresh_enabled:
            return

        # Schedule the next interval before running the network job.  A slow refresh
        # therefore never leaves the visible countdown stopped at zero.
        self._schedule_next_market_auto_refresh()
        if self._market_refresh_thread and self._market_refresh_thread.isRunning():
            self.lbl_market_update.setText("自动刷新已跳过：当前已有刷新任务，已重新计时")
            return
        self.lbl_market_update.setText("每 10 分钟自动刷新：正在刷新行情…")
        self._refresh_all_market_data()

    def _refresh_all_market_data(self, force_eco: bool = False):
        """刷新大盘；普通模式优先使用本地 ECO 快照。"""
        if not self._market_tracked_items:
            QMessageBox.warning(self, "提示", "大盘中没有饰品，请先搜索添加！")
            return

        # 如果已有刷新线程在运行，不允许重复启动
        if self._market_refresh_thread and self._market_refresh_thread.isRunning():
            QMessageBox.information(self, "提示", "正在刷新行情中，请等待完成...")
            return

        token = self.db.get_config("csqaq_token")
        eco_partner = self.db.get_config("eco_partner_id")
        eco_rsa = self.db.get_config("eco_rsa_key")

        self.lbl_market_update.setText("正在强制更新 ECO..." if force_eco else "正在刷新行情...")

        self._market_refresh_thread = QThread()
        self._market_refresh_worker = MarketRefreshWorker()
        self._market_refresh_worker.moveToThread(self._market_refresh_thread)

        # 连接信号
        self._market_refresh_worker.progress.connect(self._on_market_refresh_progress)
        self._market_refresh_worker.row_updated.connect(self._on_market_refresh_row_updated)
        self._market_refresh_worker.finished.connect(self._on_market_refresh_finished)
        self._market_refresh_worker.error.connect(lambda msg: logger.warning(f"[大盘刷新] {msg}"))

        # 安全清理
        self._market_refresh_worker.finished.connect(self._market_refresh_thread.quit)
        self._market_refresh_worker.finished.connect(self._market_refresh_worker.deleteLater)
        self._market_refresh_thread.finished.connect(self._market_refresh_thread.deleteLater)
        self._market_refresh_thread.finished.connect(self._cleanup_market_refresh_thread)

        # 启动
        items_copy = list(self._market_tracked_items)
        self._market_refresh_thread.started.connect(
            lambda: self._market_refresh_worker.refresh_all(
                token, eco_partner, eco_rsa, items_copy, self._build_market_hash_name_for_entry, force_eco
            )
        )
        self._market_refresh_thread.start()

    def _build_market_hash_name_for_entry(self, entry: dict) -> str:
        """从 entry 构建 market_hash_name"""
        return entry.get("market_hash_name", entry["name"])

    def _on_market_refresh_progress(self, current: int, total: int, message: str):
        """刷新进度更新"""
        self.lbl_market_update.setText(f"刷新中: {message}")

    def _on_market_refresh_row_updated(self, row: int):
        """单行刷新完成，更新表格"""
        self._populate_market_table()
        self._save_market_cache()

    def _on_market_refresh_finished(self):
        """全部刷新完成"""
        # A completed manual or scheduled refresh represents one consistent market
        # snapshot, so all rows receive the same persisted success timestamp.
        completed_at = datetime.now().isoformat(timespec="seconds")
        for entry in self._market_tracked_items:
            entry["updated_at"] = completed_at
        self._populate_market_table()
        self._save_market_cache()
        eco_status = getattr(self._market_refresh_worker, "eco_status_text", "")
        suffix = f" · {eco_status}" if eco_status else ""
        self.lbl_market_update.setText(f"最后更新: {QTime.currentTime().toString('HH:mm:ss')}{suffix}")

    def _cleanup_market_refresh_thread(self):
        """清理市场刷新线程引用"""
        self._market_refresh_thread = None
        self._market_refresh_worker = None

    # ── 市场数据缓存 ──

    def _save_market_cache(self):
        """将当前大盘数据保存到 market_cache.json"""
        cache_data = {}
        for entry in self._market_tracked_items:
            key = entry["key"]
            cache_data[key] = {
                "key": key,
                "name": entry["name"],
                "phase": entry.get("phase", "-"),
                "market_hash_name": entry.get("market_hash_name", entry["name"]),
                "image_url": entry.get("image_url", ""),
                "csqaq_price": entry.get("csqaq_price", 0.0),
                "csqaq_good_id": entry.get("csqaq_good_id", ""),
                "csqaq_min_sell_price": entry.get("csqaq_min_sell_price", entry.get("csqaq_price", 0.0)),
                "csqaq_min_sell_platform": entry.get("csqaq_min_sell_platform", ""),
                "csqaq_detail_fetched_at": entry.get("csqaq_detail_fetched_at", 0),
                "eco_min_rent": entry.get("eco_min_rent", 0.0),
                "c5_id": entry.get("c5_id", ""),
                "yyyp_id": entry.get("yyyp_id", ""),
                "igxe_id": entry.get("igxe_id", ""),
                "eco_id": entry.get("eco_id", ""),
                "c5_short_rent": entry.get("c5_short_rent", 0.0),
                "c5_long_rent": entry.get("c5_long_rent", 0.0),
                "yyyp_short_rent": entry.get("yyyp_short_rent", 0.0),
                "yyyp_long_rent": entry.get("yyyp_long_rent", 0.0),
                "igxe_short_rent": entry.get("igxe_short_rent", 0.0),
                "igxe_long_rent": entry.get("igxe_long_rent", 0.0),
                "links": entry.get("links", {}),
                "updated_at": entry.get("updated_at", ""),
                "detail": entry.get("detail", {}),
            }
        MarketCache.save(cache_data)

    # ── 搜索并添加 ──

    def query_csqaq_market(self):
        """搜索 CSQAQ 并将结果添加到大盘"""
        keyword = self.market_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入要查询的饰品名称！")
            return

        token = self.db.get_config("csqaq_token")
        if not token:
            QMessageBox.warning(self, "提示", "请先在【⚙️ 系统与费率设置】中填入 CSQAQ 的 ApiToken！")
            return

        self.market_search_btn.setEnabled(False)
        self.market_search_btn.setText("⏳ 搜索中...")

        # 直接使用批量价格查询接口，传入关键词作为 market_hash_name
        self._start_worker(
            worker_fn=lambda w: w.batch_price_csqaq(token, [keyword]),
            on_finished=self._on_search_add_result,
            on_error=self._on_csqaq_error,
        )

    def _on_search_add_result(self, result):
        """CSQAQ 搜索回调：自动将结果添加到大盘"""
        self.market_search_btn.setEnabled(True)
        self.market_search_btn.setText("🔍 搜索并添加")

        tag, data = result
        if tag != "batch_price" or not data.get("success"):
            QMessageBox.warning(self, "未找到结果", "CSQAQ 中未搜索到匹配的饰品！")
            return

        price_data = data.get("data", {})
        added = 0
        for mhn, prices in price_data.items():
            # 检查是否已存在
            key = f"{mhn}|-"
            if any(e["key"] == key for e in self._market_tracked_items):
                continue

            entry = {
                "key": key,
                "name": mhn,
                "phase": "-",
                "market_hash_name": mhn,
                "csqaq_price": float(prices.get("min_sell_price", prices.get("buff_price", 0.0))),
                "csqaq_good_id": prices.get("good_id", ""),
                "csqaq_min_sell_price": float(prices.get("min_sell_price", prices.get("buff_price", 0.0))),
                "eco_min_rent": 0.0,
                "links": {},
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "detail": {
                    "buff_price": prices.get("buff_price", 0.0),
                    "yy_price": prices.get("yy_price", 0.0),
                    "steam_price": prices.get("steam_price", 0.0),
                    "min_sell_price": prices.get("min_sell_price", 0.0),
                    "name_zh": prices.get("name_zh", ""),
                },
            }
            self._apply_schema_mapping(entry)
            self._market_tracked_items.append(entry)
            added += 1

        self._populate_market_table()
        self._save_market_cache()
        QMessageBox.information(self, "成功", f"已添加 {added} 个饰品到大盘！")

    def _on_csqaq_error(self, error_msg):
        """CSQAQ 错误回调"""
        self.market_search_btn.setEnabled(True)
        self.market_search_btn.setText("🔍 搜索并添加")
        QMessageBox.critical(self, "API 错误", error_msg)

    def _on_bind_csqaq_ip(self):
        """手动触发 CSQAQ IP 绑定"""
        token = self.db.get_config("csqaq_token")
        if not token:
            QMessageBox.warning(self, "提示", "请先在设置中填入 CSQAQ ApiToken！")
            return
        from modules.csqaq_client import CSQAQClient
        client = CSQAQClient(token)
        result = client.bind_local_ip()
        if result.get("code") == 200:
            ip = result.get("data", "unknown")
            QMessageBox.information(self, "绑定成功", f"公网 IP {ip} 已绑定到 CSQAQ Token 白名单！")
        else:
            QMessageBox.warning(self, "绑定失败", result.get("msg", "未知错误"))

    # ═══════════════════════════════════════════
    # Tab 3: 设置 (保持不变)
    # ═══════════════════════════════════════════

    def init_settings_tab(self):
        layout = QVBoxLayout(self.tab_settings)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        group_csqaq = QGroupBox("📊 CSQAQ 数据开放 API 配置")
        form_csqaq = QFormLayout(group_csqaq)
        self.cfg_csqaq = QLineEdit(self.db.get_config("csqaq_token"))
        self.cfg_csqaq.setPlaceholderText("粘贴登录 CSQAQ 个人中心获取的 ApiToken")
        form_csqaq.addRow("CSQAQ ApiToken:", self.cfg_csqaq)

        bind_ip_btn = QPushButton("🌐 一键绑定当前公网 IP")
        bind_ip_btn.setObjectName("primaryBtn")
        bind_ip_btn.clicked.connect(self._on_bind_csqaq_ip)
        form_csqaq.addRow(bind_ip_btn)
        layout.addWidget(group_csqaq)

        group_api = QGroupBox("🔑 ECO 开放平台 API 配置")
        form_api = QFormLayout(group_api)
        self.cfg_partner = QLineEdit(self.db.get_config("eco_partner_id"))
        self.cfg_rsa = QLineEdit(self.db.get_config("eco_rsa_key"))
        form_api.addRow("Partner ID:", self.cfg_partner)
        form_api.addRow("RSA 私钥路径/文本:", self.cfg_rsa)
        layout.addWidget(group_api)

        group_time = QGroupBox("⏰ 自动化与刷新设置")
        form_time = QFormLayout(group_time)
        self.cfg_interval = QComboBox()
        self.cfg_interval.addItems(["禁用自动刷新", "5 分钟", "15 分钟", "30 分钟", "60 分钟"])
        cur_int = self.db.get_config("refresh_interval") or "15"
        idx_map = {"0": 0, "5": 1, "15": 2, "30": 3, "60": 4}
        self.cfg_interval.setCurrentIndex(idx_map.get(cur_int, 2))
        form_time.addRow("自动刷新频率:", self.cfg_interval)
        layout.addWidget(group_time)

        group_fee = QGroupBox("🧾 出租手续费率（用于订单净收益与年化计算）")
        form_fee = QFormLayout(group_fee)
        self.cfg_fee_inputs = {}
        fee_fields = (
            ("c5_first_fee", "C5 首次出租费率:"),
            ("c5_relet_fee", "C5 转租费率:"),
            ("eco_first_fee", "ECO 首次出租费率:"),
            ("eco_relet_fee", "ECO 转租费率:"),
            ("igxe_first_fee", "IGXE 首次出租费率:"),
            ("igxe_relet_fee", "IGXE 转租费率:"),
        )
        for config_key, label in fee_fields:
            field = QLineEdit(self.db.get_config(config_key))
            field.setPlaceholderText("例如 0.15 = 15%")
            self.cfg_fee_inputs[config_key] = field
            form_fee.addRow(label, field)
        layout.addWidget(group_fee)

        save_btn = QPushButton("💾 保存全部设置")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)
        layout.addStretch()

    # ═══════════════════════════════════════════
    # 通用异步 Worker 启动器
    # ═══════════════════════════════════════════

    def _start_worker(self, worker_fn, on_finished, on_error=None):
        """
        通用方法：在后台 QThread 中运行 ApiWorker 任务。
        返回 (thread, worker) 元组，调用方可通过闭包引用 worker。
        """
        thread = QThread()
        worker = ApiWorker()
        worker.moveToThread(thread)

        thread.started.connect(lambda: worker_fn(worker))
        worker.finished.connect(on_finished)
        if on_error:
            worker.error.connect(on_error)
        else:
            worker.error.connect(lambda msg: logger.error(msg))

        # 安全清理链
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread(thread))

        self._active_threads.append(thread)
        thread.start()
        return thread, worker

    def _cleanup_thread(self, thread):
        """从活跃线程列表中移除已结束的线程"""
        if thread in self._active_threads:
            self._active_threads.remove(thread)

    # ── 双击单行刷新行情 ──

    def _on_market_table_double_click(self, index):
        self._open_market_link(index.row(), index.column())
        return

        """双击大盘表格某行，仅刷新该行饰品的行情数据"""
        row = index.row()
        if row < 0 or row >= len(self._market_tracked_items):
            return

        entry = self._market_tracked_items[row]
        mhn = entry.get("market_hash_name", entry["name"])
        token = self.db.get_config("csqaq_token")
        eco_partner = self.db.get_config("eco_partner_id")
        eco_rsa = self.db.get_config("eco_rsa_key")

        logger.info(f"[大盘] 双击刷新单行: {mhn}")

        # 1. CSQAQ 批量价格查询（仅查这一个）
        if token:
            self._start_worker(
                worker_fn=lambda w: w.batch_price_csqaq(token, [mhn]),
                on_finished=lambda result: self._on_single_row_price_result(row, result),
                on_error=lambda msg: logger.warning(f"CSQAQ 单行查询失败 ({mhn}): {msg}"),
            )

        # 2. ECO 全量行情查询（含起租价）
        if eco_partner and eco_rsa:
            self._start_worker(
                worker_fn=lambda w: w.fetch_eco_hash_price_list(
                    eco_partner, eco_rsa, mhn, entry.get("phase", "")
                ),
                on_finished=lambda result: self._on_single_row_eco_rental_result(row, result),
                on_error=lambda msg: logger.warning(f"ECO 单行行情查询失败 ({mhn}): {msg}"),
            )

        # 3. IGXE 租赁查询
        self._start_worker(
            worker_fn=lambda w: w.search_igxe_product(mhn),
            on_finished=lambda result: self._on_single_row_igxe_search_result(row, result),
            on_error=lambda msg: logger.warning(f"IGXE 单行搜索失败 ({mhn}): {msg}"),
        )

    def _on_single_row_price_result(self, row: int, result):
        """单行 CSQAQ 价格查询回调"""
        tag, data = result
        if tag != "batch_price" or not data.get("success"):
            return
        if row < 0 or row >= len(self._market_tracked_items):
            return
        price_data = data.get("data", {})
        entry = self._market_tracked_items[row]
        mhn = entry.get("market_hash_name", entry["name"])
        prices = price_data.get(mhn, {})
        if prices:
            entry["csqaq_price"] = float(prices.get("min_sell_price", prices.get("buff_price", 0.0)))
            entry.setdefault("detail", {}).update({
                "buff_price": prices.get("buff_price", 0.0),
                "yy_price": prices.get("yy_price", 0.0),
                "steam_price": prices.get("steam_price", 0.0),
                "min_sell_price": prices.get("min_sell_price", 0.0),
                "name_zh": prices.get("name_zh", ""),
            })
        self._populate_market_table()
        self._save_market_cache()

    def _on_single_row_eco_rental_result(self, row: int, result):
        """单行 ECO 租赁查询回调"""
        tag, data = result
        if tag != "eco_rental" or not data.get("success"):
            return
        if row < 0 or row >= len(self._market_tracked_items):
            return
        entry = self._market_tracked_items[row]
        entry["eco_min_rent"] = data.get("min_rent", 0.0)
        entry.setdefault("detail", {}).update({
            "eco_listings": data.get("listings", []),
            "eco_sell_price": data.get("eco_sell_price", 0.0),
            "eco_style_name": data.get("style_name", ""),
            "eco_cache_source": data.get("cache_source", ""),
        })
        self._populate_market_table()
        self._save_market_cache()

    def _on_single_row_igxe_search_result(self, row: int, result):
        """单行 IGXE 搜索回调"""
        tag, data = result
        if tag != "igxe_search" or not data.get("success") or not data.get("results"):
            return
        if row < 0 or row >= len(self._market_tracked_items):
            return
        product_id = data["results"][0].get("product_id")
        if product_id:
            self._start_worker(
                worker_fn=lambda w: w.fetch_igxe_lease(product_id),
                on_finished=lambda result, r=row: self._on_single_row_igxe_lease_result(r, result),
                on_error=lambda msg: logger.warning(f"IGXE 单行租赁查询失败: {msg}"),
            )

    def _on_single_row_igxe_lease_result(self, row: int, result):
        """单行 IGXE 租赁回调"""
        tag, data = result
        if tag != "igxe_lease" or not data.get("success"):
            return
        if row < 0 or row >= len(self._market_tracked_items):
            return
        entry = self._market_tracked_items[row]
        entry["igxe_min_rent"] = data.get("min_rent", 0.0)
        entry.setdefault("detail", {})["igxe_listings"] = data.get("listings", [])
        self._populate_market_table()
        self._save_market_cache()

    # ═══════════════════════════════════════════
    # 数据加载
    # ═══════════════════════════════════════════

    def _rental_history_for_item(self, item):
        float_key = _rental_float_key(item.get("float_val"))
        if not float_key:
            return []
        orders = [
            order for order in self.db.get_rental_orders()
            if _rental_float_matches(item.get("float_val"), order.get("float_val"))
        ]
        return sorted(orders, key=lambda order: _parse_rental_datetime(order.get("start_time")))

    def _latest_rental_for_item(self, item):
        history = self._rental_history_for_item(item)
        if not history:
            return None, []
        return history[-1], history

    @staticmethod
    def _order_number(value) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _order_rental_days(self, order) -> float:
        stored_days = self._order_number(order.get("rental_days"))
        if stored_days > 0:
            return stored_days
        start = _parse_rental_datetime(order.get("start_time"))
        end = _parse_rental_datetime(order.get("return_time"))
        if end <= start:
            return 0.0
        duration = (end - start).total_seconds() / 86400
        # Legacy C5 rows were saved before ``rental_days`` existed.  Its page
        # only provides timestamps, so render their duration consistently with
        # the new C5 clipboard parser.
        if order.get("platform") == "C5GAME":
            return float(max(0, round(duration)))
        return max(0.0, duration)

    def _order_daily_rent(self, order) -> float:
        stored_daily = self._order_number(order.get("daily_rent"))
        if stored_daily > 0:
            return stored_daily
        days = self._order_rental_days(order)
        gross_income = self._order_number(order.get("income"))
        return gross_income / days if gross_income > 0 and days > 0 else 0.0

    @staticmethod
    def _order_discount_rate(order) -> float:
        """Return an IGXE continuous-rental discount multiplier, if present."""
        if order.get("platform") != "IGXE":
            return 1.0
        raw_text = str(order.get("raw_text", "") or "")
        match = re.search(
            r"(?:连续出租折扣|转租折扣)\s*[：:]?\s*(\d+(?:\.\d+)?)\s*折",
            raw_text,
            flags=re.DOTALL,
        )
        if not match:
            return 1.0
        try:
            return min(1.0, max(0.0, float(match.group(1)) / 10))
        except ValueError:
            return 1.0

    def _rental_end_datetime(self, order) -> datetime:
        """Use rental end, not the later item-return deadline, for alerts/CD."""
        raw_text = str(order.get("raw_text", "") or "")
        if order.get("platform") == "IGXE":
            match = re.search(
                r"租赁到期时间\s*[：:]?\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
                raw_text,
                flags=re.DOTALL,
            )
            if match:
                parsed = _parse_rental_datetime(match.group(1))
                if parsed > datetime.min:
                    return parsed
        if order.get("platform") == "ECOSteam":
            start = _parse_rental_datetime(order.get("start_time"))
            rental_days = self._order_rental_days(order)
            if start > datetime.min and rental_days > 0:
                return start + timedelta(days=rental_days)
        return _parse_rental_datetime(order.get("return_time"))

    def _order_gross_income(self, order) -> float:
        daily_rent = self._order_daily_rent(order)
        rental_days = self._order_rental_days(order)
        if daily_rent > 0 and rental_days > 0:
            return daily_rent * rental_days * self._order_discount_rate(order)
        return self._order_number(order.get("income"))

    def _order_fee_rate(self, order, history) -> float:
        platform_prefix = {
            "C5GAME": "c5",
            "ECOSteam": "eco",
            "IGXE": "igxe",
        }.get(order.get("platform", ""), "")
        if not platform_prefix:
            return 0.0
        fee_kind = "relet" if self._is_relet_order(order, history) else "first"
        try:
            return min(0.999, max(0.0, float(self.db.get_config(f"{platform_prefix}_{fee_kind}_fee") or 0.0)))
        except ValueError:
            return 0.0

    @staticmethod
    def _order_key(order):
        return str(order.get("platform", "")), str(order.get("order_no", ""))

    def _is_relet_order(self, order, history) -> bool:
        """A new order is a relet only when it follows the prior one within CD."""
        order_key = self._order_key(order)
        order_index = next(
            (index for index, candidate in enumerate(history)
             if self._order_key(candidate) == order_key),
            -1,
        )
        if order_index <= 0:
            return False
        previous_order = history[order_index - 1]
        previous_end = self._rental_end_datetime(previous_order)
        current_start = _parse_rental_datetime(order.get("start_time"))
        if previous_end <= datetime.min or current_start <= datetime.min:
            return False
        # A non-transfer rental goes through a seven-day cooldown.  Direct
        # handover can overlap slightly in the two platform timestamps.
        return current_start - previous_end < timedelta(days=7)

    @staticmethod
    def _net_amount(gross_amount, fee_rate) -> float:
        """Multiply currency values in decimal form before rendering/summing them."""
        try:
            gross = Decimal(str(gross_amount))
            fee = Decimal(str(fee_rate))
            return float(gross * (Decimal("1") - fee))
        except (InvalidOperation, TypeError, ValueError):
            return 0.0

    def _order_net_income(self, order, history) -> float:
        net_income = self._net_amount(
            self._order_gross_income(order), self._order_fee_rate(order, history)
        )
        if order.get("platform") == "IGXE":
            # IGXE settles its displayed order amount by truncating to cents.
            return float(Decimal(str(net_income)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
        return net_income

    def _total_net_income(self, history) -> float:
        return sum(self._order_net_income(order, history) for order in history)

    @staticmethod
    def _countdown_text(end_time):
        remaining = end_time - datetime.now()
        if remaining.total_seconds() <= 0:
            return "已到期"
        total_seconds = int(remaining.total_seconds())
        days, remainder = divmod(total_seconds, 24 * 60 * 60)
        hours, remainder = divmod(remainder, 60 * 60)
        minutes, _seconds = divmod(remainder, 60)
        if days:
            return f"剩 {days}天{hours}小时{minutes}分"
        return f"剩 {hours}小时{minutes}分"

    def _rental_status_display(self, latest_order, history, fallback_status):
        """Build a live status cell for the latest order of one physical item."""
        order_status = str(latest_order.get("status", "") or "").strip()
        rental_label = "已转租" if self._is_relet_order(latest_order, history) else "已出租"
        if order_status != "租赁中":
            if order_status in {"已转交", "已完成"}:
                return f"{rental_label} · {order_status}", None
            return order_status or fallback_status, None

        end_time = self._rental_end_datetime(latest_order)
        if end_time <= datetime.min:
            return f"{rental_label} · 租赁到期未知", QColor("#fab387")

        remaining_seconds = (end_time - datetime.now()).total_seconds()
        color = QColor("#f38ba8") if remaining_seconds <= 12 * 60 * 60 else QColor("#a6e3a1")
        return f"{rental_label} · {self._countdown_text(end_time)}", color

    def _update_dashboard_rental_countdowns(self):
        """Update only visible rental countdown cells once a second."""
        for state in self._dashboard_rental_rows.values():
            status_item = self.table.item(state["row"], 6)
            if status_item is None:
                continue
            status_text, status_color = self._rental_status_display(
                state["latest_order"], state["history"], state["fallback_status"]
            )
            status_item.setText(status_text)
            if status_color is not None:
                status_item.setForeground(status_color)

    def show_selected_rental_history(self):
        selected_row = self.table.currentRow()
        if selected_row < 0:
            QMessageBox.information(self, "订单历史", "请先选择一个资产。")
            return
        item_id_cell = self.table.item(selected_row, 0)
        if item_id_cell is None:
            return
        item_id = int(item_id_cell.text())
        item = next((candidate for candidate in self.current_items if candidate["id"] == item_id), None)
        if not item:
            return
        history = self._rental_history_for_item(item)
        if not history:
            QMessageBox.information(self, "订单历史", "该磨损度尚未同步到出租订单。")
            return
        display_history = []
        for order in history:
            display_order = dict(order)
            # Older browser-imported rows predate the structured fields.  Fill
            # their derived values here so the history dialog remains useful.
            display_order["rental_days"] = self._order_rental_days(order)
            display_order["daily_rent"] = self._order_daily_rent(order)
            display_order["net_income"] = self._order_net_income(order, history)
            rental_end = self._rental_end_datetime(order)
            if rental_end > datetime.min:
                display_order["return_time"] = rental_end.strftime("%Y-%m-%d %H:%M:%S")
            display_history.append(display_order)
        RentalHistoryDialog(item["name"], item.get("float_val", ""), display_history, self).exec()

    def load_data(self):
        """Render assets using the newest imported order for each float value."""
        self.current_items = self.db.get_all_items()
        filter_platform = self.filter_box.currentText()
        self.table.setRowCount(0)
        self._dashboard_rental_rows = {}
        total_cost = 0.0
        daily_rent_total = 0.0
        rented_count = 0

        row = 0
        for item in self.current_items:
            if filter_platform != "全部平台" and item["platform"] != filter_platform:
                continue
            total_cost += float(item.get("cost", 0.0) or 0.0)
            latest_order, history = self._latest_rental_for_item(item)
            status_text = item.get("status", "在库")
            status_color = None
            rental_days = float(item.get("days", 0.0) or 0.0)
            daily_rent = float(item.get("rent", 0.0) or 0.0)
            net_daily_rent = daily_rent
            net_income = float(item.get("income", 0.0) or 0.0)
            total_net_income = net_income

            if latest_order:
                end = self._rental_end_datetime(latest_order)
                rental_days = self._order_rental_days(latest_order)
                daily_rent = self._order_daily_rent(latest_order)
                fee_rate = self._order_fee_rate(latest_order, history)
                net_daily_rent = self._net_amount(
                    daily_rent * self._order_discount_rate(latest_order), fee_rate
                )
                net_income = self._order_net_income(latest_order, history)
                total_net_income = self._total_net_income(history)
                order_status = latest_order.get("status", "")
                status_text, status_color = self._rental_status_display(
                    latest_order, history, status_text
                )
                if order_status == "租赁中":
                    if end <= datetime.min or end > datetime.now():
                        rented_count += 1
                        daily_rent_total += net_daily_rent

            self.table.insertRow(row)
            values = [
                str(item["id"]), item["name"], item.get("phase", "-"), item.get("float_val", ""),
                f"¥ {float(item.get('cost', 0.0) or 0.0):.2f}", item.get("platform", ""), status_text,
                f"{rental_days:g} 天" if rental_days > 0 else "—",
                _money_text(net_daily_rent) if net_daily_rent > 0 else "—",
                _money_text(net_income) if net_income > 0 else "—",
                _money_text(total_net_income) if total_net_income > 0 else "—",
            ]
            for column, value in enumerate(values):
                table_item = QTableWidgetItem(str(value))
                if column == 6 and status_color:
                    table_item.setForeground(status_color)
                self.table.setItem(row, column, table_item)
            if latest_order and str(latest_order.get("status", "") or "").strip() == "租赁中":
                self._dashboard_rental_rows[item["id"]] = {
                    "row": row,
                    "latest_order": latest_order,
                    "history": history,
                    "fallback_status": item.get("status", "在库"),
                }
            row += 1

        annual_rate = (daily_rent_total * 365 / total_cost * 100) if total_cost > 0 else 0.0
        self.card_cost.findChildren(QLabel)[1].setText(f"¥ {total_cost:,.2f}")
        self.card_income.findChildren(QLabel)[1].setText(_money_text(daily_rent_total))
        self.card_rented.findChildren(QLabel)[1].setText(f"{rented_count} / {len(self.current_items)} 件")
        self.card_rate.findChildren(QLabel)[1].setText(f"{annual_rate:.2f}%")
        self.lbl_last_update.setText(f"最后更新：{QTime.currentTime().toString('HH:mm:ss')}")
        return

        self.current_items = self.db.get_all_items()
        filter_p = self.filter_box.currentText()

        self.table.setRowCount(0)
        total_cost, daily_rent, rented_count = 0.0, 0.0, 0

        row = 0
        for item in self.current_items:
            if filter_p != "全部平台" and item["platform"] != filter_p:
                continue

            total_cost += item["cost"]
            if item["status"] == "已出租":
                rented_count += 1
                daily_rent += item["rent"]

            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(item["id"])))

            name_item = QTableWidgetItem(item["name"])
            name_item.setToolTip(item["name"])
            self.table.setItem(row, 1, name_item)

            self.table.setItem(row, 2, QTableWidgetItem(item["phase"]))
            self.table.setItem(row, 3, QTableWidgetItem(item["pattern"]))
            self.table.setItem(row, 4, QTableWidgetItem(item["float_val"]))
            self.table.setItem(row, 5, QTableWidgetItem(f"¥ {item['cost']:.2f}"))
            self.table.setItem(row, 6, QTableWidgetItem(item["platform"]))

            status_item = QTableWidgetItem()
            expire_h = item.get("expire_hours", 999.0)
            if item["status"] == "已出租":
                if expire_h <= 12.0:
                    status_item.setText(f"已出租 (⏰ 剩 {expire_h:.1f}h 到期)")
                    status_item.setForeground(QColor("#f38ba8"))
                else:
                    status_item.setText(f"已出租 (剩 {int(expire_h//24)}天)")
                    status_item.setForeground(QColor("#a6e3a1"))
            else:
                status_item.setText(item["status"])

            self.table.setItem(row, 7, status_item)
            self.table.setItem(row, 8, QTableWidgetItem(f"¥ {item['rent']:.2f}"))
            self.table.setItem(row, 9, QTableWidgetItem(str(item["days"])))
            self.table.setItem(row, 10, QTableWidgetItem(f"¥ {item['income']:.2f}"))
            self.table.setItem(row, 11, QTableWidgetItem(item["note"]))
            row += 1

        rate = (daily_rent * 365 / total_cost * 100) if total_cost > 0 else 0.0
        self.card_cost.findChildren(QLabel)[1].setText(f"¥ {total_cost:,.2f}")
        self.card_income.findChildren(QLabel)[1].setText(f"¥ {daily_rent:.2f}")
        self.card_rented.findChildren(QLabel)[1].setText(f"{rented_count} / {len(self.current_items)} 件")
        self.card_rate.findChildren(QLabel)[1].setText(f"{rate:.2f}%")
        self.lbl_last_update.setText(f"最后更新: {QTime.currentTime().toString('HH:mm:ss')}")

    # ═══════════════════════════════════════════
    # 饰品 CRUD
    # ═══════════════════════════════════════════

    def edit_selected_item(self):
        selected_row = self.table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "提示", "请先选择要修改的饰品！")
            return

        item_id = int(self.table.item(selected_row, 0).text())
        target_item = next((i for i in self.current_items if i["id"] == item_id), None)

        if target_item:
            dialog = ItemEditDialog(target_item, self)
            if dialog.exec() == QDialog.Accepted:
                new_data = dialog.get_data()
                self.db.update_item(item_id, new_data)
                self.load_data()
                QMessageBox.information(self, "成功", "饰品修改成功，已自动写回 items.json！")

    def add_item(self):
        dialog = ItemEditDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            new_data = dialog.get_data()
            self.db.add_item(new_data)
            self.load_data()
            QMessageBox.information(self, "成功", "新增饰品成功，已自动写回 items.json！")

    def delete_selected_item(self):
        selected_row = self.table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "提示", "请先选择要删除的饰品！")
            return

        item_id = int(self.table.item(selected_row, 0).text())
        item_name = self.table.item(selected_row, 1).text()

        confirm = QMessageBox.question(
            self, "确认删除",
            f"确定要删除【{item_name}】吗？\n该操作会同步从 items.json 中移除此记录！",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            self.db.delete_item(item_id)
            self.load_data()
            QMessageBox.information(self, "成功", "饰品已删除！")

    # ═══════════════════════════════════════════
    # 自动刷新
    # ═══════════════════════════════════════════

    def on_auto_refresh(self):
        """定时自动刷新：仅重新加载本地数据，不自动刷新大盘行情（需手动触发）"""
        logger.info("自动刷新: 重新加载数据")
        self.load_data()
        # 大盘行情不再自动刷新，仅保留本地数据刷新

    # ═══════════════════════════════════════════
    # 设置保存
    # ═══════════════════════════════════════════

    def save_settings(self):
        partner_id = self.cfg_partner.text().strip()
        rsa_key = self.cfg_rsa.text().strip()
        csqaq_token = self.cfg_csqaq.text().strip()

        if csqaq_token and len(csqaq_token) < 8:
            QMessageBox.warning(self, "校验提示", "CSQAQ ApiToken 格式似乎不正确，请确认。")

        self.db.save_config("csqaq_token", csqaq_token)
        self.db.save_config("eco_partner_id", partner_id)
        self.db.save_config("eco_rsa_key", rsa_key)

        for config_key, field in self.cfg_fee_inputs.items():
            try:
                fee_rate = float(field.text().strip())
            except ValueError:
                QMessageBox.warning(self, "费率校验", f"{config_key} 必须是 0 到 1 之间的小数，例如 0.15。")
                return
            if not 0 <= fee_rate < 1:
                QMessageBox.warning(self, "费率校验", f"{config_key} 必须介于 0 和 1 之间。")
                return
            self.db.save_config(config_key, f"{fee_rate:g}")

        int_val_map = {0: "0", 1: "5", 2: "15", 3: "30", 4: "60"}
        self.db.save_config("refresh_interval", int_val_map[self.cfg_interval.currentIndex()])

        self.update_timer_interval()
        self.load_data()
        QMessageBox.information(self, "成功", "系统与 API 设置已成功保存在本地 DB！")

    def update_timer_interval(self):
        cur_int = self.db.get_config("refresh_interval") or "15"
        mins = int(cur_int)
        if mins > 0:
            self.timer.start(mins * 60 * 1000)
        else:
            self.timer.stop()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = CS2ManagerApp()
    win.show()
    sys.exit(app.exec())
