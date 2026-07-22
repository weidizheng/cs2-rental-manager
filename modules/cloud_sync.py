"""Encrypted portable bundles for manual Google Drive synchronisation."""

from __future__ import annotations

import base64
import json
import platform
from datetime import datetime
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes

from modules.atomic_io import atomic_write_json
from modules.image_cache import MarketCache
from modules.paths import get_private_path


SYNC_FORMAT = "cs2-rental-manager-sync"
SYNC_VERSION = 2
SYNC_FILENAME = "CS2RentalSync.cs2sync"
PBKDF2_ITERATIONS = 600_000
API_CONFIG_KEYS = (
    "csqaq_token",
    "csfloat_api_key",
    "eco_partner_id",
    "eco_rsa_key",
    "auto_usd_cny_rate",
    "usd_cny_rate",
)
WATCH_SYNC_FIELDS = (
    "key",
    "name",
    "phase",
    "market_hash_name",
    "image_url",
    "links",
    "schema_id",
    "paint_index",
    "csqaq_good_id",
    "c5_id",
    "yyyp_id",
    "igxe_id",
    "eco_id",
)


def get_sync_directory() -> Path:
    directory = get_private_path("cloud-sync")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_sync_inbox_directory() -> Path:
    directory = get_sync_directory() / "inbox"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_sync_outbox_directory() -> Path:
    directory = get_sync_directory() / "outbox"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _atomic_write_json(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


def _prune_sync_files(
    directory: Path,
    keep: int,
    preferred: Path | None = None,
) -> int:
    """Keep a bounded number of bundles, always preserving ``preferred``."""
    keep = max(1, int(keep))
    directory = Path(directory)
    preferred = Path(preferred).resolve() if preferred else None
    candidates = []
    for candidate in directory.glob("*.cs2sync"):
        if not candidate.is_file():
            continue
        try:
            modified_at = candidate.stat().st_mtime_ns
        except OSError:
            continue
        candidates.append((modified_at, candidate.name.casefold(), candidate))

    candidates.sort(reverse=True)
    retained: set[Path] = set()
    if preferred is not None:
        retained.add(preferred)
    for _modified_at, _name, candidate in candidates:
        resolved = candidate.resolve()
        if len(retained) >= keep:
            break
        retained.add(resolved)

    removed = 0
    for _modified_at, _name, candidate in candidates:
        if candidate.resolve() in retained:
            continue
        candidate.unlink(missing_ok=True)
        removed += 1
    return removed


def _watch_item_count(cache: dict) -> int:
    categories = cache.get("categories", []) if isinstance(cache, dict) else []
    return sum(
        len(category.get("items", []))
        for category in categories
        if isinstance(category, dict) and isinstance(category.get("items"), list)
    )


def _normalise_categories(cache: dict) -> tuple[list[dict], str]:
    categories = cache.get("categories") if isinstance(cache, dict) else None
    if isinstance(categories, list):
        valid = []
        for index, category in enumerate(categories, start=1):
            if not isinstance(category, dict):
                continue
            items = category.get("items", [])
            valid.append({
                "id": str(category.get("id") or f"category_{index}"),
                "name": str(category.get("name") or f"分类 {index}"),
                "items": [dict(item) for item in items if isinstance(item, dict)]
                if isinstance(items, list) else [],
            })
        return valid, str(cache.get("active_category_id") or "")

    legacy_items = [
        dict(item) for item in cache.values() if isinstance(item, dict)
    ] if isinstance(cache, dict) else []
    return ([{"id": "rentals", "name": "出租品", "items": legacy_items}]
            if legacy_items else []), "rentals"


def _portable_watch_cache(cache: dict) -> dict:
    """Export identities and links, not stale prices from another device."""
    categories, active = _normalise_categories(cache)
    portable_categories = []
    for category in categories:
        portable_categories.append({
            "id": category["id"],
            "name": category["name"],
            "items": [
                {
                    key: item[key]
                    for key in WATCH_SYNC_FIELDS
                    if key in item
                }
                for item in category["items"]
            ],
        })
    return {
        "format": "market_watchlist_v1",
        "active_category_id": active,
        "categories": portable_categories,
    }


def _portable_api_config(db) -> dict:
    config = {key: db.get_config(key) for key in API_CONFIG_KEYS}
    rsa_value = str(config.get("eco_rsa_key") or "").strip()
    if rsa_value and "\n" not in rsa_value and len(rsa_value) < 1024:
        try:
            rsa_path = Path(rsa_value).expanduser()
            if rsa_path.is_file():
                config["eco_rsa_key"] = rsa_path.read_text(encoding="utf-8")
        except OSError:
            pass
    return config


def build_sync_payload(db, market_cache: dict | None = None) -> dict:
    """Build the selective data set held only inside encrypted ciphertext."""
    if market_cache is None:
        durable_cache = db.load_market_watchlist()
        cache = durable_cache if durable_cache.get("categories") else MarketCache.load()
    else:
        cache = market_cache
    assets_by_id = {
        item.get("id"): str(item.get("asset_id") or "")
        for item in db.get_all_items()
    }
    portable_orders = []
    for stored_order in db.get_rental_orders():
        order = dict(stored_order)
        local_item_id = order.pop("item_id", None)
        asset_id = assets_by_id.get(local_item_id, "")
        if asset_id:
            order["asset_id"] = asset_id
        portable_orders.append(order)
    return {
        "format": SYNC_FORMAT,
        "version": SYNC_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "source_device": platform.node() or "unknown-device",
        "data": {
            "rental_orders": portable_orders,
            "market_watchlist": _portable_watch_cache(
                cache if isinstance(cache, dict) else {}
            ),
            "api_config": _portable_api_config(db),
        },
    }


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    if len(str(password or "")) < 8:
        raise ValueError("同步口令至少需要 8 个字符")
    return PBKDF2(
        str(password).encode("utf-8"),
        salt,
        dkLen=32,
        count=iterations,
        hmac_hash_module=SHA256,
    )


def _encrypt_payload(payload: dict, password: str) -> dict:
    salt = get_random_bytes(16)
    nonce = get_random_bytes(12)
    key = _derive_key(password, salt, PBKDF2_ITERATIONS)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=16)
    cipher.update(f"{SYNC_FORMAT}:{SYNC_VERSION}".encode("ascii"))
    plaintext = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    def encode(value):
        return base64.b64encode(value).decode("ascii")
    return {
        "format": SYNC_FORMAT,
        "version": SYNC_VERSION,
        "exported_at": payload["exported_at"],
        "source_device": payload["source_device"],
        "encryption": {
            "algorithm": "AES-256-GCM",
            "kdf": "PBKDF2-HMAC-SHA256",
            "iterations": PBKDF2_ITERATIONS,
            "salt": encode(salt),
            "nonce": encode(nonce),
            "tag": encode(tag),
        },
        "ciphertext": encode(ciphertext),
    }


def _decode_base64(value: Any, label: str) -> bytes:
    try:
        return base64.b64decode(str(value).encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"同步包的 {label} 编码无效") from exc


def _validate_payload(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("format") != SYNC_FORMAT:
        raise ValueError("解密内容不是 CS2 Rental Manager 同步数据")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("同步包缺少 data 数据区")
    if not isinstance(data.get("rental_orders"), list):
        raise ValueError("同步包的出租订单格式无效")
    if not isinstance(data.get("market_watchlist"), dict):
        raise ValueError("同步包的行情收藏格式无效")
    if not isinstance(data.get("api_config"), dict):
        raise ValueError("同步包的 API 配置格式无效")
    return payload


def export_sync_bundle(db, password: str, path: str | Path | None = None) -> dict:
    """Create an AES-GCM encrypted bundle suitable for cloud storage."""
    target = Path(path) if path else get_sync_outbox_directory() / SYNC_FILENAME
    payload = build_sync_payload(db)
    _atomic_write_json(target, _encrypt_payload(payload, password))
    removed_old_bundles = 0
    outbox_directory = get_sync_outbox_directory()
    if target.parent.resolve() == outbox_directory.resolve():
        removed_old_bundles = _prune_sync_files(
            outbox_directory, keep=1, preferred=target
        )
    watchlist = payload["data"]["market_watchlist"]
    api_config = payload["data"]["api_config"]
    return {
        "path": str(target),
        "orders": len(payload["data"]["rental_orders"]),
        "categories": len(watchlist.get("categories", [])),
        "watch_items": _watch_item_count(watchlist),
        "api_configs": sum(bool(str(value or "").strip()) for value in api_config.values()),
        "removed_old_bundles": removed_old_bundles,
    }


def load_sync_bundle(path: str | Path, password: str) -> dict:
    """Authenticate, decrypt and validate a sync bundle before import."""
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as bundle_file:
            envelope = json.load(bundle_file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"同步包格式损坏（第 {exc.lineno} 行，第 {exc.colno} 列）") from exc
    except OSError as exc:
        raise ValueError(f"无法读取同步包：{exc}") from exc

    if not isinstance(envelope, dict) or envelope.get("format") != SYNC_FORMAT:
        raise ValueError("文件不是 CS2 Rental Manager 同步包")
    if envelope.get("version") != SYNC_VERSION:
        raise ValueError(f"不支持的同步包版本：{envelope.get('version')}")
    encryption = envelope.get("encryption")
    if not isinstance(encryption, dict) or encryption.get("algorithm") != "AES-256-GCM":
        raise ValueError("同步包未使用受支持的加密格式")
    try:
        iterations = int(encryption.get("iterations"))
    except (TypeError, ValueError) as exc:
        raise ValueError("同步包的密钥派生参数无效") from exc
    if not 100_000 <= iterations <= 2_000_000:
        raise ValueError("同步包的密钥派生次数不在安全范围内")

    salt = _decode_base64(encryption.get("salt"), "salt")
    nonce = _decode_base64(encryption.get("nonce"), "nonce")
    tag = _decode_base64(encryption.get("tag"), "tag")
    ciphertext = _decode_base64(envelope.get("ciphertext"), "ciphertext")
    key = _derive_key(password, salt, iterations)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=16)
    cipher.update(f"{SYNC_FORMAT}:{SYNC_VERSION}".encode("ascii"))
    try:
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except ValueError as exc:
        raise ValueError("同步口令错误，或文件已被修改") from exc
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("同步包解密后的数据无效") from exc
    return _validate_payload(payload)


def _watch_identity(item: dict) -> str:
    key = str(item.get("key") or "").strip()
    if key:
        return key.casefold()
    name = str(item.get("market_hash_name") or item.get("name") or "").strip()
    phase = str(item.get("phase") or "-").strip()
    return f"{name}|{phase}".casefold()


def merge_market_caches(local_cache: dict, incoming_cache: dict) -> dict:
    """Merge watch identities while retaining local-only items and quote data."""
    local_categories, local_active = _normalise_categories(local_cache)
    incoming_categories, incoming_active = _normalise_categories(incoming_cache)
    merged = [
        {"id": category["id"], "name": category["name"], "items": list(category["items"])}
        for category in local_categories
    ]
    by_id = {category["id"]: category for category in merged}
    by_name = {category["name"].strip().casefold(): category for category in merged}
    incoming_id_map = {}

    for incoming in incoming_categories:
        target = by_id.get(incoming["id"])
        if target is None:
            target = by_name.get(incoming["name"].strip().casefold())
        if target is None:
            target = {"id": incoming["id"], "name": incoming["name"], "items": []}
            merged.append(target)
            by_id[target["id"]] = target
            by_name[target["name"].strip().casefold()] = target
        elif target["id"] == incoming["id"]:
            target["name"] = incoming["name"]
            by_name[target["name"].strip().casefold()] = target
        incoming_id_map[incoming["id"]] = target["id"]

        item_positions = {
            _watch_identity(item): index
            for index, item in enumerate(target["items"])
            if _watch_identity(item)
        }
        for item in incoming["items"]:
            identity = _watch_identity(item)
            if identity and identity in item_positions:
                position = item_positions[identity]
                combined = dict(target["items"][position])
                combined.update(item)
                target["items"][position] = combined
            else:
                target["items"].append(dict(item))
                if identity:
                    item_positions[identity] = len(target["items"]) - 1

    if not merged:
        merged = [{"id": "rentals", "name": "出租品", "items": []}]
    valid_ids = {category["id"] for category in merged}
    active = local_active if local_active in valid_ids else ""
    if not active and incoming_active in incoming_id_map:
        active = incoming_id_map[incoming_active]
    if active not in valid_ids:
        active = merged[0]["id"]
    return {
        "format": "market_categories_v1",
        "active_category_id": active,
        "categories": merged,
    }


def import_sync_bundle(db, path: str | Path, password: str) -> dict:
    """Back up local sync data, then merge the authenticated imported bundle."""
    payload = load_sync_bundle(path, password)
    backup_directory = get_sync_directory() / "backups"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_directory / f"local-before-import-{timestamp}.cs2sync"
    export_sync_bundle(db, password, backup_path)
    removed_old_backups = _prune_sync_files(
        backup_directory, keep=3, preferred=backup_path
    )

    data = payload["data"]
    orders = []
    for stored_order in data["rental_orders"]:
        if not isinstance(stored_order, dict):
            continue
        order = dict(stored_order)
        # SQLite IDs are local to a device. Older v2 bundles may contain one;
        # always discard it and resolve the portable asset_id/float locally.
        order.pop("item_id", None)
        orders.append(order)
    grouped_orders: dict[str, list[dict]] = {}
    for order in orders:
        platform_name = str(order.get("platform") or "").strip()
        if platform_name and str(order.get("order_no") or "").strip():
            grouped_orders.setdefault(platform_name, []).append(order)
    imported_config = {
        key: value
        for key, value in data["api_config"].items()
        if key in API_CONFIG_KEYS and str(value or "").strip()
    }
    local_watchlist = db.load_market_watchlist()
    if not local_watchlist.get("categories"):
        local_watchlist = MarketCache.load()
    merged_market_cache = merge_market_caches(
        local_watchlist, data["market_watchlist"]
    )
    db.merge_sync_data(grouped_orders, imported_config, merged_market_cache)
    MarketCache.save(merged_market_cache)
    return {
        "source": str(path),
        "source_device": payload.get("source_device", ""),
        "exported_at": payload.get("exported_at", ""),
        "backup_path": str(backup_path),
        "removed_old_backups": removed_old_backups,
        "orders": len(orders),
        "categories": len(merged_market_cache.get("categories", [])),
        "watch_items": _watch_item_count(merged_market_cache),
        "api_configs": len(imported_config),
    }
