# 🚀 CS2 Rental Manager — 优化实施计划

> 历史说明：本文记录早期优化方案，相关异步 Worker、数据库锁、日志、重试和订单解析已经实施；后续的首页防抖快照渲染、行情模型视图，以及 SQLite 行情完整持久化也已落地。当前 v3.1 的架构、数据模型、迁移、安全边界、验证命令与后续优先级以 [MAINTENANCE.md](MAINTENANCE.md) 为准；不要直接复制本文的旧示例代码覆盖现有实现。

## 目录
1. [异步线程改造 (QThread)](#1-异步线程改造-qthread)
2. [ECO 真实订单数据联动](#2-eco-真实订单数据联动)
3. [代码审查与鲁棒性修复](#3-代码审查与鲁棒性修复)
4. [实施步骤与文件清单](#4-实施步骤与文件清单)

---

## 1. 异步线程改造 (QThread)

### 1.1 问题分析
当前所有 API 调用（CSQAQ 搜索/详情、ECO 订单拉取、IGXE 行情）均在 **主线程（GUI 线程）** 中同步执行。当网络延迟或 API 无响应时，界面将完全卡死，用户体验极差。

### 1.2 解决方案
创建通用 `ApiWorker`（基于 `QObject` + `QThread` 模式），将网络请求移至后台线程，通过信号（`Signal`）安全返回结果到主线程。

### 1.3 架构设计

```
┌─────────────────────────────────────────────────┐
│  CS2ManagerApp (主线程/GUI)                      │
│  ┌───────────────────────────────────────────┐   │
│  │  QThreadPool / 专用 QThread               │   │
│  │  ┌─────────────────────────────────────┐  │   │
│  │  │  ApiWorker (QObject)                │  │   │
│  │  │  - run_search(keyword)              │  │   │
│  │  │  - run_detail(item_id)              │  │   │
│  │  │  - run_eco_orders(partner_id, key)  │  │   │
│  │  │  - run_igxe_lease(product_id)       │  │   │
│  │  │  signals: finished(result)          │  │   │
│  │  │           error(msg)                │  │   │
│  │  └─────────────────────────────────────┘  │   │
│  └───────────────────────────────────────────┘   │
│                                                   │
│  回调处理: 更新 UI 组件 / 刷新表格 / 弹窗提示      │
└─────────────────────────────────────────────────┘
```

### 1.4 新增文件: `modules/workers.py`

```python
# modules/workers.py
from PySide6.QtCore import QObject, Signal
from modules.csqaq_client import CSQAQClient
from modules.eco_client import ECOClient
from modules.igxe_client import IGXEClient

class ApiWorker(QObject):
    finished = Signal(object)   # 成功时返回结果数据
    error = Signal(str)         # 失败时返回错误消息

    def __init__(self):
        super().__init__()
        self._is_canceled = False

    def cancel(self):
        self._is_canceled = True

    # --- CSQAQ ---
    def search_csqaq(self, token: str, keyword: str):
        try:
            client = CSQAQClient(token)
            result = client.search_item(keyword)
            if not self._is_canceled:
                self.finished.emit(("search", result))
        except Exception as e:
            self.error.emit(f"CSQAQ 搜索失败: {e}")

    def detail_csqaq(self, token: str, item_id: int):
        try:
            client = CSQAQClient(token)
            result = client.get_item_detail(item_id)
            if not self._is_canceled:
                self.finished.emit(("detail", result))
        except Exception as e:
            self.error.emit(f"CSQAQ 详情失败: {e}")

    # --- ECO ---
    def fetch_eco_orders(self, partner_id: str, rsa_key: str):
        try:
            client = ECOClient(partner_id, rsa_key)
            result = client.get_self_rent_goods()
            if not self._is_canceled:
                self.finished.emit(("eco_orders", result))
        except Exception as e:
            self.error.emit(f"ECO 订单拉取失败: {e}")

    # --- IGXE ---
    def fetch_igxe_lease(self, product_id):
        try:
            client = IGXEClient()
            result = client.get_lease_market_info(product_id)
            if not self._is_canceled:
                self.finished.emit(("igxe_lease", result))
        except Exception as e:
            self.error.emit(f"IGXE 行情失败: {e}")
```

### 1.5 main.py 调用方式改造

```python
# 在 CS2ManagerApp 中:
from PySide6.QtCore import QThread
from modules.workers import ApiWorker

def query_csqaq_market(self):
    token = self.db.get_config("csqaq_token")
    keyword = self.market_input.text().strip()
    # ... 校验逻辑 ...

    self.thread = QThread()
    self.worker = ApiWorker()
    self.worker.moveToThread(self.thread)

    self.thread.started.connect(lambda: self.worker.search_csqaq(token, keyword))
    self.worker.finished.connect(self.on_csqaq_search_result)
    self.worker.error.connect(self.on_api_error)
    self.worker.finished.connect(self.thread.quit)
    self.worker.finished.connect(self.worker.deleteLater)
    self.thread.finished.connect(self.thread.deleteLater)

    # 禁用按钮 + 显示加载状态
    self.market_search_btn.setEnabled(False)
    self.market_search_btn.setText("⏳ 查询中...")
    self.thread.start()
```

---

## 2. ECO 真实订单数据联动

### 2.1 问题分析
- `eco_client.py` 已实现 `get_self_rent_goods()` 方法，但 **从未在 main.py 中被调用**
- 本地 `items.json` 中的 ECO 平台饰品状态（如 `expire_hours`、`income`、`status`）需要与 ECO 官方订单数据自动对齐
- 匹配依据：ECO 返回的 `float_val`（磨损度）或 `pattern`（图案模板 Seed）

### 2.2 匹配与同步逻辑

```
ECO API 返回订单列表
        │
        ▼
遍历每个订单，提取:
  - float_value (磨损度)
  - pattern (图案模板)
  - rent (日租金)
  - remain_days / expire_time
  - order_status (出租中/已结束)
        │
        ▼
与本地 items.json 中 platform=="ECOSteam" 的条目进行匹配:
  - 优先按 float_val 精确匹配
  - 若 float_val 为空或未匹配，按 pattern 匹配
  - 若均未匹配，按 name 模糊匹配
        │
        ▼
匹配成功 → 更新本地条目的:
  - status → "已出租" / "在库"
  - expire_hours → 根据到期时间计算
  - rent → 同步 ECO 日租金
  - income → 同步累计已收收益
        │
        ▼
写回 SQLite + 自动同步 items.json
```

### 2.3 新增方法: `eco_client.py` 增加解析与匹配辅助

```python
def parse_orders_for_sync(self, api_response: dict) -> list[dict]:
    """
    将 ECO API 返回的订单数据解析为标准化列表，便于与本地 items 匹配。
    返回格式:
    [
        {
            "float_val": "0.02291420",
            "pattern": "927",
            "name": "★ 折叠刀 | 多普勒 (崭新出厂)",
            "rent": 2.20,
            "income": 19.80,
            "expire_hours": 168.0,
            "status": "已出租",
            "order_id": "ECO123456"
        },
        ...
    ]
    """
    orders = []
    raw_list = api_response.get("Data", []) if isinstance(api_response, dict) else []
    for order in raw_list:
        orders.append({
            "float_val": str(order.get("FloatValue", "")),
            "pattern": str(order.get("Pattern", "")),
            "name": order.get("GoodsName", ""),
            "rent": float(order.get("DayRent", 0.0)),
            "income": float(order.get("TotalIncome", 0.0)),
            "expire_hours": float(order.get("RemainHours", 999.0)),
            "status": "已出租" if order.get("OrderStatus") == 1 else "在库",
            "order_id": str(order.get("OrderId", "")),
        })
    return orders
```

### 2.4 main.py 新增同步入口

```python
def sync_eco_orders(self):
    """后台拉取 ECO 订单并与本地数据对齐"""
    partner_id = self.db.get_config("eco_partner_id")
    rsa_key = self.db.get_config("eco_rsa_key")
    if not partner_id or not rsa_key:
        return

    # 使用 ApiWorker 后台拉取
    self.eco_thread = QThread()
    self.eco_worker = ApiWorker()
    self.eco_worker.moveToThread(self.eco_thread)
    self.eco_thread.started.connect(
        lambda: self.eco_worker.fetch_eco_orders(partner_id, rsa_key)
    )
    self.eco_worker.finished.connect(self._on_eco_orders_fetched)
    self.eco_worker.error.connect(lambda msg: print(f"ECO 同步错误: {msg}"))
    self.eco_worker.finished.connect(self.eco_thread.quit)
    self.eco_worker.finished.connect(self.eco_worker.deleteLater)
    self.eco_thread.finished.connect(self.eco_thread.deleteLater)
    self.eco_thread.start()

def _on_eco_orders_fetched(self, result):
    tag, data = result
    if tag != "eco_orders" or not data:
        return

    parser = ECOClient("", "")  # 仅用于调用解析方法
    remote_orders = parser.parse_orders_for_sync(data)

    local_items = self.db.get_all_items()
    updated_count = 0

    for remote in remote_orders:
        match = self._find_matching_item(remote, local_items)
        if match:
            self.db.update_item(match["id"], {
                "status": remote["status"],
                "expire_hours": remote["expire_hours"],
                "rent": remote["rent"],
                "income": remote["income"],
            })
            updated_count += 1

    if updated_count > 0:
        self.load_data()
        print(f"[ECO 同步] 已更新 {updated_count} 件饰品状态")

def _find_matching_item(self, remote, local_items):
    """按 float_val > pattern > name 优先级匹配"""
    # 1. 精确匹配 float_val
    for item in local_items:
        if item["platform"] == "ECOSteam" and item["float_val"] == remote["float_val"]:
            return item
    # 2. 匹配 pattern
    for item in local_items:
        if item["platform"] == "ECOSteam" and item["pattern"] == remote["pattern"]:
            return item
    # 3. 模糊匹配 name
    for item in local_items:
        if item["platform"] == "ECOSteam" and remote["name"] and remote["name"] in item["name"]:
            return item
    return None
```

---

## 3. 代码审查与鲁棒性修复

### 3.1 发现的问题清单

| # | 问题 | 位置 | 严重性 | 修复方案 |
|---|------|------|--------|----------|
| 1 | `ItemEditDialog` 基类使用 `QDialog if 'QDialog' in globals() else QWidget` 这种不安全的内省写法 | `main.py:11` | 🔴 高 | 直接继承 `QDialog`，移除 globals() 判断 |
| 2 | `QDialog` 在多个方法中重复 `from PySide6.QtWidgets import QDialog` | `main.py:14,182,335,344` | 🟡 中 | 移到文件顶部一次性导入 |
| 3 | `print()` 与 `logging` 混用，无统一日志配置 | 多个文件 | 🟡 中 | 统一使用 `logging`，配置写入文件 |
| 4 | `CSQAQClient` 将 `token` 同时放在 Header 和 POST body 中 | `csqaq_client.py:23,35` | 🟡 中 | 统一为 Header 方式，body 中去掉 token |
| 5 | `ECOClient.__init__` 只接受私钥字符串，不支持从文件路径读取 | `eco_client.py:12` | 🟡 中 | 增加 `private_key_path` 参数，自动读取文件 |
| 6 | `IGXEClient` 已定义但从未在 `main.py` 中使用 | `igxe_client.py` | 🟢 低 | 保留供后续扩展，或集成到市场 Tab |
| 7 | 无网络请求超时/重试机制 | 所有 client | 🟡 中 | 增加指数退避重试装饰器 |
| 8 | `query_csqaq_market` 无加载状态指示 | `main.py:367` | 🟡 中 | 按钮禁用 + 文本反馈 |
| 9 | `save_settings` 无输入校验（空值、格式） | `main.py:403` | 🟡 中 | 增加基本校验与提示 |
| 10 | `load_data` 中 `filter_p` 匹配逻辑不严谨（`not in filter_p`） | `main.py:279` | 🟢 低 | 改为精确相等比较 |
| 11 | `export_items_to_json` 无写锁，多线程下可能竞态 | `db_manager.py:174` | 🟡 中 | 增加文件锁或线程锁 |
| 12 | `get_connection()` 每次创建新连接，无连接池 | `db_manager.py:25` | 🟢 低 | 使用单例连接或连接池 |

### 3.2 关键修复详情

#### 3.2.1 修复 ItemEditDialog 基类 (main.py:11)
```python
# 修改前
class ItemEditDialog(QDialog if 'QDialog' in globals() else QWidget):

# 修改后
from PySide6.QtWidgets import QDialog

class ItemEditDialog(QDialog):
```

#### 3.2.2 统一日志配置 (新增 `modules/logger.py`)
```python
import logging
import os

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

def setup_logger(name: str = "CS2Rental") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 文件 Handler
    fh = logging.FileHandler(
        os.path.join(LOG_DIR, "app.log"), encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))
    logger.addHandler(fh)

    # 控制台 Handler
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"
    ))
    logger.addHandler(ch)

    return logger

logger = setup_logger()
```

#### 3.2.3 ECO 私钥支持文件路径 (eco_client.py)
```python
def __init__(self, partner_id: str, private_key_str: str = "", private_key_path: str = ""):
    self.partner_id = partner_id
    if private_key_path and not private_key_str:
        with open(private_key_path, "r", encoding="utf-8") as f:
            private_key_str = f.read()
    self.private_key_str = private_key_str
    ...
```

#### 3.2.4 网络请求重试装饰器 (新增 `modules/retry.py`)
```python
import time
import functools

def retry(max_retries=3, delay=1.0, backoff=2.0):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt < max_retries - 1:
                        time.sleep(delay * (backoff ** attempt))
            raise last_exc
        return wrapper
    return decorator
```

#### 3.2.5 数据库写锁 (db_manager.py)
```python
import threading
_write_lock = threading.Lock()

def export_items_to_json(self):
    with _write_lock:
        # ... 原有逻辑 ...
```

---

## 4. 实施步骤与文件清单

### 4.1 实施顺序（按依赖关系）

```
Step 1: 创建基础工具模块
  ├── modules/logger.py      (统一日志)
  ├── modules/retry.py       (重试装饰器)
  └── modules/workers.py     (异步 Worker)

Step 2: 修复现有代码问题
  ├── main.py                (QDialog 导入修复、ItemEditDialog 基类修复、filter 逻辑修复)
  ├── modules/db_manager.py  (写锁、连接优化)
  ├── modules/eco_client.py  (私钥文件路径支持)
  └── modules/csqaq_client.py (token 传递方式统一)

Step 3: 异步线程改造
  └── main.py                (query_csqaq_market → 异步 Worker)

Step 4: ECO 订单联动
  ├── modules/eco_client.py  (parse_orders_for_sync 方法)
  └── main.py                (sync_eco_orders + 自动定时同步)

Step 5: 集成测试与验证
  ├── 测试异步搜索不卡 UI
  ├── 测试 ECO 订单匹配更新
  └── 测试设置保存与加载
```

### 4.2 修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `modules/logger.py` | **新建** | 统一日志配置 |
| `modules/retry.py` | **新建** | 网络请求重试装饰器 |
| `modules/workers.py` | **新建** | QThread 异步 Worker |
| `main.py` | **修改** | 异步改造 + ECO 联动 + 多项修复 |
| `modules/eco_client.py` | **修改** | 增加文件路径支持 + 订单解析方法 |
| `modules/db_manager.py` | **修改** | 增加写锁 |
| `modules/csqaq_client.py` | **修改** | 统一 token 传递方式 |

### 4.3 风险与注意事项

1. **QThread 生命周期管理**: Worker 和 Thread 必须正确清理，避免内存泄漏。使用 `deleteLater` + `finished` 信号链。
2. **ECO API 字段映射**: 实际 ECO API 返回字段可能与假设不同，需根据真实响应调整 `parse_orders_for_sync` 中的字段名。
3. **匹配精度**: `float_val` 匹配要求两端数据精度一致（保留足够小数位），建议统一为字符串比较。
4. **写锁性能**: 高频自动同步时，写锁可能成为瓶颈。当前场景（手动修改 + 定时同步）下影响可忽略。
5. **日志轮转**: 建议后续增加 `RotatingFileHandler` 避免日志文件无限增长。
