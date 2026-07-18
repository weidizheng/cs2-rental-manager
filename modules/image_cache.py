"""
图片与行情本地缓存模块

功能：
1. 本地图片异步下载与缓存 (data/images/{market_hash_name}.png)
2. 行情缓存落盘 (data/market_cache.json)，冷启动时直接读取渲染界面
"""
import os
import json
import logging
import requests
from typing import Optional, Dict, Any

from modules.paths import get_private_data_dir

logger = logging.getLogger("CS2Rental")

DATA_DIR = str(get_private_data_dir())
IMAGES_DIR = os.path.join(DATA_DIR, "images")
MARKET_CACHE_PATH = os.path.join(DATA_DIR, "market_cache.json")


class ImageCache:
    """本地图片缓存管理器"""

    @staticmethod
    def get_local_path(market_hash_name: str) -> str:
        """
        获取本地图片路径。
        将特殊字符替换为下划线，防止文件系统不兼容。
        """
        safe_name = (
            market_hash_name
            .replace("/", "_")
            .replace("|", "_")
            .replace(":", "_")
            .replace(" ", "_")
        )
        return os.path.join(IMAGES_DIR, f"{safe_name}.png")

    @staticmethod
    def exists(market_hash_name: str) -> bool:
        """检查本地图片是否存在"""
        return os.path.exists(ImageCache.get_local_path(market_hash_name))

    @staticmethod
    def download(market_hash_name: str, url: str) -> Optional[str]:
        """
        下载图片到本地缓存（同步，适用于 QThread 工作线程）。

        Args:
            market_hash_name: 饰品唯一标识名
            url: 图片远程 URL（来自 ECO GoodsImg 或 CSQAQ image_url）

        Returns:
            本地文件路径（成功）或 None（失败/已存在）
        """
        local_path = ImageCache.get_local_path(market_hash_name)
        if os.path.exists(local_path):
            return local_path
        if not url:
            return None
        try:
            os.makedirs(IMAGES_DIR, exist_ok=True)
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                logger.info(f"[ImageCache] 图片下载成功: {market_hash_name}")
                return local_path
            else:
                logger.warning(
                    f"[ImageCache] 图片下载 HTTP {resp.status_code}: {market_hash_name}"
                )
        except Exception as e:
            logger.warning(f"[ImageCache] 图片下载异常 ({market_hash_name}): {e}")
        return None


class MarketCache:
    """行情数据缓存管理器（落盘到 data/market_cache.json）"""

    @staticmethod
    def _summary(data: Dict[str, Any]) -> str:
        """Human-readable entry count for both legacy and category-aware caches."""
        categories = data.get("categories") if isinstance(data, dict) else None
        if isinstance(categories, list):
            item_count = sum(
                len(category.get("items", []))
                for category in categories
                if isinstance(category, dict) and isinstance(category.get("items", []), list)
            )
            return f"{item_count} 条，{len(categories)} 个分类"
        return f"{len(data) if isinstance(data, dict) else 0} 条"

    @staticmethod
    def save(data: Dict[str, Any]):
        """
        保存行情缓存到 JSON 文件。

        Args:
            data: 以 "{name}|{phase}" 为 key 的行情数据字典
        """
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(MARKET_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"[MarketCache] 行情缓存已保存 ({MarketCache._summary(data)})")
        except Exception as e:
            logger.warning(f"[MarketCache] 保存失败: {e}")

    @staticmethod
    def load() -> Dict[str, Any]:
        """
        从 JSON 文件加载行情缓存。

        Returns:
            缓存字典（文件不存在或解析失败时返回空字典）
        """
        if os.path.exists(MARKET_CACHE_PATH):
            try:
                with open(MARKET_CACHE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"[MarketCache] 行情缓存已加载 ({MarketCache._summary(data)})")
                return data
            except Exception as e:
                logger.warning(f"[MarketCache] 读取失败: {e}")
        return {}
