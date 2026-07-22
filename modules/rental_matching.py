"""Deterministic order-to-asset matching without any UI dependencies."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


def rental_float_matches(asset_value: Any, order_value: Any) -> bool:
    """Match a full platform float to an older, possibly truncated asset float."""
    try:
        asset_text = str(asset_value).strip()
        asset_float = Decimal(asset_text)
        order_float = Decimal(str(order_value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return False
    if not asset_float.is_finite() or not order_float.is_finite():
        return False
    if asset_float == order_float:
        return True
    decimal_places = len(asset_text.partition(".")[2])
    if decimal_places <= 0:
        return False
    tolerance = Decimal("0.5") * (Decimal(10) ** -decimal_places)
    return abs(asset_float - order_float) < tolerance


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
