"""Parser for text copied from the C5 rental-order page."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from modules.rental_terms import classify_rental_term


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _split_yuan_amount(lines: list[str]) -> float:
    """Read C5's copied price, including the common ``19`` + ``.8`` layout."""
    for index, line in enumerate(lines):
        if "￥" not in line and "¥" not in line:
            continue
        fragments: list[str] = []
        after_symbol = re.split(r"[￥¥]", line, maxsplit=1)[-1].strip().replace(" ", "")
        candidates = [after_symbol, *lines[index + 1:index + 4]]
        for candidate in candidates:
            compact = candidate.strip().replace(" ", "").replace(",", "")
            if not compact:
                continue
            if not re.fullmatch(r"\d+|\.\d+|\d+\.\d+", compact):
                break
            fragments.append(compact)
            joined = "".join(fragments)
            if "." in joined or len(fragments) >= 2:
                break
        try:
            return float("".join(fragments))
        except ValueError:
            continue
    return 0.0


def _duration_days(start_time: str, return_time: str) -> float:
    try:
        start = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(return_time, "%Y-%m-%d %H:%M:%S")
        # C5's list only exposes timestamps, while its rental duration is a
        # whole number of days.  Small timestamp offsets must not turn a
        # nine-day order into an awkward ``9.001`` day value in the dashboard.
        return float(max(0, round((end - start).total_seconds() / 86400)))
    except ValueError:
        return 0.0


def parse_c5_rent_text(page_text: str) -> list[dict[str, Any]]:
    """Extract the stable fields visible in C5's rental-list text.

    C5 is a client-rendered page and can change its markup.  We intentionally
    accept only records with an order number.
    """
    order_starts = list(re.finditer(r"订单号\s*[：:]?\s*(\d{8,})", page_text))
    orders: list[dict[str, Any]] = []
    statuses = ("租赁中", "已转交", "已归还", "已完成", "已取消", "已关闭", "已退款")

    for index, start in enumerate(order_starts):
        end = order_starts[index + 1].start() if index + 1 < len(order_starts) else len(page_text)
        block = page_text[start.start():end]
        order_no = start.group(1)
        datetimes = [
            re.sub(r"\s+", " ", value)
            for value in re.findall(r"20\d{2}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2}", block)
        ]
        status = next((item for item in statuses if item in block), "")
        float_val = _first_match(r"(?:磨损|磨损度)\s*[：:]?\s*([0-9.]+)", block)

        lines = [line.strip() for line in block.splitlines() if line.strip()]
        item_name = next((line for line in lines if "|" in line and len(line) <= 100), "")
        if not item_name:
            for line in lines:
                compact = line.replace(" ", "")
                if (
                    "订单号" in line
                    or "磨损" in line
                    or "查看详情" in line
                    or "归还时间" in line
                    or "实际收入" in line
                    or line in statuses
                    or re.fullmatch(r"20\d{2}-\d{2}-\d{2}\s*\d{2}:\d{2}:\d{2}", line)
                    or re.fullmatch(r"[￥¥]?\s*[\d.]+", compact)
                ):
                    continue
                if 2 <= len(line) <= 100:
                    item_name = line
                    break

        start_time = datetimes[0] if datetimes else ""
        return_time = datetimes[1] if len(datetimes) > 1 else ""
        income = _split_yuan_amount(lines)
        rental_days = _duration_days(start_time, return_time)
        daily_rent = income / rental_days if income > 0 and rental_days > 0 else 0.0

        orders.append(
            {
                "order_no": order_no,
                "item_name": item_name,
                "float_val": float_val,
                "daily_rent": daily_rent,
                "rental_days": rental_days,
                "rental_type": classify_rental_term("C5GAME", rental_days, block),
                "deposit": 0.0,
                "income": income,
                "start_time": start_time,
                "return_time": return_time,
                "status": status,
                "raw_text": block[:2000],
            }
        )
    return orders
