"""Clipboard parsers for the fixed C5, ECO and IGXE rental-order layouts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from modules.c5_rental_browser import parse_c5_rent_text


def _float(value: str | None) -> float:
    try:
        return float((value or "").replace(",", "").strip())
    except ValueError:
        return 0.0


def _first(pattern: str, text: str, flags: int = re.IGNORECASE | re.DOTALL) -> str:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else ""


def _split_blocks(pattern: str, text: str) -> list[str]:
    starts = list(re.finditer(pattern, text, re.MULTILINE))
    return [
        text[start.start(): starts[index + 1].start() if index + 1 < len(starts) else len(text)]
        for index, start in enumerate(starts)
    ]


def _normal_datetime(value: str) -> str:
    compact = re.sub(r"\s+", " ", value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y年%m月%d日 %H:%M:%S"):
        try:
            return datetime.strptime(compact, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""


def _status_from_text(text: str, default: str = "") -> str:
    for status in ("租赁中", "已转交", "已完成", "已取消", "已关闭", "已退款"):
        if status in text:
            return status
    return default


def parse_eco_clipboard(text: str) -> list[dict[str, Any]]:
    starts = list(re.finditer(r"(?m)^\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+订单编号：\s*(\d+)", text))
    orders: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        block = text[start.start(): starts[index + 1].start() if index + 1 < len(starts) else len(text)]
        start_time, order_no = start.group(1), start.group(2)
        item_name = _first(r"\[([^\]]+)\]\(", block)
        float_val = _first(r"磨损\s*[：:]\s*([0-9.]+)", block)
        daily_rent = _float(_first(r"￥\s*([0-9.]+)\s*/\s*天", block))
        rental_days = _float(_first(r"×\s*([0-9.]+)\s*[（(]\s*(?:长租|短租)", block))
        deposit = _float(_first(r"含押金\s*￥\s*([0-9.]+)", block))
        return_time = _normal_datetime(_first(r"(20\d{2}年\d{2}月\d{2}日\s+\d{2}:\d{2}:\d{2})\s*前归还", block))
        orders.append({
            "order_no": order_no,
            "item_name": item_name,
            "float_val": float_val,
            "daily_rent": daily_rent,
            "rental_days": rental_days,
            "deposit": deposit,
            "income": daily_rent * rental_days,
            "start_time": start_time,
            "return_time": return_time,
            "status": _status_from_text(block),
            "raw_text": block[:4000],
        })
    return orders


def parse_igxe_clipboard(text: str) -> list[dict[str, Any]]:
    blocks = _split_blocks(r"(?m)^\s*订单类型\s*[：:]", text)
    orders: list[dict[str, Any]] = []
    for block in blocks:
        trade_id = _first(r"https?://www\.igxe\.cn/lease/trade/730/(\d+)", block)
        if not trade_id:
            continue
        start_time = _normal_datetime(_first(r"创建时间\s*[：:]\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", block))
        return_time = _normal_datetime(_first(r"归还截止时间\s*[：:]\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", block))
        item_name = _first(r"\[([^\]]+)\]\(https?://www\.igxe\.cn/lease/trade/730/", block)
        float_val = _first(r"磨损\s*([0-9.]+)", block)
        daily_rent = _float(_first(r"租赁价格[\s\S]{0,80}?￥\s*([0-9.]+)\s*/\s*天", block))
        # IGXE copied text often preserves Markdown emphasis, for example
        # ``**8天**`` and ``**￥** **3288.48**``.
        rental_days = _float(_first(r"出租天数\s*[：:][\s*]*([0-9.]+)\s*天", block))
        deposit = _float(_first(r"饰品押金[\s\S]{0,80}?￥[\s*]*([0-9.]+)", block))
        income = _float(_first(r"订单金额[\s\S]{0,80}?￥\s*([0-9.]+)", block))
        if income <= 0:
            income = daily_rent * rental_days
        parsed_return = _parse_datetime_or_min(return_time)
        status = "租赁中" if parsed_return and parsed_return > datetime.now() else "已完成"
        orders.append({
            "order_no": f"IGXE-{trade_id}",
            "item_name": item_name,
            "float_val": float_val,
            "daily_rent": daily_rent,
            "rental_days": rental_days,
            "deposit": deposit,
            "income": income,
            "start_time": start_time,
            "return_time": return_time,
            "status": status,
            "raw_text": block[:4000],
        })
    return orders


def _parse_datetime_or_min(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def detect_clipboard_platform(text: str) -> str:
    if "归还截止时间" in text and "igxe.cn/lease/trade" in text:
        return "IGXE"
    if "订单编号" in text and ("前归还" in text or "ECO_" in text):
        return "ECOSteam"
    if "订单号" in text and "查看详情" in text:
        return "C5GAME"
    return ""


def parse_rental_clipboard(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Detect one platform's copied order page and return structured orders."""
    platform = detect_clipboard_platform(text)
    if platform == "C5GAME":
        return platform, parse_c5_rent_text(text)
    if platform == "ECOSteam":
        return platform, parse_eco_clipboard(text)
    if platform == "IGXE":
        return platform, parse_igxe_clipboard(text)
    raise ValueError("未识别到 C5、ECO 或 IGXE 的订单页格式。")
