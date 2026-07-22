import json
import os
import sys
import signal
import time
import re
import copy
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from datetime import datetime, timedelta
from urllib.parse import quote
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QHeaderView,
    QFormLayout, QGroupBox, QMessageBox,
    QAbstractItemView, QDialog, QScrollArea,
    QCheckBox, QFrame, QPlainTextEdit,
    QMenu, QInputDialog, QFileDialog, QStackedWidget, QToolButton, QSizePolicy,
    QGridLayout,
)
from PySide6.QtCore import (
    Qt, QTimer, QThread, QUrl, QTime, QSize, QByteArray, QEvent, QLocale,
    QSettings,
)
from PySide6.QtGui import (
    QFont, QColor, QPixmap, QIcon, QPainter, QDesktopServices, QDoubleValidator, QIntValidator, QShortcut, QKeySequence,
)
from PySide6.QtSvg import QSvgRenderer

from modules.db_manager import DBManager
from modules.workers import (
    ApiWorker,
    ApiWorkerCallbackRelay,
    MarketRefreshWorker,
    csfloat_cny_display_price,
    csfloat_quote_is_fresh,
)
from modules.logger import logger
from modules.image_cache import ImageCache, MarketCache
from modules.cs2_item_schema import CS2ItemSchema, phase_hint_from_search
from modules.rental_order_parsers import parse_rental_clipboard
from modules.domain_models import InventoryItemDraft
from modules.rental_matching import match_order_to_items
from modules.csfloat_buy_analysis import (
    analyze_csfloat_buy_order as _csfloat_buy_order_analysis,
)
from modules.dashboard_service import (
    RENTAL_RELET_WINDOW,
    adjust_cost_by_percent as _adjust_cost_by_percent,
    build_dashboard_rental_history_index as _build_rental_history_index,
    is_non_earning_rental_status as _is_non_earning_rental_status,
    money_text as _money_text,
    parse_rental_datetime as _parse_rental_datetime,
    platform_rent_benchmark as _platform_rent_benchmark,
    price_gap as _price_gap,
    rental_lifecycle_state as _rental_lifecycle_state,
    rental_term as _rental_term,
    sort_dashboard_records as _sort_dashboard_records,
)
from modules.ui_theme import APP_QSS
from modules.version import __version__
from modules.csfloat_client import CSFloatClient
from modules.exchange_rate_client import ExchangeRateClient
from modules.cloud_sync import (
    SYNC_FILENAME,
    export_sync_bundle,
    get_sync_directory,
    get_sync_inbox_directory,
    get_sync_outbox_directory,
    import_sync_bundle,
    load_sync_bundle,
)


ORDER_PAGE_URLS = {
    "c5": ("C5", "https://www.c5game.com/user/rent?actag=2"),
    "eco": ("ECO", "https://www.ecosteam.cn/html/person/rentrecordlist.html"),
    "igxe": ("IGXE", "https://www.igxe.cn/lease/seller-order-list"),
}


# Detailed market requests rotate in the background so a large watch list
# never stalls behind one monolithic refresh.
MARKET_ROLLING_REFRESH_SECONDS = 12
CSFLOAT_BUY_DETAIL_LIMIT = 1
CSFLOAT_BUY_AUTO_REFRESH_SECONDS = 10 * 60


def _parse_cooldown_datetime(value) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return datetime.min


AI_MARKET_IMPORT_PROMPT = """你是 CS2 饰品数据整理助手。请根据我提供的一张或多张库存截图，提取其中可监测的 CS2 饰品；忽略价格、涨跌、数量、库存标签、磨损、图案模板和购买成本。

严格只返回一个合法 JSON 对象，不要 Markdown、不写解释：
{
  "items": [
    {
      "name": "中文完整饰品名，例如：折叠刀（★） | 多普勒（崭新出厂）",
      "phase": "P1、P2、P3、P4、Ruby、Sapphire、Emerald 或 -",
      "market_hash_name": "英文 Steam Market Hash Name，例如：★ Flip Knife | Doppler (Factory New)"
    }
  ]
}

规则：
1. 每种“饰品名 + 相位”只输出一条；P1 和 P3 可以分别输出，软件会自动合并为一条行情观察项。
2. phase 只填写截图明确显示的相位/宝石；没有明确相位时填写 "-"，不要猜测。
3. market_hash_name 必须是 Steam 英文完整名称；多普勒的相位不写进 market_hash_name。
4. name 不要附加价格、磨损、库存数量或备注。
5. 看不清或无法确定的条目不要输出。"""


AI_ASSET_IMPORT_PROMPT = """你是 CS2 库存录入助手。请根据我提供的一张或多张库存/购买记录截图，整理每一件真实饰品。不要合并磨损不同的同名饰品。

严格只返回一个合法 JSON 对象，不要 Markdown、不要解释：
{
  "items": [
    {
      "name": "中文完整饰品名",
      "market_hash_name": "英文 Steam Market Hash Name",
      "phase": "P1、P2、P3、P4、Ruby、Sapphire、Emerald、Black Pearl 或 -",
      "pattern": "图案模板；看不清填 -",
      "float_val": "完整磨损值，例如 0.123456789",
      "cost": 1234.56,
      "platform": "C5GAME、ECOSteam、悠悠有品、IGXE 或 BUFF",
      "status": "在库 或 CD冷却",
      "cooldown_hours": 0,
      "note": "可选备注"
    }
  ]
}

规则：
1. name 必须使用中文；market_hash_name 必须是对应的 Steam 英文完整名称。
2. float_val 和 cost 看不清时不要猜测，省略该条。
3. 刚买入且仍在交易冷却的饰品填写 status="CD冷却"，cooldown_hours 填截图显示的剩余小时；否则填 0。
4. phase 没有明确显示时填 "-"；不要根据图片颜色猜宝石或相位。
5. 每件实物单独一条，不能因为名称相同而合并。"""


TABLE_SORT_ROLE = Qt.UserRole + 101


class SortAwareTableWidgetItem(QTableWidgetItem):
    """Sort by an optional raw value while preserving a formatted display string."""

    def __lt__(self, other):
        left = self.data(TABLE_SORT_ROLE)
        right = other.data(TABLE_SORT_ROLE) if isinstance(other, QTableWidgetItem) else None
        if left is not None and right is not None:
            try:
                return float(left) < float(right)
            except (TypeError, ValueError):
                return str(left).casefold() < str(right).casefold()
        return super().__lt__(other)


def parse_ai_market_items(text: str) -> tuple[list[dict], list[str]]:
    """Parse the deliberately small JSON contract used by the AI market helper."""
    payload_text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", payload_text, flags=re.IGNORECASE)
    if fenced:
        payload_text = fenced.group(1).strip()
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return [], ["AI 返回内容不是合法 JSON。请只粘贴 JSON 对象或数组。"]

    raw_items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return [], ["JSON 顶层必须是 {\"items\": [...]} 或直接的数组。"]

    items: list[dict] = []
    errors: list[str] = []
    for index, value in enumerate(raw_items, start=1):
        if not isinstance(value, dict):
            errors.append(f"第 {index} 条不是对象，已忽略。")
            continue
        items.append(value)
    if not items and not errors:
        errors.append("没有读取到任何饰品。")
    return items, errors


class ItemEditDialog(QDialog):
    """可视化修改与新增饰品弹窗"""

    def __init__(self, item_data=None, parent=None):
        super().__init__(parent)
        self.item_data = item_data or {}
        self.setWindowTitle("修改饰品数据" if item_data else "手动新增饰品")
        self.resize(400, 580)
        self.setStyleSheet(APP_QSS)

        layout = QFormLayout(self)
        self.name_in = QLineEdit(self.item_data.get("name", ""))
        self.name_in.textChanged.connect(self._auto_build_mhn)

        self.mhn_in = QLineEdit(self.item_data.get("market_hash_name", ""))
        self.mhn_in.setPlaceholderText("英文 market_hash_name，如: ★ Bayonet | Doppler (Factory New)")

        self.phase_in = QComboBox()
        self.phase_in.addItems(["-", "P1", "P2", "P3", "P4", "Ruby", "Sapphire", "Emerald", "Black Pearl"])
        self.phase_in.setCurrentText(self.item_data.get("phase", "-"))
        self.phase_in.currentTextChanged.connect(self._auto_build_mhn)

        self.pattern_in = QLineEdit(str(self.item_data.get("pattern", "-")))
        self.float_in = QLineEdit(str(self.item_data.get("float_val", "0.000")))
        self.cost_in = QLineEdit(str(self.item_data.get("cost", "0.00")))

        decimal_locale = QLocale.c()
        float_validator = QDoubleValidator(0.0, 0.999999999999, 12, self)
        float_validator.setNotation(QDoubleValidator.StandardNotation)
        float_validator.setLocale(decimal_locale)
        self.float_in.setValidator(float_validator)
        for field in (self.cost_in,):
            validator = QDoubleValidator(0.0, 1_000_000_000.0, 2, self)
            validator.setNotation(QDoubleValidator.StandardNotation)
            validator.setLocale(decimal_locale)
            field.setValidator(validator)

        self.platform_box = QComboBox()
        self.platform_box.addItems(["BUFF", "C5GAME", "ECOSteam", "悠悠有品", "IGXE"])
        self.platform_box.setCurrentText(self.item_data.get("platform", "C5GAME"))

        self.status_box = QComboBox()
        self.status_box.addItems(["在库", "已出租", "CD冷却"])
        self.status_box.setCurrentText(self.item_data.get("status", "在库"))

        self.rent_in = QLineEdit(str(self.item_data.get("rent", "0.00")))
        self.days_in = QLineEdit(str(self.item_data.get("days", "0")))
        stored_cooldown = _parse_cooldown_datetime(
            self.item_data.get("cooldown_until", "")
        )
        if stored_cooldown > datetime.now():
            remaining_hours = max(
                0.0, (stored_cooldown - datetime.now()).total_seconds() / 3600
            )
            expire_value = f"{remaining_hours:.2f}"
        else:
            expire_value = str(self.item_data.get("expire_hours", "999.0"))
        self.expire_in = QLineEdit(expire_value)
        self._initial_expire_text = expire_value
        self.income_in = QLineEdit(str(self.item_data.get("income", "0.00")))
        self.days_in.setValidator(QIntValidator(0, 1_000_000, self))
        for field, decimals in (
            (self.rent_in, 4),
            (self.income_in, 2),
        ):
            validator = QDoubleValidator(0.0, 1_000_000_000.0, decimals, self)
            validator.setNotation(QDoubleValidator.StandardNotation)
            validator.setLocale(decimal_locale)
            field.setValidator(validator)
        cooldown_validator = QDoubleValidator(0.0, 720.0, 2, self)
        cooldown_validator.setNotation(QDoubleValidator.StandardNotation)
        cooldown_validator.setLocale(decimal_locale)
        self.expire_in.setValidator(cooldown_validator)

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
        layout.addRow("新购 CD 剩余小时:", self.expire_in)
        layout.addRow("累计已收收益 (元):", self.income_in)

        self.validation_label = QLabel("")
        self.validation_label.setObjectName("validationError")
        self.validation_label.setWordWrap(True)
        self.validation_label.setVisible(False)
        layout.addRow(self.validation_label)

        save_btn = QPushButton("保存饰品")
        save_btn.setObjectName("primaryBtn")
        save_btn.clicked.connect(self._accept_if_valid)
        layout.addRow(save_btn)

        for field in (
            self.name_in, self.mhn_in, self.pattern_in, self.float_in,
            self.cost_in, self.rent_in, self.days_in, self.expire_in,
            self.income_in,
        ):
            field.textChanged.connect(self._clear_validation_error)
        self.status_box.currentTextChanged.connect(self._on_item_status_changed)

    def _on_item_status_changed(self, status):
        if status != "CD冷却":
            return
        try:
            hours = float(self.expire_in.text().strip())
        except (TypeError, ValueError):
            hours = 999
        if hours > 720:
            self.expire_in.setText("168")

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
                    "Black Pearl": "Black Pearl",
                }
                phase_en = phase_map.get(phase, phase)
                mhn = f"★ {weapon_en} | {skin_en} ({exterior_en}) - {phase_en}"

            self.mhn_in.setText(mhn)

    def _clear_validation_error(self):
        self.validation_label.clear()
        self.validation_label.setVisible(False)

    def _raw_form_data(self):
        return {
            "name": self.name_in.text().strip(),
            "market_hash_name": self.mhn_in.text().strip(),
            "phase": self.phase_in.currentText(),
            "pattern": self.pattern_in.text().strip() or "-",
            "float_val": self.float_in.text().strip(),
            "cost": self.cost_in.text().strip(),
            "platform": self.platform_box.currentText(),
            "status": self.status_box.currentText(),
            "rent": self.rent_in.text().strip(),
            "days": self.days_in.text().strip(),
            "expire_hours": self.expire_in.text().strip(),
            "income": self.income_in.text().strip(),
            "note": self.item_data.get("note", ""),
            # Preserve the stable platform/asset identity even though it is not
            # directly edited in this dialog.
            "asset_id": self.item_data.get("asset_id", ""),
            "cooldown_until": self.item_data.get("cooldown_until", ""),
        }

    def _validated_data(self):
        record = InventoryItemDraft.from_form(self._raw_form_data()).to_record()
        if record["status"] == "CD冷却":
            if not 0 < record["expire_hours"] <= 720:
                raise ValueError("新购 CD 剩余小时必须大于 0 且不超过 720")
            existing_until = _parse_cooldown_datetime(
                self.item_data.get("cooldown_until", "")
            )
            if (
                existing_until > datetime.now()
                and self.item_data.get("status") == "CD冷却"
                and self.expire_in.text().strip() == self._initial_expire_text
            ):
                record["cooldown_until"] = existing_until.isoformat(
                    timespec="seconds"
                )
            else:
                record["cooldown_until"] = (
                    datetime.now() + timedelta(hours=record["expire_hours"])
                ).isoformat(timespec="seconds")
        else:
            record["cooldown_until"] = ""
        return record

    def _accept_if_valid(self):
        try:
            self._validated_data()
        except ValueError as exc:
            self.validation_label.setText(str(exc))
            self.validation_label.setVisible(True)
            return
        self.accept()

    def get_data(self):
        """Return the same validated record that allowed the dialog to close."""
        return self._validated_data()


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
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "平台", "状态", "出租时间", "租赁到期", "租期", "日租（原价）", "订单金额", "转租奖励（成本）", "净收入",
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
                _money_text(income),
                _money_text(order.get("transfer_reward", 0.0) or 0.0)
                if order.get("transfer_reward_known") else "—",
                _money_text(order.get("net_income", income) or 0.0),
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
                f"转租奖励：{_money_text(order.get('transfer_reward', 0.0) or 0.0)}"
                f"（{order.get('reward_status', '未读取')}）"
                if order.get("transfer_reward_known") else "转租奖励：未从 C5 订单详情读取",
                f"净收入：{_money_text(order.get('net_income', order.get('income', 0.0)) or 0.0)}",
            ]),
        )


class RentalImportPreviewDialog(QDialog):
    """Show parsed clipboard orders before the user allows a database write."""

    def __init__(self, platform, orders, items=None, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.orders = orders
        self.items = list(items or [])
        self.setWindowTitle("确认导入出租订单")
        self.resize(1240, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"已识别为 {platform}，共解析到 {len(orders)} 条订单。请核对后再确认写入本地记录。"
        ))

        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels([
            "平台", "订单号", "饰品", "磨损", "出租时间", "归还时间",
            "租期", "日租（原价）", "订单金额", "转租奖励（成本）", "状态", "关联资产",
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("alternate-background-color: #1e1e2e;")
        header = self.table.horizontalHeader()
        header.setFont(QFont("Microsoft YaHei", 11, QFont.DemiBold))
        self.table.setFont(QFont("Microsoft YaHei", 11))
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for column in (0, 1, 3, 4, 5, 6, 7, 8, 9, 10):
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
                _money_text(income),
                _money_text(order.get("transfer_reward", 0.0) or 0.0)
                if order.get("transfer_reward_known") else "—",
                order.get("status", "") or "—",
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
            association = match_order_to_items(order, self.items)
            if association["item_id"] is not None:
                order["item_id"] = association["item_id"]
                order["match_method"] = association["method"]
                order["match_confidence"] = association["confidence"]
            selector = QComboBox()
            selector.addItem("暂不关联", None)
            selected_index = 0
            for asset in self.items:
                label = f"{asset.get('name', '未命名')} · {asset.get('float_val', '')}"
                selector.addItem(label, asset.get("id"))
                if asset.get("id") == association["item_id"]:
                    selected_index = selector.count() - 1
            selector.setCurrentIndex(selected_index)
            method_label = {
                "asset_id": "Asset ID 唯一匹配",
                "exact_float": "磨损精确匹配",
                "fuzzy_float": "磨损截断匹配，请核对",
                "ambiguous_float": "存在多个候选，请手动选择",
                "unmatched": "未自动匹配",
            }.get(association["method"], association["method"])
            selector.setToolTip(method_label)
            selector.currentIndexChanged.connect(
                lambda _index, target=order, field=selector: self._set_order_asset(target, field)
            )
            self.table.setCellWidget(row, 11, selector)
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

    @staticmethod
    def _set_order_asset(order, selector):
        item_id = selector.currentData()
        if item_id is None:
            order.pop("item_id", None)
            order["match_method"] = "unmatched"
            order["match_confidence"] = 0.0
            return
        order["item_id"] = int(item_id)
        order["match_method"] = "manual"
        order["match_confidence"] = 1.0


class MarketAIImportDialog(QDialog):
    """A local paste-in workflow for vision-AI assisted market watch imports."""

    def __init__(self, normalize_item, parent=None):
        super().__init__(parent)
        self._normalize_item = normalize_item
        self.validated_items: list[dict] = []
        self.setWindowTitle("AI 辅助批量添加大盘饰品")
        self.resize(1060, 720)

        layout = QVBoxLayout(self)
        instruction = QLabel(
            "图片不会上传到本软件：复制提示词 → 连同截图发给任意可识图 AI → 将它返回的 JSON 粘贴到下方。"
        )
        instruction.setWordWrap(True)
        instruction.setStyleSheet("color: #a6adc8;")
        layout.addWidget(instruction)

        prompt_group = QGroupBox("1. 复制给 AI 的提示词")
        prompt_layout = QVBoxLayout(prompt_group)
        self.prompt_edit = QPlainTextEdit(AI_MARKET_IMPORT_PROMPT)
        self.prompt_edit.setReadOnly(True)
        self.prompt_edit.setMaximumHeight(195)
        prompt_layout.addWidget(self.prompt_edit)
        copy_prompt_button = QPushButton("📋 复制提示词")
        copy_prompt_button.setObjectName("primaryBtn")
        copy_prompt_button.clicked.connect(self._copy_prompt)
        prompt_layout.addWidget(copy_prompt_button)
        layout.addWidget(prompt_group)

        response_group = QGroupBox("2. 粘贴 AI 返回的 JSON")
        response_layout = QVBoxLayout(response_group)
        self.response_edit = QPlainTextEdit()
        self.response_edit.setPlaceholderText('{"items": [{"name": "…", "phase": "P1", "market_hash_name": "…"}]}')
        self.response_edit.setMinimumHeight(120)
        response_layout.addWidget(self.response_edit)
        parse_button = QPushButton("🔎 解析并预览")
        parse_button.clicked.connect(self._parse_response)
        response_layout.addWidget(parse_button)
        layout.addWidget(response_group)

        preview_group = QGroupBox("3. 导入预览（仅“可导入”项会被写入本地行情观察列表）")
        preview_layout = QVBoxLayout(preview_group)
        self.status_label = QLabel("尚未解析。")
        self.status_label.setWordWrap(True)
        preview_layout.addWidget(self.status_label)
        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(4)
        self.preview_table.setHorizontalHeaderLabels(["中文名称", "相位", "Steam MarketHashName", "检查结果"])
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setWordWrap(False)
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        preview_layout.addWidget(self.preview_table)
        layout.addWidget(preview_group, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        self.confirm_button = QPushButton("✅ 确认批量添加")
        self.confirm_button.setObjectName("successBtn")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

    def _copy_prompt(self):
        QApplication.clipboard().setText(AI_MARKET_IMPORT_PROMPT)
        self.status_label.setText("提示词已复制；现在可将截图与提示词一起发送给 AI。")

    def _parse_response(self):
        self.preview_table.setRowCount(0)
        self.validated_items = []
        raw_items, errors = parse_ai_market_items(self.response_edit.toPlainText())
        for index, raw_item in enumerate(raw_items, start=1):
            entry, message = self._normalize_item(raw_item)
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            values = [
                str(raw_item.get("name", "")),
                str(raw_item.get("phase", "-")),
                str(raw_item.get("market_hash_name", raw_item.get("mhn", ""))),
                message,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if entry is None and column == 3:
                    item.setForeground(QColor("#f38ba8"))
                self.preview_table.setItem(row, column, item)
            if entry is not None:
                self.validated_items.append(entry)
            else:
                errors.append(f"第 {index} 条：{message}")

        self.confirm_button.setEnabled(bool(self.validated_items))
        if self.validated_items:
            suffix = f"；{len(errors)} 条需要修正" if errors else ""
            self.status_label.setText(f"已确认 {len(self.validated_items)} 条可导入{suffix}。")
        else:
            self.status_label.setText("没有可导入的饰品。" + (" " + "；".join(errors[:3]) if errors else ""))


class AssetAIImportDialog(QDialog):
    """Paste and validate AI-extracted inventory rows before one-click import."""

    def __init__(self, normalize_item, parent=None):
        super().__init__(parent)
        self._normalize_item = normalize_item
        self.validated_items: list[dict] = []
        self.setWindowTitle("AI 辅助批量导入资产")
        self.resize(1180, 740)

        layout = QVBoxLayout(self)
        instruction = QLabel(
            "软件不会上传截图：复制提示词，与库存截图一起交给可识图 AI，再把返回的 JSON 粘贴到这里。"
        )
        instruction.setWordWrap(True)
        instruction.setStyleSheet("color: #a6adc8;")
        layout.addWidget(instruction)

        prompt_group = QGroupBox("1. 复制资产识别提示词")
        prompt_layout = QVBoxLayout(prompt_group)
        prompt_edit = QPlainTextEdit(AI_ASSET_IMPORT_PROMPT)
        prompt_edit.setReadOnly(True)
        prompt_edit.setMaximumHeight(210)
        prompt_layout.addWidget(prompt_edit)
        copy_button = QPushButton("📋 复制提示词")
        copy_button.setObjectName("primaryBtn")
        copy_button.clicked.connect(self._copy_prompt)
        prompt_layout.addWidget(copy_button)
        layout.addWidget(prompt_group)

        response_group = QGroupBox("2. 粘贴 AI 返回的 JSON")
        response_layout = QVBoxLayout(response_group)
        self.response_edit = QPlainTextEdit()
        self.response_edit.setPlaceholderText(
            '{"items": [{"name": "…", "float_val": "0.…", "cost": 0, "status": "在库"}]}'
        )
        response_layout.addWidget(self.response_edit)
        parse_button = QPushButton("🔎 解析并预览")
        parse_button.clicked.connect(self._parse_response)
        response_layout.addWidget(parse_button)
        layout.addWidget(response_group)

        self.status_label = QLabel("尚未解析。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(9)
        self.preview_table.setHorizontalHeaderLabels([
            "中文名称", "相位", "磨损", "成本", "平台", "状态",
            "CD 小时", "英文 MarketHashName", "检查结果",
        ])
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        preview_header = self.preview_table.horizontalHeader()
        for column in range(7):
            preview_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        preview_header.setSectionResizeMode(7, QHeaderView.Stretch)
        preview_header.setSectionResizeMode(8, QHeaderView.ResizeToContents)
        layout.addWidget(self.preview_table, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.confirm_button = QPushButton("✅ 一键导入资产")
        self.confirm_button.setObjectName("successBtn")
        self.confirm_button.setEnabled(False)
        self.confirm_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)

    def _copy_prompt(self):
        QApplication.clipboard().setText(AI_ASSET_IMPORT_PROMPT)
        self.status_label.setText("资产提示词已复制。")

    def _parse_response(self):
        self.preview_table.setRowCount(0)
        self.validated_items = []
        raw_items, errors = parse_ai_market_items(self.response_edit.toPlainText())
        for index, raw_item in enumerate(raw_items, start=1):
            entry, message = self._normalize_item(raw_item)
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            values = [
                raw_item.get("name", ""), raw_item.get("phase", "-"),
                raw_item.get("float_val", ""), raw_item.get("cost", ""),
                raw_item.get("platform", ""), raw_item.get("status", "在库"),
                raw_item.get("cooldown_hours", 0),
                raw_item.get("market_hash_name", raw_item.get("mhn", "")), message,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if entry is None and column == 8:
                    cell.setForeground(QColor("#f38ba8"))
                self.preview_table.setItem(row, column, cell)
            if entry is None:
                errors.append(f"第 {index} 条：{message}")
            else:
                self.validated_items.append(entry)

        self.confirm_button.setEnabled(bool(self.validated_items))
        if self.validated_items:
            suffix = f"；{len(errors)} 条需要修正" if errors else ""
            self.status_label.setText(
                f"已确认 {len(self.validated_items)} 件可导入{suffix}。确认后才会写入资产库。"
            )
        else:
            self.status_label.setText(
                "没有可导入的资产。" + (" " + "；".join(errors[:3]) if errors else "")
            )


class MarketItemSearchDialog(QDialog):
    """Local schema search results; users choose before a watch item is created."""

    PHASE_LABELS = {
        "Ruby": "红宝石 (Ruby)",
        "Sapphire": "蓝宝石 (Sapphire)",
        "Emerald": "绿宝石 (Emerald)",
        "Black Pearl": "黑珍珠 (Black Pearl)",
        "P1": "P1 (Phase 1)",
        "P2": "P2 (Phase 2)",
        "P3": "P3 (Phase 3)",
        "P4": "P4 (Phase 4)",
    }

    def __init__(self, query, records, parent=None):
        super().__init__(parent)
        self.records = records
        self.setWindowTitle("选择要添加的饰品")
        self.resize(950, 560)

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"“{query}”匹配到 {len(records)} 个本地饰品。请选择要加入当前观察分类的条目。\n"
            "多普勒不同相位会共用 Steam 名称，请以“相位”和“模板 ID”为准。"
        )
        summary.setStyleSheet("color: #a6adc8;")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["饰品名称（含相位）", "相位", "Steam Market Hash Name", "磨损", "模板 ID"])
        self.table.setRowCount(len(records))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        for row, record in enumerate(records):
            phase = str(record.get("phase") or "-")
            phase_label = self.PHASE_LABELS.get(phase, phase)
            display_name = record.get("name_zh", "")
            if phase != "-":
                display_name = f"{display_name} · {phase_label}"
            name_item = QTableWidgetItem(display_name)
            name_item.setData(Qt.UserRole, row)
            self.table.setItem(row, 0, name_item)
            phase_item = QTableWidgetItem(phase_label)
            phase_item.setForeground(QColor("#f9e2af") if phase != "-" else QColor("#6c7086"))
            self.table.setItem(row, 1, phase_item)
            self.table.setItem(row, 2, QTableWidgetItem(record.get("market_hash_name", "")))
            self.table.setItem(row, 3, QTableWidgetItem(record.get("wear_zh", "")))
            self.table.setItem(row, 4, QTableWidgetItem(str(record.get("paint_index", ""))))
        if records:
            self.table.selectRow(0)
        self.table.doubleClicked.connect(self.accept)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("添加选中饰品")
        confirm.setObjectName("primaryBtn")
        confirm.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)

    def selected_records(self):
        selected_rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.records[row] for row in selected_rows if 0 <= row < len(self.records)]


_LINE_ICON_PATHS = {
    "app": "M4 4h16v16H4z M7 15l3-3 2 2 4-5 M7 8h.01 M11 8h.01 M15 8h.01",
    "dashboard": "M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z",
    "market": "M4 18l5-6 4 3 7-9 M15 6h5v5",
    "buy_orders": "M4 7h16v10H4z M7 12h4 M16 10v4 M14 12h4",
    "settings": "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M19 12h1 M4 12h1 M12 4V3 M12 21v-1 M17 7l1-1 M6 18l1-1 M17 17l1 1 M6 6l1 1",
    "external": "M14 4h6v6 M20 4l-9 9 M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5",
    "clipboard": "M9 5h6 M9 3h6a2 2 0 0 1 2 2v15H7V5a2 2 0 0 1 2-2z M9 10h6 M9 14h4",
    "add": "M12 5v14 M5 12h14",
    "edit": "M4 20h4l10-10-4-4L4 16v4z M13 7l4 4 M15 5l1-1a2 2 0 0 1 3 3l-1 1",
    "history": "M4 12a8 8 0 1 0 2-5.4 M4 5v5h5 M12 8v5l3 2",
    "delete": "M4 7h16 M10 11v5 M14 11v5 M9 7V4h6v3 M6 7l1 14h10l1-14",
    "refresh": "M20 11a8 8 0 0 0-14-5L4 8 M4 4v4h4 M4 13a8 8 0 0 0 14 5l2-2 M20 20v-4h-4",
    "search": "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M16 16l4 4",
    "minimize": "M5 12h14",
    "maximize": "M5 5h14v14H5z",
    "restore": "M8 8h11v11H8z M5 16H4V5h11v1",
    "close": "M6 6l12 12 M18 6L6 18",
}


def make_line_icon(name: str, color: str = "#cdd6f4", size: int = 18) -> QIcon:
    """Render the small, consistent SVG line-icon set used by the application."""
    path = _LINE_ICON_PATHS.get(name, _LINE_ICON_PATHS["app"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<path d="{path}" fill="none" stroke="{color}" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


class CustomTitleBar(QFrame):
    """Frameless-window header with native window actions and a drag surface."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_offset = None
        self.setObjectName("customTitleBar")
        self.setFixedHeight(40)
        self.setCursor(Qt.ArrowCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)

        app_icon = QLabel()
        app_icon.setPixmap(make_line_icon("app", "#89b4fa", 20).pixmap(20, 20))
        app_icon.setFixedSize(24, 24)
        layout.addWidget(app_icon)

        title = QLabel("CS2 出租管理")
        title.setObjectName("windowTitle")
        layout.addWidget(title)

        self.sync_label = QLabel("● 本地数据已加载")
        self.sync_label.setObjectName("syncStatus")
        layout.addWidget(self.sync_label)
        layout.addStretch()

        self.min_button = self._control_button("minimize", "最小化")
        self.max_button = self._control_button("maximize", "最大化")
        self.close_button = self._control_button("close", "关闭", close=True)
        self.min_button.clicked.connect(self._window.showMinimized)
        self.max_button.clicked.connect(self.toggle_maximize)
        self.close_button.clicked.connect(self._window.close)
        layout.addWidget(self.min_button)
        layout.addWidget(self.max_button)
        layout.addWidget(self.close_button)

    def _control_button(self, icon_name, tooltip, close=False):
        button = QToolButton()
        button.setObjectName("closeControl" if close else "windowControl")
        button.setIcon(make_line_icon(icon_name, "#f5e0dc" if close else "#bac2de", 16))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(40, 40)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        return button

    def set_sync_status(self, text, tone="#a6e3a1"):
        self.sync_label.setText(f"● {text}")
        self.sync_label.setStyleSheet(f"color: {tone};")

    def toggle_maximize(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.update_window_state()

    def update_window_state(self):
        icon_name = "restore" if self._window.isMaximized() else "maximize"
        tooltip = "还原窗口" if self._window.isMaximized() else "最大化"
        self.max_button.setIcon(make_line_icon(icon_name, "#bac2de", 16))
        self.max_button.setToolTip(tooltip)
        self.max_button.setAccessibleName(tooltip)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton and not self._window.isMaximized():
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class CS2ManagerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui_settings = QSettings("CS2RentalManager", "Desktop")
        self.db = DBManager()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setWindowTitle("CS2 出租管理")
        self.setWindowIcon(make_line_icon("app", "#89b4fa", 64))
        self.resize(1400, 800)
        self.setMinimumSize(1080, 650)
        self._resize_margin = 6

        # 保存活跃线程引用，防止被 GC
        self._active_threads = []

        # 市场行情刷新专用线程/Worker（单线程顺序队列）
        self._market_refresh_thread = None
        self._market_refresh_worker = None
        self._market_refresh_groups = {}
        self._market_rolling_sync_enabled = False
        self._market_refresh_background = False
        self._market_refresh_fast_only = False
        self._market_search_in_progress = False
        # One rolling cycle covers every distinct market lookup once.  This is
        # UI-only state: timestamps remain the source of truth after restart.
        self._market_rolling_cycle_pending = set()
        self._market_rolling_cycle_known = set()
        self._market_rolling_cycle_total = 0
        self._sync_progress_buttons = []
        self._dashboard_rental_rows = {}

        # 当前市场详情页选中的物品标识 (name|phase)
        self._current_market_item_key = ""
        # 市场页数据列表: [{name, phase, market_hash_name, ...}]
        self._market_tracked_items = []
        self._market_categories = {}
        self._active_market_category_id = "rentals"
        self._market_thumbnail_cache = {}
        self._csfloat_buy_order_rows = []
        self._csfloat_buy_detail_cursor = 0
        self._csfloat_buy_refresh_in_progress = False
        self._csfloat_buy_has_loaded = False
        self._csfloat_buy_refresh_background = False
        self._csfloat_buy_last_auto_refresh_at = 0.0
        self._csfloat_buy_fx_rate = self._configured_usd_cny_rate()
        self._csfloat_buy_fx_source = "手动备用"

        self.apply_theme()
        self.init_ui()

        self.market_rolling_refresh_timer = QTimer(self)
        self.market_rolling_refresh_timer.timeout.connect(self._run_rolling_market_refresh)

        # Keep rental end-time displays live without reloading data or making API calls.
        self.rental_countdown_timer = QTimer(self)
        self.rental_countdown_timer.timeout.connect(self._update_dashboard_rental_countdowns)
        # The visible countdown is minute-granular; a 30-second cadence keeps
        # it current without restyling every rented row once per second.
        self.rental_countdown_timer.start(30 * 1000)

        # Relative market-update labels are local UI state and never trigger API calls.
        self.market_relative_time_timer = QTimer(self)
        self.market_relative_time_timer.timeout.connect(self._update_market_relative_times)
        self.market_relative_time_timer.start(60 * 1000)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_auto_refresh)
        self.update_timer_interval()

        # Detailed market sync is enabled by default.  It can be paused from
        # the visible progress button without affecting manual F5 refreshes.
        self._market_rolling_sync_enabled = True
        self.update_timer_interval()
        self._reset_market_rolling_cycle()
        self.market_rolling_refresh_timer.start(MARKET_ROLLING_REFRESH_SECONDS * 1000)
        self._restore_ui_state()

    def apply_theme(self):
        self.setStyleSheet(APP_QSS)

    def init_ui(self):
        central_widget = QWidget()
        central_widget.setObjectName("appRoot")
        central_widget.setMouseTracking(True)
        QApplication.instance().installEventFilter(self)
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.title_bar = CustomTitleBar(self, central_widget)
        layout.addWidget(self.title_bar)

        workspace = QHBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)

        nav = QFrame()
        nav.setObjectName("navPanel")
        nav.setFixedWidth(166)
        self.nav_panel = nav
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(8, 12, 8, 0)
        nav_layout.setSpacing(4)
        caption = QLabel("工作区")
        caption.setObjectName("navCaption")
        caption.setFont(QFont("Microsoft YaHei", 11, QFont.DemiBold))
        nav_layout.addWidget(caption)
        self.navigation_buttons = []
        for index, label, icon_name in (
            (0, "资产总览", "dashboard"),
            (1, "大盘行情", "market"),
            (2, "CSF 求购", "buy_orders"),
        ):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setIcon(make_line_icon(icon_name, "#a6adc8", 17))
            button.setIconSize(QSize(20, 20))
            button.setMinimumHeight(44)
            button.setFont(QFont("Microsoft YaHei", 11, QFont.DemiBold))
            button.clicked.connect(lambda checked=False, page=index: self.switch_page(page))
            nav_layout.addWidget(button)
            self.navigation_buttons.append(button)
        nav_layout.addStretch()
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("settingsNavButton")
        self.settings_button.setIcon(make_line_icon("settings", "#a6adc8", 17))
        self.settings_button.setIconSize(QSize(19, 19))
        self.settings_button.setText("设置")
        self.settings_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.settings_button.setToolTip("系统与 API 设置")
        self.settings_button.setStyleSheet(
            "QToolButton { color: #a6adc8; padding: 7px 8px; border-radius: 6px; }"
            "QToolButton:hover { color: #cdd6f4; background: #1e1e2e; }"
        )
        self.settings_button.clicked.connect(lambda: self.switch_page(3))
        nav_layout.addWidget(self.settings_button)
        footer = QLabel(f"CS2 Rental Manager\nv{__version__} · 本地优先")
        footer.setObjectName("navFooter")
        nav_layout.addWidget(footer)
        workspace.addWidget(nav)

        self.tabs = QStackedWidget()
        self.tabs.setObjectName("pageStack")
        workspace.addWidget(self.tabs, 1)
        layout.addLayout(workspace, 1)

        self.tab_dashboard = QWidget()
        self.init_dashboard_tab()
        self.tabs.addWidget(self.tab_dashboard)

        self.tab_market = QWidget()
        self.init_market_tab()
        self.tabs.addWidget(self.tab_market)

        self.tab_csfloat_buy = QWidget()
        self.init_csfloat_buy_orders_tab()
        self.tabs.addWidget(self.tab_csfloat_buy)

        self.tab_settings = QWidget()
        self.init_settings_tab()
        self.tabs.addWidget(self.tab_settings)
        self.switch_page(0)
        self._responsive_mode = ""
        self._apply_responsive_layout(force=True)
        self._install_shortcuts()

        self.undo_delete_button = QPushButton("撤销删除")
        self.undo_delete_button.setObjectName("primaryBtn")
        self.undo_delete_button.clicked.connect(self._undo_last_delete)
        self.undo_delete_button.setVisible(False)
        self.statusBar().addPermanentWidget(self.undo_delete_button)
        self.statusBar().setStyleSheet(
            "QStatusBar { background: #11111b; color: #cdd6f4; border-top: 1px solid #313244; }"
        )
        self._last_deleted_item_id = None

    def _install_shortcuts(self):
        self._app_shortcuts = []

        def add(sequence, callback):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._app_shortcuts.append(shortcut)

        for sequence, page in (("Alt+1", 0), ("Alt+2", 1), ("Alt+3", 2), ("Alt+4", 3)):
            add(sequence, lambda target=page: self.switch_page(target))
        add("Alt+Left", lambda: self._step_market_category(-1) if self.tabs.currentIndex() == 1 else None)
        add("Alt+Right", lambda: self._step_market_category(1) if self.tabs.currentIndex() == 1 else None)
        add("Ctrl+N", lambda: self.add_item() if self.tabs.currentIndex() == 0 else None)
        add("Ctrl+F", self._focus_current_search)

    def _step_workspace(self, direction):
        """Move between the three primary workspaces with wrap-around."""
        workspace_count = len(getattr(self, "navigation_buttons", []))
        if workspace_count <= 0:
            return
        current = self.tabs.currentIndex()
        if not 0 <= current < workspace_count:
            current = 0 if direction > 0 else workspace_count - 1
        else:
            current = (current + direction) % workspace_count
        self.switch_page(current)

    def _focus_current_search(self):
        target = (
            getattr(self, "dashboard_search", None)
            if self.tabs.currentIndex() == 0
            else getattr(self, "market_filter_input", None)
            if self.tabs.currentIndex() == 1
            else None
        )
        if target is not None:
            target.setFocus()
            target.selectAll()

    def _restore_ui_state(self):
        geometry = self.ui_settings.value("window/geometry")
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)
        for key, table_name in (
            ("dashboard/header", "table"),
            ("market/header", "market_table"),
            ("buy_orders/header", "csfloat_buy_table"),
        ):
            table = getattr(self, table_name, None)
            state = self.ui_settings.value(key)
            if table is not None and isinstance(state, QByteArray) and not state.isEmpty():
                table.horizontalHeader().restoreState(state)
        try:
            page = int(self.ui_settings.value("window/page", 0))
        except (TypeError, ValueError):
            page = 0
        self.switch_page(page if 0 <= page < self.tabs.count() else 0)

    def _save_ui_state(self):
        self.ui_settings.setValue("window/geometry", self.saveGeometry())
        self.ui_settings.setValue("window/page", self.tabs.currentIndex())
        for key, table_name in (
            ("dashboard/header", "table"),
            ("market/header", "market_table"),
            ("buy_orders/header", "csfloat_buy_table"),
        ):
            table = getattr(self, table_name, None)
            if table is not None:
                self.ui_settings.setValue(key, table.horizontalHeader().saveState())
        self.ui_settings.sync()

    def switch_page(self, index):
        """Switch a left-navigation page without recreating existing page widgets."""
        self.tabs.setCurrentIndex(index)
        for button_index, button in enumerate(self.navigation_buttons):
            button.setChecked(button_index == index)
        if index == 0 and hasattr(self, "table"):
            self.load_data()
        elif index == 2 and not self._csfloat_buy_has_loaded:
            self._request_global_sync_now()

    def _resize_edges_at(self, position):
        if self.isMaximized():
            return Qt.Edges()
        edges = Qt.Edges()
        if position.x() <= self._resize_margin:
            edges |= Qt.LeftEdge
        elif position.x() >= self.width() - self._resize_margin:
            edges |= Qt.RightEdge
        if position.y() <= self._resize_margin:
            edges |= Qt.TopEdge
        elif position.y() >= self.height() - self._resize_margin:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _resize_cursor(edges):
        if edges in (Qt.LeftEdge, Qt.RightEdge):
            return Qt.SizeHorCursor
        if edges in (Qt.TopEdge, Qt.BottomEdge):
            return Qt.SizeVerCursor
        if edges in (Qt.TopEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge):
            return Qt.SizeFDiagCursor
        if edges:
            return Qt.SizeBDiagCursor
        return Qt.ArrowCursor

    def eventFilter(self, watched, event):
        """Handle resizing plus WASD workspace/category navigation and F5."""
        if isinstance(watched, QWidget) and watched.window() is self:
            if (
                event.type() == QEvent.KeyPress
                and hasattr(self, "tabs")
                and event.modifiers() == Qt.NoModifier
            ):
                key = event.key()
                on_market_page = self.tabs.currentWidget() is getattr(self, "tab_market", None)
                if key == Qt.Key_F5 and on_market_page:
                    if not event.isAutoRepeat():
                        self._request_global_sync_now()
                    return True
                focus = QApplication.focusWidget()
                editing_text = isinstance(focus, (QLineEdit, QPlainTextEdit, QComboBox))
                if not editing_text and not event.isAutoRepeat():
                    if key == Qt.Key_W:
                        self._step_workspace(-1)
                        return True
                    if key == Qt.Key_S:
                        self._step_workspace(1)
                        return True
                    if key == Qt.Key_A and on_market_page:
                        self._step_market_category(-1)
                        return True
                    if key == Qt.Key_D and on_market_page:
                        self._step_market_category(1)
                        return True
            if event.type() in (QEvent.MouseMove, QEvent.MouseButtonPress):
                position = self.mapFromGlobal(event.globalPosition().toPoint())
                edges = self._resize_edges_at(position)
                if event.type() == QEvent.MouseMove:
                    cursor = self._resize_cursor(edges)
                    if self.cursor().shape() != cursor:
                        self.setCursor(cursor)
                elif event.button() == Qt.LeftButton and edges:
                    handle = self.windowHandle()
                    if handle is not None and handle.startSystemResize(edges):
                        return True
        return super().eventFilter(watched, event)

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.update_window_state()
        super().changeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "tabs"):
            self._apply_responsive_layout()

    def _reflow_dashboard_cards(self, columns):
        grid = getattr(self, "dashboard_card_layout", None)
        cards = getattr(self, "dashboard_cards", None)
        if grid is None or not cards:
            return
        while grid.count():
            grid.takeAt(0)
        for index, card in enumerate(cards):
            grid.addWidget(card, index // columns, index % columns)
        for column in range(6):
            grid.setColumnStretch(column, 1 if column < columns else 0)

    def _apply_responsive_layout(self, force=False):
        """Adapt dense pages without letting important text collapse."""
        width = self.width()
        mode = "compact" if width < 1180 else "medium" if width < 1480 else "wide"
        if not force and mode == getattr(self, "_responsive_mode", ""):
            return
        self._responsive_mode = mode

        nav = getattr(self, "nav_panel", None)
        if nav is not None:
            nav.setFixedWidth(142 if mode == "compact" else 156 if mode == "medium" else 166)

        self._reflow_dashboard_cards(3 if mode != "wide" else 6)

        if hasattr(self, "table"):
            self.table.setColumnWidth(0, 58 if mode == "compact" else 68)
            self.table.setFont(QFont("Microsoft YaHei", 9 if mode == "compact" else 10))
            if mode == "compact":
                dashboard_header = self.table.horizontalHeader()
                for column, width_value in ((4, 88), (5, 132), (7, 160), (9, 92), (12, 108)):
                    dashboard_header.setSectionResizeMode(column, QHeaderView.Interactive)
                    self.table.setColumnWidth(column, width_value)
            else:
                dashboard_header = self.table.horizontalHeader()
                dashboard_header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
                for column in (4, 5, 9, 12):
                    dashboard_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)

        if hasattr(self, "market_table"):
            self.market_table.setColumnWidth(0, 70 if mode != "wide" else 84)
            self.market_table.setColumnWidth(1, 225 if mode == "compact" else 250 if mode == "medium" else 290)
            self.market_table.setFont(QFont("Microsoft YaHei", 10 if mode != "wide" else 11))
        if hasattr(self, "csfloat_buy_table"):
            self.csfloat_buy_table.setFont(QFont("Microsoft YaHei", 9 if mode == "compact" else 10))
            self.csfloat_buy_table.setColumnWidth(0, 245 if mode == "compact" else 310)
        shortcut_hint = getattr(self, "market_shortcut_hint", None)
        if shortcut_hint is not None:
            shortcut_hint.setVisible(mode != "compact")

        # Re-render only when crossing a breakpoint, so per-cell fonts and row
        # heights follow the new density without doing work on every resize pixel.
        if hasattr(self, "current_items") and hasattr(self, "table"):
            self.load_data()
        if hasattr(self, "_market_tracked_items") and hasattr(self, "market_table"):
            self._populate_market_table()

    # ═══════════════════════════════════════════
    # Tab 1: 资产仪表盘 (Beautified)
    # ═══════════════════════════════════════════

    def init_dashboard_tab(self):
        layout = QVBoxLayout(self.tab_dashboard)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 顶部标题栏 ──
        header = QHBoxLayout()
        title = QLabel("资产总览")
        title.setObjectName("titleLabel")
        header.addWidget(title)
        header.addStretch()
        self.lbl_last_update = QLabel("最后更新: --")
        self.lbl_last_update.setStyleSheet("color: #a6adc8; font-size: 12px;")
        header.addWidget(self.lbl_last_update)
        self.dashboard_sync_progress_button = self._create_global_sync_button()
        header.addWidget(self.dashboard_sync_progress_button)
        layout.addLayout(header)

        # ── 统计卡片 ──
        self.dashboard_card_layout = QGridLayout()
        self.dashboard_card_layout.setHorizontalSpacing(10)
        self.dashboard_card_layout.setVerticalSpacing(10)
        self.card_cost = self.create_card("买入总资产", "¥ 0.00", "#89b4fa")
        self.card_market_profit = self.create_card(
            "饰品行情盈亏", "—", "#bac2de", emphasis=True
        )
        self.card_market_profit.setToolTip(
            "CSQAQ 当前全网最低售价合计 − 对应饰品总成本；不包含租金收益，不受平台筛选影响。"
        )
        self.card_total_income = self.create_card("累计租金净收益", "¥ 0.00", "#cba6f7")
        self.card_total_income.setToolTip(
            "全部饰品订单的累计净租金；已取消、已关闭、已退款订单不计收益，不受平台筛选影响。"
        )
        self.card_income = self.create_card("当前每日净收益", "¥ 0.00", "#a6e3a1")
        self.card_rented = self.create_card("在租件数", "0 / 0 件", "#f9e2af")
        self.card_rate = self.create_card("在租年化（总资产）", "0.0%", "#f38ba8")
        self.dashboard_cards = [
            self.card_cost, self.card_market_profit, self.card_total_income,
            self.card_income, self.card_rented, self.card_rate,
        ]
        for index, card in enumerate(self.dashboard_cards):
            self.dashboard_card_layout.addWidget(card, index // 3, index % 3)
        layout.addLayout(self.dashboard_card_layout)

        # ── 工具栏 ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        add_btn = QPushButton("新增饰品")
        self._set_button_icon(add_btn, "add")
        add_btn.clicked.connect(self.add_item)

        ai_asset_import_btn = QPushButton("AI 批量导入")
        ai_asset_import_btn.setObjectName("primaryBtn")
        self._set_button_icon(ai_asset_import_btn, "clipboard", "#11111b")
        ai_asset_import_btn.setToolTip(
            "复制资产识别模板和截图给 AI，再粘贴 JSON 一键导入"
        )
        ai_asset_import_btn.clicked.connect(self._open_ai_asset_import)

        edit_btn = QPushButton("修改选中")
        edit_btn.setObjectName("primaryBtn")
        self._set_button_icon(edit_btn, "edit", "#11111b")
        edit_btn.clicked.connect(self.edit_selected_item)

        history_btn = QPushButton("订单历史")
        self._set_button_icon(history_btn, "history")
        history_btn.clicked.connect(self.show_selected_rental_history)

        del_btn = QPushButton("删除")
        del_btn.setObjectName("dangerBtn")
        self._set_button_icon(del_btn, "delete", "#11111b")
        del_btn.clicked.connect(self.delete_selected_item)

        refresh_btn = QPushButton("刷新")
        self._set_button_icon(refresh_btn, "refresh")
        refresh_btn.clicked.connect(self.load_data)

        self.dashboard_search = QLineEdit()
        self.dashboard_search.setPlaceholderText("搜索饰品名称 / 磨损（Ctrl+F）")
        self.dashboard_search.setClearButtonEnabled(True)
        self.dashboard_search.setMaximumWidth(230)
        self.dashboard_search.textChanged.connect(self.load_data)

        self.status_filter_box = QComboBox()
        self.status_filter_box.addItems(["全部状态", "在库", "出租中", "待转租", "CD冷却"])
        self.status_filter_box.currentTextChanged.connect(self.load_data)
        self.status_filter_box.setFixedWidth(100)

        self.filter_box = QComboBox()
        self.filter_box.addItems(["全部平台", "C5GAME", "ECOSteam", "悠悠有品", "IGXE", "BUFF"])
        self.filter_box.currentTextChanged.connect(self.load_data)
        self.filter_box.setFixedWidth(120)

        toolbar.addWidget(add_btn)
        toolbar.addWidget(ai_asset_import_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(history_btn)
        toolbar.addWidget(del_btn)
        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(self.dashboard_search)
        self.order_tools_toggle = QPushButton("订单工具 ▸")
        self.order_tools_toggle.setCheckable(True)
        self.order_tools_toggle.setToolTip("展开 C5、ECO、IGXE 网页入口和剪贴板订单导入")
        self._set_button_icon(self.order_tools_toggle, "external")
        self.order_tools_toggle.toggled.connect(self._toggle_order_tools_panel)
        toolbar.addWidget(self.order_tools_toggle)
        toolbar.addStretch()
        toolbar.addWidget(self.status_filter_box)
        toolbar.addWidget(QLabel("平台:"))
        toolbar.addWidget(self.filter_box)
        self.dashboard_columns_btn = QToolButton()
        self.dashboard_columns_btn.setText("列")
        self.dashboard_columns_btn.setToolTip("选择资产表显示的列")
        self.dashboard_columns_btn.clicked.connect(
            lambda: self._open_column_menu(self.table, self.dashboard_columns_btn)
        )
        toolbar.addWidget(self.dashboard_columns_btn)
        layout.addLayout(toolbar)

        # Rarely-used platform shortcuts stay in one compact row and are hidden
        # by default, leaving the asset table more vertical room.
        self.order_tools_panel = QFrame()
        self.order_tools_panel.setObjectName("orderToolsPanel")
        order_tools_layout = QHBoxLayout(self.order_tools_panel)
        order_tools_layout.setContentsMargins(8, 6, 8, 6)
        order_tools_layout.setSpacing(8)
        for platform_key, label in (("c5", "打开 C5"), ("eco", "打开 ECO"), ("igxe", "打开 IGXE")):
            platform_button = QPushButton(label)
            self._set_button_icon(platform_button, "external")
            platform_button.clicked.connect(
                lambda checked=False, key=platform_key: self._open_default_browser_order_page(key)
            )
            order_tools_layout.addWidget(platform_button)
        self.clipboard_import_btn = QPushButton("从剪贴板导入订单")
        self.clipboard_import_btn.setObjectName("successBtn")
        self._set_button_icon(self.clipboard_import_btn, "clipboard", "#11111b")
        self.clipboard_import_btn.setToolTip("复制 C5、ECO 或 IGXE 订单页文本后点击，程序会自动识别平台。")
        self.clipboard_import_btn.clicked.connect(self._import_rental_orders_from_clipboard)
        order_tools_layout.addWidget(self.clipboard_import_btn)
        order_tools_layout.addStretch()
        self.order_tools_panel.setVisible(False)
        layout.addWidget(self.order_tools_panel)

        # ── 饰品表格 ──
        self.table = QTableWidget()
        self.table.setColumnCount(13)
        self.table.setHorizontalHeaderLabels([
            "图片", "饰品名称", "相位", "磨损", "成本", "售价－成本",
            "平台", "状态", "租期 / 类型", "日租（净）", "同租期租金差",
            "本单净收入", "累计净收益",
        ])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 68)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        for column in range(2, 13):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.horizontalHeaderItem(4).setToolTip(
            "右键成本单元格，可按百分比把手续费计入当前成本。输入 1 表示成本增加 1%。"
        )
        self.table.horizontalHeaderItem(5).setToolTip(
            "CSQAQ 全网最低售价减去当前成本；百分比以成本为基准。"
        )
        self.table.horizontalHeaderItem(10).setToolTip(
            "本单原始日租减去同平台、同长短租类型的最低日租；ECO 使用其唯一返回的最低租金。"
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("alternate-background-color: #1e1e2e;")
        self.table.verticalHeader().setDefaultSectionSize(64)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_dashboard_context_menu)
        self.table.doubleClicked.connect(self.show_selected_rental_history)

        self.dashboard_empty_label = QLabel("暂无符合条件的资产。可点击“新增饰品”或清除搜索筛选。")
        self.dashboard_empty_label.setAlignment(Qt.AlignCenter)
        self.dashboard_empty_label.setStyleSheet("color: #6c7086; padding: 10px;")
        self.dashboard_empty_label.setVisible(False)
        layout.addWidget(self.dashboard_empty_label)
        layout.addWidget(self.table)

    def _toggle_order_tools_panel(self, expanded):
        """Reveal the compact order-import shortcuts only when requested."""
        self.order_tools_panel.setVisible(bool(expanded))
        self.order_tools_toggle.setText("订单工具 ▾" if expanded else "订单工具 ▸")

    def create_card(self, title, val, color, emphasis=False):
        """创建美化后的统计卡片"""
        w = QFrame()
        w.setObjectName("emphasisCard" if emphasis else "cardFrame")
        w.setMinimumHeight(82)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(13, 9, 13, 9)
        lay.setSpacing(4)

        t = QLabel(title)
        t.setObjectName("cardTitle")
        t.setWordWrap(True)
        v = QLabel(val)
        v.setObjectName("cardValue")
        v.setStyleSheet(f"color: {color}; font-size: 19px; font-weight: bold;")

        lay.addWidget(t)
        lay.addWidget(v)
        return w

    @staticmethod
    def _set_button_icon(button, icon_name, color="#cdd6f4"):
        button.setIcon(make_line_icon(icon_name, color, 16))
        button.setIconSize(QSize(16, 16))

    def _open_column_menu(self, table, button):
        menu = QMenu(self)
        for column in range(table.columnCount()):
            header_item = table.horizontalHeaderItem(column)
            label = header_item.text() if header_item is not None else f"第 {column + 1} 列"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not table.isColumnHidden(column))
            action.toggled.connect(
                lambda visible, index=column: table.setColumnHidden(index, not visible)
            )
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _show_toast(self, message, duration_ms=4000):
        self.statusBar().showMessage(str(message), duration_ms)
        if hasattr(self, "title_bar"):
            self.title_bar.set_sync_status(str(message), "#a6e3a1")

    def _undo_last_delete(self):
        item_id = self._last_deleted_item_id
        if item_id is None:
            return
        try:
            self.db.restore_item(item_id)
        except Exception as exc:
            QMessageBox.warning(self, "撤销删除失败", str(exc))
            return
        self._last_deleted_item_id = None
        self.undo_delete_button.setVisible(False)
        self.load_data()
        self._show_toast("已恢复刚才删除的饰品")

    def _hide_undo_for(self, item_id):
        if self._last_deleted_item_id == item_id:
            self._last_deleted_item_id = None
            self.undo_delete_button.setVisible(False)

    # ═══════════════════════════════════════════
    # Tab 2: 一览式大盘行情 (Refactored)
    # ═══════════════════════════════════════════

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
        preview = RentalImportPreviewDialog(
            platform, orders, self.db.get_all_items(), self
        )
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
        title = QLabel("大盘行情")
        title.setObjectName("titleLabel")
        header.addWidget(title)
        header.addStretch()
        self.lbl_market_update = QLabel("最后更新: --")
        self.lbl_market_update.setStyleSheet("color: #a6adc8; font-size: 12px;")
        header.addWidget(self.lbl_market_update)
        layout.addLayout(header)

        category_layout = QHBoxLayout()
        category_layout.setSpacing(8)
        category_label = QLabel("观察分类")
        category_label.setStyleSheet("color: #a6adc8; font-weight: 700;")
        self.market_category_box = QComboBox()
        self.market_category_box.setMinimumWidth(180)
        self.market_category_box.setToolTip("快捷键：Alt+1/2/3 切换页面，Alt+←/→ 切换分类，F5 刷新")
        self.market_category_box.currentIndexChanged.connect(self._on_market_category_changed)
        new_category_btn = QPushButton("新建分类")
        self._set_button_icon(new_category_btn, "add")
        new_category_btn.clicked.connect(self._create_market_category)
        rename_category_btn = QPushButton("重命名")
        self._set_button_icon(rename_category_btn, "edit")
        rename_category_btn.clicked.connect(self._rename_market_category)
        delete_category_btn = QPushButton("删除分类")
        delete_category_btn.setObjectName("dangerBtn")
        self._set_button_icon(delete_category_btn, "delete", "#11111b")
        delete_category_btn.clicked.connect(self._delete_market_category)
        self.market_category_controls = [
            self.market_category_box,
            new_category_btn,
            rename_category_btn,
            delete_category_btn,
        ]
        category_layout.addWidget(category_label)
        category_layout.addWidget(self.market_category_box)
        category_layout.addWidget(new_category_btn)
        category_layout.addWidget(rename_category_btn)
        category_layout.addWidget(delete_category_btn)
        self.market_shortcut_hint = QLabel("W/S 工作区 · A/D 分类 · F5 刷新")
        self.market_shortcut_hint.setStyleSheet("color: #6c7086; font-size: 11px;")
        category_layout.addWidget(self.market_shortcut_hint)
        category_layout.addStretch()
        layout.addLayout(category_layout)

        # ── 搜索栏 ──
        search_layout = QHBoxLayout()
        self.market_input = QLineEdit()
        self.market_input.setPlaceholderText("输入饰品名称（例如：流浪者匕首 多普勒 红宝石 / Ruby）")
        self.market_search_btn = QPushButton("搜索并添加")
        self.market_search_btn.setObjectName("primaryBtn")
        self._set_button_icon(self.market_search_btn, "search", "#11111b")
        self.market_search_btn.clicked.connect(self.query_csqaq_market)
        search_layout.addWidget(self.market_input, 1)
        search_layout.addWidget(self.market_search_btn)

        ai_import_btn = QPushButton("AI 批量添加")
        ai_import_btn.setObjectName("primaryBtn")
        self._set_button_icon(ai_import_btn, "clipboard", "#11111b")
        ai_import_btn.setToolTip("复制提示词和截图交给 AI，再将 JSON 返回结果粘贴进来批量添加")
        ai_import_btn.clicked.connect(self._open_ai_market_import)
        search_layout.addWidget(ai_import_btn)

        refresh_market_btn = QPushButton("立即同步")
        refresh_market_btn.setObjectName("successBtn")
        self._set_button_icon(refresh_market_btn, "refresh", "#11111b")
        refresh_market_btn.clicked.connect(self._request_global_sync_now)
        refresh_market_btn.setToolTip("按全局频率排队同步；不会绕过 CSFloat 频控（快捷键：F5）")
        search_layout.addWidget(refresh_market_btn)

        self.market_refresh_progress_label = self._create_global_sync_button()
        search_layout.addWidget(self.market_refresh_progress_label)

        self.market_remove_btn = QPushButton("删除选中")
        self.market_remove_btn.setObjectName("dangerBtn")
        self._set_button_icon(self.market_remove_btn, "delete", "#11111b")
        self.market_remove_btn.clicked.connect(self._remove_selected_market_items)
        search_layout.addWidget(self.market_remove_btn)

        self.market_filter_input = QLineEdit()
        self.market_filter_input.setPlaceholderText("筛选当前列表（Ctrl+F）")
        self.market_filter_input.setClearButtonEnabled(True)
        self.market_filter_input.setMaximumWidth(210)
        self.market_filter_input.textChanged.connect(self._apply_market_filter)
        search_layout.addWidget(self.market_filter_input)

        self.market_columns_btn = QToolButton()
        self.market_columns_btn.setText("列")
        self.market_columns_btn.setToolTip("选择行情表显示的列")
        self.market_columns_btn.clicked.connect(
            lambda: self._open_column_menu(self.market_table, self.market_columns_btn)
        )
        search_layout.addWidget(self.market_columns_btn)

        layout.addLayout(search_layout)

        # ── 一览式大盘表格 ──
        self.market_table = QTableWidget()
        self.market_table.setColumnCount(9)
        hdr = self.market_table.horizontalHeader()
        self.market_table.setHorizontalHeaderLabels([
            "图片", "饰品名称 / Phase", "CSQAQ 国内最低",
            "CSFloat底价", "ECO 最低日租",
            "C5 租金（短 / 长）", "悠悠 租金（短 / 长）",
            "IGXE 租金（短 / 长）", "更新时间",
        ])
        hdr.setFont(QFont("Microsoft YaHei", 11, QFont.DemiBold))
        self.market_table.setFont(QFont("Microsoft YaHei", 11))
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        # Keep the name readable even when all market columns exceed the viewport.
        # The table already has a horizontal scrollbar, so a stable name width is
        # preferable to squeezing long skin names into two or three tiny lines.
        hdr.setSectionResizeMode(1, QHeaderView.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.market_table.setColumnWidth(0, 84)
        self.market_table.setColumnWidth(1, 290)
        market_vertical_header = self.market_table.verticalHeader()
        market_vertical_header.setVisible(False)
        market_vertical_header.setDefaultSectionSize(86)
        self.market_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.market_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.market_table.setSortingEnabled(True)
        self.market_table.horizontalHeader().setSectionsMovable(True)
        self.market_table.setAlternatingRowColors(True)
        self.market_table.setStyleSheet("alternate-background-color: #1e1e2e;")
        for column in range(2, 9):
            hdr.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.market_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.market_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.market_table.customContextMenuRequested.connect(self._show_market_context_menu)
        # Double-clicking a supported quote/name cell opens its platform page.
        self.market_table.doubleClicked.connect(self._on_market_table_double_click)
        self.market_empty_label = QLabel("当前分类还没有观察品，可通过搜索或 AI 批量添加。")
        self.market_empty_label.setAlignment(Qt.AlignCenter)
        self.market_empty_label.setStyleSheet("color: #6c7086; padding: 10px;")
        self.market_empty_label.setVisible(False)
        layout.addWidget(self.market_empty_label)
        layout.addWidget(self.market_table)

        # ── 初始加载 ──
        self._init_market_from_cache()

    def _merge_durable_watchlist(self, durable, cached):
        """Use SQLite identities as truth while retaining rebuildable cached quotes."""
        durable_categories = durable.get("categories", []) if isinstance(durable, dict) else []
        cached_categories = cached.get("categories", []) if isinstance(cached, dict) else []
        if not durable_categories:
            return cached

        cached_by_category = {}
        cached_global = {}
        for category in cached_categories:
            if not isinstance(category, dict):
                continue
            category_id = str(category.get("id") or "")
            per_category = {}
            for entry in category.get("items", []):
                if not isinstance(entry, dict):
                    continue
                identity = self._market_watch_identity(
                    entry.get("market_hash_name", entry.get("name", "")),
                    entry.get("phase", "-"),
                ).casefold()
                per_category[identity] = entry
                cached_global.setdefault(identity, entry)
            cached_by_category[category_id] = per_category

        merged_categories = []
        for category in durable_categories:
            category_id = str(category.get("id") or "")
            items = []
            for durable_entry in category.get("items", []):
                if not isinstance(durable_entry, dict):
                    continue
                identity = self._market_watch_identity(
                    durable_entry.get("market_hash_name", durable_entry.get("name", "")),
                    durable_entry.get("phase", "-"),
                ).casefold()
                merged_entry = dict(
                    cached_by_category.get(category_id, {}).get(identity)
                    or cached_global.get(identity)
                    or {}
                )
                merged_entry.update(durable_entry)
                items.append(merged_entry)
            merged_categories.append({
                "id": category_id,
                "name": str(category.get("name") or category_id),
                "items": items,
            })
        active = str(durable.get("active_category_id") or "")
        return {
            "format": "market_categories_v1",
            "active_category_id": active,
            "categories": merged_categories,
        }

    def _init_market_from_cache(self):
        """Load category-aware market cache without making a network request."""
        cached = MarketCache.load()
        durable = self.db.load_market_watchlist()
        has_durable_watchlist = bool(durable.get("categories"))
        if has_durable_watchlist:
            cached = self._merge_durable_watchlist(durable, cached)
        migrated_legacy_cache = False
        if isinstance(cached, dict) and isinstance(cached.get("categories"), list):
            for index, raw_category in enumerate(cached["categories"], start=1):
                if not isinstance(raw_category, dict):
                    continue
                category_id = str(raw_category.get("id") or f"category_{index}")
                category_name = str(raw_category.get("name") or f"分类 {index}").strip()
                raw_items = raw_category.get("items", [])
                if isinstance(raw_items, dict):
                    raw_items = raw_items.values()
                if not isinstance(raw_items, (list, tuple)):
                    raw_items = []
                self._market_categories[category_id] = {
                    "name": category_name or f"分类 {index}",
                    "items": [dict(entry) for entry in raw_items if isinstance(entry, dict)],
                }
            self._active_market_category_id = str(cached.get("active_category_id") or "rentals")
        elif cached:
            # Version 3.0 wrote a flat key-to-entry dictionary.  Keep every
            # existing watched item and migrate it to the default category.
            migrated_legacy_cache = True
            self._market_categories = {
                "rentals": {
                    "name": "出租品",
                    "items": [dict(entry) for entry in cached.values() if isinstance(entry, dict)],
                }
            }

        self._ensure_market_categories()
        self._activate_market_category(self._active_market_category_id, render=False)
        for entry in self._market_tracked_items:
            self._apply_schema_mapping(entry)
        changed = self._deduplicate_market_tracked_items()
        self._refresh_market_category_selector()

        if cached:
            logger.info(
                "[大盘] 已加载 %s 个分类，当前“%s”有 %s 条行情数据",
                len(self._market_categories),
                self._market_categories[self._active_market_category_id]["name"],
                len(self._market_tracked_items),
            )
            if migrated_legacy_cache or changed or not has_durable_watchlist:
                self._save_market_cache()
            self._populate_market_table()
            self.lbl_market_update.setText(f"最后更新: {QTime.currentTime().toString('HH:mm:ss')} (缓存)")
        else:
            # No prior list: seed the default "出租品" category from inventory.
            self._refresh_market_tracked_list()
            self._save_market_cache()
            self.lbl_market_update.setText("最后更新: -- (来自库存)")

    def _ensure_market_categories(self):
        if not self._market_categories:
            self._market_categories = {"rentals": {"name": "出租品", "items": []}}
        if self._active_market_category_id not in self._market_categories:
            self._active_market_category_id = next(iter(self._market_categories))

    def _refresh_market_category_selector(self):
        if not hasattr(self, "market_category_box"):
            return
        previous = self.market_category_box.blockSignals(True)
        self.market_category_box.clear()
        for category_id, category in self._market_categories.items():
            count = len(category.get("items", []))
            self.market_category_box.addItem(f"{category['name']} · {count} 件", category_id)
        index = self.market_category_box.findData(self._active_market_category_id)
        self.market_category_box.setCurrentIndex(max(0, index))
        self.market_category_box.blockSignals(previous)

    def _activate_market_category(self, category_id, render=True):
        self._ensure_market_categories()
        if category_id not in self._market_categories:
            category_id = next(iter(self._market_categories))
        self._active_market_category_id = category_id
        self._market_tracked_items = self._market_categories[category_id]["items"]
        self._current_market_item_key = ""
        for entry in self._market_tracked_items:
            self._apply_schema_mapping(entry)
        self._refresh_market_category_selector()
        if render and hasattr(self, "market_table"):
            self._populate_market_table()

    def _on_market_category_changed(self, index):
        category_id = self.market_category_box.itemData(index)
        if not category_id or category_id == self._active_market_category_id:
            return
        self._activate_market_category(category_id)
        self._save_market_cache()

    def _step_market_category(self, direction):
        """Move left/right through categories, wrapping at either end."""
        if not hasattr(self, "market_category_box") or not self.market_category_box.isEnabled():
            return False
        count = self.market_category_box.count()
        if count < 2:
            return False
        current = max(0, self.market_category_box.currentIndex())
        self.market_category_box.setCurrentIndex((current + direction) % count)
        return True

    def _set_market_category_controls_enabled(self, enabled):
        for control in getattr(self, "market_category_controls", []):
            control.setEnabled(enabled)

    def _market_refresh_is_running(self):
        return bool(self._market_refresh_thread and self._market_refresh_thread.isRunning())

    def _update_market_category_controls(self):
        self._set_market_category_controls_enabled(
            not self._market_search_in_progress and not self._market_refresh_is_running()
        )

    def _create_market_category(self):
        name, accepted = QInputDialog.getText(self, "新建观察分类", "分类名称：")
        name = name.strip()
        if not accepted or not name:
            return
        if any(category["name"] == name for category in self._market_categories.values()):
            QMessageBox.warning(self, "新建观察分类", "已存在同名分类，请换一个名称。")
            return
        category_id = f"category_{int(time.time() * 1000)}"
        while category_id in self._market_categories:
            category_id = f"{category_id}_1"
        self._market_categories[category_id] = {"name": name, "items": []}
        self._activate_market_category(category_id)
        self._save_market_cache()

    def _rename_market_category(self):
        category = self._market_categories.get(self._active_market_category_id)
        if category is None:
            return
        name, accepted = QInputDialog.getText(
            self, "重命名观察分类", "分类名称：", text=category["name"]
        )
        name = name.strip()
        if not accepted or not name or name == category["name"]:
            return
        if any(
            other_id != self._active_market_category_id and other["name"] == name
            for other_id, other in self._market_categories.items()
        ):
            QMessageBox.warning(self, "重命名观察分类", "已存在同名分类，请换一个名称。")
            return
        category["name"] = name
        self._refresh_market_category_selector()
        self._save_market_cache()

    def _delete_market_category(self):
        if len(self._market_categories) <= 1:
            QMessageBox.information(self, "删除观察分类", "至少需要保留一个观察分类。")
            return
        category = self._market_categories.get(self._active_market_category_id)
        if category is None:
            return
        answer = QMessageBox.question(
            self,
            "删除观察分类",
            f"删除“{category['name']}”及其中 {len(category['items'])} 个观察饰品？\n此操作不会删除资产或订单。",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        deleted_id = self._active_market_category_id
        del self._market_categories[deleted_id]
        self._activate_market_category(next(iter(self._market_categories)))
        self._save_market_cache()

    def _refresh_market_tracked_list(self):
        """从数据库加载库存物品，按 (名称 + 相位) 去重后填充到大盘表格"""
        db_items = self.db.get_all_items()
        seen = set()
        refreshed_items = []
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
            refreshed_items.append(entry)
            self._apply_schema_mapping(entry)
        self._market_categories[self._active_market_category_id]["items"] = refreshed_items
        self._market_tracked_items = refreshed_items
        self._deduplicate_market_tracked_items()
        self._refresh_market_category_selector()
        self._populate_market_table()

    @staticmethod
    def _apply_schema_mapping(entry: dict):
        """Fill standard Steam market name and image URL from the local schema."""
        mapped_item = CS2ItemSchema.lookup_variant(
            entry.get("name", ""),
            entry.get("market_hash_name", ""),
            entry.get("phase", "-"),
            entry.get("paint_index", ""),
        )
        if mapped_item:
            entry["name"] = mapped_item.get("name_zh") or "未知饰品"
            entry["market_hash_name"] = mapped_item["market_hash_name"]
            entry["image_url"] = mapped_item.get("image", "")
            entry["schema_id"] = mapped_item["id"]
            entry["paint_index"] = mapped_item.get("paint_index", "")

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
            self._market_categories[self._active_market_category_id]["items"] = merged
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
        for row in range(self.market_table.rowCount()):
            entry = self._market_entry_at_row(row)
            updated_item = self.market_table.item(row, 8)
            if entry is not None and updated_item is not None:
                updated_item.setText(self._market_updated_text(entry))

    def _market_entry_at_row(self, row):
        item = self.market_table.item(row, 1)
        identity = item.data(Qt.UserRole) if item is not None else ""
        for entry in self._market_tracked_items:
            candidate = self._market_watch_identity(
                entry.get("market_hash_name", entry.get("name", "")),
                entry.get("phase", "-"),
            )
            if candidate == identity:
                return entry
        return None

    def _apply_market_filter(self):
        if not hasattr(self, "market_table"):
            return
        query = (
            self.market_filter_input.text().strip().casefold()
            if hasattr(self, "market_filter_input") else ""
        )
        for row in range(self.market_table.rowCount()):
            item = self.market_table.item(row, 1)
            text = item.text().casefold() if item is not None else ""
            tooltip = item.toolTip().casefold() if item is not None else ""
            self.market_table.setRowHidden(row, bool(query and query not in text and query not in tooltip))

    @staticmethod
    def _market_link_platform(column):
        return {
            1: "csqaq",
            2: "csqaq",
            3: "csfloat",
            4: "eco",
            5: "c5",
            6: "yyyp",
            7: "igxe",
        }.get(column, "")

    def _default_market_link(self, entry, platform):
        detail = entry.get("detail", {})
        name = detail.get("name_zh") or entry.get("name", "")
        if platform == "csqaq":
            good_id = entry.get("csqaq_good_id") or detail.get("csqaq_good_id") or detail.get("good_id")
            return f"https://csqaq.com/goods/{good_id}" if good_id else ""
        if platform == "csfloat":
            listing_id = entry.get("csfloat_listing_id")
            return f"https://csfloat.com/item/{listing_id}" if listing_id else ""
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
        platform = self._market_link_platform(column)
        if not platform:
            return
        entry = self._market_entry_at_row(row)
        if entry is None:
            return
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
        entry = self._market_entry_at_row(index.row())
        if entry is None:
            return
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
        rows = sorted({index.row() for index in self.market_table.selectionModel().selectedRows()})
        if not rows:
            QMessageBox.information(self, "大盘编辑", "请先选择要从大盘移除的饰品（可按 Ctrl 或 Shift 多选）。")
            return
        selected_entries = [self._market_entry_at_row(row) for row in rows]
        selected_entries = [entry for entry in selected_entries if entry is not None]
        names = [entry.get("name", "") for entry in selected_entries]
        answer = QMessageBox.question(
            self,
            "确认移除",
            f"从大盘移除 {len(rows)} 个饰品？这不会删除资产库存。\n\n" + "\n".join(names[:8]),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        selected_ids = {id(entry) for entry in selected_entries}
        self._market_tracked_items[:] = [
            entry for entry in self._market_tracked_items if id(entry) not in selected_ids
        ]
        self._populate_market_table()
        self._refresh_market_category_selector()
        self._save_market_cache()

    def _market_thumbnail(self, local_path: str) -> QPixmap:
        """Return one cached 64px thumbnail, invalidated when the file changes."""
        try:
            modified_ns = os.stat(local_path).st_mtime_ns
        except OSError:
            return QPixmap()
        cached = self._market_thumbnail_cache.get(local_path)
        if cached and cached[0] == modified_ns:
            return cached[1]
        source = QPixmap(local_path)
        thumbnail = (
            source.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if not source.isNull() else QPixmap()
        )
        self._market_thumbnail_cache[local_path] = (modified_ns, thumbnail)
        return thumbnail

    def _set_market_metric_cell(
        self, row, column, primary, secondary, color, tooltip="", emphasize=True
    ):
        """Render a large primary price with smaller supporting text."""
        plain_text = primary + (f"\n{secondary}" if secondary else "")
        # A cell widget paints the visible content. Keeping the same text in the
        # underlying item makes Qt paint both layers, which causes the duplicated
        # and overlapping prices seen in the market table.
        item = QTableWidgetItem("")
        item.setData(Qt.UserRole, plain_text)
        item.setData(Qt.AccessibleTextRole, plain_text)
        item.setToolTip(tooltip)
        self.market_table.setItem(row, column, item)

        container = QWidget()
        container.setAttribute(Qt.WA_TransparentForMouseEvents)
        container.setToolTip(tooltip)
        layout = QVBoxLayout(container)
        compact = getattr(self, "_responsive_mode", "wide") != "wide"
        layout.setContentsMargins(5 if compact else 7, 4 if compact else 5, 5 if compact else 7, 4 if compact else 5)
        layout.setSpacing(1)
        layout.addStretch(1)

        primary_label = QLabel(primary)
        primary_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        primary_label.setFont(QFont(
            "Microsoft YaHei",
            (12 if compact else 14) if emphasize else (9 if compact else 10),
            QFont.Bold if emphasize else QFont.Normal,
        ))
        primary_label.setStyleSheet(f"color: {color}; background: transparent;")
        layout.addWidget(primary_label)
        if secondary:
            secondary_label = QLabel(secondary)
            secondary_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            secondary_label.setFont(QFont("Microsoft YaHei", 8 if compact else 9))
            secondary_label.setStyleSheet("color: #9399b2; background: transparent;")
            secondary_label.setWordWrap(not compact)
            layout.addWidget(secondary_label)
        layout.addStretch(1)
        self.market_table.setCellWidget(row, column, container)

    def _populate_market_table(self):
        """Render domestic quotes, fixed-price CSFloat listings and rental quotes."""
        sorting_enabled = self.market_table.isSortingEnabled()
        self.market_table.setSortingEnabled(False)
        self.market_table.setUpdatesEnabled(False)
        self.market_table.setRowCount(len(self._market_tracked_items))
        compact = getattr(self, "_responsive_mode", "wide") != "wide"
        for i, entry in enumerate(self._market_tracked_items):
            self.market_table.setRowHeight(i, 76 if compact else 86)

            image_label = QLabel()
            image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setContentsMargins(4, 4, 4, 4)
            image_label.setStyleSheet("background-color: #181825; border: none;")
            local_img = ImageCache.get_local_path(entry.get("market_hash_name", entry["name"]))
            if os.path.exists(local_img):
                thumbnail = self._market_thumbnail(local_img)
                if not thumbnail.isNull():
                    image_label.setPixmap(thumbnail)
            else:
                image_label.setText("图片")
            self.market_table.setCellWidget(i, 0, image_label)

            detail_name = (entry.get("detail") or {}).get("name_zh", "")
            display_name = CS2ItemSchema.chinese_display_name(
                detail_name or entry.get("name", ""),
                entry.get("market_hash_name", ""),
                entry.get("phase", "-"),
                entry.get("paint_index", ""),
            )
            entry["name"] = display_name
            if entry.get("phase") and entry["phase"] != "-":
                display_name += f"  [{entry['phase']}]"
            name_item = QTableWidgetItem(display_name)
            name_item.setToolTip(entry.get("market_hash_name", entry["name"]))
            name_item.setData(
                Qt.UserRole,
                self._market_watch_identity(
                    entry.get("market_hash_name", entry.get("name", "")),
                    entry.get("phase", "-"),
                ),
            )
            name_item.setFont(QFont("Microsoft YaHei", 11 if compact else 13, QFont.DemiBold))
            self.market_table.setItem(i, 1, name_item)

            lowest = self._market_number(entry.get("csqaq_min_sell_price"))
            if lowest <= 0:
                lowest = self._market_number(entry.get("csqaq_price"))
            platform = entry.get("csqaq_min_sell_platform", "")
            self._set_market_metric_cell(
                i,
                2,
                f"¥ {lowest:.2f}" if lowest > 0 else "暂无",
                platform if lowest > 0 and platform else "CSQAQ 未提供",
                "#a6e3a1" if lowest > 0 else "#6c7086",
                f"CSQAQ 国内最低平台：{platform or '暂无'}",
                emphasize=lowest > 0,
            )

            csfloat_cny = self._market_number(entry.get("csfloat_min_sell_cny"))
            csfloat_buy_cny = self._market_number(entry.get("csfloat_highest_buy_cny"))
            csfloat_status = str(entry.get("csfloat_status") or "")
            csfloat_buy_status = str(entry.get("csfloat_buy_status") or "")
            normalized_status = csfloat_status.removeprefix("skipped_")
            mhn = entry.get("market_hash_name", entry["name"])
            cached_query_mhn = str(entry.get("csfloat_query_mhn") or "")
            if cached_query_mhn and cached_query_mhn != mhn:
                # Never display another item's cached quote after editing or
                # remapping the tracked name.
                csfloat_cny = 0.0
                csfloat_buy_cny = 0.0
                normalized_status = "name_changed"
            csfloat_fresh = csfloat_quote_is_fresh(entry, mhn)

            def domestic_gap_text(value):
                if value <= 0 or lowest <= 0:
                    return "暂无国内对比"
                amount = value - lowest
                percent = amount / lowest * 100
                if compact:
                    return f"较国内 {amount:+.2f}"
                return f"较国内 {amount:+.2f}（{percent:+.1f}%）"

            sell_gap = domestic_gap_text(csfloat_cny)
            buy_gap = domestic_gap_text(csfloat_buy_cny)
            difference = (csfloat_cny - lowest) / lowest * 100 if csfloat_cny > 0 and lowest > 0 else None
            csfloat_secondary_parts = []
            if csfloat_cny > 0:
                csfloat_primary = f"底 ¥ {csfloat_cny:.2f}"
                csfloat_secondary_parts.append(sell_gap)
                if not csfloat_fresh and not compact:
                    stale_reason = {
                        "deferred": "排队刷新",
                        "rate_limited": "频控冷却",
                        "unauthorized": "密钥无效",
                        "forbidden": "访问被拒",
                        "network": "网络失败",
                        "invalid_json": "响应异常",
                    }.get(normalized_status, "待刷新")
                    csfloat_secondary_parts.append(f"底价缓存 · {stale_reason}")
            else:
                reason_map = {
                    "no_listing": "无一口价在售",
                    "missing_api_key": "未配置 API Key",
                    "unauthorized": "API Key 无效",
                    "forbidden": "访问被拒绝",
                    "rate_limited": "频控冷却中",
                    "network": "网络请求失败",
                    "invalid_json": "响应格式异常",
                    "deferred": "排队到下一轮",
                    "name_changed": "商品名称已变更，待刷新",
                }
                if normalized_status.startswith("http_"):
                    reason = "CSFloat 服务异常"
                elif normalized_status == "no_listing" and not csfloat_fresh:
                    reason = "无在售缓存已过期"
                else:
                    reason = reason_map.get(normalized_status, "尚未刷新")
                csfloat_primary = "底价暂无"
                csfloat_secondary_parts.append(reason)
            if csfloat_buy_cny > 0:
                csfloat_secondary_parts.append(
                    f"{'求' if compact else '最高求'} ¥ {csfloat_buy_cny:.2f} · {buy_gap}"
                )
            elif csfloat_buy_status and csfloat_buy_status not in {"no_listing", "no_buy_order"}:
                csfloat_secondary_parts.append("最高求购待刷新")
            if csfloat_cny <= 0:
                csfloat_color = "#6c7086"
            elif difference is not None and difference < -0.05:
                csfloat_color = "#a6e3a1"
            elif difference is not None and difference > 0.05:
                csfloat_color = "#f38ba8"
            else:
                csfloat_color = "#89dceb"
            fx_rate = self._market_number(entry.get("csfloat_fx_rate"))
            fx_source = str(entry.get("csfloat_fx_source") or "")
            fx_reference_date = str(entry.get("csfloat_fx_reference_date") or "")
            if fx_source == "csfloat":
                fx_source_text = "CSFloat 官网展示汇率"
            elif fx_source.startswith("ecb"):
                fx_source_text = "欧洲央行（ECB）参考汇率"
            elif fx_source == "manual":
                fx_source_text = "设置中的手工备用汇率"
            else:
                fx_source_text = "未记录"
            try:
                fetched_at = datetime.fromtimestamp(
                    float(entry.get("csfloat_fetched_at", 0) or 0)
                )
            except (TypeError, ValueError, OSError, OverflowError):
                fetched_at = None
            try:
                buy_fetched_at = datetime.fromtimestamp(
                    float(entry.get("csfloat_buy_fetched_at", 0) or 0)
                )
            except (TypeError, ValueError, OSError, OverflowError):
                buy_fetched_at = None
            buy_qty = int(self._market_number(entry.get("csfloat_highest_buy_qty")))
            hybrid_properties = entry.get("csfloat_highest_buy_hybrid_properties") or {}
            csfloat_tooltip = (
                "仅统计 CSFloat 的 buy_now 固定售价，已排除拍卖。"
                + (f"\n换算汇率：1 USD = ¥ {fx_rate:.4f}" if fx_rate > 0 else "")
                + f"\n汇率来源：{fx_source_text}"
                + (f"（{fx_reference_date}）" if fx_reference_date else "")
                + (f"\n报价时间：{fetched_at:%Y-%m-%d %H:%M:%S}" if fetched_at else "")
                + (f"\n最高求购数量：{buy_qty}" if buy_qty > 0 else "")
                + (f"\n最高求购时间：{buy_fetched_at:%Y-%m-%d %H:%M:%S}" if buy_fetched_at else "")
                + ("\n最高求购附带磨损/模板等条件。" if hybrid_properties else "")
                + "\n人民币价格仅用于对比，不含汇兑、平台或提现费用。"
            )
            self._set_market_metric_cell(
                i,
                3,
                csfloat_primary,
                "\n".join(csfloat_secondary_parts),
                csfloat_color,
                csfloat_tooltip,
                emphasize=csfloat_cny > 0,
            )

            eco_rent = self._market_number(entry.get("eco_min_rent"))
            eco_item = QTableWidgetItem(self._market_price_text(eco_rent, "ECO 无租金", "/天"))
            eco_item.setForeground(QColor("#89b4fa") if eco_rent > 0 else QColor("#6c7086"))
            eco_item.setFont(QFont("Microsoft YaHei", 10 if compact else 12, QFont.DemiBold))
            self.market_table.setItem(i, 4, eco_item)

            rent_columns = (
                (5, "c5_short_rent", "c5_long_rent", "CSQAQ 未提供 C5 报价", "#f9e2af"),
                (6, "yyyp_short_rent", "yyyp_long_rent", "CSQAQ 未提供悠悠报价", "#94e2d5"),
                (7, "igxe_short_rent", "igxe_long_rent", "CSQAQ 未提供 IGXE 报价", "#cba6f7"),
            )
            for column, short_key, long_key, unavailable, color in rent_columns:
                rent_text = self._market_rent_text(entry.get(short_key), entry.get(long_key), unavailable)
                rent_item = QTableWidgetItem(rent_text)
                available = self._market_number(entry.get(short_key)) > 0 or self._market_number(entry.get(long_key)) > 0
                rent_item.setForeground(QColor(color) if available else QColor("#6c7086"))
                rent_item.setFont(QFont("Microsoft YaHei", 10 if compact else 11, QFont.DemiBold if available else QFont.Normal))
                self.market_table.setItem(i, column, rent_item)

            self.market_table.setItem(
                i, 8, QTableWidgetItem(self._market_updated_text(entry))
            )
        self.market_table.setUpdatesEnabled(True)
        self.market_table.setSortingEnabled(sorting_enabled)
        self.market_empty_label.setVisible(self.market_table.rowCount() == 0)
        self._apply_market_filter()
        self.market_table.viewport().update()

    # ── 刷新全部行情（顺序队列） ──

    def _reset_market_rolling_cycle(self, refresh_items=None):
        """Start a new UI progress cycle for the currently observed items."""
        if refresh_items is None:
            refresh_items, _ = self._collect_all_market_refresh_items()
        identities = {
            self._market_watch_identity(
                entry.get("market_hash_name", entry.get("name", "")),
                entry.get("phase", "-"),
            )
            for entry in refresh_items
        }
        self._market_rolling_cycle_known = identities
        self._market_rolling_cycle_pending = set(identities)
        self._market_rolling_cycle_total = len(identities)
        self._update_market_rolling_progress()

    def _sync_market_rolling_cycle(self, refresh_items):
        """Keep the progress denominator correct when the watch list changes."""
        identities = {
            self._market_watch_identity(
                entry.get("market_hash_name", entry.get("name", "")),
                entry.get("phase", "-"),
            )
            for entry in refresh_items
        }
        added = identities - self._market_rolling_cycle_known
        removed = self._market_rolling_cycle_known - identities
        if added or removed:
            self._market_rolling_cycle_pending.difference_update(removed)
            self._market_rolling_cycle_pending.update(added)
            self._market_rolling_cycle_total = max(
                len(identities), self._market_rolling_cycle_total - len(removed) + len(added)
            )
            self._market_rolling_cycle_known = identities
        self._update_market_rolling_progress()

    def _create_global_sync_button(self):
        """Create another view of the single shared background-sync control."""
        button = QPushButton("数据同步 --")
        button.setMinimumWidth(150)
        button.setObjectName("primaryBtn")
        self._set_button_icon(button, "history", "#11111b")
        button.clicked.connect(self._toggle_market_rolling_sync)
        self._sync_progress_buttons.append(button)
        self._update_market_rolling_progress()
        return button

    def _update_market_rolling_progress(self):
        buttons = getattr(self, "_sync_progress_buttons", [])
        if not buttons:
            return
        cooldown = CSFloatClient.cooldown_remaining()
        cooldown_reason = CSFloatClient.cooldown_reason()
        total = self._market_rolling_cycle_total
        if cooldown > 0:
            text = f"同步等待 {cooldown}s"
        elif total <= 0:
            text = "同步暂停" if not self._market_rolling_sync_enabled else "同步 --"
        else:
            completed = max(0, min(total, total - len(self._market_rolling_cycle_pending)))
            percent = int(round(completed * 100 / total))
            prefix = "同步暂停" if not self._market_rolling_sync_enabled else "同步"
            if self._csfloat_buy_refresh_in_progress:
                activity = " · CSF"
            else:
                activity = ""
            text = f"{prefix} {percent}%（{completed}/{total}）{activity}"
        state = "运行中，点击暂停" if self._market_rolling_sync_enabled else "已暂停，点击继续"
        tooltip = (
            f"全局后台数据同步{state}。\n"
            "统一控制资产本地更新、大盘逐件行情和 CSFloat 求购监测；"
            "所有工作区共用同一个服务端自适应请求间隔和冷却时间。"
            + (f"\n当前反馈：{cooldown_reason}，剩余约 {cooldown} 秒。" if cooldown else "")
        )
        for button in buttons:
            button.setText(text)
            button.setToolTip(tooltip)

    def _request_global_sync_now(self):
        """Queue one dispatcher cycle without bypassing shared pacing/cooldown."""
        if not self._market_rolling_sync_enabled:
            self.lbl_market_update.setText("全局数据同步已暂停；点击“同步暂停”按钮继续")
            return False
        cooldown = CSFloatClient.cooldown_remaining()
        if cooldown > 0:
            reason = CSFloatClient.cooldown_reason() or "CSFloat 服务端频控"
            self.lbl_market_update.setText(
                f"全局同步等待：{reason}，约 {cooldown} 秒后自动继续"
            )
            self.csfloat_buy_status_label.setText(
                f"{reason} · 约 {cooldown} 秒后由全局调度自动继续"
            )
            self._update_market_rolling_progress()
            return False
        if self._market_refresh_is_running() or self._csfloat_buy_refresh_in_progress:
            self.lbl_market_update.setText("全局同步已在运行，本次操作沿用当前队列")
            return False
        self._run_rolling_market_refresh()
        return True

    def _toggle_market_rolling_sync(self):
        """Pause/resume every automatic data source behind the shared buttons."""
        self._market_rolling_sync_enabled = not self._market_rolling_sync_enabled
        if self._market_rolling_sync_enabled:
            self.market_rolling_refresh_timer.start(MARKET_ROLLING_REFRESH_SECONDS * 1000)
            self.update_timer_interval()
            self._update_market_rolling_progress()
            self.lbl_market_update.setText("后台数据同步已继续")
            QTimer.singleShot(0, self._run_rolling_market_refresh)
            return

        self.market_rolling_refresh_timer.stop()
        self.timer.stop()
        self._update_market_rolling_progress()
        if (
            self._market_refresh_is_running()
            or self._csfloat_buy_refresh_in_progress
        ):
            self.lbl_market_update.setText("后台数据同步已暂停（当前请求完成后停止）")
        else:
            self.lbl_market_update.setText("后台数据同步已暂停")

    def _run_rolling_market_refresh(self):
        """Refresh one oldest item without blocking category navigation."""
        if (
            not self._market_rolling_sync_enabled
            or self._market_search_in_progress
            or self._market_refresh_is_running()
            or self._csfloat_buy_refresh_in_progress
        ):
            return
        cooldown = CSFloatClient.cooldown_remaining()
        if cooldown > 0:
            reason = CSFloatClient.cooldown_reason() or "CSFloat 服务端频控"
            self.lbl_market_update.setText(
                f"全局同步等待：{reason}，约 {cooldown} 秒后自动继续"
            )
            self.csfloat_buy_status_label.setText(
                f"{reason} · 约 {cooldown} 秒后自动继续"
            )
            self._update_market_rolling_progress()
            return
        if self._maybe_auto_refresh_csfloat_buy_orders():
            return
        refresh_items, refresh_groups = self._collect_all_market_refresh_items()
        if not refresh_items:
            self._reset_market_rolling_cycle(refresh_items)
            return
        self._sync_market_rolling_cycle(refresh_items)
        if not self._market_rolling_cycle_pending:
            # Keep 100% visible until the next timer tick, then start a new
            # round so the indicator reads as a completed full scan.
            self._reset_market_rolling_cycle(refresh_items)
        candidates = [
            entry for entry in refresh_items
            if self._market_watch_identity(
                entry.get("market_hash_name", entry.get("name", "")),
                entry.get("phase", "-"),
            ) in self._market_rolling_cycle_pending
        ]
        target = min(
            candidates or refresh_items,
            key=lambda entry: float(entry.get("rolling_refreshed_at", 0) or 0),
        )
        identity = self._market_watch_identity(
            target.get("market_hash_name", target.get("name", "")),
            target.get("phase", "-"),
        )
        self._refresh_all_market_data(
            fast_only=False,
            background=True,
            refresh_items=[target],
            refresh_groups={identity: refresh_groups.get(identity, [target])},
        )

    def _collect_all_market_refresh_items(self):
        """Collect every category and deduplicate identical network lookups."""
        groups: dict[str, list[dict]] = {}
        for category in self._market_categories.values():
            for entry in category.get("items", []):
                self._apply_schema_mapping(entry)
                identity = self._market_watch_identity(
                    entry.get("market_hash_name", entry.get("name", "")),
                    entry.get("phase", "-"),
                )
                groups.setdefault(identity, []).append(entry)
        return [entries[0] for entries in groups.values()], groups

    def _refresh_all_market_data(
        self,
        force_eco: bool = False,
        fast_only: bool = True,
        background: bool = False,
        refresh_items: list | None = None,
        refresh_groups: dict | None = None,
    ):
        """Run either the fast batch layer or one rolling detailed item."""
        if self._csfloat_buy_refresh_in_progress:
            if not background:
                QMessageBox.information(
                    self,
                    "数据同步中",
                    "CSFloat 求购正在读取，请等待当前全局请求完成。",
                )
            return
        if refresh_items is None or refresh_groups is None:
            refresh_items, refresh_groups = self._collect_all_market_refresh_items()
        if not refresh_items:
            if not background:
                QMessageBox.warning(self, "提示", "所有大盘分类中都没有饰品，请先搜索添加！")
            return

        # 如果已有刷新线程在运行，不允许重复启动
        if self._market_refresh_thread and self._market_refresh_thread.isRunning():
            if not background:
                QMessageBox.information(self, "提示", "正在刷新行情中，请等待完成...")
            return

        token = self.db.get_config("csqaq_token")
        eco_partner = self.db.get_config("eco_partner_id")
        eco_rsa = self.db.get_config("eco_rsa_key")
        csfloat_api_key = self.db.get_config("csfloat_api_key")
        auto_usd_cny_rate = self.db.get_config("auto_usd_cny_rate") != "0"
        try:
            usd_cny_rate = float(self.db.get_config("usd_cny_rate") or 7.20)
        except (TypeError, ValueError):
            usd_cny_rate = 7.20

        if background:
            self.lbl_market_update.setText(
                f"后台滚动刷新：{refresh_items[0].get('name', '饰品')[:18]}"
            )
        else:
            category_count = len(self._market_categories)
            label = "批量更新国内最低价" if fast_only else "完整刷新"
            self.lbl_market_update.setText(
                f"{label}：{category_count} 个分类、{len(refresh_items)} 个饰品..."
            )
            self.title_bar.set_sync_status("行情刷新中", "#89b4fa")
            self._set_market_category_controls_enabled(False)
        self._market_refresh_groups = refresh_groups
        self._market_refresh_background = background
        self._market_refresh_fast_only = fast_only

        self._market_refresh_thread = QThread()
        self._market_refresh_worker = MarketRefreshWorker()
        # The worker must never mutate dictionaries owned by the GUI thread.
        items_copy = copy.deepcopy(refresh_items)
        self._market_refresh_worker.configure_refresh(
            token, eco_partner, eco_rsa, items_copy,
            self._build_market_hash_name_for_entry, force_eco,
            csfloat_api_key, usd_cny_rate, auto_usd_cny_rate,
            fast_only, background,
        )
        self._market_refresh_worker.moveToThread(self._market_refresh_thread)

        # 连接信号
        self._market_refresh_worker.progress.connect(self._on_market_refresh_progress)
        self._market_refresh_worker.row_updated.connect(self._on_market_refresh_row_updated)
        self._market_refresh_worker.result_ready.connect(self._on_market_refresh_finished)
        self._market_refresh_worker.error.connect(lambda msg: logger.warning(f"[大盘刷新] {msg}"))

        # 安全清理
        self._market_refresh_worker.task_completed.connect(self._market_refresh_thread.quit)
        self._market_refresh_worker.task_completed.connect(self._market_refresh_worker.deleteLater)
        self._market_refresh_thread.finished.connect(self._market_refresh_thread.deleteLater)
        self._market_refresh_thread.finished.connect(self._cleanup_market_refresh_thread)

        # 启动
        self._market_refresh_thread.started.connect(self._market_refresh_worker.run_refresh)
        self._market_refresh_thread.start()

    def _build_market_hash_name_for_entry(self, entry: dict) -> str:
        """从 entry 构建 market_hash_name"""
        return entry.get("market_hash_name", entry["name"])

    def _on_market_refresh_progress(self, current: int, total: int, message: str):
        """刷新进度更新"""
        self.lbl_market_update.setText(f"刷新中: {message}")

    def _on_market_refresh_row_updated(self, row: int):
        """Keep the worker lightweight; the completed snapshot is rendered once."""
        # Rebuilding and writing the entire table for every CSFloat row makes a
        # large watch list O(n²).  Progress remains visible in the header, and
        # the finished handler atomically renders/persists the complete result.
        return

    def _on_market_refresh_finished(self, result):
        """Merge an isolated worker snapshot into every category on the GUI thread."""
        identity_fields = {
            "key", "name", "phase", "market_hash_name", "image_url", "links", "schema_id"
        }
        refreshed_items = result.get("items", []) if isinstance(result, dict) else []
        for entries, refreshed in zip(self._market_refresh_groups.values(), refreshed_items):
            if not entries:
                continue
            for duplicate in entries:
                for field, value in refreshed.items():
                    if field not in identity_fields:
                        duplicate[field] = copy.deepcopy(value)

        completed_at = datetime.now().isoformat(timespec="seconds")
        for entries in self._market_refresh_groups.values():
            for entry in entries:
                entry["updated_at"] = completed_at
                if self._market_refresh_background:
                    entry["rolling_refreshed_at"] = int(time.time())
        rolling_cycle_completed = False
        if self._market_refresh_background:
            self._market_rolling_cycle_pending.difference_update(
                self._market_refresh_groups.keys()
            )
            rolling_cycle_completed = not self._market_rolling_cycle_pending
            self._update_market_rolling_progress()
        self._populate_market_table()
        self._save_market_cache()
        if not self._market_refresh_background or rolling_cycle_completed:
            self.load_data()
        eco_status = str(result.get("eco_status_text", ""))
        csfloat_status = str(result.get("csfloat_status_text", ""))
        status_parts = [status for status in (eco_status, csfloat_status) if status]
        suffix = f" · {' · '.join(status_parts)}" if status_parts else ""
        if self._market_refresh_background:
            self.lbl_market_update.setText(f"后台滚动已更新 {QTime.currentTime().toString('HH:mm:ss')}{suffix}")
        else:
            self.lbl_market_update.setText(f"最后更新: {QTime.currentTime().toString('HH:mm:ss')}{suffix}")
            self.title_bar.set_sync_status("行情已更新", "#a6e3a1")
        self._market_refresh_groups = {}

    def _cleanup_market_refresh_thread(self):
        """清理市场刷新线程引用"""
        self._market_refresh_thread = None
        self._market_refresh_worker = None
        self._market_refresh_background = False
        self._market_refresh_fast_only = False
        self._update_market_category_controls()

    # ── 市场数据缓存 ──

    def _save_market_cache(self):
        """Persist every category, including its independently cached quotes and links."""
        self._ensure_market_categories()
        self._market_categories[self._active_market_category_id]["items"] = self._market_tracked_items

        def cache_entry(entry):
            key = entry["key"]
            return {
                "key": key,
                "name": entry["name"],
                "phase": entry.get("phase", "-"),
                "market_hash_name": entry.get("market_hash_name", entry["name"]),
                "image_url": entry.get("image_url", ""),
                "schema_id": entry.get("schema_id", ""),
                "paint_index": entry.get("paint_index", ""),
                "csqaq_price": entry.get("csqaq_price", 0.0),
                "csqaq_good_id": entry.get("csqaq_good_id", ""),
                "csqaq_min_sell_price": entry.get("csqaq_min_sell_price", entry.get("csqaq_price", 0.0)),
                "csqaq_min_sell_platform": entry.get("csqaq_min_sell_platform", ""),
                "csqaq_detail_fetched_at": entry.get("csqaq_detail_fetched_at", 0),
                "csfloat_fetched_at": entry.get("csfloat_fetched_at", 0),
                "csfloat_last_attempt_at": entry.get("csfloat_last_attempt_at", 0),
                "csfloat_query_mhn": entry.get("csfloat_query_mhn", ""),
                "csfloat_fx_rate": entry.get("csfloat_fx_rate", 0.0),
                "csfloat_fx_source": entry.get("csfloat_fx_source", ""),
                "csfloat_fx_reference_date": entry.get("csfloat_fx_reference_date", ""),
                "csfloat_price_cents": entry.get("csfloat_price_cents", 0),
                "csfloat_min_sell_usd": entry.get("csfloat_min_sell_usd", 0.0),
                "csfloat_min_sell_cny": entry.get("csfloat_min_sell_cny", 0.0),
                "csfloat_listing_id": entry.get("csfloat_listing_id", ""),
                "csfloat_float_value": entry.get("csfloat_float_value"),
                "csfloat_paint_seed": entry.get("csfloat_paint_seed"),
                "csfloat_status": entry.get("csfloat_status", ""),
                "csfloat_buy_fetched_at": entry.get("csfloat_buy_fetched_at", 0),
                "csfloat_buy_last_attempt_at": entry.get("csfloat_buy_last_attempt_at", 0),
                "csfloat_highest_buy_price_cents": entry.get("csfloat_highest_buy_price_cents", 0),
                "csfloat_highest_buy_usd": entry.get("csfloat_highest_buy_usd", 0.0),
                "csfloat_highest_buy_cny": entry.get("csfloat_highest_buy_cny", 0.0),
                "csfloat_highest_buy_qty": entry.get("csfloat_highest_buy_qty", 0),
                "csfloat_highest_buy_hybrid_properties": entry.get("csfloat_highest_buy_hybrid_properties", {}),
                "csfloat_buy_status": entry.get("csfloat_buy_status", ""),
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
                "rolling_refreshed_at": entry.get("rolling_refreshed_at", 0),
                "detail": entry.get("detail", {}),
            }

        categories = []
        for category_id, category in self._market_categories.items():
            categories.append({
                "id": category_id,
                "name": category["name"],
                "items": [cache_entry(entry) for entry in category.get("items", [])],
            })
        payload = {
            "format": "market_categories_v1",
            "active_category_id": self._active_market_category_id,
            "categories": categories,
        }
        self.db.save_market_watchlist(payload)
        MarketCache.save(payload)

    @staticmethod
    def _normalize_market_phase(value) -> str:
        text = str(value or "-").strip()
        compact = text.upper().replace(" ", "")
        aliases = {
            "": "-", "-": "-", "NONE": "-", "N/A": "-",
            "P1": "P1", "P2": "P2", "P3": "P3", "P4": "P4", "P1/P3": "P1 / P3",
            "RUBY": "Ruby", "红宝石": "Ruby",
            "SAPPHIRE": "Sapphire", "蓝宝石": "Sapphire",
            "EMERALD": "Emerald", "绿宝石": "Emerald",
            "BLACKPEARL": "Black Pearl", "黑珍珠": "Black Pearl",
        }
        return aliases.get(compact, "")

    @classmethod
    def _market_watch_identity(cls, market_hash_name, phase) -> str:
        normalized_phase = cls._normalize_market_phase(phase) or str(phase or "-").strip()
        phase_key = normalized_phase.upper().replace(" ", "")
        if phase_key in {"P1", "P3", "P1/P3"}:
            phase_key = "P1/P3"
        return f"{str(market_hash_name).strip()}|{phase_key}"

    def _normalize_ai_market_item(self, raw_item: dict) -> tuple[dict | None, str]:
        """Validate an AI item locally and turn it into a market-watch entry."""
        name = str(raw_item.get("name", "") or "").strip()
        market_hash_name = str(raw_item.get("market_hash_name", raw_item.get("mhn", "")) or "").strip()
        phase = self._normalize_market_phase(raw_item.get("phase", "-"))
        if not phase:
            return None, "相位必须是 P1/P2/P3/P4、Ruby/Sapphire/Emerald/Black Pearl 或 -。"

        mapped = CS2ItemSchema.lookup(name) if name else None
        mapped = mapped or (CS2ItemSchema.lookup(market_hash_name) if market_hash_name else None)
        status = ""
        if mapped:
            canonical_mhn = mapped["market_hash_name"]
            if market_hash_name and market_hash_name != canonical_mhn:
                status = "已用本地映射校正英文名"
            else:
                status = "已通过本地映射校验"
            market_hash_name = canonical_mhn
            name = mapped.get("name_zh", "") or "未知饰品"
            image_url = mapped.get("image", "")
        else:
            if not name:
                return None, "缺少中文名称，且英文名未命中本地映射。"
            if not market_hash_name or "|" not in market_hash_name:
                return None, "缺少可用的英文 market_hash_name。"
            image_url = ""
            status = "未命中本地映射，将使用 AI 提供的英文名"

        entry = {
            "key": f"{market_hash_name}|{phase}",
            "name": name,
            "phase": phase,
            "market_hash_name": market_hash_name,
            "image_url": image_url,
            "csqaq_price": 0.0,
            "eco_min_rent": 0.0,
            "c5_short_rent": 0.0,
            "c5_long_rent": 0.0,
            "yyyp_short_rent": 0.0,
            "yyyp_long_rent": 0.0,
            "igxe_short_rent": 0.0,
            "igxe_long_rent": 0.0,
            "links": {},
            "updated_at": "",
            "detail": {},
        }
        return entry, status

    def _open_ai_market_import(self):
        dialog = MarketAIImportDialog(self._normalize_ai_market_item, self)
        if dialog.exec() != QDialog.Accepted or not dialog.validated_items:
            return

        existing = {
            self._market_watch_identity(entry.get("market_hash_name", entry.get("name", "")), entry.get("phase", "-"))
            for entry in self._market_tracked_items
        }
        added = 0
        skipped = 0
        for entry in dialog.validated_items:
            identity = self._market_watch_identity(entry["market_hash_name"], entry["phase"])
            if identity in existing:
                skipped += 1
                continue
            self._market_tracked_items.append(entry)
            existing.add(identity)
            added += 1

        if added:
            self._deduplicate_market_tracked_items()
            self._populate_market_table()
            self._refresh_market_category_selector()
            self._save_market_cache()
        QMessageBox.information(
            self,
            "AI 批量添加完成",
            f"已添加 {added} 条，跳过 {skipped} 条重复项。\n"
            "新条目已保存到本地观察列表；点击“立即同步”后会按全局频率获取报价。",
        )

    # ── 搜索并添加 ──

    def query_csqaq_market(self):
        """Resolve human text locally first, then add explicit user selections."""
        keyword = self.market_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入要查询的饰品名称！")
            return

        local_matches = CS2ItemSchema.search(keyword, limit=100)
        if local_matches:
            dialog = MarketItemSearchDialog(keyword, local_matches, self)
            if dialog.exec() != QDialog.Accepted:
                return
            selected_records = dialog.selected_records()
            if not selected_records:
                QMessageBox.information(self, "添加饰品", "请至少选择一个饰品。")
                return
            self._add_local_market_search_records(keyword, selected_records)
            return

        # The CSQAQ batch endpoint accepts full market hash names, not a
        # keyword search.  Keep it only as a fallback for unmapped exact names.
        if "|" not in keyword or "(" not in keyword:
            QMessageBox.information(
                self,
                "未找到本地映射",
                "未找到匹配饰品。可尝试补充武器、皮肤或磨损，例如“蝴蝶刀 伽马多普勒”。",
            )
            return

        token = self.db.get_config("csqaq_token")
        if not token:
            QMessageBox.warning(self, "提示", "请先在【⚙️ 系统与费率设置】中填入 CSQAQ 的 ApiToken！")
            return

        self.market_search_btn.setEnabled(False)
        self.market_search_btn.setText("搜索中...")
        self._market_search_in_progress = True
        self._update_market_category_controls()

        # 直接使用批量价格查询接口，传入关键词作为 market_hash_name
        self._start_worker(
            worker_fn=lambda w: w.batch_price_csqaq(token, [keyword]),
            on_finished=self._on_search_add_result,
            on_error=self._on_csqaq_error,
        )

    @staticmethod
    def _phase_hint_from_search(keyword):
        return phase_hint_from_search(keyword)

    def _add_local_market_search_records(self, keyword, records):
        query_phase = self._phase_hint_from_search(keyword)
        existing = {
            self._market_watch_identity(entry.get("market_hash_name", entry.get("name", "")), entry.get("phase", "-"))
            for entry in self._market_tracked_items
        }
        added = 0
        skipped = 0
        for record in records:
            phase = self._normalize_market_phase(record.get("phase", query_phase)) or query_phase
            market_hash_name = record.get("market_hash_name", "")
            if not market_hash_name:
                continue
            identity = self._market_watch_identity(market_hash_name, phase)
            if identity in existing:
                skipped += 1
                continue
            entry = {
                "key": f"{market_hash_name}|{phase}",
                "name": record.get("name_zh", market_hash_name),
                "phase": phase,
                "market_hash_name": market_hash_name,
                "image_url": record.get("image", ""),
                "schema_id": record.get("id", ""),
                "paint_index": record.get("paint_index", ""),
                "csqaq_price": 0.0,
                "eco_min_rent": 0.0,
                "c5_short_rent": 0.0,
                "c5_long_rent": 0.0,
                "yyyp_short_rent": 0.0,
                "yyyp_long_rent": 0.0,
                "igxe_short_rent": 0.0,
                "igxe_long_rent": 0.0,
                "links": {},
                "updated_at": "",
                "detail": {},
            }
            self._apply_schema_mapping(entry)
            self._market_tracked_items.append(entry)
            existing.add(identity)
            added += 1

        if added:
            self._deduplicate_market_tracked_items()
            self._populate_market_table()
            self._refresh_market_category_selector()
            self._save_market_cache()
        QMessageBox.information(
            self,
            "添加饰品",
            f"已添加 {added} 条，跳过 {skipped} 条重复项。\n点击“立即同步”即可排队查询报价。",
        )

    def _on_search_add_result(self, result):
        """CSQAQ 搜索回调：自动将结果添加到大盘"""
        self.market_search_btn.setEnabled(True)
        self.market_search_btn.setText("搜索并添加")
        self._market_search_in_progress = False
        self._update_market_category_controls()

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
        self._refresh_market_category_selector()
        self._save_market_cache()
        QMessageBox.information(self, "成功", f"已添加 {added} 个饰品到大盘！")

    def _on_csqaq_error(self, error_msg):
        """CSQAQ 错误回调"""
        self.market_search_btn.setEnabled(True)
        self.market_search_btn.setText("搜索并添加")
        self._market_search_in_progress = False
        self._update_market_category_controls()
        QMessageBox.critical(self, "API 错误", error_msg)

    def _on_bind_csqaq_ip(self):
        """Bind in a worker so a slow network never freezes the GUI thread."""
        token = self.db.get_config("csqaq_token")
        if not token:
            QMessageBox.warning(self, "提示", "请先在设置中填入 CSQAQ ApiToken！")
            return
        self.bind_ip_btn.setEnabled(False)
        self.bind_ip_btn.setText("正在绑定…")

        def task(worker):
            from modules.csqaq_client import CSQAQClient
            result = CSQAQClient(token).bind_local_ip()
            if not worker._is_canceled:
                worker.finished.emit(("csqaq_bind_ip", result))

        self._start_worker(
            task,
            self._on_bind_csqaq_ip_finished,
            self._on_bind_csqaq_ip_error,
        )

    def _on_bind_csqaq_ip_finished(self, payload):
        _tag, result = payload
        self.bind_ip_btn.setEnabled(True)
        self.bind_ip_btn.setText("一键绑定当前公网 IP")
        if result.get("code") == 200:
            ip = result.get("data", "unknown")
            QMessageBox.information(self, "绑定成功", f"公网 IP {ip} 已绑定到 CSQAQ Token 白名单！")
        else:
            QMessageBox.warning(self, "绑定失败", result.get("msg", "未知错误"))

    def _on_bind_csqaq_ip_error(self, message):
        self.bind_ip_btn.setEnabled(True)
        self.bind_ip_btn.setText("一键绑定当前公网 IP")
        QMessageBox.warning(self, "绑定失败", str(message))

    # ═══════════════════════════════════════════
    # Tab 3: CSFloat 求购监测
    # ═══════════════════════════════════════════

    def init_csfloat_buy_orders_tab(self):
        layout = QVBoxLayout(self.tab_csfloat_buy)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("CSFloat 求购监测")
        title.setObjectName("titleLabel")
        header.addWidget(title)
        header.addStretch()
        self.csfloat_buy_status_label = QLabel("尚未读取")
        self.csfloat_buy_status_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        header.addWidget(self.csfloat_buy_status_label)
        profile_button = QPushButton("打开 CSFloat 求购")
        profile_button.setToolTip("打开 CSFloat Profile 求购管理页")
        profile_button.clicked.connect(self._open_csfloat_profile)
        header.addWidget(profile_button)
        self.csfloat_sync_progress_button = self._create_global_sync_button()
        header.addWidget(self.csfloat_sync_progress_button)
        layout.addLayout(header)

        summary = QGridLayout()
        summary.setHorizontalSpacing(10)
        summary.setVerticalSpacing(10)
        self.csfloat_balance_card = self.create_card("可用余额", "$ --", "#a6e3a1")
        self.csfloat_pending_card = self.create_card("待结算余额", "$ --", "#f9e2af")
        self.csfloat_order_count_card = self.create_card("有效求购", "-- 单", "#89b4fa")
        self.csfloat_top_count_card = self.create_card("处于最高价位", "-- 单", "#cba6f7")
        for column, card in enumerate((
            self.csfloat_balance_card,
            self.csfloat_pending_card,
            self.csfloat_order_count_card,
            self.csfloat_top_count_card,
        )):
            summary.addWidget(card, 0, column)
            summary.setColumnStretch(column, 1)
        layout.addLayout(summary)

        self.csfloat_buy_table = QTableWidget()
        self.csfloat_buy_table.setColumnCount(7)
        self.csfloat_buy_table.setHorizontalHeaderLabels([
            "饰品（点击打开）", "我的求购", "数量 / 条件", "市场最高求购",
            "排名与差价", "近期成交接近度", "购买力参考",
        ])
        header_view = self.csfloat_buy_table.horizontalHeader()
        header_view.setFont(QFont("Microsoft YaHei", 10, QFont.DemiBold))
        header_view.setSectionResizeMode(0, QHeaderView.Interactive)
        self.csfloat_buy_table.setColumnWidth(0, 310)
        for column in range(1, 7):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header_view.setStretchLastSection(False)
        self.csfloat_buy_table.verticalHeader().setVisible(False)
        self.csfloat_buy_table.verticalHeader().setDefaultSectionSize(78)
        self.csfloat_buy_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.csfloat_buy_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.csfloat_buy_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.csfloat_buy_table.setAlternatingRowColors(True)
        self.csfloat_buy_table.setStyleSheet("alternate-background-color: #1e1e2e;")
        self.csfloat_buy_table.cellClicked.connect(self._open_csfloat_buy_item_from_cell)
        layout.addWidget(self.csfloat_buy_table, 1)

    @staticmethod
    def _set_card_value(card, text, color=None):
        value = card.findChild(QLabel, "cardValue") if card is not None else None
        if value is None:
            return
        value.setText(text)
        if color:
            value.setStyleSheet(f"color: {color}; font-size: 19px; font-weight: bold;")

    def _configured_usd_cny_rate(self):
        try:
            rate = float(self.db.get_config("usd_cny_rate") or 7.20)
        except (TypeError, ValueError):
            rate = 7.20
        return rate if 1 <= rate <= 20 else 7.20

    @staticmethod
    def _fx_source_label(source):
        return {
            "CSFloat": "CSFloat 官网汇率",
            "ECB": "欧洲央行参考汇率",
            "manual": "手动备用汇率",
        }.get(str(source or ""), str(source or "备用汇率"))

    @staticmethod
    def _csfloat_error_text(result):
        error = str((result or {}).get("error") or "unknown")
        labels = {
            "missing_api_key": "未配置 CSFloat API Key",
            "unauthorized": "CSFloat API Key 无效",
            "forbidden": "CSFloat 拒绝访问",
            "rate_limited": "触发 CSFloat 频控，请稍后重试",
            "network": "网络请求失败",
            "invalid_json": "CSFloat 返回格式异常",
        }
        if error == "rate_limited":
            source = str((result or {}).get("rate_limit_source") or "CSFloat 服务端频控")
            retry_after = int((result or {}).get("retry_after") or 0)
            return source + (f"，约 {retry_after} 秒后自动继续" if retry_after else "")
        if error.startswith("http_"):
            return f"CSFloat 服务返回 {error.removeprefix('http_')}"
        return labels.get(error, error)

    def _maybe_auto_refresh_csfloat_buy_orders(self):
        """Run due buy-order monitoring inside the shared rolling scheduler."""
        if not self._market_rolling_sync_enabled:
            return False
        api_key = self.db.get_config("csfloat_api_key").strip()
        if not api_key:
            return False
        elapsed = time.time() - self._csfloat_buy_last_auto_refresh_at
        if elapsed < CSFLOAT_BUY_AUTO_REFRESH_SECONDS:
            return False
        cooldown = CSFloatClient.cooldown_remaining()
        if cooldown > 0:
            reason = CSFloatClient.cooldown_reason() or "CSFloat 服务端频控"
            self.csfloat_buy_status_label.setText(
                f"{reason} · 约 {cooldown} 秒后由全局调度自动继续"
            )
            return False
        return self._refresh_csfloat_buy_orders(background=True)

    def _refresh_csfloat_buy_orders(self, background=False):
        if self._csfloat_buy_refresh_in_progress:
            return False
        if self._market_refresh_is_running():
            return False
        api_key = self.db.get_config("csfloat_api_key").strip()
        if not api_key:
            self.csfloat_buy_status_label.setText("请先在设置中填写 API Key")
            if not background:
                QMessageBox.information(self, "CSFloat 求购", "请先在设置中填写并保存 CSFloat API Key。")
            return False
        cooldown = CSFloatClient.cooldown_remaining()
        if cooldown > 0:
            reason = CSFloatClient.cooldown_reason() or "CSFloat 服务端频控"
            self.csfloat_buy_status_label.setText(
                f"{reason} · 约 {cooldown} 秒后由全局调度自动继续"
            )
            return False

        self._csfloat_buy_refresh_in_progress = True
        self._csfloat_buy_refresh_background = bool(background)
        prefix = "后台自动读取" if background else "读取"
        self.csfloat_buy_status_label.setText(f"{prefix}账户和求购；详细分析约需 20–30 秒")
        self._update_market_rolling_progress()

        manual_fx_rate = self._configured_usd_cny_rate()
        auto_fx_rate = self.db.get_config("auto_usd_cny_rate") != "0"
        detail_cursor = self._csfloat_buy_detail_cursor
        cached_details = {
            str((record.get("order") or {}).get("id") or ""): copy.deepcopy(
                record.get("detail") or {}
            )
            for record in self._csfloat_buy_order_rows
            if str((record.get("order") or {}).get("id") or "")
        }

        def task(worker):
            if auto_fx_rate:
                fx_result = ExchangeRateClient().get_usd_cny(manual_fx_rate)
            else:
                fx_result = {
                    "rate": manual_fx_rate,
                    "source": "manual",
                    "reference_date": "",
                    "status": "manual_configured",
                }
            client = CSFloatClient(api_key)
            account = client.get_account()
            if not account.get("success"):
                worker.error.emit(self._csfloat_error_text(account))
                return
            order_result = client.get_my_buy_orders(limit=100)
            if not order_result.get("success"):
                worker.error.emit(self._csfloat_error_text(order_result))
                return

            orders = order_result.get("orders", [])
            details = cached_details
            if orders:
                offset = detail_cursor % len(orders)
                detail_orders = (orders[offset:] + orders[:offset])[
                    :CSFLOAT_BUY_DETAIL_LIMIT
                ]
                next_detail_cursor = (offset + len(detail_orders)) % len(orders)
            else:
                detail_orders = []
                next_detail_cursor = 0
            for order in detail_orders:
                if worker._is_canceled:
                    return
                order_id = str(order.get("id") or "")
                market_hash_name = str(order.get("market_hash_name") or "")
                detail = {
                    "listing": {},
                    "market_order": {},
                    "sales": [],
                    "detail_error": "",
                }
                listing = client.get_lowest_buy_now(market_hash_name)
                detail["listing"] = listing
                if listing.get("success") and listing.get("found"):
                    market_order = client.get_highest_buy_order(
                        listing.get("listing_id", ""), limit=50
                    )
                    detail["market_order"] = market_order
                elif not listing.get("success"):
                    detail["detail_error"] = self._csfloat_error_text(listing)

                sales_result = client.get_recent_sales(market_hash_name)
                if sales_result.get("success"):
                    detail["sales"] = sales_result.get("sales", [])
                elif not detail["detail_error"]:
                    detail["detail_error"] = self._csfloat_error_text(sales_result)
                details[order_id] = detail

                if (
                    listing.get("error") == "rate_limited"
                    or sales_result.get("error") == "rate_limited"
                ):
                    break

            worker.finished.emit(("csfloat_buy_orders", {
                "account": account,
                "orders": orders,
                "count": order_result.get("count", len(orders)),
                "details": details,
                "detail_limit": CSFLOAT_BUY_DETAIL_LIMIT,
                "detail_updated": len(detail_orders),
                "next_detail_cursor": next_detail_cursor,
                "fx": fx_result,
            }))

        self._start_worker(
            worker_fn=task,
            on_finished=self._on_csfloat_buy_orders_loaded,
            on_error=self._on_csfloat_buy_orders_error,
        )
        return True

    def _on_csfloat_buy_orders_error(self, error_message):
        was_background = self._csfloat_buy_refresh_background
        self._csfloat_buy_refresh_in_progress = False
        self._csfloat_buy_refresh_background = False
        if was_background:
            self._csfloat_buy_last_auto_refresh_at = time.time()
        self.csfloat_buy_status_label.setText(str(error_message))
        self._update_market_rolling_progress()
        if was_background:
            logger.warning("[CSFloat求购] 后台自动同步失败: %s", error_message)
        else:
            QMessageBox.warning(self, "CSFloat 求购读取失败", str(error_message))

    def _on_csfloat_buy_orders_loaded(self, result):
        _tag, payload = result
        self._csfloat_buy_refresh_in_progress = False
        self._csfloat_buy_refresh_background = False
        self._csfloat_buy_last_auto_refresh_at = time.time()
        self._csfloat_buy_has_loaded = True
        self._csfloat_buy_detail_cursor = int(payload.get("next_detail_cursor") or 0)
        self._update_market_rolling_progress()

        account = payload.get("account", {})
        orders = payload.get("orders", [])
        details = payload.get("details", {})
        fx = payload.get("fx") or {}
        try:
            rate = float(fx.get("rate") or self._configured_usd_cny_rate())
        except (TypeError, ValueError):
            rate = self._configured_usd_cny_rate()
        self._csfloat_buy_fx_rate = rate
        self._csfloat_buy_fx_source = self._fx_source_label(fx.get("source"))
        balance = int(account.get("balance_cents") or 0)
        pending = int(account.get("pending_balance_cents") or 0)
        self._set_card_value(
            self.csfloat_balance_card,
            f"¥{csfloat_cny_display_price(balance, rate):.2f}  /  ${balance / 100:.2f}",
            "#a6e3a1",
        )
        self._set_card_value(
            self.csfloat_pending_card,
            f"¥{csfloat_cny_display_price(pending, rate):.2f}  /  ${pending / 100:.2f}",
            "#f9e2af",
        )
        self._set_card_value(self.csfloat_order_count_card, f"{len(orders)} 单", "#89b4fa")

        rows = []
        top_count = 0
        for order in orders:
            detail = details.get(str(order.get("id") or ""), {})
            market_order = detail.get("market_order", {})
            market_price = (
                int(market_order.get("price_cents") or 0)
                if market_order.get("success") and market_order.get("found") else 0
            )
            analysis = _csfloat_buy_order_analysis(
                order.get("price"), market_price, detail.get("sales", [])
            )
            if analysis["at_top"]:
                top_count += 1
            rows.append({
                "order": order,
                "detail": detail,
                "analysis": analysis,
                "observed_at": time.time(),
            })
        self._csfloat_buy_order_rows = rows
        self._set_card_value(self.csfloat_top_count_card, f"{top_count} 单", "#cba6f7")
        self._populate_csfloat_buy_order_table()

        analyzed = sum(bool(row["detail"]) for row in rows)
        username = account.get("username") or "当前账户"
        updated_details = int(payload.get("detail_updated") or 0)
        limit_note = (
            ""
            if len(orders) <= payload.get("detail_limit", 1)
            else f"；本轮轮换分析 {updated_details} 单，已有详情 {analyzed} 单"
        )
        self.csfloat_buy_status_label.setText(
            f"{username} · {datetime.now().strftime('%H:%M:%S')} 已更新 · "
            f"1 USD = ¥{rate:.4f}（{self._csfloat_buy_fx_source}）{limit_note}"
        )

    def _csfloat_table_item(self, text, color="#cdd6f4", tooltip=""):
        item = QTableWidgetItem(str(text))
        item.setForeground(QColor(color))
        item.setToolTip(tooltip)
        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        return item

    def _populate_csfloat_buy_order_table(self):
        table = self.csfloat_buy_table
        rate = self._csfloat_buy_fx_rate
        table.setUpdatesEnabled(False)
        table.setRowCount(len(self._csfloat_buy_order_rows))
        for row_index, record in enumerate(self._csfloat_buy_order_rows):
            order = record["order"]
            detail = record["detail"]
            analysis = record["analysis"]
            own_price = analysis["own_price_cents"]
            market_price = analysis["market_price_cents"]
            hybrid = order.get("hybrid_properties") or {}
            table.setRowHeight(row_index, 78)

            market_hash_name = order.get("market_hash_name") or ""
            name = CS2ItemSchema.chinese_display_name(
                "", market_hash_name, order.get("phase", "-"), order.get("paint_index", "")
            )
            name_item = self._csfloat_table_item(name, "#f5e0dc", market_hash_name)
            name_item.setFont(QFont("Microsoft YaHei", 10, QFont.DemiBold))
            table.setItem(row_index, 0, name_item)
            table.setItem(row_index, 1, self._csfloat_table_item(
                f"¥{csfloat_cny_display_price(own_price, rate):.2f}\n${own_price / 100:.2f}", "#89b4fa"
            ))
            condition_text = "普通求购" if not hybrid else "限定：" + ", ".join(sorted(hybrid.keys()))
            table.setItem(row_index, 2, self._csfloat_table_item(
                f"{int(order.get('qty') or 1)} 件\n{condition_text}",
                "#f9e2af" if hybrid else "#a6adc8",
                json.dumps(hybrid, ensure_ascii=False) if hybrid else "不含磨损或模板限制",
            ))

            if market_price > 0:
                market_text = f"¥{csfloat_cny_display_price(market_price, rate):.2f}\n${market_price / 100:.2f}"
                market_color = "#a6e3a1"
            else:
                market_text = "暂无\n" + (detail.get("detail_error") or "未取得最高价")
                market_color = "#6c7086"
            table.setItem(row_index, 3, self._csfloat_table_item(market_text, market_color))

            rank_color = "#a6e3a1" if analysis["at_top"] else "#f38ba8" if market_price else "#6c7086"
            target = analysis.get("target_price_cents")
            rank_secondary = f"下一合法档 ${target / 100:.2f}" if target and not analysis["at_top"] else ""
            table.setItem(row_index, 4, self._csfloat_table_item(
                analysis["price_status"] + (f"\n{rank_secondary}" if rank_secondary else ""), rank_color
            ))

            nearest = analysis.get("nearest_sale")
            if nearest:
                sold_date = nearest.get("sold_at", "").replace("T", " ")[:10]
                sales_text = (
                    f"最接近 ${nearest['price'] / 100:.2f}（{nearest['signed_gap_percent']:+.1f}%）\n"
                    f"2%内 {analysis['within_2_percent']} · 5%内 {analysis['within_5_percent']} / "
                    f"{analysis['sales_count']} 笔"
                )
                sales_tip = f"最接近成交日期：{sold_date or '未知'}"
            else:
                sales_text = "暂无成交样本"
                sales_tip = detail.get("detail_error") or "接口没有返回可用成交"
            table.setItem(row_index, 5, self._csfloat_table_item(sales_text, "#cba6f7", sales_tip))
            table.setItem(row_index, 6, self._csfloat_table_item(
                analysis["purchase_signal"] + "\n仅作参考", analysis["signal_color"],
                "近期成交接近求购价，说明该价位曾有成交，但不保证当前一定能成交。",
            ))

        table.setUpdatesEnabled(True)

    def _open_csfloat_profile(self):
        QDesktopServices.openUrl(QUrl("https://csfloat.com/profile"))
        self.csfloat_buy_status_label.setText("已打开 CSFloat 求购管理页")

    def _open_csfloat_buy_item_from_cell(self, row_index, column):
        if column != 0 or not 0 <= row_index < len(self._csfloat_buy_order_rows):
            return
        record = self._csfloat_buy_order_rows[row_index]
        order = record.get("order") or {}
        detail = record.get("detail") or {}
        listing = detail.get("listing") or {}
        listing_id = str(listing.get("listing_id") or "").strip()
        market_hash_name = str(order.get("market_hash_name") or "").strip()
        if listing_id:
            url = f"https://csfloat.com/item/{quote(listing_id)}"
        elif market_hash_name:
            url = f"https://csfloat.com/search?market_hash_name={quote(market_hash_name)}"
        else:
            return
        QDesktopServices.openUrl(QUrl(url))
        self.csfloat_buy_status_label.setText("已打开对应饰品页面")

    # ═══════════════════════════════════════════
    # Tab 4: 设置
    # ═══════════════════════════════════════════

    def init_settings_tab(self):
        outer_layout = QVBoxLayout(self.tab_settings)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_content = QWidget()
        layout = QVBoxLayout(settings_content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        settings_scroll.setWidget(settings_content)
        outer_layout.addWidget(settings_scroll)

        settings_header = QHBoxLayout()
        settings_title = QLabel("设置")
        settings_title.setObjectName("titleLabel")
        settings_header.addWidget(settings_title)
        settings_header.addStretch()
        self.settings_sync_progress_button = self._create_global_sync_button()
        settings_header.addWidget(self.settings_sync_progress_button)
        layout.addLayout(settings_header)

        group_cloud = QGroupBox("Google Drive 手动同步")
        cloud_layout = QVBoxLayout(group_cloud)
        cloud_note = QLabel(
            "同步出租订单、行情收藏分类和 API 配置。同步包整体采用 AES-256-GCM 口令加密；"
            "上传到 Google Drive 后，另一台电脑下载并放入下方收件箱目录即可导入。"
            "订单与收藏采用合并模式，不删除本机独有数据。"
        )
        cloud_note.setWordWrap(True)
        cloud_note.setStyleSheet("color: #a6adc8;")
        cloud_layout.addWidget(cloud_note)
        self.cloud_sync_path_label = QLabel(f"同步收件箱：{get_sync_inbox_directory()}")
        self.cloud_sync_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.cloud_sync_path_label.setStyleSheet("color: #89b4fa; font-size: 12px;")
        cloud_layout.addWidget(self.cloud_sync_path_label)
        cloud_buttons = QHBoxLayout()
        open_drive_button = QPushButton("打开 Google Drive 网页")
        open_drive_button.setObjectName("primaryBtn")
        self._set_button_icon(open_drive_button, "external", "#11111b")
        open_drive_button.clicked.connect(self._open_google_drive)
        self.export_sync_button = QPushButton("生成加密同步包")
        self.export_sync_button.setObjectName("successBtn")
        self._set_button_icon(self.export_sync_button, "clipboard", "#11111b")
        self.export_sync_button.clicked.connect(self._export_cloud_sync_bundle)
        self.import_sync_button = QPushButton("导入下载的同步包")
        self._set_button_icon(self.import_sync_button, "refresh")
        self.import_sync_button.clicked.connect(self._import_cloud_sync_bundle)
        open_folder_button = QPushButton("打开本地同步目录")
        self._set_button_icon(open_folder_button, "external")
        open_folder_button.clicked.connect(self._open_cloud_sync_directory)
        for button in (
            open_drive_button,
            self.export_sync_button,
            self.import_sync_button,
            open_folder_button,
        ):
            cloud_buttons.addWidget(button)
        cloud_buttons.addStretch()
        cloud_layout.addLayout(cloud_buttons)
        layout.addWidget(group_cloud)

        group_csqaq = QGroupBox("CSQAQ 数据开放 API 配置")
        form_csqaq = QFormLayout(group_csqaq)
        self.cfg_csqaq = QLineEdit(self.db.get_config("csqaq_token"))
        self.cfg_csqaq.setPlaceholderText("粘贴登录 CSQAQ 个人中心获取的 ApiToken")
        self.cfg_csqaq.setEchoMode(QLineEdit.Password)
        form_csqaq.addRow("CSQAQ ApiToken:", self.cfg_csqaq)

        self.bind_ip_btn = QPushButton("一键绑定当前公网 IP")
        self.bind_ip_btn.setObjectName("primaryBtn")
        self._set_button_icon(self.bind_ip_btn, "external", "#11111b")
        self.bind_ip_btn.clicked.connect(self._on_bind_csqaq_ip)
        form_csqaq.addRow(self.bind_ip_btn)
        layout.addWidget(group_csqaq)

        group_csfloat = QGroupBox("CSFloat API 配置（行情、账户与求购）")
        form_csfloat = QFormLayout(group_csfloat)
        self.cfg_csfloat = QLineEdit(self.db.get_config("csfloat_api_key"))
        self.cfg_csfloat.setPlaceholderText("粘贴 CSFloat Profile > Developer 中创建的 API Key")
        self.cfg_csfloat.setEchoMode(QLineEdit.Password)
        self.cfg_auto_usd_cny = QCheckBox(
            "自动使用 CSFloat 官网汇率（失败时回退 ECB）"
        )
        self.cfg_auto_usd_cny.setChecked(
            self.db.get_config("auto_usd_cny_rate") != "0"
        )
        self.cfg_usd_cny = QLineEdit(self.db.get_config("usd_cny_rate") or "7.20")
        self.cfg_usd_cny.setPlaceholderText("例如 7.20；自动获取失败时作为备用值")
        form_csfloat.addRow("CSFloat API Key:", self.cfg_csfloat)
        form_csfloat.addRow("汇率来源:", self.cfg_auto_usd_cny)
        form_csfloat.addRow("手工备用 USD/CNY:", self.cfg_usd_cny)
        layout.addWidget(group_csfloat)

        group_api = QGroupBox("ECO 开放平台 API 配置")
        form_api = QFormLayout(group_api)
        self.cfg_partner = QLineEdit(self.db.get_config("eco_partner_id"))
        self.cfg_rsa = QLineEdit(self.db.get_config("eco_rsa_key"))
        self.cfg_rsa.setEchoMode(QLineEdit.Password)
        form_api.addRow("Partner ID:", self.cfg_partner)
        form_api.addRow("RSA 私钥路径/文本:", self.cfg_rsa)
        layout.addWidget(group_api)

        self.cfg_show_secrets = QCheckBox("临时显示 API Token、Key 和 RSA 私钥")
        self.cfg_show_secrets.toggled.connect(self._set_secret_fields_visible)
        layout.addWidget(self.cfg_show_secrets)

        group_time = QGroupBox("自动化与刷新设置")
        form_time = QFormLayout(group_time)
        self.cfg_interval = QComboBox()
        self.cfg_interval.addItems(["禁用自动刷新", "5 分钟", "15 分钟", "30 分钟", "60 分钟"])
        cur_int = self.db.get_config("refresh_interval") or "15"
        idx_map = {"0": 0, "5": 1, "15": 2, "30": 3, "60": 4}
        self.cfg_interval.setCurrentIndex(idx_map.get(cur_int, 2))
        form_time.addRow("自动刷新频率:", self.cfg_interval)
        layout.addWidget(group_time)

        group_fee = QGroupBox("出租手续费率（用于订单净收益与年化计算）")
        form_fee = QFormLayout(group_fee)
        fee_note = QLabel(
            "费率修改并保存后，已有订单的净收益、累计净收益和年化统计也会立即按新费率重新计算。"
        )
        fee_note.setWordWrap(True)
        fee_note.setStyleSheet("color: #a6adc8; font-weight: normal;")
        form_fee.addRow(fee_note)
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

        save_btn = QPushButton("保存全部设置")
        save_btn.setObjectName("primaryBtn")
        self._set_button_icon(save_btn, "clipboard", "#11111b")
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)
        layout.addStretch()

    def _set_secret_fields_visible(self, visible):
        mode = QLineEdit.Normal if visible else QLineEdit.Password
        for field in (self.cfg_csqaq, self.cfg_csfloat, self.cfg_rsa):
            field.setEchoMode(mode)

    def _ask_cloud_sync_password(self, confirm=False):
        password, accepted = QInputDialog.getText(
            self,
            "同步包加密口令",
            "输入至少 8 位口令（软件不会保存此口令）：",
            QLineEdit.Password,
        )
        if not accepted:
            return None
        if len(password) < 8:
            QMessageBox.warning(self, "同步包加密口令", "口令至少需要 8 个字符。")
            return None
        if confirm:
            repeated, accepted = QInputDialog.getText(
                self,
                "确认同步口令",
                "再次输入相同口令：",
                QLineEdit.Password,
            )
            if not accepted:
                return None
            if repeated != password:
                QMessageBox.warning(self, "同步包加密口令", "两次输入的口令不一致。")
                return None
        return password

    @staticmethod
    def _open_local_directory(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_google_drive(self):
        QDesktopServices.openUrl(QUrl("https://drive.google.com/drive/my-drive"))

    def _open_cloud_sync_directory(self):
        self._open_local_directory(get_sync_directory())

    def _export_cloud_sync_bundle(self):
        password = self._ask_cloud_sync_password(confirm=True)
        if password is None:
            return
        target = get_sync_outbox_directory() / SYNC_FILENAME
        self.export_sync_button.setEnabled(False)
        self.export_sync_button.setText("正在加密…")

        def task(worker):
            result = export_sync_bundle(self.db, password, target)
            if not worker._is_canceled:
                worker.finished.emit(("cloud_export", result))

        self._start_worker(task, self._on_cloud_export_finished, self._on_cloud_export_error)

    def _on_cloud_export_error(self, message):
        self.export_sync_button.setEnabled(True)
        self.export_sync_button.setText("生成加密同步包")
        logger.error("生成云盘同步包失败: %s", message)
        QMessageBox.critical(self, "生成同步包失败", str(message))

    def _on_cloud_export_finished(self, payload):
        _tag, result = payload
        self.export_sync_button.setEnabled(True)
        self.export_sync_button.setText("生成加密同步包")
        self.title_bar.set_sync_status("加密同步包已生成", "#a6e3a1")
        QMessageBox.information(
            self,
            "加密同步包已生成",
            "\n".join([
                f"订单：{result['orders']} 条",
                f"收藏分类：{result['categories']} 个",
                f"观察饰品：{result['watch_items']} 个",
                f"API 配置：{result['api_configs']} 项",
                "",
                f"文件：{result['path']}",
                "请将该 .cs2sync 文件上传到自己的 Google Drive。",
                "导入另一台电脑时必须输入相同口令。",
            ]),
        )
        self._open_local_directory(get_sync_outbox_directory())

    def _import_cloud_sync_bundle(self):
        source, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择从 Google Drive 下载的同步包",
            str(get_sync_inbox_directory()),
            "CS2 加密同步包 (*.cs2sync);;所有文件 (*)",
        )
        if not source:
            return
        password = self._ask_cloud_sync_password()
        if password is None:
            return
        self.import_sync_button.setEnabled(False)
        self.import_sync_button.setText("正在验证…")

        def task(worker):
            preview = load_sync_bundle(source, password)
            if not worker._is_canceled:
                worker.finished.emit(("cloud_preview", preview))

        self._start_worker(
            task,
            lambda payload: self._on_cloud_import_preview(payload, source, password),
            self._on_cloud_import_error,
        )

    def _on_cloud_import_error(self, message):
        self.import_sync_button.setEnabled(True)
        self.import_sync_button.setText("导入下载的同步包")
        logger.error("读取或导入云盘同步包失败: %s", message)
        QMessageBox.warning(self, "无法导入同步包", str(message))

    def _on_cloud_import_preview(self, payload, source, password):
        _tag, preview = payload

        data = preview["data"]
        categories = data["market_watchlist"].get("categories", [])
        watch_items = sum(
            len(category.get("items", []))
            for category in categories
            if isinstance(category, dict) and isinstance(category.get("items"), list)
        )
        api_configs = sum(
            bool(str(value or "").strip()) for value in data["api_config"].values()
        )
        answer = QMessageBox.question(
            self,
            "确认合并同步数据",
            "\n".join([
                f"来源电脑：{preview.get('source_device') or '未知'}",
                f"导出时间：{preview.get('exported_at') or '未知'}",
                f"出租订单：{len(data['rental_orders'])} 条",
                f"收藏分类：{len(categories)} 个",
                f"观察饰品：{watch_items} 个",
                f"API 配置：{api_configs} 项",
                "",
                "导入会按订单号和观察品标识合并，不删除本机独有数据。",
                "本机当前同步数据会先生成一份加密备份。是否继续？",
            ]),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            self.import_sync_button.setEnabled(True)
            self.import_sync_button.setText("导入下载的同步包")
            return
        self.import_sync_button.setText("正在备份并合并…")

        def task(worker):
            result = import_sync_bundle(self.db, source, password)
            if not worker._is_canceled:
                worker.finished.emit(("cloud_import", result))

        self._start_worker(task, self._on_cloud_import_finished, self._on_cloud_import_error)

    def _on_cloud_import_finished(self, payload):
        _tag, result = payload
        self.import_sync_button.setEnabled(True)
        self.import_sync_button.setText("导入下载的同步包")

        self.cfg_csqaq.setText(self.db.get_config("csqaq_token"))
        self.cfg_csfloat.setText(self.db.get_config("csfloat_api_key"))
        self.cfg_partner.setText(self.db.get_config("eco_partner_id"))
        self.cfg_rsa.setText(self.db.get_config("eco_rsa_key"))
        self.cfg_auto_usd_cny.setChecked(self.db.get_config("auto_usd_cny_rate") != "0")
        self.cfg_usd_cny.setText(self.db.get_config("usd_cny_rate") or "7.20")
        self._market_categories = {}
        self._market_tracked_items = []
        self._active_market_category_id = "rentals"
        self._init_market_from_cache()
        self.load_data()
        self.title_bar.set_sync_status("云盘同步数据已合并", "#a6e3a1")
        QMessageBox.information(
            self,
            "同步包导入完成",
            "\n".join([
                f"订单：{result['orders']} 条",
                f"合并后收藏分类：{result['categories']} 个",
                f"合并后观察饰品：{result['watch_items']} 个",
                f"API 配置：{result['api_configs']} 项",
                "",
                f"导入前备份：{result['backup_path']}",
            ]),
        )

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
        worker.configure_task(worker_fn)
        worker.moveToThread(thread)

        error_callback = on_error or logger.error
        callback_relay = ApiWorkerCallbackRelay(
            on_finished, error_callback, parent=self
        )

        # QThread does not take Python ownership of a worker moved into it.
        # Keep explicit references until the thread finishes; otherwise the
        # wrapper can be garbage-collected before ``started`` invokes ``run``,
        # leaving an idle thread and a UI that appears permanently stuck.
        thread._worker_ref = worker
        thread._callback_relay_ref = callback_relay

        thread.started.connect(worker.run)
        worker.finished.connect(callback_relay.handle_finished)
        worker.error.connect(callback_relay.handle_error)

        # 安全清理链
        worker.task_completed.connect(thread.quit)
        worker.task_completed.connect(worker.deleteLater)
        # Remove the exact tracked object before scheduling Qt ownership cleanup.
        # Depending on sender() here made fast failures intermittently leave a
        # finished thread in ``_active_threads``.
        thread.finished.connect(lambda tracked=thread: self._cleanup_thread(tracked))
        thread.finished.connect(callback_relay.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._active_threads.append(thread)
        thread.start()
        return thread, worker

    def _cleanup_thread(self, thread=None):
        """从活跃线程列表中移除已结束的线程"""
        if thread is None:
            thread = self.sender()
        if thread in self._active_threads:
            self._active_threads.remove(thread)

    def _cancel_active_workers(self):
        if self._market_refresh_worker is not None:
            self._market_refresh_worker.cancel()
        for thread in list(self._active_threads):
            worker = getattr(thread, "_worker_ref", None)
            if worker is not None:
                worker.cancel()
            thread.requestInterruption()

    def _has_running_workers(self):
        market_running = bool(
            self._market_refresh_thread and self._market_refresh_thread.isRunning()
        )
        return market_running or any(thread.isRunning() for thread in self._active_threads)

    def _finish_pending_close(self):
        if self._has_running_workers():
            QTimer.singleShot(100, self._finish_pending_close)
            return
        self._threads_stopped_for_close = True
        self.close()

    def _on_market_table_double_click(self, index):
        self._open_market_link(index.row(), index.column())

    # ═══════════════════════════════════════════
    # 数据加载
    # ═══════════════════════════════════════════

    def _rental_history_for_item(self, item):
        histories = _build_rental_history_index(
            self.db.get_all_items(), self.db.get_rental_orders()
        )
        return histories.get(item.get("id"), [])

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
        stored_end = _parse_rental_datetime(order.get("rental_end_time"))
        if stored_end > datetime.min:
            return stored_end
        raw_text = str(order.get("raw_text", "") or "")
        if order.get("platform") in {"C5GAME", "IGXE"}:
            match = re.search(
                r"租赁到期(?:时间)?\s*[：:]?\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
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

    def _order_transfer_reward(self, order) -> float:
        """C5 reward is a separate landlord cost, not part of its service fee."""
        if order.get("platform") != "C5GAME" or not order.get("transfer_reward_known"):
            return 0.0
        return max(0.0, self._order_number(order.get("transfer_reward")))

    def _order_fee_rate(self, order, history, fee_rates=None) -> float:
        platform_prefix = {
            "C5GAME": "c5",
            "ECOSteam": "eco",
            "IGXE": "igxe",
        }.get(order.get("platform", ""), "")
        if not platform_prefix:
            return 0.0
        fee_kind = "relet" if self._is_relet_order(order, history) else "first"
        config_key = f"{platform_prefix}_{fee_kind}_fee"
        try:
            raw_rate = (
                fee_rates.get(config_key, 0.0)
                if fee_rates is not None
                else self.db.get_config(config_key)
            )
            return min(0.999, max(0.0, float(raw_rate or 0.0)))
        except ValueError:
            return 0.0

    def _dashboard_fee_rates(self) -> dict[str, float]:
        """Read every dashboard fee once for one render pass."""
        rates = {}
        for platform_prefix in ("c5", "eco", "igxe"):
            for fee_kind in ("first", "relet"):
                config_key = f"{platform_prefix}_{fee_kind}_fee"
                try:
                    rates[config_key] = min(
                        0.999,
                        max(0.0, float(self.db.get_config(config_key) or 0.0)),
                    )
                except ValueError:
                    rates[config_key] = 0.0
        return rates

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
        # Only a new order inside the 12-hour handover window is a transfer.
        # Platform timestamps can overlap slightly around the handover second.
        return (
            previous_end - timedelta(minutes=30)
            <= current_start
            <= previous_end + RENTAL_RELET_WINDOW
        )

    @staticmethod
    def _net_amount(gross_amount, fee_rate) -> float:
        """Multiply currency values in decimal form before rendering/summing them."""
        try:
            gross = Decimal(str(gross_amount))
            fee = Decimal(str(fee_rate))
            return float(gross * (Decimal("1") - fee))
        except (InvalidOperation, TypeError, ValueError):
            return 0.0

    def _order_net_income(self, order, history, fee_rates=None) -> float:
        if _is_non_earning_rental_status(order.get("status")):
            return 0.0
        net_income = self._net_amount(
            self._order_gross_income(order),
            self._order_fee_rate(order, history, fee_rates),
        )
        net_income -= self._order_transfer_reward(order)
        if order.get("platform") == "IGXE":
            # IGXE settles its displayed order amount by truncating to cents.
            return float(Decimal(str(net_income)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
        return net_income

    def _total_net_income(self, history, fee_rates=None) -> float:
        return sum(
            self._order_net_income(order, history, fee_rates) for order in history
        )

    @staticmethod
    def _countdown_text(end_time, now=None):
        remaining = end_time - (now or datetime.now())
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
        if _is_non_earning_rental_status(order_status):
            return order_status or fallback_status, None

        end_time = self._rental_end_datetime(latest_order)
        if end_time <= datetime.min:
            if order_status in {"已归还", "已完成"}:
                return "CD冷却 · 开始时间未知", QColor("#cba6f7")
            return f"{rental_label} · 租赁到期未知", QColor("#fab387")

        now = datetime.now()
        state, state_end = _rental_lifecycle_state(end_time, now)
        if state == "rented":
            remaining_seconds = (end_time - now).total_seconds()
            color = QColor("#f38ba8") if remaining_seconds <= 12 * 60 * 60 else QColor("#a6e3a1")
            return f"{rental_label} · {self._countdown_text(end_time, now)}", color
        if state == "pending_relet":
            return f"待转租中 · {self._countdown_text(state_end, now)}", QColor("#fab387")
        if state == "cooldown":
            return f"CD冷却 · {self._countdown_text(state_end, now)}", QColor("#cba6f7")
        return "在库 · CD已结束", QColor("#a6e3a1")

    def _manual_cooldown_status(self, cooldown_until):
        deadline = _parse_cooldown_datetime(cooldown_until)
        if deadline <= datetime.min:
            return "CD冷却 · 到期时间未知", QColor("#fab387")
        if deadline <= datetime.now():
            return "在库 · CD已结束", QColor("#a6e3a1")
        return (
            f"CD冷却 · {self._countdown_text(deadline)}",
            QColor("#cba6f7"),
        )

    @staticmethod
    def _status_pill_style(text, color=None):
        """Use status colour on text only, without a filled badge background."""
        tone = color.name().lower() if isinstance(color, QColor) else ""
        if tone == "#f38ba8" or "已到期" in text:
            foreground = "#f38ba8"
        elif tone == "#a6e3a1":
            foreground = "#a6e3a1"
        elif tone == "#fab387" or "待转租中" in text or "未导入" in text or "未知" in text:
            foreground = "#fab387"
        elif tone == "#cba6f7" or "CD冷却" in text:
            foreground = "#cba6f7"
        elif "已转租" in text:
            foreground = "#cba6f7"
        elif "已出租" in text:
            foreground = "#89b4fa"
        else:
            foreground = "#bac2de"
        return (
            f"background: transparent; color: {foreground}; border: none; "
            "padding: 3px 4px; font-weight: 700;"
        )

    def _create_status_pill(self, text, color=None):
        pill = QLabel(text)
        pill.setObjectName("statusPill")
        pill.setAlignment(Qt.AlignCenter)
        pill.setMinimumHeight(25)
        pill.setContentsMargins(3, 1, 3, 1)
        style = self._status_pill_style(text, color)
        pill.setStyleSheet(style)
        pill.setProperty("rentalStatusStyle", style)
        pill.setAttribute(Qt.WA_TransparentForMouseEvents)
        return pill

    def _update_status_pill(self, pill, text, color=None):
        style = self._status_pill_style(text, color)
        if pill.text() != text:
            pill.setText(text)
        if pill.property("rentalStatusStyle") != style:
            pill.setStyleSheet(style)
            pill.setProperty("rentalStatusStyle", style)

    def _update_dashboard_rental_countdowns(self):
        """Update countdown cells by stable asset id, even after user sorting."""
        for item_id, state in self._dashboard_rental_rows.items():
            row = self._dashboard_row_for_item_id(item_id)
            if row < 0:
                continue
            pill = self.table.cellWidget(row, 7)
            if pill is None:
                continue
            if state.get("manual_cooldown_until") is not None:
                status_text, status_color = self._manual_cooldown_status(
                    state["manual_cooldown_until"]
                )
            else:
                status_text, status_color = self._rental_status_display(
                    state["latest_order"], state["history"], state["fallback_status"]
                )
            self._update_status_pill(pill, status_text, status_color)

    def _dashboard_row_for_item_id(self, item_id):
        for row in range(self.table.rowCount()):
            name_item = self.table.item(row, 1)
            if name_item is not None and name_item.data(Qt.UserRole) == item_id:
                return row
        return -1

    def _dashboard_item_id_at_row(self, row):
        """Read the hidden database ID stored on the visible name cell."""
        name_item = self.table.item(row, 1)
        if name_item is None:
            return None
        try:
            return int(name_item.data(Qt.UserRole))
        except (TypeError, ValueError):
            return None

    def _show_dashboard_context_menu(self, position):
        """Adjust the persisted asset cost by a user-entered surcharge percent."""
        index = self.table.indexAt(position)
        if not index.isValid() or index.column() != 4:
            return
        item_id = self._dashboard_item_id_at_row(index.row())
        if item_id is None:
            return
        item = next(
            (candidate for candidate in self.current_items if candidate["id"] == item_id),
            None,
        )
        if item is None:
            return

        self.table.selectRow(index.row())
        menu = QMenu(self)
        adjust_action = menu.addAction("按手续费百分比增加成本…")
        chosen = menu.exec(self.table.viewport().mapToGlobal(position))
        if chosen != adjust_action:
            return

        current_cost = float(item.get("cost", 0.0) or 0.0)
        percentage, accepted = QInputDialog.getDouble(
            self,
            "成本计入手续费",
            (
                f"当前成本：{_money_text(current_cost)}\n"
                "输入要增加的手续费百分比（输入 1 = 成本增加 1%）："
            ),
            1.0,
            0.0,
            100.0,
            2,
        )
        if not accepted or percentage == 0:
            return
        new_cost = _adjust_cost_by_percent(current_cost, percentage)
        updated_item = dict(item)
        updated_item["cost"] = new_cost
        self.db.update_item(item_id, updated_item)
        self.load_data()
        QMessageBox.information(
            self,
            "成本已更新",
            (
                f"原成本：{_money_text(current_cost)}\n"
                f"增加手续费：{percentage:g}%\n"
                f"新成本：{_money_text(new_cost)}"
            ),
        )

    def _build_dashboard_market_quote_index(self):
        """Index all cached quotes once while preserving first-match order."""
        identity_quotes = {}
        name_quotes = {}
        for category in self._market_categories.values():
            for quote_entry in category.get("items", []):
                quote_identity = self._market_watch_identity(
                    quote_entry.get(
                        "market_hash_name", quote_entry.get("name", "")
                    ),
                    quote_entry.get("phase", "-"),
                )
                identity_quotes.setdefault(quote_identity, quote_entry)
                fallback_name = (
                    str(quote_entry.get("name", "")).strip().casefold()
                )
                if fallback_name:
                    name_quotes.setdefault(fallback_name, quote_entry)
        return identity_quotes, name_quotes

    def _dashboard_market_quote(self, item, quote_index=None):
        """Find the cached market row matching an inventory asset."""
        if quote_index is None:
            quote_index = self._build_dashboard_market_quote_index()
        identity_quotes, name_quotes = quote_index
        market_hash_name = self._build_market_hash_name(item)
        target_identity = self._market_watch_identity(
            market_hash_name, item.get("phase", "-")
        )
        fallback_name = str(item.get("name", "")).strip().casefold()
        return identity_quotes.get(target_identity) or name_quotes.get(fallback_name) or {}

    @staticmethod
    def _dashboard_gap_item(value, benchmark, tooltip) -> QTableWidgetItem:
        if value <= 0 or benchmark <= 0:
            item = SortAwareTableWidgetItem("—")
            item.setForeground(QColor("#6c7086"))
            item.setFont(QFont("Microsoft YaHei", 11))
            item.setToolTip(tooltip + "\n暂无可比较的有效行情。")
            return item
        difference, percentage = _price_gap(value, benchmark)
        sign = "+" if difference > 0 else "-" if difference < 0 else ""
        item = SortAwareTableWidgetItem(
            f"{sign}¥ {abs(difference):.2f} ({percentage:+.1f}%)"
        )
        item.setData(TABLE_SORT_ROLE, difference)
        item.setFont(QFont("Microsoft YaHei", 12, QFont.DemiBold))
        if difference > 0:
            item.setForeground(QColor("#a6e3a1"))
        elif difference < 0:
            item.setForeground(QColor("#f38ba8"))
        else:
            item.setForeground(QColor("#bac2de"))
        item.setToolTip(tooltip)
        return item

    @staticmethod
    def _dashboard_rent_gap_item(value, benchmark, rental_term, tooltip) -> QTableWidgetItem:
        term_text = {"short": "短租", "long": "长租"}.get(rental_term, "租期未知")
        if value <= 0 or benchmark <= 0:
            item = SortAwareTableWidgetItem(f"{term_text} · —")
            item.setForeground(QColor("#6c7086"))
            item.setFont(QFont("Microsoft YaHei", 10))
            reason = (
                "租期类型未识别，暂不计算。"
                if rental_term not in {"short", "long"}
                else "暂无可比较的有效租金行情。"
            )
            item.setToolTip(tooltip + "\n" + reason)
            return item
        difference, percentage = _price_gap(value, benchmark)
        direction = "高" if difference > 0 else "低" if difference < 0 else "持平"
        if difference == 0:
            text = f"{term_text} · 持平"
        else:
            text = f"{term_text} · {direction} ¥ {abs(difference):.2f} ({percentage:+.1f}%)"
        item = SortAwareTableWidgetItem(text)
        item.setData(TABLE_SORT_ROLE, difference)
        item.setFont(QFont("Microsoft YaHei", 11, QFont.DemiBold))
        if difference > 0:
            item.setForeground(QColor("#a6e3a1"))
        elif difference < 0:
            item.setForeground(QColor("#f38ba8"))
        else:
            item.setForeground(QColor("#bac2de"))
        item.setToolTip(tooltip)
        return item

    def show_selected_rental_history(self):
        selected_row = self.table.currentRow()
        if selected_row < 0:
            QMessageBox.information(self, "订单历史", "请先选择一个资产。")
            return
        item_id = self._dashboard_item_id_at_row(selected_row)
        if item_id is None:
            return
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
        """Render assets grouped by active-rental platform, item type and cost."""
        sorting_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.current_items = self.db.get_all_items()
        rental_histories = _build_rental_history_index(
            self.current_items,
            self.db.get_rental_orders(),
        )
        market_quote_index = self._build_dashboard_market_quote_index()
        fee_rates = self._dashboard_fee_rates()
        filter_platform = self.filter_box.currentText()
        search_widget = getattr(self, "dashboard_search", None)
        search_text = (
            search_widget.text().strip().casefold() if search_widget is not None else ""
        )
        status_filter = (
            self.status_filter_box.currentText()
            if hasattr(self, "status_filter_box") else "全部状态"
        )
        self.table.setRowCount(0)
        self._dashboard_rental_rows = {}
        total_cost = 0.0
        daily_rent_total = 0.0
        rented_count = 0
        priced_asset_count = 0
        portfolio_market_value = 0.0
        portfolio_priced_cost = 0.0
        portfolio_total_net_income = 0.0

        dashboard_records = []
        for item in self.current_items:
            history = rental_histories.get(item.get("id"), [])
            latest_order = history[-1] if history else None
            is_currently_rented = False
            if latest_order and str(latest_order.get("status", "") or "").strip() == "租赁中":
                rental_end = self._rental_end_datetime(latest_order)
                is_currently_rented = rental_end <= datetime.min or rental_end > datetime.now()
            elif not latest_order and str(item.get("status", "") or "").strip() == "已出租":
                is_currently_rented = True
            sort_platform = (
                str(latest_order.get("platform", "")).strip()
                if is_currently_rented and latest_order else str(item.get("platform", "")).strip()
            )
            dashboard_records.append({
                "item": item,
                "latest_order": latest_order,
                "history": history,
                "platform": sort_platform or "未分类",
                "is_currently_rented": is_currently_rented,
            })

        # Portfolio market P/L always represents every asset, independent of
        # the table's current platform filter.
        for record in dashboard_records:
            portfolio_item = record["item"]
            if record["history"]:
                order_net_incomes = [
                    self._order_net_income(order, record["history"], fee_rates)
                    for order in record["history"]
                ]
                record["latest_net_income"] = order_net_incomes[-1]
                record["total_net_income"] = sum(order_net_incomes)
            else:
                record["latest_net_income"] = float(
                    portfolio_item.get("income", 0.0) or 0.0
                )
                record["total_net_income"] = record["latest_net_income"]
            portfolio_total_net_income += record["total_net_income"]
            portfolio_quote = self._dashboard_market_quote(
                portfolio_item, market_quote_index
            )
            portfolio_price = self._market_number(
                portfolio_quote.get("csqaq_min_sell_price")
            )
            if portfolio_price <= 0:
                portfolio_price = self._market_number(portfolio_quote.get("csqaq_price"))
            if portfolio_price > 0:
                priced_asset_count += 1
                portfolio_market_value += portfolio_price
                portfolio_priced_cost += float(portfolio_item.get("cost", 0.0) or 0.0)

        row = 0
        for record in _sort_dashboard_records(dashboard_records):
            item = record["item"]
            if filter_platform != "全部平台" and item["platform"] != filter_platform:
                continue
            searchable = f"{item.get('name', '')} {item.get('float_val', '')}".casefold()
            if search_text and search_text not in searchable:
                continue
            cost = float(item.get("cost", 0.0) or 0.0)
            latest_order = record["latest_order"]
            history = record["history"]
            status_text = item.get("status", "在库")
            status_color = None
            rental_days = float(item.get("days", 0.0) or 0.0)
            daily_rent = float(item.get("rent", 0.0) or 0.0)
            net_daily_rent = daily_rent
            net_income = float(item.get("income", 0.0) or 0.0)
            total_net_income = net_income
            counts_as_rented = False

            if latest_order:
                end = self._rental_end_datetime(latest_order)
                rental_days = self._order_rental_days(latest_order)
                daily_rent = self._order_daily_rent(latest_order)
                net_income = record["latest_net_income"]
                net_daily_rent = net_income / rental_days if rental_days > 0 else 0.0
                total_net_income = record["total_net_income"]
                order_status = latest_order.get("status", "")
                status_text, status_color = self._rental_status_display(
                    latest_order, history, status_text
                )
                if order_status == "租赁中":
                    if end <= datetime.min or end > datetime.now():
                        counts_as_rented = True
            elif str(item.get("status", "") or "").strip() == "已出租":
                # Older manual inventory rows can be marked as rented before
                # their platform order has been imported.  Keep them visible
                # in portfolio totals, but never fabricate an end-time.
                status_text = "已出租 · 未导入订单"
                status_color = QColor("#fab387")
                counts_as_rented = True
            elif str(item.get("status", "") or "").strip() == "CD冷却":
                status_text, status_color = self._manual_cooldown_status(
                    item.get("cooldown_until", "")
                )

            status_matches = {
                "全部状态": True,
                "在库": status_text.startswith("在库"),
                "出租中": status_text.startswith(("已出租", "已转租")),
                "待转租": "待转租" in status_text,
                "CD冷却": "CD冷却" in status_text,
            }.get(status_filter, True)
            if not status_matches:
                continue
            total_cost += cost
            if counts_as_rented:
                rented_count += 1
                daily_rent_total += net_daily_rent

            market_quote = self._dashboard_market_quote(item, market_quote_index)
            market_sell_price = self._market_number(
                market_quote.get("csqaq_min_sell_price")
            )
            if market_sell_price <= 0:
                market_sell_price = self._market_number(market_quote.get("csqaq_price"))
            rental_platform = (
                str(latest_order.get("platform", "")).strip()
                if latest_order else str(item.get("platform", "")).strip()
            )
            rental_term = _rental_term(
                rental_platform,
                rental_days,
                latest_order.get("rental_type", "") if latest_order else "",
            )
            platform_min_rent, rent_benchmark_label = _platform_rent_benchmark(
                rental_platform, market_quote, rental_term
            )
            rental_term_text = {"short": "短租", "long": "长租"}.get(
                rental_term, "类型未知"
            )

            self.table.insertRow(row)
            values = [
                "", item["name"], item.get("phase", "-"), item.get("float_val", ""),
                _money_text(cost), "", item.get("platform", ""), status_text,
                f"{rental_days:g} 天 · {rental_term_text}" if rental_days > 0 else "—",
                _money_text(net_daily_rent) if net_daily_rent > 0 else "—", "",
                _money_text(net_income) if net_income > 0 else "—",
                _money_text(total_net_income) if total_net_income > 0 else "—",
            ]

            image_label = QLabel()
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setContentsMargins(3, 3, 3, 3)
            image_label.setStyleSheet("background: transparent; color: #6c7086;")
            image_mhn = str(
                market_quote.get("market_hash_name")
                or self._build_market_hash_name(item)
                or item.get("name", "")
            )
            image_path = ImageCache.get_local_path(image_mhn)
            thumbnail = self._market_thumbnail(image_path) if os.path.exists(image_path) else QPixmap()
            if not thumbnail.isNull():
                image_label.setPixmap(
                    thumbnail.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                image_label.setText("—")
                image_label.setToolTip("该饰品图片尚未缓存")
            self.table.setCellWidget(row, 0, image_label)
            self.table.setRowHeight(row, 64)

            for column, value in enumerate(values):
                if column == 0:
                    continue
                if column == 7:
                    self.table.setCellWidget(row, column, self._create_status_pill(status_text, status_color))
                    continue
                if column in (5, 10):
                    continue
                table_item = SortAwareTableWidgetItem(str(value))
                if column == 1:
                    table_item.setData(Qt.UserRole, item["id"])
                    table_item.setToolTip(item["name"])
                    table_item.setFont(QFont("Microsoft YaHei", 12, QFont.DemiBold))
                elif column in (4, 9, 11, 12):
                    table_item.setFont(QFont("Microsoft YaHei", 11, QFont.DemiBold))
                    numeric_values = {
                        4: cost,
                        9: net_daily_rent,
                        11: net_income,
                        12: total_net_income,
                    }
                    table_item.setData(TABLE_SORT_ROLE, numeric_values[column])
                elif column == 6:
                    table_item.setFont(QFont("Microsoft YaHei", 9))
                    table_item.setForeground(QColor("#9399b2"))
                elif column in (2, 3, 8):
                    table_item.setFont(QFont("Microsoft YaHei", 10))
                self.table.setItem(row, column, table_item)
            self.table.setItem(
                row,
                5,
                self._dashboard_gap_item(
                    market_sell_price,
                    cost,
                    (
                        f"CSQAQ 全网最低：{_money_text(market_sell_price)}\n"
                        f"当前成本：{_money_text(cost)}\n"
                        "计算：全网最低售价 − 当前成本"
                    ),
                ),
            )
            self.table.setItem(
                row,
                10,
                self._dashboard_rent_gap_item(
                    daily_rent,
                    platform_min_rent,
                    rental_term,
                    (
                        f"出租平台：{rental_platform or '未知'}\n"
                        f"对比类型：{rental_term_text}"
                        + ("（ECO 仅提供单一行情）" if rental_platform == "ECOSteam" else "")
                        + "\n"
                        f"当前原始日租：{_money_text(daily_rent)}\n"
                        f"{rent_benchmark_label or '同租期最低日租'}：{_money_text(platform_min_rent)}\n"
                        "计算：本单原始日租 − 同平台同租期最低日租\n"
                        "正数表示本单租金高于当前最低价。"
                    ),
                ),
            )
            if latest_order and not _is_non_earning_rental_status(latest_order.get("status")):
                self._dashboard_rental_rows[item["id"]] = {
                    "row": row,
                    "latest_order": latest_order,
                    "history": history,
                    "fallback_status": item.get("status", "在库"),
                }
            elif str(item.get("status", "") or "").strip() == "CD冷却":
                self._dashboard_rental_rows[item["id"]] = {
                    "manual_cooldown_until": item.get("cooldown_until", "")
                }
            row += 1

        annual_rate = (daily_rent_total * 365 / total_cost * 100) if total_cost > 0 else 0.0
        self.card_cost.findChildren(QLabel)[1].setText(f"¥ {total_cost:,.2f}")
        profit_value_label = self.card_market_profit.findChildren(QLabel)[1]
        if priced_asset_count:
            holding_profit = portfolio_market_value - portfolio_priced_cost
            profit_value_label.setText(f"{'+' if holding_profit > 0 else ''}¥ {holding_profit:,.2f}")
            profit_value_label.setStyleSheet(
                "color: %s; font-size: 20px; font-weight: bold;"
                % ("#a6e3a1" if holding_profit > 0 else "#f38ba8" if holding_profit < 0 else "#bac2de")
            )
            self.card_market_profit.setToolTip(
                f"饰品行情盈亏：{_money_text(holding_profit)}\n"
                "计算：CSQAQ 当前全网最低售价合计 − 对应饰品总成本\n"
                f"行情覆盖 {priced_asset_count}/{len(self.current_items)} 件；"
                "不包含租金收益，不受平台筛选影响。"
            )
        else:
            profit_value_label.setText("—")
            profit_value_label.setStyleSheet("color: #6c7086; font-size: 20px; font-weight: bold;")
            self.card_market_profit.setToolTip(
                "暂无可用于计算饰品行情盈亏的 CSQAQ 行情；租金收益仍在旁边单独展示。"
            )
        self.card_income.findChildren(QLabel)[1].setText(_money_text(daily_rent_total))
        self.card_total_income.findChildren(QLabel)[1].setText(
            _money_text(portfolio_total_net_income)
        )
        self.card_total_income.setToolTip(
            "全部饰品订单的累计净租金；已取消、已关闭、已退款订单不计收益，"
            "不受平台筛选影响。"
        )
        self.card_rented.findChildren(QLabel)[1].setText(f"{rented_count} / {row} 件")
        self.card_rate.findChildren(QLabel)[1].setText(f"{annual_rate:.2f}%")
        self.lbl_last_update.setText(f"最后更新：{QTime.currentTime().toString('HH:mm:ss')}")
        if hasattr(self, "title_bar"):
            self.title_bar.set_sync_status("本地数据已同步", "#a6e3a1")
        self.dashboard_empty_label.setVisible(row == 0)
        self.table.setSortingEnabled(sorting_enabled)
        return

    # ═══════════════════════════════════════════
    # 饰品 CRUD
    # ═══════════════════════════════════════════

    def _normalize_ai_asset_item(self, raw_item):
        """Validate one AI-extracted physical asset without guessing missing values."""
        name = str(raw_item.get("name") or "").strip()
        market_hash_name = str(
            raw_item.get("market_hash_name", raw_item.get("mhn", "")) or ""
        ).strip()
        phase = self._normalize_market_phase(raw_item.get("phase", "-"))
        if not phase:
            return None, "相位格式无效"

        mapped = CS2ItemSchema.lookup_variant(
            name, market_hash_name, phase, str(raw_item.get("paint_index") or "")
        )
        if mapped:
            name = str(mapped.get("name_zh") or name).strip()
            market_hash_name = str(mapped.get("market_hash_name") or market_hash_name)
        if not re.search(r"[\u3400-\u9fff]", name):
            return None, "缺少可确认的中文名称"
        if not market_hash_name or "|" not in market_hash_name:
            return None, "缺少有效的英文 market_hash_name"

        platform_aliases = {
            "C5": "C5GAME", "C5GAME": "C5GAME",
            "ECO": "ECOSteam", "ECOSTEAM": "ECOSteam",
            "悠悠": "悠悠有品", "悠悠有品": "悠悠有品",
            "IGXE": "IGXE", "BUFF": "BUFF",
        }
        platform_raw = str(raw_item.get("platform") or "C5GAME").strip()
        platform = platform_aliases.get(platform_raw.upper(), platform_aliases.get(platform_raw))
        if not platform:
            return None, "平台必须是 C5GAME、ECOSteam、悠悠有品、IGXE 或 BUFF"

        status = str(raw_item.get("status") or "在库").strip()
        if status not in {"在库", "CD冷却"}:
            return None, "新资产状态只能是“在库”或“CD冷却”"
        try:
            cooldown_hours = float(raw_item.get("cooldown_hours", 0) or 0)
        except (TypeError, ValueError):
            return None, "CD 剩余小时必须是数字"
        if cooldown_hours < 0 or cooldown_hours > 24 * 30:
            return None, "CD 剩余小时必须介于 0 和 720 之间"
        if status == "CD冷却" and cooldown_hours <= 0:
            return None, "CD冷却资产必须填写大于 0 的剩余小时"

        values = {
            "name": name,
            "market_hash_name": market_hash_name,
            "phase": phase,
            "pattern": str(raw_item.get("pattern") or "-").strip() or "-",
            "float_val": raw_item.get("float_val", ""),
            "cost": raw_item.get("cost", ""),
            "platform": platform,
            "status": status,
            "rent": 0,
            "days": 0,
            "income": 0,
            "expire_hours": cooldown_hours if status == "CD冷却" else 999,
            "note": str(raw_item.get("note") or "").strip(),
        }
        try:
            record = InventoryItemDraft.from_form(values).to_record()
        except ValueError as exc:
            return None, str(exc)
        record["cooldown_until"] = (
            (datetime.now() + timedelta(hours=cooldown_hours)).isoformat(
                timespec="seconds"
            )
            if status == "CD冷却" else ""
        )
        return record, "可导入"

    def _open_ai_asset_import(self):
        dialog = AssetAIImportDialog(self._normalize_ai_asset_item, self)
        if dialog.exec() != QDialog.Accepted or not dialog.validated_items:
            return
        existing = {
            (
                str(item.get("market_hash_name") or item.get("name") or "").casefold(),
                str(item.get("float_val") or "").strip(),
            )
            for item in self.db.get_all_items()
        }
        added = 0
        skipped = 0
        for record in dialog.validated_items:
            identity = (
                str(record.get("market_hash_name") or record.get("name") or "").casefold(),
                str(record.get("float_val") or "").strip(),
            )
            if identity in existing:
                skipped += 1
                continue
            self.db.add_item(record)
            existing.add(identity)
            added += 1
        self.load_data()
        self._show_toast(f"AI 资产导入完成：新增 {added} 件，跳过 {skipped} 件重复项")

    def edit_selected_item(self):
        selected_row = self.table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "提示", "请先选择要修改的饰品！")
            return

        item_id = self._dashboard_item_id_at_row(selected_row)
        if item_id is None:
            return
        target_item = next((i for i in self.current_items if i["id"] == item_id), None)

        if target_item:
            dialog = ItemEditDialog(target_item, self)
            if dialog.exec() == QDialog.Accepted:
                new_data = dialog.get_data()
                self.db.update_item(item_id, new_data)
                self.load_data()
                self._show_toast("饰品修改已保存")

    def add_item(self):
        dialog = ItemEditDialog(parent=self)
        if dialog.exec() == QDialog.Accepted:
            new_data = dialog.get_data()
            self.db.add_item(new_data)
            self.load_data()
            self._show_toast("新饰品已添加")

    def delete_selected_item(self):
        selected_row = self.table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "提示", "请先选择要删除的饰品！")
            return

        item_id = self._dashboard_item_id_at_row(selected_row)
        if item_id is None:
            return
        name_item = self.table.item(selected_row, 1)
        item_name = name_item.text() if name_item is not None else "该饰品"

        confirm = QMessageBox.question(
            self, "确认删除",
            f"确定要删除【{item_name}】吗？\n删除后可在 10 秒内撤销。",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            self.db.delete_item(item_id)
            self.load_data()
            self._last_deleted_item_id = item_id
            self.undo_delete_button.setVisible(True)
            self._show_toast("饰品已移入可恢复状态，点击右下角可撤销", 10_000)
            QTimer.singleShot(10_000, lambda deleted=item_id: self._hide_undo_for(deleted))

    # ═══════════════════════════════════════════
    # 自动刷新
    # ═══════════════════════════════════════════

    def on_auto_refresh(self):
        """Reload local data as the lightweight layer of global sync."""
        if not self._market_rolling_sync_enabled:
            return
        logger.info("自动刷新: 重新加载数据")
        self.load_data()

    # ═══════════════════════════════════════════
    # 设置保存
    # ═══════════════════════════════════════════

    def save_settings(self):
        partner_id = self.cfg_partner.text().strip()
        rsa_key = self.cfg_rsa.text().strip()
        csqaq_token = self.cfg_csqaq.text().strip()
        csfloat_api_key = self.cfg_csfloat.text().strip()

        try:
            usd_cny_rate = float(self.cfg_usd_cny.text().strip())
        except ValueError:
            QMessageBox.warning(self, "汇率校验", "美元兑人民币汇率必须是数字，例如 7.20。")
            return
        if not 1 <= usd_cny_rate <= 20:
            QMessageBox.warning(self, "汇率校验", "美元兑人民币汇率应介于 1 和 20 之间。")
            return

        if csqaq_token and len(csqaq_token) < 8:
            QMessageBox.warning(self, "校验提示", "CSQAQ ApiToken 格式似乎不正确，请确认。")

        fee_values = {}
        for config_key, field in self.cfg_fee_inputs.items():
            try:
                fee_rate = float(field.text().strip())
            except ValueError:
                QMessageBox.warning(self, "费率校验", f"{config_key} 必须是 0 到 1 之间的小数，例如 0.15。")
                return
            if not 0 <= fee_rate < 1:
                QMessageBox.warning(self, "费率校验", f"{config_key} 必须介于 0 和 1 之间。")
                return
            fee_values[config_key] = f"{fee_rate:g}"

        # Validate every field before the first write so a bad fee cannot leave
        # the API section only partially saved.
        int_val_map = {0: "0", 1: "5", 2: "15", 3: "30", 4: "60"}
        config_values = {
            "csqaq_token": csqaq_token,
            "csfloat_api_key": csfloat_api_key,
            "auto_usd_cny_rate": "1" if self.cfg_auto_usd_cny.isChecked() else "0",
            "usd_cny_rate": f"{usd_cny_rate:.4f}",
            "eco_partner_id": partner_id,
            "eco_rsa_key": rsa_key,
            "refresh_interval": int_val_map[self.cfg_interval.currentIndex()],
            **fee_values,
        }
        self.db.save_configs(config_values)

        self.update_timer_interval()
        self.load_data()
        self._show_toast("设置已保存，费率已重新应用于收益统计")

    def update_timer_interval(self):
        if not self._market_rolling_sync_enabled:
            self.timer.stop()
            return
        cur_int = self.db.get_config("refresh_interval") or "15"
        mins = int(cur_int)
        if mins > 0:
            self.timer.start(mins * 60 * 1000)
        else:
            self.timer.stop()

    def closeEvent(self, event):
        """Stop local timers and release files before the window disappears."""
        self.timer.stop()
        self.rental_countdown_timer.stop()
        self.market_relative_time_timer.stop()
        self.market_rolling_refresh_timer.stop()
        self._save_ui_state()
        if not getattr(self, "_threads_stopped_for_close", False):
            self._cancel_active_workers()
            if self._has_running_workers():
                self.title_bar.set_sync_status("正在安全结束后台任务…", "#f9e2af")
                event.ignore()
                if not getattr(self, "_close_poll_started", False):
                    self._close_poll_started = True
                    QTimer.singleShot(100, self._finish_pending_close)
                return
            self._threads_stopped_for_close = True
        self.db.close()
        event.accept()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)

    def request_shutdown(*_):
        """Let terminal interrupts stop Qt without leaking into Python callbacks."""
        logger.info("收到终端中断信号，正在安全退出")
        app.quit()

    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_shutdown)

    # Give Python regular execution points while Qt owns the main event loop so
    # console signals are dispatched promptly even when the window is idle.
    signal_poll_timer = QTimer()
    signal_poll_timer.timeout.connect(lambda: None)
    signal_poll_timer.start(250)

    win = CS2ManagerApp()
    win.show()
    sys.exit(app.exec())
