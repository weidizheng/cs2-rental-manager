"""IGXE 当前公开接口连通性诊断。

不读取本地密钥、Cookie 或账号信息；用于确认网站目前允许匿名访问哪些接口。
运行：python test_igxe_api.py
"""

from __future__ import annotations

import json
from typing import Any

import requests


BASE_URL = "https://www.igxe.cn"
APP_ID = 730  # CS2 / CS:GO
KEYWORD = "折叠刀"


def show_response(name: str, response: requests.Response) -> dict[str, Any] | None:
    print(f"\n[{name}] HTTP {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type', '')}")
    try:
        payload = response.json()
    except ValueError:
        print("返回非 JSON（前 300 字符）：")
        print(response.text[:300])
        return None

    print("响应：")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
    return payload


def main() -> int:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/134.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE_URL}/free-lease",
        }
    )

    # 目前可匿名访问：用于获取筛选项/分类等元数据。
    conditions = session.get(
        f"{BASE_URL}/api/v2/product/search-condition/{APP_ID}", timeout=20
    )
    conditions_payload = show_response("公开筛选条件 search-condition", conditions)

    # 旧代码缺少 /730，会直接 404；加上 app_id 后目前仍会被服务端拒绝。
    product_search = session.get(
        f"{BASE_URL}/api/v2/product/search/{APP_ID}",
        params={"app_id": APP_ID, "keyword": KEYWORD, "page_no": 1, "page_size": 20},
        timeout=20,
    )
    show_response("商品搜索 product/search", product_search)

    # 租赁列表当前要求登录。此处特意不加载用户 Cookie，避免凭据进入代码或日志。
    lease_list = session.get(
        f"{BASE_URL}/api/v2/lease/trade-list/{APP_ID}/0",
        params={
            "app_id": APP_ID,
            "keyword": KEYWORD,
            "is_lease": 1,
            "is_free": 1,
            "page_no": 1,
            "page_size": 20,
        },
        timeout=20,
    )
    show_response("租赁列表 lease/trade-list", lease_list)

    if not isinstance(conditions_payload, dict) or not conditions_payload.get("status"):
        print("\n结论：公开筛选条件接口不可用，请检查网络或网站接口变动。")
        return 1

    print("\n结论：公开筛选条件接口可用；商品搜索/租赁价格仍需官方授权或登录态。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
