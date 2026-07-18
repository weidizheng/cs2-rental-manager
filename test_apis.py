"""
CSQAQ 独立接口测试脚本 (严格遵守 1.05s 频控限制)
"""

import json
import os
import sys
import time
import requests

# 解决 Windows 控制台编码问题
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "data", "configs.json")


def load_token() -> str:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("csqaq_token", "").strip()
        except Exception as e:
            print(f"[!] 读取 configs.json 失败: {e}")
    return ""


def test_csqaq():
    print("=" * 60)
    print(" 🚀 CSQAQ 批量售价接口单独连通性测试")
    print("=" * 60)

    token = load_token()
    if not token:
        print("\n[错误] 未在 data/configs.json 中找到 csqaq_token！")
        return

    url = "https://api.csqaq.com/api/v1/goods/getPriceByMarketHashName"
    headers = {
        "ApiToken": token,
        "Content-Type": "application/json; charset=utf-8",
    }

    # 测试饰品列表
    test_items = [
        "★ Flip Knife | Doppler (Factory New)",
        "★ Nomad Knife | Fade (Factory New)",
    ]

    payload = {"marketHashNameList": test_items}

    print(f"\n[+] 当前 ApiToken: {token[:6]}...{token[-4:]}")
    print(f"[+] 请求 Endpoint: {url}")
    print(f"[+] 查询饰品: {test_items}")

    start_time = time.time()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"\n[<] HTTP 状态码: {resp.status_code}")

        if resp.status_code == 200:
            try:
                data = resp.json()
                print(
                    f"\n[成功] 返回 JSON 数据:\n{json.dumps(data, ensure_ascii=False, indent=2)}"
                )
            except Exception as e:
                print(f"\n[失败] 响应解析 JSON 崩溃: {e}")
                print(f"原始 Response: {resp.text[:300]}")
        elif resp.status_code == 429:
            print(
                "\n[警告] 触发 429 频控封禁！请等待至少 30 秒后再重新测试。"
            )
        else:
            print(f"\n[失败] 接口返回错误内容: {resp.text[:300]}")

    except Exception as e:
        print(f"\n[!] 请求发生异常: {e}")

    # ⏱️ 强制加入 1.05 秒安全冷却时间
    time.sleep(1.05)
    print(f"\n[✓] 测试完成，请求耗时: {time.time() - start_time:.2f}s")
    print("=" * 60)


if __name__ == "__main__":
    test_csqaq()