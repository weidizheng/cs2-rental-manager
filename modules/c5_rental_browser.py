"""Visible, manual-only C5 rental-order reader.

The browser profile is private runtime data.  This module never imports a
user's normal Chrome profile, stores a password, or attempts to solve a
captcha.  A person completes login/verification in the Playwright window.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.paths import get_private_path


C5_RENT_URL = "https://www.c5game.com/user/rent?actag=2"


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
    accept only records with an order number; the original HTML snapshot is
    retained privately to make selector updates auditable when it changes.
    """
    order_starts = list(re.finditer(r"订单号\s*[：:]?\s*(\d{8,})", page_text))
    orders: list[dict[str, Any]] = []
    statuses = ("租赁中", "已转交", "已完成", "已取消", "已关闭", "已退款")

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
                "deposit": 0.0,
                "income": income,
                "start_time": start_time,
                "return_time": return_time,
                "status": status,
                "raw_text": block[:2000],
            }
        )
    return orders


class C5RentalBrowser:
    """Runs a visible persistent Chromium profile for one manual C5 action."""

    profile_dir = get_private_path("browser-profiles", "c5game")

    @classmethod
    def _launch_context(cls, playwright):
        cls.profile_dir.mkdir(parents=True, exist_ok=True)
        return playwright.chromium.launch_persistent_context(
            user_data_dir=str(cls.profile_dir),
            headless=False,
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
        )

    @staticmethod
    def _navigate(page) -> None:
        page.goto(C5_RENT_URL, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2_500)

    def open_login(self) -> dict[str, Any]:
        """Open C5 and wait until the user closes the browser window."""
        from playwright.sync_api import Error, sync_playwright

        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._navigate(page)
                # Closing the visible window is the explicit completion signal.
                while context.pages:
                    page.wait_for_timeout(400)
            except Error:
                pass
            finally:
                try:
                    context.close()
                except Error:
                    pass
        return {"success": True, "message": "C5 浏览器已关闭，登录状态已保存在私有档案中。"}

    def sync_orders(self) -> dict[str, Any]:
        """Read one manually triggered C5 rental-list page and close it."""
        from playwright.sync_api import Error, sync_playwright

        snapshot_path: Path | None = None
        with sync_playwright() as playwright:
            context = self._launch_context(playwright)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._navigate(page)
                page_text = page.locator("body").inner_text(timeout=15_000)
                page_html = page.content()

                snapshot_dir = get_private_path("browser-snapshots")
                snapshot_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                snapshot_path = snapshot_dir / f"c5-rent-{timestamp}.html"
                snapshot_path.write_text(page_html, encoding="utf-8")

                needs_login = "订单号" not in page_text and (
                    "登录" in page_text or "请先登录" in page_text
                )
                orders = parse_c5_rent_text(page_text)
                return {
                    "success": True,
                    "needs_login": needs_login,
                    "orders": orders,
                    "snapshot_path": str(snapshot_path),
                    "page_preview": page_text[:500],
                }
            except Error as exc:
                return {
                    "success": False,
                    "needs_login": False,
                    "orders": [],
                    "snapshot_path": str(snapshot_path) if snapshot_path else "",
                    "error": str(exc),
                }
            finally:
                try:
                    context.close()
                except Error:
                    pass
