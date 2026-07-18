"""
API 调试测试脚本
运行后会打印详细的请求/响应信息，帮助诊断 API 问题

用法:
    python test_api_debug.py
"""
import json
import os
import sys

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.eco_client import ECOClient
from modules.csqaq_client import CSQAQClient


def load_configs():
    """从 configs.json 加载配置"""
    config_path = os.path.join("data", "configs.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_eco_hash_price_list(configs):
    """测试 ECO GetHashNameAndPriceList"""
    print("=" * 60)
    print("[TEST 1] ECO GetHashNameAndPriceList")
    print("=" * 60)

    partner_id = configs.get("eco_partner_id", "")
    rsa_key = configs.get("eco_rsa_key", "")

    if not partner_id or not rsa_key:
        print("[SKIP] Missing ECO PartnerId or RSA key")
        return None

    print(f"PartnerId: {partner_id[:20]}...")
    print(f"RSA Key length: {len(rsa_key)}")

    client = ECOClient(partner_id=partner_id, private_key_str=rsa_key)

    import time
    timestamp = str(int(time.time()))
    sign = client._generate_sign(timestamp)

    payload = {
        "GameID": "730",
        "NeedStyleInfo": True,
        "PartnerId": partner_id,
        "Timestamp": timestamp,
        "Sign": sign,
    }

    print(f"\nRequest URL: https://openapi.ecosteam.cn/Api/Market/GetHashNameAndPriceList")
    print(f"Request Body: {json.dumps(payload, ensure_ascii=False, indent=2)}")

    import requests
    try:
        resp = requests.post(
            "https://openapi.ecosteam.cn/Api/Market/GetHashNameAndPriceList",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json-patch+json"},
            timeout=15
        )
        print(f"\nHTTP Status: {resp.status_code}")

        data = resp.json()
        code = data.get("ResultCode", data.get("code", data.get("Code", "N/A")))
        print(f"\nParsed Result:")
        print(f"  ResultCode: {code}")
        print(f"  ResultMsg: {data.get('ResultMsg', data.get('msg', 'N/A'))}")

        result_data = data.get("ResultData", [])
        print(f"  ResultData count: {len(result_data)}")

        if result_data:
            print(f"\n  First 3 samples:")
            for i, item in enumerate(result_data[:3]):
                safe_h = (item.get('HashName') or '').encode('ascii', 'replace').decode('ascii')
                print(f"    [{i}] HashName={safe_h}, IdNum={item.get('IdNum')}, Price={item.get('Price')}")

            # Search for specific items
            search_names = [
                "Flip Knife | Doppler (Factory New)",
                "Specialist Gloves | Crimson Web (Field-Tested)",
            ]
            print(f"\n  Search specific items:")
            found_ids = {}
            for name in search_names:
                # Try exact match first
                found = [item for item in result_data if item.get("HashName") == name]
                if not found:
                    # Try with star prefix
                    found = [item for item in result_data if item.get("HashName") == f"★ {name}"]
                if found:
                    item = found[0]
                    safe_h = (item.get('HashName') or '').encode('ascii', 'replace').decode('ascii')
                    print(f"    [OK] Found: {safe_h}, IdNum={item.get('IdNum')}")
                    found_ids[name] = str(item.get('IdNum'))
                else:
                    # Fuzzy search
                    fuzzy = [item for item in result_data if name.split("|")[0].strip() in (item.get("HashName") or "")]
                    if fuzzy:
                        print(f"    [WARN] No exact match for '{name}', found similar:")
                        for f in fuzzy[:3]:
                            safe_fn = (f.get('HashName') or '').encode('ascii', 'replace').decode('ascii')
                            print(f"       - {safe_fn} (IdNum={f.get('IdNum')})")
                    else:
                        print(f"    [MISS] Not found: {name}")

            return data, found_ids
        return data, {}
    except Exception as e:
        print(f"\n[ERR] Request failed: {e}")
        return None, {}


def test_eco_batch_goods_detail(configs, goods_nums):
    """测试 ECO BatchGetGoodsDetail with GoodsNum (IdNum as strings)"""
    print("\n" + "=" * 60)
    print("[TEST 2] ECO BatchGetGoodsDetail (using GoodsNum/IdNum as STRINGS)")
    print("=" * 60)

    partner_id = configs.get("eco_partner_id", "")
    rsa_key = configs.get("eco_rsa_key", "")

    if not partner_id or not rsa_key:
        print("[SKIP] Missing ECO PartnerId or RSA key")
        return None

    if not goods_nums:
        print("[SKIP] No GoodsNum/IdNum to test")
        return None

    client = ECOClient(partner_id=partner_id, private_key_str=rsa_key)

    import time
    timestamp = str(int(time.time()))
    sign = client._generate_sign(timestamp)

    # Convert to strings
    goods_nums_str = [str(n) for n in goods_nums]

    payload = {
        "GameId": "730",
        "GoodsNum": goods_nums_str,
        "PartnerId": partner_id,
        "Timestamp": timestamp,
        "Sign": sign,
    }

    print(f"\nRequest URL: https://openapi.ecosteam.cn/Api/Market/BatchGetGoodsDetail")
    print(f"Request Body: {json.dumps(payload, ensure_ascii=False, indent=2)}")

    import requests
    try:
        resp = requests.post(
            "https://openapi.ecosteam.cn/Api/Market/BatchGetGoodsDetail",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json-patch+json"},
            timeout=15
        )
        print(f"\nHTTP Status: {resp.status_code}")

        data = resp.json()
        code = data.get("ResultCode", data.get("code", data.get("Code", "N/A")))
        print(f"\nParsed Result:")
        print(f"  ResultCode: {code}")
        print(f"  ResultMsg: {data.get('ResultMsg', data.get('msg', 'N/A'))}")

        result_data = data.get("ResultData") or data.get("resultData") or []
        if result_data is None:
            result_data = []
        print(f"  ResultData count: {len(result_data)}")

        if result_data:
            print(f"\n  Returned items:")
            for item in result_data:
                h_name = item.get("HashName", "")
                safe_h = h_name.encode('ascii', 'replace').decode('ascii')
                print(f"    [OK] {safe_h}")
                print(f"       GoodsImg: {item.get('GoodsImg', 'N/A')}")
                print(f"       PaintIndexLabel: {item.get('PaintIndexLabel', 'N/A')}")
        else:
            print("  [WARN] ResultData is empty")

        return data
    except Exception as e:
        print(f"\n[ERR] Request failed: {e}")
        return None


def test_csqaq_price(configs, test_hash_names):
    """测试 CSQAQ 批量价格查询"""
    print("\n" + "=" * 60)
    print("[TEST 3] CSQAQ getPriceByMarketHashName")
    print("=" * 60)

    token = configs.get("csqaq_token", "")
    if not token:
        print("[SKIP] Missing CSQAQ Token")
        return None

    print(f"Token: {token[:10]}...")

    client = CSQAQClient(token)

    # Test both with and without star prefix
    test_names_with_star = [f"★ {name}" if not name.startswith("★") else name for name in test_hash_names]

    print(f"\nTesting market_hash_name list (with star prefix):")
    for name in test_names_with_star:
        safe_name = name.encode('ascii', 'replace').decode('ascii')
        print(f"  - {safe_name}")

    result = client.get_prices_by_hash_names(test_names_with_star)

    print(f"\nReturned result count: {len(result)}")
    if result:
        print(f"\nReturned data:")
        for name, info in result.items():
            safe_name = name.encode('ascii', 'replace').decode('ascii')
            print(f"  [OK] {safe_name}:")
            print(f"     buff_price: {info.get('buff_price')}")
            print(f"     yy_price: {info.get('yy_price')}")
            print(f"     min_sell_price: {info.get('min_sell_price')}")
    else:
        print("  [WARN] No data returned")

    return result


def main():
    print("CS2 Item Manager API Debug Test")
    print("=" * 60)

    configs = load_configs()
    print(f"Config loaded")
    print(f"  CSQAQ Token: {'[OK] Configured' if configs.get('csqaq_token') else '[NO] Missing'}")
    print(f"  ECO PartnerId: {'[OK] Configured' if configs.get('eco_partner_id') else '[NO] Missing'}")
    print(f"  ECO RSA Key: {'[OK] Configured' if configs.get('eco_rsa_key') else '[NO] Missing'}")

    # Test names (without star prefix, will add if needed)
    test_names = [
        "Flip Knife | Doppler (Factory New)",
        "Nomad Knife | Doppler (Factory New)",
        "Specialist Gloves | Crimson Web (Field-Tested)",
        "Sport Gloves | Vice (Field-Tested)",
        "Driver Gloves | Boom! (Field-Tested)",
    ]

    # 1. Test ECO GetHashNameAndPriceList and get IdNum mapping
    eco_price_data, found_ids = test_eco_hash_price_list(configs)

    # 2. Test ECO BatchGetGoodsDetail with IdNum as strings
    if found_ids:
        goods_nums = list(found_ids.values())
        print(f"\nUsing IdNums (as strings) for BatchGetGoodsDetail: {goods_nums}")
        test_eco_batch_goods_detail(configs, goods_nums)
    else:
        print("\n[SKIP] No IdNums found, skipping BatchGetGoodsDetail test")

    # 3. Test CSQAQ
    test_csqaq_price(configs, test_names)

    print("\n" + "=" * 60)
    print("Test completed")
    print("=" * 60)


if __name__ == "__main__":
    main()
