import json
import os
import sys
import logging
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView,
    QTabWidget, QFormLayout, QGroupBox, QMessageBox,
    QAbstractItemView, QDialog, QSplitter, QScrollArea,
    QCheckBox, QFrame,
)
from PySide6.QtCore import Qt, QTimer, QThread, QUrl, QTime
from PySide6.QtGui import QFont, QColor, QPixmap, QIcon, QPainter, QLinearGradient, QBrush

from modules.db_manager import DBManager
from modules.workers import ApiWorker, MarketRefreshWorker, C5RentalWorker
from modules.logger import logger
from modules.image_cache import ImageCache, MarketCache
from modules.cs2_item_schema import CS2ItemSchema


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
        self.note_in = QLineEdit(self.item_data.get("note", ""))

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
        layout.addRow("备注信息:", self.note_in)

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
            "note": self.note_in.text().strip()
        }


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
        self._c5_thread = None
        self._c5_worker = None

        # 当前市场详情页选中的物品标识 (name|phase)
        self._current_market_item_key = ""
        # 市场页数据列表: [{name, phase, market_hash_name, ...}]
        self._market_tracked_items = []

        self.apply_theme()
        self.init_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_auto_refresh)
        self.update_timer_interval()

        self.load_data()

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

        self.tab_orders = QWidget()
        self.init_orders_tab()
        self.tabs.addTab(self.tab_orders, "📋 出租订单")

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
        self.card_rate = self.create_card("📈 估算年化率", "0.0%", "#f38ba8")
        for c in [self.card_cost, self.card_income, self.card_rented, self.card_rate]:
            card_layout.addWidget(c)
        layout.addLayout(card_layout)

        platform_sync = QGroupBox("平台订单同步（仅手动读取）")
        platform_layout = QHBoxLayout(platform_sync)
        self.c5_login_btn = QPushButton("C5 登录")
        self.c5_login_btn.setObjectName("primaryBtn")
        self.c5_login_btn.clicked.connect(lambda: self._run_c5_task("login"))
        self.c5_sync_btn = QPushButton("同步 C5 出租订单")
        self.c5_sync_btn.setObjectName("successBtn")
        self.c5_sync_btn.clicked.connect(lambda: self._run_c5_task("sync"))
        eco_login_btn = QPushButton("ECO 登录（待接入）")
        eco_login_btn.setEnabled(False)
        igxe_login_btn = QPushButton("IGXE 登录（待接入）")
        igxe_login_btn.setEnabled(False)
        self.c5_sync_status = QLabel("C5：尚未同步。登录、验证码与刷新均需你手动触发。")
        self.c5_sync_status.setStyleSheet("color: #a6adc8;")
        platform_layout.addWidget(self.c5_login_btn)
        platform_layout.addWidget(self.c5_sync_btn)
        platform_layout.addWidget(eco_login_btn)
        platform_layout.addWidget(igxe_login_btn)
        platform_layout.addWidget(self.c5_sync_status, 1)
        layout.addWidget(platform_sync)

        # ── 工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        add_btn = QPushButton("➕ 新增饰品")
        add_btn.clicked.connect(self.add_item)

        edit_btn = QPushButton("✏️ 修改选中")
        edit_btn.setObjectName("primaryBtn")
        edit_btn.clicked.connect(self.edit_selected_item)

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
        toolbar.addWidget(del_btn)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("筛选:"))
        toolbar.addWidget(self.filter_box)
        layout.addLayout(toolbar)

        # ── 饰品表格 ──
        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels([
            "ID", "饰品名称", "相位", "模板", "磨损度", "成本(元)",
            "平台", "状态/倒计时", "日租金", "已租天数", "累计收益", "备注"
        ])

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(11, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("alternate-background-color: #1e1e2e;")
        self.table.doubleClicked.connect(self.edit_selected_item)

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

    def _set_c5_controls_enabled(self, enabled):
        self.c5_login_btn.setEnabled(enabled)
        self.c5_sync_btn.setEnabled(enabled)

    def _run_c5_task(self, task):
        if self._c5_thread and self._c5_thread.isRunning():
            QMessageBox.information(self, "C5", "C5 浏览器任务正在运行，请先完成或关闭浏览器窗口。")
            return

        self._set_c5_controls_enabled(False)
        if task == "login":
            self.c5_sync_status.setText("C5 浏览器已打开：请手动登录、完成验证码，然后关闭浏览器窗口。")
        else:
            self.c5_sync_status.setText("正在读取 C5 出租订单（本次手动请求）…")

        self._c5_thread = QThread()
        self._c5_worker = C5RentalWorker()
        self._c5_worker.moveToThread(self._c5_thread)
        self._c5_thread.started.connect(
            self._c5_worker.open_login if task == "login" else self._c5_worker.sync_orders
        )
        self._c5_worker.finished.connect(self._on_c5_task_finished)
        self._c5_worker.error.connect(self._on_c5_task_error)
        self._c5_worker.finished.connect(self._c5_thread.quit)
        self._c5_worker.error.connect(self._c5_thread.quit)
        self._c5_worker.finished.connect(self._c5_worker.deleteLater)
        self._c5_worker.error.connect(self._c5_worker.deleteLater)
        self._c5_thread.finished.connect(self._c5_thread.deleteLater)
        self._c5_thread.finished.connect(self._cleanup_c5_task)
        self._c5_thread.start()

    def _on_c5_task_finished(self, payload):
        task, result = payload
        if task == "login":
            self.c5_sync_status.setText("C5 登录窗口已关闭；现在可以点击“同步 C5 出租订单”。")
            return

        if not result.get("success"):
            self.c5_sync_status.setText(f"C5 同步失败：{result.get('error', '未知错误')}")
            return
        if result.get("needs_login"):
            self.c5_sync_status.setText("C5 登录状态无效：请先点击“C5 登录”并在窗口中完成登录。")
            return

        orders = result.get("orders", [])
        self.db.upsert_rental_orders("C5GAME", orders)
        self._refresh_orders_table()
        snapshot_path = result.get("snapshot_path", "")
        self.c5_sync_status.setText(f"C5 已同步 {len(orders)} 条订单；原始页面快照已私有保存。")
        logger.info("[C5] 手动同步 %s 条订单，页面快照：%s", len(orders), snapshot_path)
        if not orders:
            QMessageBox.information(
                self,
                "C5 同步完成",
                "页面已读取，但未解析到订单。请保留登录状态，并把日志中的私有页面快照告知我以校准解析规则。",
            )

    def _on_c5_task_error(self, message):
        logger.warning("[C5] %s", message)
        self.c5_sync_status.setText(message)
        QMessageBox.warning(self, "C5", message)

    def _cleanup_c5_task(self):
        self._c5_thread = None
        self._c5_worker = None
        self._set_c5_controls_enabled(True)

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

        refresh_market_btn = QPushButton("🔄 刷新行情（用缓存）")
        refresh_market_btn.setObjectName("successBtn")
        refresh_market_btn.clicked.connect(self._refresh_all_market_data)
        search_layout.addWidget(refresh_market_btn)

        force_eco_btn = QPushButton("☁️ 强制更新 ECO")
        force_eco_btn.setObjectName("primaryBtn")
        force_eco_btn.setToolTip("忽略本地 ECO 缓存并重新下载完整行情快照")
        force_eco_btn.clicked.connect(lambda: self._refresh_all_market_data(force_eco=True))
        search_layout.addWidget(force_eco_btn)

        layout.addLayout(search_layout)

        # ── 一览式大盘表格 ──
        # 列: 图片(48x48) | 饰品名称+Phase | CSQAQ最低售价 | ECO最低日租 | IGXE最低日租 | 更新时间 | 展开明细
        self.market_table = QTableWidget()
        self.market_table.setColumnCount(7)
        self.market_table.setHorizontalHeaderLabels([
            "图片", "饰品名称 / Phase", "CSQAQ 最低售价",
            "ECO 最低日租", "IGXE 最低日租", "更新时间", "操作"
        ])
        hdr = self.market_table.horizontalHeader()
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

    def _populate_market_table(self):
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
                "eco_min_rent": entry.get("eco_min_rent", 0.0),
                "igxe_min_rent": entry.get("igxe_min_rent", 0.0),
                "updated_at": QTime.currentTime().toString("HH:mm:ss"),
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
                "eco_min_rent": 0.0,
                "igxe_min_rent": 0.0,
                "updated_at": QTime.currentTime().toString("HH:mm:ss"),
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

    def load_data(self):
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

        int_val_map = {0: "0", 1: "5", 2: "15", 3: "30", 4: "60"}
        self.db.save_config("refresh_interval", int_val_map[self.cfg_interval.currentIndex()])

        self.update_timer_interval()
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
