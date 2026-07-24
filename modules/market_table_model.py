"""Model/view implementation for the market watch table."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont, QPixmap

from modules.cs2_item_schema import CS2ItemSchema
from modules.workers import csfloat_quote_is_fresh


HEADERS = (
    "图片",
    "饰品名称 / Phase",
    "CSQAQ 国内最低",
    "CSFloat 底价",
    "ECO 最低日租",
    "C5 租金（短 / 长）",
    "悠悠 租金（短 / 长）",
    "IGXE 租金（短 / 长）",
    "更新时间",
)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


class MarketTableModel(QAbstractTableModel):
    """A compact, sortable projection of market-watch dictionaries."""

    def __init__(
        self,
        *,
        thumbnail_provider: Callable[[dict], QPixmap],
        updated_text: Callable[[dict], str],
        parent=None,
    ):
        super().__init__(parent)
        self._thumbnail_provider = thumbnail_provider
        self._updated_text = updated_text
        self._entries: list[dict] = []
        self._rows: list[list[dict[str, Any]]] = []
        self._visible_rows: list[int] = []
        self._query = ""

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._visible_rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and 0 <= section < len(HEADERS):
            return HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._visible_rows)):
            return None
        cell = self._rows[self._visible_rows[index.row()]][index.column()]
        if role == Qt.DisplayRole:
            return cell.get("text", "")
        if role == Qt.DecorationRole:
            return cell.get("decoration")
        if role == Qt.ToolTipRole:
            return cell.get("tooltip", "")
        if role == Qt.ForegroundRole:
            return cell.get("color")
        if role == Qt.FontRole:
            return cell.get("font")
        if role == Qt.TextAlignmentRole:
            return int(cell.get("alignment", Qt.AlignVCenter | Qt.AlignLeft))
        if role == Qt.UserRole:
            return self._entries[self._visible_rows[index.row()]]
        return None

    def set_entries(self, entries: list[dict]):
        self.beginResetModel()
        self._entries = entries
        self._rows = [self._build_row(entry) for entry in entries]
        self._visible_rows = self._filtered_row_indexes()
        self.endResetModel()

    def set_filter(self, query: str):
        query = str(query or "").strip().casefold()
        if query == self._query:
            return
        self.beginResetModel()
        self._query = query
        self._visible_rows = self._filtered_row_indexes()
        self.endResetModel()

    def refresh_display(self):
        """Rebuild inexpensive text roles after relative timestamps change."""
        self.beginResetModel()
        self._rows = [self._build_row(entry) for entry in self._entries]
        self._visible_rows = self._filtered_row_indexes()
        self.endResetModel()

    def entry_at(self, index: QModelIndex) -> dict | None:
        if not index.isValid() or not (0 <= index.row() < len(self._visible_rows)):
            return None
        return self._entries[self._visible_rows[index.row()]]

    def sort(self, column, order=Qt.AscendingOrder):
        if not 0 <= column < len(HEADERS):
            return
        descending = order == Qt.DescendingOrder
        self.layoutAboutToBeChanged.emit()
        self._visible_rows.sort(
            key=lambda row: self._rows[row][column].get("sort", ""), reverse=descending
        )
        self.layoutChanged.emit()

    def _filtered_row_indexes(self) -> list[int]:
        if not self._query:
            return list(range(len(self._entries)))
        matched = []
        for row, entry in enumerate(self._entries):
            searchable = " ".join(
                (
                    str(entry.get("name", "")),
                    str(entry.get("market_hash_name", "")),
                    str(entry.get("phase", "")),
                )
            ).casefold()
            if self._query in searchable:
                matched.append(row)
        return matched

    @staticmethod
    def _cell(
        text="",
        *,
        color=None,
        tooltip="",
        sort="",
        decoration=None,
        font=None,
        alignment=Qt.AlignVCenter | Qt.AlignLeft,
    ):
        return {
            "text": text,
            "color": QColor(color) if color else None,
            "tooltip": tooltip,
            "sort": sort,
            "decoration": decoration,
            "font": font,
            "alignment": alignment,
        }

    def _build_row(self, entry: dict) -> list[dict[str, Any]]:
        thumbnail = self._thumbnail_provider(entry)
        detail_name = (entry.get("detail") or {}).get("name_zh", "")
        display_name = CS2ItemSchema.chinese_display_name(
            detail_name or entry.get("name", ""),
            entry.get("market_hash_name", ""),
            entry.get("phase", "-"),
            entry.get("paint_index", ""),
        )
        if entry.get("phase") and entry["phase"] != "-":
            display_name += f"  [{entry['phase']}]"
        name_font = QFont("Microsoft YaHei", 12, QFont.DemiBold)

        lowest = _number(entry.get("csqaq_min_sell_price")) or _number(entry.get("csqaq_price"))
        platform = str(entry.get("csqaq_min_sell_platform") or "")
        domestic_text = f"¥ {lowest:.2f}" if lowest else "暂无"
        domestic_note = platform if lowest and platform else "CSQAQ 未提供"

        market_hash_name = str(entry.get("market_hash_name", ""))
        cached_query_name = str(entry.get("csfloat_query_mhn") or "")
        csfloat_status = str(entry.get("csfloat_status") or "").removeprefix("skipped_")
        csfloat = _number(entry.get("csfloat_min_sell_cny"))
        csfloat_buy = _number(entry.get("csfloat_highest_buy_cny"))
        if cached_query_name and cached_query_name != market_hash_name:
            csfloat = 0.0
            csfloat_buy = 0.0
            csfloat_status = "name_changed"
        fresh = csfloat_quote_is_fresh(entry, market_hash_name)
        if csfloat:
            csfloat_text = f"¥ {csfloat:.2f}"
            if csfloat_buy:
                csfloat_text += f"\n最高求购 ¥ {csfloat_buy:.2f}"
            if not fresh:
                csfloat_text += "\n缓存待刷新"
            difference = (csfloat - lowest) / lowest if lowest else 0
            csfloat_color = "#a6e3a1" if difference < -0.0005 else "#f38ba8" if difference > 0.0005 else "#89dceb"
        else:
            reason_map = {
                "name_changed": "名称已变更，待刷新",
                "missing_api_key": "未配置 API Key",
                "unauthorized": "API Key 无效",
                "forbidden": "访问被拒绝",
                "rate_limited": "频控冷却中",
                "network": "网络请求失败",
                "invalid_json": "响应异常",
                "deferred": "排队到下一轮",
                "no_listing": "无固定价在售",
            }
            csfloat_text = "底价暂无\n" + reason_map.get(csfloat_status, "尚未刷新")
            csfloat_color = "#6c7086"
        csfloat_tip = "CSFloat 固定价格，不含竞拍；人民币价格仅用于比较。"

        eco = _number(entry.get("eco_min_rent"))
        eco_text = f"¥ {eco:.2f}/天" if eco else "暂无（ECO 无租金）"

        def rent_text(short_key: str, long_key: str, label: str):
            short = _number(entry.get(short_key))
            long = _number(entry.get(long_key))
            if not short and not long:
                return "暂无", "#6c7086", 0.0
            parts = []
            if short:
                parts.append(f"短 ¥ {short:.2f}")
            if long:
                parts.append(f"长 ¥ {long:.2f}")
            return " / ".join(parts), label, max(short, long)

        c5_text, c5_color, c5_sort = rent_text("c5_short_rent", "c5_long_rent", "#f9e2af")
        yyyp_text, yyyp_color, yyyp_sort = rent_text("yyyp_short_rent", "yyyp_long_rent", "#94e2d5")
        igxe_text, igxe_color, igxe_sort = rent_text("igxe_short_rent", "igxe_long_rent", "#cba6f7")
        normal_font = QFont("Microsoft YaHei", 10)
        metric_font = QFont("Microsoft YaHei", 11, QFont.DemiBold)
        return [
            self._cell(decoration=thumbnail, tooltip=entry.get("market_hash_name", ""), sort=display_name),
            self._cell(display_name, tooltip=entry.get("market_hash_name", display_name), sort=display_name.casefold(), font=name_font),
            self._cell(domestic_text + f"\n{domestic_note}", color="#a6e3a1" if lowest else "#6c7086", tooltip=f"CSQAQ 国内最低平台：{platform or '暂无'}", sort=lowest, font=metric_font),
            self._cell(csfloat_text, color=csfloat_color, tooltip=csfloat_tip, sort=csfloat, font=metric_font),
            self._cell(eco_text, color="#89b4fa" if eco else "#6c7086", sort=eco, font=normal_font),
            self._cell(c5_text, color=c5_color, sort=c5_sort, font=normal_font),
            self._cell(yyyp_text, color=yyyp_color, sort=yyyp_sort, font=normal_font),
            self._cell(igxe_text, color=igxe_color, sort=igxe_sort, font=normal_font),
            self._cell(self._updated_text(entry), color="#9399b2", sort=str(entry.get("updated_at", "")), font=normal_font),
        ]
