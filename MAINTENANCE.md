# CS2 饰品出租管理终端：维护与实现说明

> 本文档是当前代码实现的维护基线。`README.md` 只保留项目概览；换电脑、升级接口、修改数据结构或排查问题时，以本文为准。

## 1. 当前功能边界

应用是一个本地 PySide6 桌面程序，入口为 `main.py`，当前有三个标签页：

| 标签页 | 已实现的功能 | 说明 |
| --- | --- | --- |
| 资产与出租管理 | 饰品增删改、资产统计、C5 手动同步、订单历史、到期倒计时 | C5 是唯一已接入的订单读取平台 |
| 一览式大盘行情 | CSQAQ 售价/租金聚合、ECO 最低日租、平台跳转、行情观察列表与缓存 | 不直接请求 IGXE 行情接口 |
| 系统与费率设置 | 保存 CSQAQ Token、ECO Partner ID/RSA 私钥、资产页本地刷新间隔与费率 | 设置只写入私密数据目录 |

目前**没有**自动登录、验证码绕过、AI OCR 录单、ECO/IGXE 订单网页读取，也没有启用单独的“出租订单”标签页。`main.py` 中保留的旧订单页代码未挂到界面，不应视为已启用功能。

## 2. 快速运行与换电脑

### 首次安装

在项目根目录执行：

```powershell
C:\Users\AS\AppData\Local\Programs\Python\Python313\python.exe -m pip install -r requirements.txt
C:\Users\AS\AppData\Local\Programs\Python\Python313\python.exe -m playwright install chromium
C:\Users\AS\AppData\Local\Programs\Python\Python313\python.exe main.py
```

`playwright install chromium` 是 C5 可见浏览器功能的必需步骤；只安装 Python 包并不会下载浏览器运行时。

### 迁移到另一台电脑

1. 从私有 GitHub 仓库克隆代码。
2. 安装 Python、依赖和 Chromium（见上节）。
3. 从自己的加密云盘复制完整的 `private-data/` 目录到项目根目录。
4. 启动 `main.py`；不要把原电脑的 Chrome 用户配置文件复制给本程序。

也可以将私密数据放到云盘固定目录，再设置环境变量：

```powershell
$env:CS2_RENTAL_DATA_DIR = 'D:\Private\cs2-rental-manager'
```

该变量未设置时，程序默认使用项目旁的 `private-data/`。两种方式都不会被 Git 提交。

### Git 同步规则

```powershell
git pull --ff-only
git status
git add main.py modules MAINTENANCE.md README.md requirements.txt
git commit -m "说明本次变更"
git push origin main
```

- 只提交代码、文档和依赖清单。
- **绝不提交** `private-data/`、`keys/`、日志、`.env`、浏览器 Profile 或页面快照。
- 更新代码前先备份 `private-data/`。它包含资产、订单、Token、ECO 私钥、缓存和 C5 登录状态。

## 3. 私密数据目录

`modules/paths.py` 统一决定私密目录。默认目录为 `private-data/`，可通过 `CS2_RENTAL_DATA_DIR` 覆盖。

| 文件/目录 | 用途 | 迁移时是否复制 |
| --- | --- | --- |
| `app.db` | SQLite 主数据：资产、设置、出租订单 | 是 |
| `items.json` | 资产表的 JSON 备份/首次导入来源 | 是 |
| `configs.json` | Token、ECO 凭据、费率、刷新设置备份 | 是，敏感 |
| `market_cache.json` | 观察列表、行情、平台 ID、自定义跳转链接、成功更新时间 | 建议复制 |
| `eco_market_cache.db` | ECO 全量行情快照（约 4 万条） | 建议复制，可省去首次重新下载 |
| `schema-source/` | ByMykel 英文/中文原始饰品数据 | 是 |
| `cs2_items_schema.json` | 从上述两个源文件生成的本地索引 | 可复制，也可自动重建 |
| `images/` | 饰品图片缓存 | 可选 |
| `browser-profiles/c5game/` | 隔离的 C5 Playwright 登录状态 | 可选但敏感；复制后通常无需重新登录 |
| `browser-snapshots/` | C5 手动同步时保存的原始 HTML | 建议私密留存，供解析规则排障 |
| `logs/` | 轮转日志 `app.log` | 可选，排障时有用 |

`app.db` 是运行时主库。`items.json` 仅在数据库为空时导入，并在资产新增、修改、删除后作为备份写回；不要在日常使用时手改 JSON 期待覆盖一个已有数据库。

## 4. 数据模型与资产页

### `items` 表

主要字段：`name`、`market_hash_name`、`phase`、`pattern`、`float_val`、`cost`、`platform`、`status`、`rent`、`days`、`income`、`expire_hours`、`note`、`asset_id`。

- UI 的新增、修改、删除先更新 SQLite，再导出 `items.json`。
- `market_hash_name` 优先使用本地饰品映射生成；手动填写的英文名可作为回退。
- 资产页按平台筛选，统计买入总资产、当前日租估算、在租件数和年化估算。

### `rental_orders` 表

主要字段：`platform`、`order_no`、`item_name`、`float_val`、`income`、`start_time`、`return_time`、`status`、`raw_text`、`synced_at`。

- 唯一键为 `(platform, order_no)`；同一订单再次同步会更新而不会重复插入。
- 订单与资产依靠**标准化后的磨损值**匹配；资产页只展示同一磨损的最新订单。
- 同一磨损的旧订单在“订单历史”中显示，并按起租时间标记为“首次出租”或“转租”。
- 双击资产行可以打开订单历史与单条详情。
- 订单同步不会自动修改库存资产的 `status`，避免因为网页解析变化错误改动资产。

### 实时到期倒计时

资产页加载后创建 1 秒定时器，只更新“状态 / 倒计时”单元格，不重新读取数据库、不请求网络。

- 最新订单状态为“租赁中”且有归还时间：显示 `剩 X天 HH:MM:SS` 或 `剩 HH:MM:SS`。
- 剩余时间大于 12 小时为绿色。
- 剩余时间小于等于 12 小时、或已到期时为红色。
- “租赁中”但网页未提供可解析的归还时间时为橙色提示。

## 5. 平台订单页与 C5 手动读取

代码位置：`modules/c5_rental_browser.py`、`modules/workers.py`、`modules/db_manager.py`。

资产页提供三个“打开订单页”按钮，均使用系统默认浏览器，因此能复用你已登录的正常浏览器会话：

| 平台 | 订单页 |
| --- | --- |
| C5 | `https://www.c5game.com/user/rent?actag=2` |
| ECO | `https://www.ecosteam.cn/html/person/rentrecordlist.html` |
| IGXE | `https://www.igxe.cn/lease/seller-order-list` |

目前这些按钮仅负责打开网页；本程序不会读取默认浏览器的 Cookie 或 DOM，因此不会在后台自动导入订单。若需自动导入，必须额外实现并明确授权一个浏览器扩展或本地 Chrome 调试连接。

C5 现有的隔离读取器流程如下：

1. 点击“C5 登录”，打开隔离的、可见的 Playwright Chromium。
2. 由用户在窗口内登录并自行完成验证码；关闭窗口即保存该隔离 Profile。
3. 点击“同步 C5 出租订单”。程序访问 `https://www.c5game.com/user/rent?actag=2`，读取当前页文本和 HTML。
4. HTML 私密保存至 `browser-snapshots/c5-rent-时间.html`，文本解析后按订单号 upsert 到 `rental_orders`。
5. 资产页按磨损值关联订单并刷新倒计时/历史。

安全与边界：

- 不读取普通 Chrome Profile，不存储密码，不尝试绕过验证码或反爬限制。
- 操作仅由按钮手动触发；不存在后台轮询 C5。
- C5 页面改版后，优先保留同步产生的 HTML 快照，再根据快照调整 `parse_c5_rent_text()` 的解析规则。

## 6. 本地 CS2 饰品映射

代码位置：`modules/cs2_item_schema.py`。

来源是 ByMykel/CSGO-API 的两个本地 JSON 文件：

```text
private-data/schema-source/skins_not_grouped.en.json
private-data/schema-source/skins_not_grouped.zh-CN.json
```

首次运行或源文件变化时，程序会自动生成 `private-data/cs2_items_schema.json`。索引将中文名映射到：

- Steam `market_hash_name`
- 图片 URL
- 物品 ID、磨损/饰品元数据

映射找不到时，程序回退到已有英文 `market_hash_name`，最后才尝试旧的内置名称规则。新饰品或新手套优先通过更新两个源 JSON 解决，不要持续扩充旧规则表。

## 7. 行情页与接口实现

### 行情观察列表

- 冷启动只读取 `market_cache.json`，不会主动发网络请求。
- 可用 CSQAQ 搜索添加；可 Ctrl/Shift 多选后从观察列表移除，不会删除资产库存。
- 市场页内仅将多普勒 P1/P3 合并为一行 `P1 / P3`；资产记录本身不会合并，因为磨损不同仍是不同实物。
- 图片按 `market_hash_name` 缓存在 `private-data/images/`。

### CSQAQ

代码位置：`modules/csqaq_client.py`、`modules/workers.py`。

| 接口 | 用途 | 频率/缓存 |
| --- | --- | --- |
| `POST /api/v1/goods/getPriceByMarketHashName` | 批量获取 CSQAQ 最低售价及 `good_id` | 每批最多 50 个英文 `market_hash_name` |
| `GET /api/v1/info/good?id={good_id}` | 获取 C5、悠悠、IGXE 的短租/长租价及各平台 ID | 低频处理；明细缓存 10 分钟 |

页面显示：CSQAQ 最低售价及对应最低平台；C5、悠悠、IGXE 的短租/长租价。IGXE 的租金数据来自 CSQAQ 明细，**当前市场刷新不会直接调用 IGXE API**。

### ECO

代码位置：`modules/eco_client.py`、`modules/eco_market_cache.py`。

- ECO 接口返回的是全量 HashName/价格/起租价快照，正常约 4 万条，因此不能为每个观察物品单独调用。
- `eco_market_cache.db` 按 ECO Partner ID 保存全量快照；有效期为 **10 分钟**。
- 手动或定时刷新时，快照仍有效则只从本地 SQLite 匹配；过期或无快照才取全量接口数据并原子替换缓存。
- ECO 的相位查询先找相位精确记录；精确记录的租金为 0 而基础款有租金时，保留相位售价并回退基础款租金。
- `eco_min_rent = 0` 表示接口/快照没有有效最低日租，不代表一定是程序匹配错误。

### 行情刷新与相对时间

- “刷新行情”在后台 `QThread` 中运行 `MarketRefreshWorker`，避免阻塞 UI。
- 行情页的 10 分钟循环刷新在启动时默认开启；到点后立即重新开始下一轮倒计时。点击倒计时按钮可暂停或重新开启。资产页不放行情自动刷新按钮。
- 一轮手动或自动刷新完成时，所有观察行写入同一个 ISO 成功时间；最右列显示 `N 分钟前更新成功`。
- 相对时间每分钟仅更新表格文字，不请求 API。`market_cache.json` 持久化该成功时间，重启后仍可计算。

### 平台网页链接

双击行情表的名称/价格列会打开默认平台链接；右键可设置或清除自定义链接。自定义值保存在 `market_cache.json`，因此迁移时复制该文件即可保留。

默认规律如下（对应 ID 来自 CSQAQ 明细）：

| 平台 | 默认 URL |
| --- | --- |
| CSQAQ | `https://csqaq.com/goods/{csqaq_good_id}` |
| ECO 租赁页 | `https://www.ecosteam.cn/goods/730-{eco_id}-1-laypageRent-0-1.html` |
| C5 | `https://www.c5game.com/csgo/{c5_id}/`；没有 ID 时使用中文名搜索 |
| 悠悠有品 | `https://www.youpin898.com/market/goods-list?listType=30&templateId={yyyp_id}&gameId=730` |
| IGXE | `https://www.igxe.cn/product/730/{igxe_id}?cur_page=6&sort_rule=1`；没有 ID 时使用中文名搜索 |

平台有 URL 结构调整、产品 ID 缺失或希望跳转销售页而非租赁页时，用右键自定义链接即可，无需改代码。

## 8. 代码地图

| 路径 | 责任 |
| --- | --- |
| `main.py` | UI、定时器、市场表渲染、链接、资产/订单关联 |
| `modules/db_manager.py` | SQLite、资产 JSON 备份、订单 upsert、设置持久化 |
| `modules/paths.py` | 私密数据目录与环境变量 |
| `modules/workers.py` | 市场与 C5 的后台线程 Worker |
| `modules/csqaq_client.py` | CSQAQ Token 接口、节流、批量/明细请求 |
| `modules/eco_client.py` | ECO 签名请求与快照获取 |
| `modules/eco_market_cache.py` | ECO 全量快照 SQLite 缓存、相位租金回退 |
| `modules/cs2_item_schema.py` | ByMykel 本地 JSON 映射及索引重建 |
| `modules/c5_rental_browser.py` | C5 可见浏览器、页面快照与文本解析 |
| `modules/image_cache.py` | 行情观察列表和图片缓存 |
| `modules/logger.py` | 私密日志轮转 |
| `requirements.txt` | Python 依赖 |

## 9. 日常排障清单

### ECO 每次返回数万条是否异常？

不是。ECO 是全量行情接口。先检查 `private-data/eco_market_cache.db` 是否存在、`eco_cache_meta` 的时间是否在 10 分钟内，以及日志是否为“本地缓存”。没有缓存或缓存过期才会重新下载全量数据。

### ECO 租金为 0 或“暂无”

依次检查：

1. `market_hash_name` 是否来自本地 schema；
2. `phase` 是否是 `P1`、`P2`、`P3`、`P4` 等可识别值；
3. ECO 快照是否真有 `RentGoodsBottomPrice`；
4. 日志中是否出现“ECO 未匹配”。

不要仅凭售价存在就推断 ECO 一定提供了租赁价。

### C5 同步为空或提示未登录

先点击“C5 登录”，在打开的独立窗口完成登录/验证码并关闭。再手动同步。若页面已读到但解析订单数为 0，保留最新 `browser-snapshots/c5-rent-*.html`，根据其可见文本更新解析器。

### 市场页没有刷新或显示旧数据

确认 CSQAQ Token、ECO Partner ID 和 RSA 私钥已在设置页保存；检查 `private-data/logs/app.log`。若只想强制重新获取 ECO 全量快照，可等待缓存超过 10 分钟后刷新；不要频繁删除缓存或密集调用接口。

### 更改字段/数据库时的规则

1. 在 `DBManager.init_db()` 用 `CREATE TABLE IF NOT EXISTS` 和可重复执行的 `ALTER TABLE` 做迁移。
2. 同时更新读取、写入、JSON 导出、UI 表格和本文件。
3. 使用临时数据目录做测试，避免测试写入真实 `private-data/`。
4. 保持 Token、私钥、页面快照与真实订单不进入 Git diff。

## 10. 验证命令

每次改动至少运行：

```powershell
python -m compileall -q main.py modules
git diff --check
```

涉及 UI 时可做无窗口冒烟检查：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -c "from PySide6.QtWidgets import QApplication; from main import CS2ManagerApp; a=QApplication([]); w=CS2ManagerApp(); print(w.tabs.count()); w.close()"
```

不要在测试脚本中调用真实 `MarketRefreshWorker.refresh_all()`，除非确实需要验证线上接口、已确认频率和凭据，并接受它更新私密缓存。

## 11. 后续开发优先级

1. 用真实 C5 页面快照持续校准解析器；必要时读取“查看详情”页补足字段。
2. 为 ECO、IGXE 增加同样的**手动、可见、用户完成验证码**订单读取适配器。
3. 为市场刷新增加结构化成功/失败结果，让某个平台失败时可更精确地展示原因。
4. 清理 `main.py` 中未启用的旧订单页/旧明细代码，降低维护成本；清理时必须先做 UI 回归测试。
