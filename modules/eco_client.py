import base64
import json
import logging
import threading
import time
from typing import Dict, Any, List

import requests
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

from modules.base_client import BaseAPIClient
from modules.eco_market_cache import CACHE_TTL_SECONDS, ECOMarketCache

logger = logging.getLogger("CS2Rental")
_snapshot_refresh_lock = threading.Lock()


class ECOClient(BaseAPIClient):
    """
    ECO 开放平台 API 客户端。

    功能：
    - 保留签名基础设施与通用请求能力
    - GetHashNameAndPriceList：获取 ECO 全量在售价格与起租价（60 秒缓存）
    - BatchGetGoodsDetail：批量获取饰品图片 (GoodsImg) 与 PaintIndexLabel

    频率限制: 5 次/秒 → min_interval=0.25
    """

    def __init__(
        self,
        partner_id: str,
        private_key_str: str = "",
        private_key_path: str = "",
    ):
        super().__init__(min_interval=0.25)
        self.partner_id = partner_id
        # 支持直接传入私钥文本，或从文件路径读取
        if private_key_path and not private_key_str:
            with open(private_key_path, "r", encoding="utf-8") as f:
                private_key_str = f.read()
        self.private_key_str = private_key_str
        self.base_url = "https://openapi.ecosteam.cn"

        self.market_cache = ECOMarketCache(partner_id)
        self.last_price_source = "none"
        self.last_cache_status: dict | None = None

    def _ensure_pem_format(self, key_str: str) -> str:
        """如果密钥是原始 base64 格式，自动包装为 PEM 格式"""
        if "BEGIN" in key_str:
            return key_str
        lines = [key_str[i:i+64] for i in range(0, len(key_str), 64)]
        return "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END RSA PRIVATE KEY-----"

    def _generate_sign(self, timestamp: str) -> str:
        """生成 ECO RSA 签名"""
        sign_str = f"PartnerId={self.partner_id}&Timestamp={timestamp}"
        pem_key = self._ensure_pem_format(self.private_key_str)
        key = RSA.import_key(pem_key)
        h = SHA256.new(sign_str.encode("utf-8"))
        signature = pkcs1_15.new(key).sign(h)
        return base64.b64encode(signature).decode("utf-8")

    def _make_signed_request(self, endpoint: str, payload: dict) -> dict:
        """
        通用签名请求方法（带防御性 JSON 解析）。

        返回解析后的 dict，若请求失败或 JSON 非法则返回空 dict。
        """
        timestamp = str(int(time.time()))
        sign = self._generate_sign(timestamp)

        payload.update({
            "PartnerId": self.partner_id,
            "Timestamp": timestamp,
            "Sign": sign,
        })

        url = f"{self.base_url}{endpoint}"
        headers = {"Content-Type": "application/json-patch+json"}

        try:
            self._wait_rate_limit()
            response = requests.post(
                url, data=json.dumps(payload), headers=headers, timeout=10
            )

            # ── 防御性响应校验 ──
            if response.status_code != 200:
                if response.status_code == 404:
                    logger.debug(f"[ECO] 接口不存在 (404): {endpoint}，已静默跳过")
                else:
                    logger.warning(
                        f"[ECO] 接口响应非 200: endpoint={endpoint}, "
                        f"Status={response.status_code}, Text={response.text[:200]}"
                    )
                return {}

            try:
                return response.json()
            except Exception:
                logger.error(
                    f"[ECO] 返回内容不是合法 JSON: endpoint={endpoint}, "
                    f"Text={response.text[:200]}"
                )
                return {}

        except Exception as e:
            logger.error(f"ECO API 请求异常 ({endpoint}): {e}")
            return {}

    # ──────────────────────────────────────────────
    # 接口 1: 获取全量在售价格与起租价（60 秒缓存）
    # ──────────────────────────────────────────────

    def _ensure_market_cache(self, force_refresh: bool = False) -> None:
        """Refresh the full ECO snapshot only when stale or explicitly forced."""
        cached_status = self.market_cache.status()
        if not force_refresh and self.market_cache.is_fresh(CACHE_TTL_SECONDS):
            self.last_price_source = "cache"
            self.last_cache_status = cached_status
            return

        with _snapshot_refresh_lock:
            # Another worker may have refreshed the SQLite cache while this one
            # was waiting for the process-wide refresh lock.
            if not force_refresh and self.market_cache.is_fresh(CACHE_TTL_SECONDS):
                self.last_price_source = "cache"
                self.last_cache_status = self.market_cache.status()
                return

            has_stale_snapshot = bool(cached_status and cached_status.get("item_count", 0))
            if not self.partner_id or not self.private_key_str:
                self.last_price_source = "stale" if has_stale_snapshot else "none"
                self.last_cache_status = cached_status
                logger.warning("[ECO] PartnerId 或私钥未配置，跳过全量更新")
                return

            endpoint = "/Api/Market/GetHashNameAndPriceList"
            resp_data = self._make_signed_request(endpoint, {"GameID": "730", "NeedStyleInfo": True})
            if not resp_data:
                self.last_price_source = "stale" if has_stale_snapshot else "none"
                self.last_cache_status = cached_status
                logger.warning("[ECO] 全量行情更新失败，保留旧缓存")
                return

            code = resp_data.get("ResultCode", resp_data.get("code", resp_data.get("Code")))
            if isinstance(code, str):
                code = int(code) if code.isdigit() else None
            if code not in (0, 200):
                msg = resp_data.get("ResultMsg", resp_data.get("msg", resp_data.get("Msg", "未知错误")))
                self.last_price_source = "stale" if has_stale_snapshot else "none"
                self.last_cache_status = cached_status
                logger.warning(f"[ECO] 全量行情业务错误: code={code}, msg={msg}")
                return

            result_list = resp_data.get("ResultData") or []
            if not isinstance(result_list, list):
                self.last_price_source = "stale" if has_stale_snapshot else "none"
                self.last_cache_status = cached_status
                logger.warning("[ECO] 全量行情 ResultData 格式异常，保留旧缓存")
                return

            self.market_cache.replace_snapshot(result_list, return_snapshot=False)
            self.last_price_source = "network"
            self.last_cache_status = self.market_cache.status()
            logger.info(
                f"[ECO] 全量行情已更新并写入本地缓存，共 "
                f"{(self.last_cache_status or {}).get('item_count', 0)} 条"
            )

    def get_hash_name_and_price_list(self, force_refresh: bool = False) -> dict:
        """Compatibility path that returns the complete cached snapshot."""
        self._ensure_market_cache(force_refresh=force_refresh)
        return self.market_cache.load_snapshot()

    def get_prices_for_hash_names(
        self, hash_names: list[str], force_refresh: bool = False
    ) -> dict:
        """Refresh if needed, then read only watched names from local SQLite."""
        self._ensure_market_cache(force_refresh=force_refresh)
        snapshot = self.market_cache.load_snapshot_for_hash_names(hash_names)
        logger.debug(
            "[ECO] SQLite 按需读取：观察 %s 个 HashName，命中 %s 条相位记录",
            len(set(hash_names)),
            len(snapshot),
        )
        return snapshot

    def get_price(self, market_hash_name: str, phase: str = "", force_refresh: bool = False) -> dict:
        """Return the exact cached ECO quote for one market name and phase."""
        snapshot = self.get_prices_for_hash_names(
            [market_hash_name], force_refresh=force_refresh
        )
        return self.market_cache.find_price(snapshot, market_hash_name, phase)

    # ──────────────────────────────────────────────
    # 接口 2: 批量获取饰品详情（图片 + PaintIndexLabel）
    # ──────────────────────────────────────────────

    def batch_get_goods_detail(
        self, market_hash_name_list: List[str]
    ) -> Dict[str, Any]:
        """
        批量获取饰品详情，包括 GoodsImg（图片 URL）和 PaintIndexLabel（Phase 标签）。

        接口: POST /Api/Market/BatchGetGoodsDetail

        Args:
            market_hash_name_list: 饰品的 market_hash_name 列表

        Returns:
            {
                "success": True/False,
                "data": {
                    "market_hash_name_1": {
                        "goodsImg": "https://...",
                        "paintIndexLabel": "Phase1",
                        ...
                    },
                    ...
                }
            }
        """
        result = {
            "success": False,
            "data": {},
        }

        # ── 防御性空值拦截：绝不向 ECO 发送空 payload ──
        if not market_hash_name_list:
            logger.warning("[ECO] BatchGetGoodsDetail 收到空列表，直接返回")
            return result

        if not self.partner_id or not self.private_key_str:
            logger.warning("[ECO] BatchGetGoodsDetail 参数不足，跳过")
            return result

        endpoint = "/Api/Market/BatchGetGoodsDetail"
        payload = {
            "GameId": "730",
            "HashName": market_hash_name_list,
        }

        resp_data = self._make_signed_request(endpoint, payload)
        if not resp_data:
            logger.warning("[ECO] BatchGetGoodsDetail 请求无响应或解析失败")
            return result

        # 尝试多种常见返回结构（兼容 ResultCode/ResultMsg 与 code/msg）
        code = resp_data.get("ResultCode", resp_data.get("code", resp_data.get("Code", 0)))
        if isinstance(code, str):
            code = int(code) if code.isdigit() else 0
        # ECO 接口 ResultCode: 0 或 200 均表示成功
        if code not in (0, 200):
            msg = resp_data.get("ResultMsg", resp_data.get("msg", resp_data.get("Msg", "未知错误")))
            logger.warning(f"[ECO] BatchGetGoodsDetail 业务错误: code={code}, msg={msg}, resp={resp_data}")
            return result

        # 官方返回结构: ResultData 是数组 [{HashName, GoodsImg, PaintIndexLabel, ...}, ...]
        result_data = resp_data.get("ResultData", resp_data.get("resultData", []))
        if not result_data:
            logger.warning("[ECO] BatchGetGoodsDetail 返回 ResultData 为空")
            return result

        # 将数组转换为以 HashName 为 key 的字典，方便调用方使用
        mapping = {}
        for item in result_data:
            h_name = item.get("HashName", item.get("hashName", ""))
            if h_name:
                mapping[h_name] = item

        result["success"] = True
        result["data"] = mapping
        logger.info(
            f"[ECO] BatchGetGoodsDetail 成功, 请求 {len(market_hash_name_list)} 个, "
            f"返回 {len(mapping)} 个"
        )
        return result
