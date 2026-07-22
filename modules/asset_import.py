"""Plan and apply safe AI-assisted inventory imports.

The import planner deliberately keeps asset identity separate from database
identity.  Human- and AI-provided names are normalized through the local item
schema, while an existing row's ``id``/``asset_id`` remains authoritative so
already-linked rental orders never move to a newly-created duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Iterable

from modules.cs2_item_schema import CS2ItemSchema
from modules.rental_matching import float_match_precision, float_precision


@dataclass(slots=True)
class ImportDecision:
    action: str
    incoming: dict[str, Any]
    existing_id: int | None = None
    merged_record: dict[str, Any] | None = None
    candidate_ids: tuple[int, ...] = field(default_factory=tuple)
    message: str = ""


def _normalized_market_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.removeprefix("★").strip()
    return re.sub(r"\s+", " ", text)


def _normalized_phase(value: Any) -> str:
    phase = str(value or "-").strip().upper().replace(" ", "")
    return phase or "-"


def asset_import_identity(item: dict[str, Any]) -> tuple[str, str]:
    """Return a schema-backed market identity without using a database ID."""
    name = str(item.get("name") or "").strip()
    market_hash_name = str(item.get("market_hash_name") or "").strip()
    phase = _normalized_phase(item.get("phase", "-"))
    mapped = CS2ItemSchema.lookup_variant(
        name,
        market_hash_name,
        phase,
        str(item.get("paint_index") or ""),
    )
    standard_name = (
        str(mapped.get("market_hash_name") or "").strip()
        if mapped else market_hash_name
    )
    # A legacy row may have no market_hash_name.  In that case the Chinese
    # display name is still a useful fallback, and schema lookup above normally
    # converts it to the same standard English identity as a new import.
    return _normalized_market_name(standard_name or name), phase


def _phases_compatible(left: str, right: str) -> bool:
    return left == right or "-" in {left, right}


def _same_asset_type(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_name, left_phase = asset_import_identity(left)
    right_name, right_phase = asset_import_identity(right)
    return bool(
        left_name
        and left_name == right_name
        and _phases_compatible(left_phase, right_phase)
    )


def _prefer_incoming_metadata(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    if float_precision(incoming.get("float_val")) > float_precision(existing.get("float_val")):
        return True
    if not str(existing.get("market_hash_name") or "").strip() and str(
        incoming.get("market_hash_name") or ""
    ).strip():
        return True
    return False


def _merged_asset_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Update current purchase metadata while preserving stable history fields."""
    merged = dict(existing)
    incoming_precision = float_precision(incoming.get("float_val"))
    existing_precision = float_precision(existing.get("float_val"))
    for field_name in (
        "name", "market_hash_name", "cost", "platform", "status",
        "expire_hours", "cooldown_until",
    ):
        if field_name in incoming:
            merged[field_name] = incoming[field_name]
    if incoming_precision > existing_precision:
        merged["float_val"] = str(incoming.get("float_val") or "").strip()
    for field_name in ("phase", "pattern", "note"):
        value = str(incoming.get(field_name) or "").strip()
        if value and value != "-":
            merged[field_name] = incoming[field_name]
    # These values may contain legacy manual totals.  A fresh AI row contains
    # zeros, so overwriting them would silently erase data not yet represented
    # by imported rental orders.
    merged["rent"] = existing.get("rent", 0)
    merged["days"] = existing.get("days", 0)
    merged["income"] = existing.get("income", 0)
    merged["asset_id"] = existing.get("asset_id", "")
    return merged


def plan_asset_import(
    existing_items: Iterable[dict[str, Any]],
    incoming_records: Iterable[dict[str, Any]],
) -> list[ImportDecision]:
    """Classify every row as add, merge, skip or ambiguous.

    Only one same-type, compatible-precision candidate may be merged.  Multiple
    candidates stay untouched because choosing by row order or rental time can
    attach financial history to the wrong physical item.
    """
    existing = [dict(item) for item in existing_items]
    decisions: list[ImportDecision] = []
    batch_rows: list[tuple[dict[str, Any], int]] = []

    for incoming in incoming_records:
        incoming = dict(incoming)
        candidates = [
            item for item in existing
            if _same_asset_type(item, incoming)
            and float_match_precision(item.get("float_val"), incoming.get("float_val")) is not None
        ]
        if len(candidates) > 1:
            ids = tuple(int(item["id"]) for item in candidates if item.get("id") is not None)
            decisions.append(ImportDecision(
                "ambiguous",
                incoming,
                candidate_ids=ids,
                message=f"发现 {len(candidates)} 个可能的已有资产，请手工确认",
            ))
            continue
        if len(candidates) == 1:
            matched = candidates[0]
            existing_id = int(matched["id"])
            if _prefer_incoming_metadata(matched, incoming):
                decisions.append(ImportDecision(
                    "merge",
                    incoming,
                    existing_id=existing_id,
                    merged_record=_merged_asset_record(matched, incoming),
                    message=f"合并已有资产 #{existing_id}，保留订单历史并采用更完整磨损",
                ))
            else:
                decisions.append(ImportDecision(
                    "skip",
                    incoming,
                    existing_id=existing_id,
                    message=f"与已有资产 #{existing_id} 重复，已跳过",
                ))
            continue

        batch_matches = [
            (record, decision_index)
            for record, decision_index in batch_rows
            if _same_asset_type(record, incoming)
            and float_match_precision(record.get("float_val"), incoming.get("float_val")) is not None
        ]
        if batch_matches:
            # The first row owns the eventual insert.  If a later duplicate has
            # more precision, upgrade that pending row instead of adding twice.
            if len(batch_matches) == 1:
                previous, decision_index = batch_matches[0]
                if float_precision(incoming.get("float_val")) > float_precision(previous.get("float_val")):
                    decisions[decision_index].incoming = incoming
                    batch_rows = [
                        (incoming if index == decision_index else record, index)
                        for record, index in batch_rows
                    ]
                decisions.append(ImportDecision(
                    "skip", incoming, message="与本批另一条资产重复，已合并为一件"
                ))
            else:
                decisions.append(ImportDecision(
                    "ambiguous", incoming, message="本批存在多个可能重复项，请手工确认"
                ))
            continue

        decision_index = len(decisions)
        decisions.append(ImportDecision("add", incoming, message="新增资产"))
        batch_rows.append((incoming, decision_index))

    return decisions


def apply_asset_import_plan(db, decisions: Iterable[ImportDecision]) -> dict[str, int]:
    """Apply only non-ambiguous decisions and return user-facing counters."""
    counts = {"added": 0, "merged": 0, "skipped": 0, "ambiguous": 0}
    for decision in decisions:
        if decision.action == "add":
            db.add_item(decision.incoming)
            counts["added"] += 1
        elif decision.action == "merge" and decision.existing_id is not None:
            db.update_item(decision.existing_id, decision.merged_record or decision.incoming)
            counts["merged"] += 1
        elif decision.action == "ambiguous":
            counts["ambiguous"] += 1
        else:
            counts["skipped"] += 1
    return counts
