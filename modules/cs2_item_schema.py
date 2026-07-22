"""Offline Chinese-to-Steam Market item mapping built from ByMykel data."""

from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from modules.paths import get_private_path


SOURCE_DIR = get_private_path("schema-source")
EN_SOURCE_PATH = SOURCE_DIR / "skins_not_grouped.en.json"
ZH_SOURCE_PATH = SOURCE_DIR / "skins_not_grouped.zh-CN.json"
INDEX_PATH = get_private_path("cs2_items_schema.json")

INDEX_FORMAT = 3


# Valve paint indexes are stable across the knives that support these finishes.
# Steam uses the same market hash name for the individual Doppler phases, so the
# paint index is the reliable local discriminator.
PHASE_BY_PAINT_INDEX = {
    "415": "Ruby",
    "416": "Sapphire",
    "417": "Black Pearl",
    "418": "P1",
    "419": "P2",
    "420": "P3",
    "421": "P4",
    "568": "Emerald",
    "569": "P1",
    "570": "P2",
    "571": "P3",
    "572": "P4",
}


def _canonical_name(value: str) -> str:
    """Normalize punctuation, whitespace and star placement for local lookup."""
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"★?(.+?)(?:\(★\))?\|(.+)\((.+)\)", text)
    if match:
        weapon, skin, wear = match.groups()
        weapon = weapon.removeprefix("★")
        return f"{weapon}(★)|{skin}({wear})"
    return text


def _search_text(value: str) -> str:
    """Normalize human search terms without requiring a full market hash name."""
    text = unicodedata.normalize("NFKC", value or "").lower()
    text = text.replace("伽马", "伽玛")
    text = re.sub(r"[\s|()（）★™_\-]+", "", text)
    # Common English inputs become comparable to the Chinese local schema.
    aliases = (
        ("butterflyknife", "蝴蝶刀"),
        ("butterfly", "蝴蝶刀"),
        ("nomadknife", "流浪者匕首"),
        ("gammadoppler", "伽玛多普勒"),
        ("gamma", "伽玛"),
        ("doppler", "多普勒"),
        ("blackpearl", "黑珍珠"),
        ("sapphire", "蓝宝石"),
        ("emerald", "绿宝石"),
        ("ruby", "红宝石"),
        ("phase1", "p1"),
        ("phase2", "p2"),
        ("phase3", "p3"),
        ("phase4", "p4"),
        ("factorynew", "崭新出厂"),
        ("minimalwear", "略有磨损"),
        ("fieldtested", "久经沙场"),
    )
    for source, target in aliases:
        text = text.replace(source, target)
    return text


def _search_fragments(value: str) -> list[str]:
    """Extract meaningful weapon/finish/wear fragments for unordered matching."""
    raw_parts = re.split(r"[|()（）]", value or "")
    fragments = []
    for part in raw_parts:
        normalized = _search_text(part)
        if len(normalized) >= 2 and normalized not in fragments:
            fragments.append(normalized)
    full = _search_text(value)
    if len(full) >= 2 and full not in fragments:
        fragments.append(full)
    return fragments


def phase_hint_from_search(value: str) -> str:
    """Return a canonical phase from either Chinese or English free text."""
    text = _search_text(value)
    phase_aliases = (
        ("黑珍珠", "Black Pearl"),
        ("红宝石", "Ruby"),
        ("蓝宝石", "Sapphire"),
        ("绿宝石", "Emerald"),
    )
    for alias, phase in phase_aliases:
        if alias in text:
            return phase
    match = re.search(r"p([1-4])", text)
    return f"P{match.group(1)}" if match else "-"


def _source_metadata(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


class CS2ItemSchema:
    """Load the compact local index and resolve names without network access."""

    _instance: "CS2ItemSchema | None" = None

    def __init__(
            self,
            by_zh_name: dict[str, dict[str, Any]],
            by_market_hash_name: dict[str, dict[str, Any]],
            records: list[dict[str, Any]] | None = None,
    ):
        self.by_zh_name = by_zh_name
        self.by_market_hash_name = by_market_hash_name
        self.records = records if records is not None else list(by_zh_name.values())

    @classmethod
    def get(cls) -> "CS2ItemSchema":
        if cls._instance is None:
            cls._instance = cls._load_or_build()
        return cls._instance

    @classmethod
    def lookup(cls, display_name: str) -> dict[str, Any] | None:
        """Resolve either a Chinese display name or an English market name."""
        schema = cls.get()
        return (
            schema.by_zh_name.get(_canonical_name(display_name))
            or schema.by_market_hash_name.get(display_name.strip())
        )

    @classmethod
    def lookup_variant(
            cls,
            display_name: str = "",
            market_hash_name: str = "",
            phase: str = "-",
            paint_index: str = "",
    ) -> dict[str, Any] | None:
        """Resolve a phase variant that shares its Steam name with other phases."""
        schema = cls.get()
        requested_phase = str(phase or "-").upper().replace(" ", "")
        requested_paint = str(paint_index or "")
        canonical_display = _canonical_name(display_name) if display_name else ""
        market_name = str(market_hash_name or "").strip()
        if requested_phase != "-" or requested_paint:
            for record in schema.records:
                record_phase = str(record.get("phase") or "-").upper().replace(" ", "")
                if requested_paint and str(record.get("paint_index") or "") != requested_paint:
                    continue
                if requested_phase != "-" and record_phase != requested_phase:
                    continue
                if market_name and record.get("market_hash_name") == market_name:
                    return record
                if canonical_display and _canonical_name(record.get("name_zh", "")) == canonical_display:
                    return record
        return cls.lookup(display_name or market_name)

    @classmethod
    def chinese_display_name(
        cls,
        display_name: str = "",
        market_hash_name: str = "",
        phase: str = "-",
        paint_index: str = "",
    ) -> str:
        """Return a Chinese UI label and never fall back to an English market name."""
        mapped = cls.lookup_variant(
            display_name, market_hash_name, phase, paint_index
        )
        if mapped and str(mapped.get("name_zh") or "").strip():
            return str(mapped["name_zh"]).strip()
        candidate = str(display_name or "").strip()
        if re.search(r"[\u3400-\u9fff]", candidate):
            return candidate
        return "未知饰品"

    @classmethod
    def search(cls, query: str, limit: int = 100) -> list[dict[str, Any]]:
        """Find local schema records from partial Chinese or English user input.

        Matching is deliberately local and read-only: the caller receives
        candidates to show the user, rather than guessing one item and making
        a remote price request with an incomplete name.
        """
        query_text = _search_text(query)
        if len(query_text) < 2:
            return []

        exact = cls.lookup(query)
        if exact:
            return [exact]

        schema = cls.get()
        ranked: list[tuple[int, int, str, dict[str, Any]]] = []
        seen_market_names = set()
        for record in schema.records:
            market_hash_name = record.get("market_hash_name", "")
            phase = str(record.get("phase") or "-")
            result_identity = (market_hash_name, phase, str(record.get("paint_index") or ""))
            if not market_hash_name or result_identity in seen_market_names:
                continue

            zh_text = _search_text(record.get("name_zh", ""))
            en_text = _search_text(market_hash_name)
            phase_text = _search_text(phase if phase != "-" else "")
            candidate_text = zh_text + en_text + phase_text
            # Do not let a generic "Doppler" substring turn a Gamma/Ruby/etc.
            # request into a regular Doppler candidate.
            required_qualifiers = ("伽玛", "红宝石", "蓝宝石", "绿宝石", "黑珍珠")
            if any(
                qualifier in query_text and qualifier not in candidate_text
                for qualifier in required_qualifiers
            ):
                continue
            required_weapons = (
                "蝴蝶刀", "m9刺刀", "刺刀", "折叠刀", "爪子刀", "弯刀", "短剑",
                "鲍伊猎刀", "猎杀者匕首", "流浪者匕首", "骷髅匕首", "系绳匕首",
                "求生匕首", "暗影双匕", "海豹短刀", "折刀",
            )
            if any(
                weapon in query_text and weapon not in candidate_text
                for weapon in required_weapons
            ):
                continue
            fragments = _search_fragments(record.get("name_zh", ""))
            fragments.extend(
                fragment for fragment in _search_fragments(market_hash_name)
                if fragment not in fragments
            )
            if phase_text and phase_text not in fragments:
                fragments.append(phase_text)

            score = 0
            if query_text in candidate_text:
                score += 100 + len(query_text)
            matched_fragments = 0
            for fragment in fragments:
                if fragment == query_text:
                    score += 80
                    matched_fragments += 1
                elif fragment in query_text:
                    score += len(fragment) * 8
                    matched_fragments += 1
                elif query_text in fragment:
                    score += len(query_text) * 2
                    matched_fragments += 1

            if score <= 0 or matched_fragments == 0:
                continue
            # Prefer normal variants when names are otherwise equally relevant.
            stattrak_penalty = (
                1
                if "stattrak" not in query_text and "stattrak" in _search_text(record.get("name_zh", ""))
                else 0
            )
            ranked.append((score, stattrak_penalty, record.get("name_zh", ""), record))
            seen_market_names.add(result_identity)

        ranked.sort(key=lambda row: (-row[0], row[1], row[2]))
        records = [record for _score, _stattrak_penalty, _name, record in ranked]
        if "stattrak" not in query_text:
            records.sort(key=lambda record: "stattrak" in _search_text(record.get("name_zh", "")))
        return records[:max(1, limit)]

    @classmethod
    def _load_or_build(cls) -> "CS2ItemSchema":
        if not EN_SOURCE_PATH.exists() or not ZH_SOURCE_PATH.exists():
            return cls({}, {})

        current_sources = {
            "en": _source_metadata(EN_SOURCE_PATH),
            "zh-CN": _source_metadata(ZH_SOURCE_PATH),
        }
        try:
            with INDEX_PATH.open("r", encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached.get("format") == INDEX_FORMAT and cached.get("sources") == current_sources:
                return cls(
                    cached.get("by_zh_name", {}),
                    cached.get("by_market_hash_name", {}),
                    cached.get("records", []),
                )
        except (OSError, ValueError, TypeError):
            pass

        return cls._build(current_sources)

    @classmethod
    def _build(cls, current_sources: dict[str, dict[str, int]]) -> "CS2ItemSchema":
        with EN_SOURCE_PATH.open("r", encoding="utf-8") as handle:
            english_items = json.load(handle)
        with ZH_SOURCE_PATH.open("r", encoding="utf-8") as handle:
            chinese_items = json.load(handle)

        english_by_id = {item.get("id"): item for item in english_items if item.get("id")}
        by_zh_name: dict[str, dict[str, Any]] = {}
        by_market_hash_name: dict[str, dict[str, Any]] = {}
        records: list[dict[str, Any]] = []

        for chinese_item in chinese_items:
            item_id = chinese_item.get("id")
            english_item = english_by_id.get(item_id)
            market_hash_name = (english_item or chinese_item).get("market_hash_name")
            chinese_name = chinese_item.get("name")
            if not item_id or not chinese_name or not market_hash_name:
                continue

            record = {
                "id": item_id,
                "name_zh": chinese_name,
                "market_hash_name": market_hash_name,
                "image": (english_item or chinese_item).get("image", ""),
                "paint_index": chinese_item.get("paint_index", ""),
                "wear_zh": (chinese_item.get("wear") or {}).get("name", ""),
                "wear_en": ((english_item or {}).get("wear") or {}).get("name", ""),
            }
            record["phase"] = PHASE_BY_PAINT_INDEX.get(str(record["paint_index"]), "-")
            records.append(record)
            by_zh_name.setdefault(_canonical_name(chinese_name), record)
            by_market_hash_name.setdefault(market_hash_name, record)

        payload = {
            "format": INDEX_FORMAT,
            "sources": current_sources,
            "by_zh_name": by_zh_name,
            "by_market_hash_name": by_market_hash_name,
            "records": records,
        }
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=INDEX_PATH.parent, suffix=".tmp") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            temp_path = Path(handle.name)
        temp_path.replace(INDEX_PATH)
        return cls(by_zh_name, by_market_hash_name, records)
