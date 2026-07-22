"""Deterministic order-to-asset matching without any UI dependencies."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Iterable


MIN_FLOAT_MATCH_DECIMAL_PLACES = 6


def _decimal_with_precision(value: Any) -> tuple[Decimal, int] | None:
    """Parse a finite decimal while preserving its reported fractional precision."""
    try:
        text = str(value).strip()
        number = Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not number.is_finite():
        return None
    return number, max(0, -number.as_tuple().exponent)


def float_precision(value: Any) -> int:
    """Return the reported number of decimal places, or ``-1`` if invalid."""
    parsed = _decimal_with_precision(value)
    return parsed[1] if parsed is not None else -1


def float_match_precision(
    left_value: Any,
    right_value: Any,
    *,
    min_decimal_places: int = MIN_FLOAT_MATCH_DECIMAL_PLACES,
) -> int | None:
    """Return the shared precision when two asset floats safely match.

    The less precise value is treated as the stored representation.  A more
    precise value matches when truncating *or* rounding it to that precision
    produces the stored value.  Low-precision values are deliberately rejected
    because they are not sufficiently distinctive for automatic association.

    Callers that have several matches can use :func:`float_precision` to prefer
    the candidate retaining the most source digits.
    """
    left = _decimal_with_precision(left_value)
    right = _decimal_with_precision(right_value)
    if left is None or right is None:
        return None

    left_number, left_precision = left
    right_number, right_precision = right
    shared_precision = min(left_precision, right_precision)
    if shared_precision < max(0, int(min_decimal_places)):
        return None

    if left_precision <= right_precision:
        shorter, longer = left_number, right_number
    else:
        shorter, longer = right_number, left_number

    quantum = Decimal(1).scaleb(-shared_precision)
    truncated = longer.quantize(quantum, rounding=ROUND_DOWN)
    rounded = longer.quantize(quantum, rounding=ROUND_HALF_UP)
    return shared_precision if shorter in (truncated, rounded) else None


def rental_float_matches(asset_value: Any, order_value: Any) -> bool:
    """Match an order to legacy asset precision using the original strict bound.

    Order association predates AI asset merging and intentionally keeps its
    half-unit boundary strict.  The broader truncation-or-rounding rule belongs
    to the same-type asset import planner, where ambiguous candidates are shown
    to the user instead of silently attaching an order.
    """
    asset = _decimal_with_precision(asset_value)
    order = _decimal_with_precision(order_value)
    if asset is None or order is None:
        return False
    asset_number, asset_precision = asset
    order_number, _order_precision = order
    if asset_number == order_number:
        return True
    if asset_precision <= 0:
        return False
    tolerance = Decimal("0.5") * (Decimal(10) ** -asset_precision)
    return abs(asset_number - order_number) < tolerance


def match_order_to_items(
    order: dict[str, Any], items: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Return one safe association, or an explicit ambiguous/unmatched result."""
    inventory = list(items)
    explicit_id = order.get("item_id")
    if explicit_id not in (None, ""):
        try:
            wanted_id = int(explicit_id)
        except (TypeError, ValueError):
            wanted_id = -1
        if any(int(item.get("id") or -2) == wanted_id for item in inventory):
            return {
                "item_id": wanted_id,
                "method": str(order.get("match_method") or "manual"),
                "confidence": float(order.get("match_confidence") or 1.0),
            }

    order_asset_id = str(order.get("asset_id") or "").strip()
    if order_asset_id:
        matches = [
            item for item in inventory
            if str(item.get("asset_id") or "").strip() == order_asset_id
        ]
        if len(matches) == 1:
            return {
                "item_id": int(matches[0]["id"]),
                "method": "asset_id",
                "confidence": 1.0,
            }
        if len(matches) > 1:
            return {"item_id": None, "method": "ambiguous_asset_id", "confidence": 0.0}

    order_float = str(order.get("float_val") or "").strip()
    if not order_float:
        return {"item_id": None, "method": "unmatched", "confidence": 0.0}

    candidates = [
        item for item in inventory
        if rental_float_matches(item.get("float_val"), order_float)
    ]
    order_name = str(order.get("item_name") or "").strip().casefold()
    if len(candidates) > 1 and order_name:
        named = [
            item for item in candidates
            if order_name in str(item.get("name") or "").casefold()
            or str(item.get("name") or "").casefold() in order_name
        ]
        if named:
            candidates = named
    if len(candidates) != 1:
        return {
            "item_id": None,
            "method": "ambiguous_float" if candidates else "unmatched",
            "confidence": 0.0,
        }

    asset_float = str(candidates[0].get("float_val") or "").strip()
    exact = asset_float == order_float
    return {
        "item_id": int(candidates[0]["id"]),
        "method": "exact_float" if exact else "fuzzy_float",
        "confidence": 1.0 if exact else 0.8,
    }


def build_rental_history_index(
    items: Iterable[dict[str, Any]],
    orders: Iterable[dict[str, Any]],
    *,
    sort_key=None,
) -> dict[int, list[dict[str, Any]]]:
    """Group orders by stable item id and use float matching only for legacy rows."""
    inventory = list(items)
    item_ids = {int(item["id"]) for item in inventory if item.get("id") is not None}
    histories: dict[int, list[dict[str, Any]]] = {
        item_id: [] for item_id in item_ids
    }
    for order in orders:
        linked_id = order.get("item_id")
        try:
            linked_id = int(linked_id) if linked_id not in (None, "") else None
        except (TypeError, ValueError):
            linked_id = None
        if linked_id not in item_ids:
            linked_id = match_order_to_items(order, inventory).get("item_id")
        if linked_id in histories:
            histories[linked_id].append(order)
    if sort_key is not None:
        for history in histories.values():
            history.sort(key=sort_key)
    return histories
