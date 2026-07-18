import base64
import json
import logging
import time
from typing import Dict, Any, List, Optional

import requests
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15

from modules.base_client import BaseAPIClient

logger = logging.getLogger("CS2Rental")


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

        # ── GetHashNameAndPriceList 60 秒内存缓存 ──
        self._hash_price_cache: dict = {}
        self._last_hash_price_time: float = 0

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

    def get_hash_name_and_price_list(self) -> dict:
        """
        获取 ECO 全量在售价格与起租价。

        接口: POST /Api/Market/GetHashNameAndPriceList
        频控: 60 秒内重复调用直接返回内存缓存，严格禁止 60 秒内重复发包。

        Body:
            GameID: "730"
            NeedStyleInfo: True
            PartnerId: ...
            Timestamp: ...

        核心提取字段:
            - Price: ECO 在售最低价
            - RentGoodsBottomPrice: ECO 起租价（直接填入 UI 的 ECO 最低日租列）
            - StyleName: 多普勒款式（Phase1 / Phase2 / Ruby / Sapphire 等）

        Returns:
            {
                "market_hash_name_1": {
                    "eco_sell_price": 100.0,
                    "eco_rent_price": 5.0,
                    "style_name": "Phase1",
                },
                ...
            }
        """
        now = time.time()

        # 60 秒缓存命中
        if self._hash_price_cache and (now - self._last_hash_price_time < 60):
            logger.debug(
                f"[ECO] GetHashNameAndPriceList 缓存命中 "
                f"({int(now - self._last_hash_price_time)}s < 60s)，直接返回"
            )
            return self._hash_price_cache

        if not self.partner_id or not self.private_key_str:
            logger.warning("[ECO] PartnerId 或私钥未配置，跳过 GetHashNameAndPriceList")
            return self._hash_price_cache

        endpoint = "/Api/Market/GetHashNameAndPriceList"
        payload = {
            "GameID": "730",
            "NeedStyleInfo": True,
        }

        resp_data = self._make_signed_request(endpoint, payload)
        if not resp_data:
            logger.warning("[ECO] GetHashNameAndPriceList 请求无响应或解析失败")
            return self._hash_price_cache

        # 解析返回结构（兼容 ResultCode/ResultMsg 与 code/msg）
        code = resp_data.get("ResultCode", resp_data.get("code", resp_data.get("Code", 0)))
        # ResultCode 可能是字符串
        if isinstance(code, str):
            code = int(code) if code.isdigit() else 0
        # ECO 接口 ResultCode: 0 或 200 均表示成功
        if code not in (0, 200):
            msg = resp_data.get("ResultMsg", resp_data.get("msg", resp_data.get("Msg", "未知错误")))
            logger.warning(f"[ECO] GetHashNameAndPriceList 业务错误: code={code}, msg={msg}, resp={resp_data}")
            return self._hash_price_cache

        result_list = resp_data.get("ResultData") or []
        mapping: dict = {}
        for item in result_list:
            h_name = item.get("HashName")
            if h_name:
                mapping[h_name] = {
                    "eco_sell_price": item.get("Price", 0.0),
                    "eco_rent_price": item.get("RentGoodsBottomPrice", 0.0),
                    "style_name": item.get("StyleName", ""),
                }

        self._hash_price_cache = mapping
        self._last_hash_price_time = now

        logger.info(
            f"[ECO] GetHashNameAndPriceList 成功, "
            f"获取 {len(mapping)} 个饰品价格/起租信息"
        )
        return mapping

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