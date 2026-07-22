import logging
import time

import requests

from modules.base_client import BaseAPIClient
from modules.retry import retry

logger = logging.getLogger("CS2Rental")


class CSQAQClient(BaseAPIClient):
    """
    CSQAQ 开放数据 API 客户端

    统一使用 POST /api/v1/goods/getPriceByMarketHashName 作为主要查询入口，
    通过 marketHashName 批量获取多平台在售底价。

    官方 Schema: data.success.{marketHashName}
    提取字段: buffSellPrice, yyyySellPrice, steamSellPrice, name

    频率限制: 1 次/秒 → min_interval=1.05
    """

    BASE_URL = "https://api.csqaq.com"

    def __init__(self, api_token: str, timeout: int = 10):
        # Detailed item endpoints are more restrictive than the batch price
        # endpoint.  Keep the whole client deliberately low-frequency.
        super().__init__(min_interval=3.0)
        self.api_token = api_token.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        })

    @retry(max_retries=2, delay=1.0)
    def bind_local_ip(self) -> dict:
        """
        调用 CSQAQ API 自动将当前请求的公网 IP 绑定到 Token 白名单中。
        接口: POST /api/v1/sys/bind_local_ip (频控: 30秒/次)

        Returns:
            dict: API 响应，如 {"code": 200, "msg": "绑定成功", "data": "45.30.178.0"}
        """
        if not self.api_token:
            logger.warning("[CSQAQ] 未配置 ApiToken，取消 IP 绑定")
            return {"code": 400, "msg": "未配置 ApiToken"}

        url = f"{self.BASE_URL}/api/v1/sys/bind_local_ip"
        headers = {
            "ApiToken": self.api_token,
            "Content-Type": "application/json",
        }

        try:
            self._wait_rate_limit()
            resp = self.session.post(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                ip = data.get("data", "unknown")
                msg = data.get("msg", "")
                logger.info(f"[CSQAQ IP 绑定] {msg}: {ip}")
                return data
            else:
                logger.warning(
                    f"[CSQAQ IP 绑定失败] HTTP Status: {resp.status_code}, "
                    f"body: {resp.text[:200]}"
                )
        except Exception as e:
            logger.error(f"[CSQAQ IP 绑定异常]: {e}")

        return {"code": 500, "msg": "请求发生异常"}

    @retry(max_retries=2, delay=1.5)
    def get_prices_by_hash_names(self, hash_name_list: list[str]) -> dict:
        """
        根据 marketHashName 列表批量获取各大平台在售价格及中文名。

        接口: POST /api/v1/goods/getPriceByMarketHashName
        官方 Schema: data.success.{marketHashName}

        Args:
            hash_name_list: 饰品的 market_hash_name 列表（自动截取前 50 个）

        Returns:
            {
                "market_hash_name_1": {
                    "good_id": ...,
                    "name_zh": "中文名",
                    "buff_price": 100.0,
                    "yy_price": 95.0,
                    "steam_price": 105.0,
                    "min_sell_price": 95.0,   # buff 与 yy 中的最低价
                },
                ...
            }
        """
        if not self.api_token or not hash_name_list:
            return {}

        self._wait_rate_limit()  # 1.05s 强保证

        url = f"{self.BASE_URL}/api/v1/goods/getPriceByMarketHashName"
        headers = {
            "ApiToken": self.api_token,
            "Content-Type": "application/json",
        }
        payload = {"marketHashNameList": hash_name_list[:50]}

        try:
            resp = self.session.post(url, headers=headers, json=payload, timeout=8)
            if resp.status_code == 200:
                res_json = resp.json()
                if res_json.get("code") == 200 and "data" in res_json:
                    success_data = res_json["data"].get("success", {})
                    parsed_result: dict = {}

                    for market_name, info in success_data.items():
                        buff_p = info.get("buffSellPrice") or 0.0
                        yy_p = info.get("yyypSellPrice") or 0.0
                        steam_p = info.get("steamSellPrice") or 0.0

                        valid_prices = [p for p in [buff_p, yy_p] if p > 0]
                        min_price = min(valid_prices) if valid_prices else 0.0

                        parsed_result[market_name] = {
                            "good_id": info.get("goodId"),
                            "name_zh": info.get("name", market_name),
                            "buff_price": buff_p,
                            "yy_price": yy_p,
                            "steam_price": steam_p,
                            "min_sell_price": min_price,
                        }

                    logger.debug(
                        f"[CSQAQ] getPriceByMarketHashName 成功, "
                        f"请求 {len(hash_name_list)} 个, "
                        f"返回 {len(parsed_result)} 个"
                    )
                    return parsed_result

                logger.warning(
                    f"[CSQAQ] getPriceByMarketHashName 业务错误: "
                    f"code={res_json.get('code')}, msg={res_json.get('msg', '')}"
                )
            elif resp.status_code == 429:
                logger.warning("[CSQAQ] 触发 429 频控，已自动冷却 3 秒")
                time.sleep(3.0)
            else:
                logger.warning(
                    f"[CSQAQ] getPriceByMarketHashName HTTP {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
        except Exception as e:
            logger.error(f"[CSQAQ] 解析响应异常: {e}")

        return {}

    @retry(max_retries=2, delay=1.5)
    def get_good_detail(self, good_id: int | str) -> dict:
        """Return the detailed CSQAQ platform quote for one known ``good_id``.

        The batch endpoint intentionally contains only the quick sell prices.
        Rental short/long prices and C5/IGXE/悠悠 IDs are exposed by this
        documented per-item endpoint instead.
        """
        if not self.api_token or not good_id:
            return {}

        try:
            self._wait_rate_limit()
            response = self.session.get(
                f"{self.BASE_URL}/api/v1/info/good",
                params={"id": good_id},
                headers={"ApiToken": self.api_token},
                timeout=self.timeout,
            )
            if response.status_code == 429:
                logger.warning("[CSQAQ] get_good_detail throttled for id=%s; keep cached detail", good_id)
                return {}
            if response.status_code != 200:
                logger.warning("[CSQAQ] get_good_detail HTTP %s for id=%s", response.status_code, good_id)
                return {}
            payload = response.json()
            if payload.get("code") != 200:
                logger.warning(
                    "[CSQAQ] get_good_detail business error for id=%s: %s",
                    good_id,
                    payload.get("msg", ""),
                )
                return {}
            info = (payload.get("data") or {}).get("goods_info") or {}
            if not isinstance(info, dict):
                return {}
            return info
        except Exception as exc:
            logger.warning("[CSQAQ] get_good_detail failed for id=%s: %s", good_id, exc)
            return {}

    # ── 兼容旧调用方 ──────────────────────────────────────────
    # 保留旧方法名作为别名，方便逐步迁移
    get_price_by_market_hash_name = get_prices_by_hash_names
