"""Pure helpers for market-watch categories and durable cache payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def normalize_phase(value: Any) -> str:
    """Return one canonical display phase, or an empty string when unsupported."""
    text = str(value or "-").strip()
    compact = text.upper().replace(" ", "")
    aliases = {
        "": "-",
        "-": "-",
        "NONE": "-",
        "N/A": "-",
        "P1": "P1",
        "P2": "P2",
        "P3": "P3",
        "P4": "P4",
        "P1/P3": "P1 / P3",
        "RUBY": "Ruby",
        "红宝石": "Ruby",
        "SAPPHIRE": "Sapphire",
        "蓝宝石": "Sapphire",
        "EMERALD": "Emerald",
        "绿宝石": "Emerald",
        "BLACKPEARL": "Black Pearl",
        "黑珍珠": "Black Pearl",
    }
    return aliases.get(compact, "")


def watch_identity(market_hash_name: Any, phase: Any) -> str:
    """Build the stable identity shared by categories, cache and sync payloads."""
    normalized_phase = normalize_phase(phase) or str(phase or "-").strip()
    phase_key = normalized_phase.upper().replace(" ", "")
    if phase_key in {"P1", "P3", "P1/P3"}:
        phase_key = "P1/P3"
    return f"{str(market_hash_name).strip()}|{phase_key}"


def merge_durable_watchlist(durable: dict, cached: dict) -> dict:
    """Use durable identities as truth while retaining any compatible quote fields."""
    durable_categories = durable.get("categories", []) if isinstance(durable, dict) else []
    cached_categories = cached.get("categories", []) if isinstance(cached, dict) else []
    if not durable_categories:
        return deepcopy(cached) if isinstance(cached, dict) else {}

    cached_by_category: dict[str, dict[str, dict]] = {}
    cached_global: dict[str, dict] = {}
    for category in cached_categories:
        if not isinstance(category, dict):
            continue
        category_id = str(category.get("id") or "")
        entries: dict[str, dict] = {}
        for entry in category.get("items", []):
            if not isinstance(entry, dict):
                continue
            identity = watch_identity(
                entry.get("market_hash_name", entry.get("name", "")), entry.get("phase", "-")
            ).casefold()
            entries[identity] = entry
            cached_global.setdefault(identity, entry)
        cached_by_category[category_id] = entries

    categories = []
    for category in durable_categories:
        if not isinstance(category, dict):
            continue
        category_id = str(category.get("id") or "")
        items = []
        for durable_entry in category.get("items", []):
            if not isinstance(durable_entry, dict):
                continue
            identity = watch_identity(
                durable_entry.get("market_hash_name", durable_entry.get("name", "")),
                durable_entry.get("phase", "-"),
            ).casefold()
            merged = dict(
                cached_by_category.get(category_id, {}).get(identity)
                or cached_global.get(identity)
                or {}
            )
            merged.update(durable_entry)
            items.append(merged)
        categories.append(
            {
                "id": category_id,
                "name": str(category.get("name") or category_id),
                "items": items,
            }
        )
    return {
        "format": "market_categories_v1",
        "active_category_id": str(durable.get("active_category_id") or ""),
        "categories": categories,
    }
