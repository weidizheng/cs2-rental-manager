import logging
import requests

from modules.base_client import BaseAPIClient

logger = logging.getLogger("CS2Rental")


class IGXEClient(BaseAPIClient):
    """
    IGXE 开放平台 API 客户端。

    频率限制: 2 次/秒 → min_interval=0.50
    """

    BASE_URL = "https://www.igxe.cn"

    def __init__(self, timeout=8):
        super().__init__(min_interval=0.50)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.igxe.cn/",
            }
        )

    def search_product(self, keyword: str):
        """搜索 IGXE 商品，获取 product_id"""
        url = f"{self.BASE_URL}/api/v2/product/search"
        try:
            self._wait_rate_limit()
            response = self.session.get(
                url, params={"keyword": keyword, "page_no": 1, "page_size": 10}, timeout=self.timeout
            )
            if response.status_code == 200:
                data = response.json().get("data", {})
                items = data.get("list", [])
                results = []
                for item in items:
                    results.append({
                        "product_id": item.get("product_id", item.get("id", 0)),
                        "name": item.get("name", ""),
                        "wear": item.get("wear", ""),
                    })
                return {"success": True, "results": results, "total": data.get("total", len(results))}
        except Exception as e:
            logger.error(f"IGXE 搜索失败: {e}")
        return {"success": False, "results": [], "total": 0}

    def get_lease_market_info(self, product_id):
        """
        查询 IGXE 租赁行情，返回多条租赁列表（含磨损度/租金/押金）
        """
        url = f"{self.BASE_URL}/api/v2/product/lease/list/{product_id}"
        try:
            self._wait_rate_limit()
            response = self.session.get(
                url, params={"page_no": 1, "page_size": 20}, timeout=self.timeout
            )
            if response.status_code == 200:
                data = response.json().get("data", {})
                items = data.get("list", [])
                listings = []
                for item in items:
                    listings.append({
                        "rent": float(item.get("rent", 0.0)),
                        "deposit": float(item.get("deposit", 0.0)),
                        "float_val": str(item.get("float_val", item.get("floatValue", ""))),
                        "wear": str(item.get("wear", item.get("wear_name", ""))),
                        "pattern": str(item.get("pattern", "")),
                    })
                return {
                    "success": True,
                    "listings": listings,
                    "min_rent": float(items[0].get("rent", 0.0)) if items else 0.0,
                    "min_deposit": float(items[0].get("deposit", 0.0)) if items else 0.0,
                    "total_count": data.get("total", len(listings)),
                }
        except Exception as e:
            logger.error(f"IGXE 请求失败: {e}")
        return {"success": False, "listings": [], "min_rent": 0.0, "min_deposit": 0.0, "total_count": 0}