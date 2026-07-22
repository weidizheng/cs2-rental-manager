"""Clipboard parsers for the fixed C5, ECO and IGXE rental-order layouts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from modules.c5_rental_browser import parse_c5_rent_text
from modules.rental_terms import classify_rental_term


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
    for status in ("已归还", "待归还", "租赁中", "已转交", "已完成", "已取消", "已关闭", "已退款"):
        if status in text:
            return status
    return default


def _transfer_status_from_text(text: str) -> str:
    """Read the explicit C5 transfer state without confusing reward text."""
    value = _first(r"转租状态\s*[：:]?\s*([^\r\n*]+)", text)
    return value.strip()[:30] if value else ""


def _end_from_start_and_days(start_time: str, rental_days: float) -> str:
    if not start_time or rental_days <= 0:
        return ""
    try:
        start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        return (start + timedelta(days=rental_days)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


def _c5_transfer_reward(text: str) -> tuple[float, str, bool]:
    """Return only a confirmed C5 transfer-reward amount, never a maximum."""
    section_match = re.search(r"转租奖励([\s\S]{0,160})", text)
    if not section_match:
        return 0.0, "", False
    section = section_match.group(1)
    if "已取消" in section:
        return 0.0, "已取消", True
    status = next((value for value in ("待发放", "已发放") if value in section), "")
    if not status or "最高奖励" in section:
        return 0.0, "", False
    amount = _float(_first(r"￥\s*([0-9.]+)", section))
    return amount, status, amount >= 0


def parse_c5_detail_clipboard(text: str) -> list[dict[str, Any]]:
    """Parse one copied C5 order-detail page, including a settled reward."""
    order_no = _first(r"订单(?:编号|号)\s*[：:]?\s*(\d{8,})", text)
    if not order_no:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    item_name = next(
        (line for line in lines if "|" in line and "http" not in line.lower() and len(line) <= 100), ""
    )
    start_time = _normal_datetime(_first(
        r"下单时间\s*[：:]?\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", text
    ))
    rental_days = _float(_first(r"租期时长[\s\S]{0,80}?([0-9.]+)\s*天", text))
    price_section = _first(r"租赁价格([\s\S]{0,120})", text)
    daily_rent = _float(_first(r"￥\s*([0-9.]+)\s*/\s*天", price_section))
    income = _float(_first(r"=\s*￥\s*([0-9.]+)", price_section))
    if income <= 0:
        income = daily_rent * rental_days
    rental_end = _normal_datetime(_first(
        r"租赁到期\s*[：:]?\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", text
    ))
    return_deadline = _normal_datetime(_first(
        r"归还截[止至]\s*[：:]?\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", text
    ))
    return_time = rental_end or _end_from_start_and_days(start_time, rental_days) or return_deadline
    reward, reward_status, reward_known = _c5_transfer_reward(text)
    return [{
        "order_no": order_no,
        "item_name": item_name,
        "float_val": _first(r"磨损\s*[：:]?\s*([0-9.]+)", text),
        "daily_rent": daily_rent,
        "rental_days": rental_days,
        "rental_type": classify_rental_term("C5GAME", rental_days, text),
        "deposit": _float(_first(r"饰品押金[\s\S]{0,80}?￥\s*([0-9.]+)", text)),
        "income": income,
        "start_time": start_time,
        "return_time": return_time,
        "rental_end_time": rental_end or return_time,
        "return_deadline": return_deadline,
        "transfer_status": _transfer_status_from_text(text),
        "status": _status_from_text(text),
        "transfer_reward": reward,
        "reward_status": reward_status,
        "transfer_reward_known": reward_known,
        "raw_text": text[:4000],
    }]


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
        return_deadline = _normal_datetime(_first(r"(20\d{2}年\d{2}月\d{2}日\s+\d{2}:\d{2}:\d{2})\s*前归还", block))
        # ECO exposes a return deadline.  Rental availability ends at the
        # explicitly ordered duration, normally about twelve hours earlier.
        return_time = _end_from_start_and_days(start_time, rental_days) or return_deadline
        orders.append({
            "order_no": order_no,
            "item_name": item_name,
            "float_val": float_val,
            "daily_rent": daily_rent,
            "rental_days": rental_days,
            "rental_type": classify_rental_term("ECOSteam", rental_days, block),
            "deposit": deposit,
            "income": daily_rent * rental_days,
            "start_time": start_time,
            "return_time": return_time,
            "rental_end_time": return_time,
            "return_deadline": return_deadline,
            "transfer_status": "",
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
        rental_end_time = _normal_datetime(_first(r"租赁到期时间\s*[：:]\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", block))
        return_deadline = _normal_datetime(_first(r"归还截止时间\s*[：:]\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", block))
        return_time = rental_end_time or return_deadline
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
            "rental_type": classify_rental_term("IGXE", rental_days, block),
            "deposit": deposit,
            "income": income,
            "start_time": start_time,
            "return_time": return_time,
            "rental_end_time": rental_end_time or return_time,
            "return_deadline": return_deadline,
            "transfer_status": "",
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
    if "订单编号" in text and "租赁价格" in text:
        return "C5GAME"
    return ""


def parse_rental_clipboard(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Detect one platform's copied order page and return structured orders."""
    platform = detect_clipboard_platform(text)
    if platform == "C5GAME":
        if "查看详情" in text:
            return platform, parse_c5_rent_text(text)
        return platform, parse_c5_detail_clipboard(text)
    if platform == "ECOSteam":
        return platform, parse_eco_clipboard(text)
    if platform == "IGXE":
        return platform, parse_igxe_clipboard(text)
    raise ValueError("未识别到 C5、ECO 或 IGXE 的订单页格式。")
