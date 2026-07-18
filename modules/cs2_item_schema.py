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

INDEX_FORMAT = 2


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


def _source_metadata(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


class CS2ItemSchema:
    """Load the compact local index and resolve names without network access."""

    _instance: "CS2ItemSchema | None" = None

    def __init__(self, by_zh_name: dict[str, dict[str, Any]], by_market_hash_name: dict[str, dict[str, Any]]):
        self.by_zh_name = by_zh_name
        self.by_market_hash_name = by_market_hash_name

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
                return cls(cached.get("by_zh_name", {}), cached.get("by_market_hash_name", {}))
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
            by_zh_name.setdefault(_canonical_name(chinese_name), record)
            by_market_hash_name.setdefault(market_hash_name, record)

        payload = {
            "format": INDEX_FORMAT,
            "sources": current_sources,
            "by_zh_name": by_zh_name,
            "by_market_hash_name": by_market_hash_name,
        }
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=INDEX_PATH.parent, suffix=".tmp") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            temp_path = Path(handle.name)
        temp_path.replace(INDEX_PATH)
        return cls(by_zh_name, by_market_hash_name)
