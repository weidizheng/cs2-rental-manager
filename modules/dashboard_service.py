"""Pure dashboard calculations kept independent from PySide widgets."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from modules.rental_matching import build_rental_history_index
from modules.rental_terms import classify_rental_term


RENTAL_RELET_WINDOW = timedelta(hours=12)
RENTAL_COOLDOWN_DURATION = timedelta(days=7)


def parse_rental_datetime(value) -> datetime:
    try:
        normalized = " ".join(str(value).split())
        return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.min


def build_dashboard_rental_history_index(items, orders) -> dict:
    return build_rental_history_index(
        items,
        orders,
        sort_key=lambda order: parse_rental_datetime(order.get("start_time")),
    )


def money_text(value) -> str:
    """Format money with financial half-up rounding instead of bankers rounding."""
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0.00")
    return f"¥ {amount:.2f}"


def adjust_cost_by_percent(cost, percentage) -> float:
    try:
        amount = Decimal(str(cost))
        rate = Decimal(str(percentage)) / Decimal("100")
        adjusted = amount * (Decimal("1") + rate)
        return float(adjusted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def price_gap(value, benchmark) -> tuple[float, float]:
    try:
        value_decimal = Decimal(str(value))
        benchmark_decimal = Decimal(str(benchmark))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0, 0.0
    if value_decimal <= 0 or benchmark_decimal <= 0:
        return 0.0, 0.0
    difference = value_decimal - benchmark_decimal
    percentage = difference / benchmark_decimal * Decimal("100")
    return float(difference), float(percentage)


def rental_term(platform, rental_days, explicit_term="") -> str:
    return classify_rental_term(platform, rental_days, explicit_term=explicit_term)


def platform_rent_benchmark(platform, quote, term) -> tuple[float, str]:
    platform = str(platform or "").strip()
    term = str(term or "").strip().casefold()
    if platform == "ECOSteam":
        field, label = "eco_min_rent", "ECO 最低日租（单一行情）"
    else:
        field_map = {
            ("C5GAME", "short"): ("c5_short_rent", "C5 短租最低日租"),
            ("C5GAME", "long"): ("c5_long_rent", "C5 长租最低日租"),
            ("悠悠有品", "short"): ("yyyp_short_rent", "悠悠短租最低日租"),
            ("悠悠有品", "long"): ("yyyp_long_rent", "悠悠长租最低日租"),
            ("IGXE", "short"): ("igxe_short_rent", "IGXE 短租最低日租"),
            ("IGXE", "long"): ("igxe_long_rent", "IGXE 长租最低日租"),
        }
        field, label = field_map.get((platform, term), ("", ""))
    try:
        value = float((quote or {}).get(field, 0.0) or 0.0) if field else 0.0
    except (TypeError, ValueError):
        value = 0.0
    return (value if value > 0 else 0.0), label


def is_non_earning_rental_status(status) -> bool:
    return str(status or "").strip() in {"已取消", "已关闭", "已退款"}


def rental_lifecycle_state(rental_end: datetime, now: datetime | None = None):
    now = now or datetime.now()
    if rental_end <= datetime.min:
        return "unknown", None
    if now < rental_end:
        return "rented", rental_end
    relet_deadline = rental_end + RENTAL_RELET_WINDOW
    if now < relet_deadline:
        return "pending_relet", relet_deadline
    cooldown_end = relet_deadline + RENTAL_COOLDOWN_DURATION
    if now < cooldown_end:
        return "cooldown", cooldown_end
    return "available", None


def sort_dashboard_records(records: list[dict]) -> list[dict]:
    platform_rented_counts: dict[str, int] = {}
    platform_total_counts: dict[str, int] = {}
    type_min_costs: dict[tuple[str, str], float] = {}
    for record in records:
        platform = str(record.get("platform") or "未分类")
        item = record["item"]
        item_type = " ".join(str(item.get("name") or "").split()).casefold()
        cost = float(item.get("cost", 0.0) or 0.0)
        platform_total_counts[platform] = platform_total_counts.get(platform, 0) + 1
        if record.get("is_currently_rented"):
            platform_rented_counts[platform] = platform_rented_counts.get(platform, 0) + 1
        type_key = (platform, item_type)
        type_min_costs[type_key] = min(type_min_costs.get(type_key, cost), cost)

    return sorted(
        records,
        key=lambda record: (
            -platform_rented_counts.get(str(record.get("platform") or "未分类"), 0),
            -platform_total_counts.get(str(record.get("platform") or "未分类"), 0),
            str(record.get("platform") or "未分类").casefold(),
            type_min_costs.get((
                str(record.get("platform") or "未分类"),
                " ".join(str(record["item"].get("name") or "").split()).casefold(),
            ), 0.0),
            " ".join(str(record["item"].get("name") or "").split()).casefold(),
            float(record["item"].get("cost", 0.0) or 0.0),
            int(record["item"].get("id", 0) or 0),
        ),
    )
