# CS2 饰品出租管理终端：维护与实现说明

> 本文档是当前代码实现的维护基线。`README.md` 只保留项目概览；换电脑、升级接口、修改数据结构或排查问题时，以本文为准。

## 1. 当前功能边界

应用是一个本地 PySide6 桌面程序，入口为 `main.py`，当前有两个主要工作区，设置页由左下角按钮进入：

| 工作区 | 已实现的功能 | 说明 |
| --- | --- | --- |
| 资产与出租管理 | 饰品增删改、AI 协作批量导入、资产统计、C5/ECO/IGXE 剪贴板导入预览、订单历史、租赁/新购 CD 倒计时 | 三个平台都通过“打开订单页 + 复制 + 预览确认导入”操作 |
| 一览式大盘行情 | CSQAQ 售价/租金聚合、CSFloat 最低一口价与国内价差、ECO 最低日租、平台跳转、可切换的观察分类与缓存 | 不直接请求 IGXE 行情接口 |
| 系统与费率设置 | 保存 CSQAQ Token、CSFloat API Key/美元汇率、ECO Partner ID/RSA 私钥、资产页本地刷新间隔、费率与开机自启动 | 普通设置写入私密数据目录；开机启动项是当前 Windows 用户的本机设置 |

目前**没有**自动登录、验证码绕过、直接调用 AI API/OCR 录单，也不启用浏览器扩展或隔离浏览器读取。资产页和大盘页的 AI 协作导入都只复制固定提示词并接收用户粘贴的 JSON，不会上传、保存或处理截图文件。三个平台的固定格式订单文字可通过剪贴板预览后导入。未挂载的旧订单页实现已经删除，避免维护两套展示路径。

界面采用集中 QSS 主题：深浅表面、圆角与轻边界区分导航、内容、卡片和数据表；蓝色只表示主操作，绿色表示完成/成功，红色表示删除或异常，琥珀色表示需要留意，普通工具控件保持中性。主操作、危险操作、悬停、按下、选中和键盘焦点均有不同反馈。页面标题包含简短上下文说明，切换以 150ms 淡入表现且不阻塞输入；若用户启用“减少页面切换动效”，则不执行该过渡。调整视觉样式时优先修改 `modules/ui_theme.py`，不要把通用样式散落到业务代码。

## 2. 快速运行与换电脑

### 首次安装

在项目根目录执行：

```powershell
python -m pip install -r requirements.txt
python main.py
```

### 迁移到另一台电脑

1. 从私有 GitHub 仓库克隆代码。
2. 安装 Python 和项目依赖（见上节）。
3. 从自己的加密云盘复制完整的 `private-data/` 目录到项目根目录。
4. 通过口令加密的 `.cs2sync` 包迁移接口凭据，或在新电脑重新填写。DPAPI 密文不能跨 Windows 用户直接解密。
5. 启动 `main.py`；不要把原电脑的 Chrome 用户配置文件复制给本程序。

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
| `app.db` | SQLite 主数据：资产、设置、出租订单、订单关联、观察分类/观察品、报价与自定义链接 | 是 |
| `items.json` | 资产表的 JSON 备份/首次导入来源 | 是 |
| `configs.json` | 接口凭据的 DPAPI 密文，以及美元汇率、费率、刷新设置的原子备份 | 可复制；凭据需在同一 Windows 用户下解密 |
| `market_cache.json` | 旧版行情数据迁移输入；新版只在 SQLite 尚无对应报价时读取，不再写入 | 无需复制 |
| `eco_market_cache.db` | ECO 全量行情快照（约 4 万条） | 建议复制，可省去首次重新下载 |
| `exchange_rate_cache.json` | CSFloat/ECB 汇率、来源与失败冷却时间 | 建议复制，也可自动重建 |
| `schema-source/` | ByMykel 英文/中文原始饰品数据 | 是 |
| `cs2_items_schema.json` | 从上述两个源文件生成的本地索引 | 可复制，也可自动重建 |
| `images/` | 饰品图片缓存 | 可选 |
| `browser-profiles/c5game/` | 历史隔离 C5 读取器的登录状态 | 可选，当前界面不使用 |
| `logs/` | 轮转日志 `app.log` | 可选，排障时有用 |
| `cloud-sync/` | 加密同步包的 `inbox`、`outbox` 和导入前备份 | 按需复制；`.cs2sync` 已加密 |

`app.db` 是运行时主库。`items.json` 仅在数据库为空时导入，并在资产新增、修改、删除后作为备份写回；不要在日常使用时手改 JSON 期待覆盖一个已有数据库。

### 3.1 存储审计与生命周期

2026-07-24 的实测工作目录约 298MB；空间主要集中在私密运行数据、打包工作目录和重复的发布包，而非源码。以下表格是维护时的唯一清理判断依据。清理前必须关闭软件；绝不以节省空间为由删除业务主库、凭据备份或尚未验证的发布包。

| 位置 | 当前约占用 | 生命周期 | 是否可清理 | 处理规则 |
| --- | ---: | --- | --- | --- |
| `private-data/app.db`、`items.json`、`configs.json` | 小 | 业务真源/恢复备份 | 否 | 先做加密同步包或离线备份；`configs.json` 的 DPAPI 凭据只可由原 Windows 用户读取。 |
| `private-data/app.db-wal`、`app.db-shm` | 小、波动 | SQLite 运行时 | 不手动清理 | 软件运行时不得移动或删除；正常关闭数据库后由 SQLite 自行检查点/收敛。 |
| `private-data/schema-source/` | 约 70MB | 本地映射的原始中英文源数据 | 当前不可 | 当前 `CS2ItemSchema` 需要两份源 JSON 校验并构建索引；源文件缺失时名称映射为空。未来应改为只发布紧凑索引或可选源数据更新包。 |
| `private-data/cs2_items_schema.json` | 约 34MB | 由原始源数据生成的索引 | 当前不可单独清理 | 与源数据共同构成本地离线搜索；删除后会在下次启动重建。 |
| `private-data/browser-profiles/c5game/` | 约 28MB | 旧隔离浏览器 Profile，可能含登录态 | 是，确认后 | 当前界面不使用它；先移至项目外的加密归档，确认不需要旧 C5 登录档案后再删除，绝不可提交或上传。 |
| `private-data/images/` | 约 5.4MB | 可下载缩略图缓存 | 是 | 程序自动限制为 250MB 或 2,000 张；手动清理只会导致后续重新下载。 |
| `private-data/eco_market_cache.db` | 约 7.5MB | ECO 全量行情快照 | 是 | 程序保留当前账号和最近一个旧账号；删除会导致下一次请求重新下载全量快照。 |
| `private-data/market_cache.json` | 约 0.1MB | 旧版迁移输入 | 建议保留 | 新版不写入，但启动时仍可用它补齐旧 SQLite 行缺少的报价；空间极小，不是优化目标。 |
| `private-data/*.pre-*.bak` | 当前小于 0.2MB | 一次性迁移前快照 | 可按需归档 | 保留最近一次成功迁移即可；后续迁移代码应加入数量或天数上限，防止长期累计。 |
| `private-data/logs/` | 约 0.8MB | 可重建诊断日志 | 是 | 已有约 6MB 轮转上限；仅在排障结束后清理。 |
| `build/` | 成功打包后为 0MB | PyInstaller 工作目录 | 是 | `build_exe.bat` 成功时自动清理；打包失败时保留，以便排查。 |
| `release/` | 约 97MB | 发布候选 EXE | 是，需选择 | 只保留一个已验证的正式 `CS2租赁管理.exe`；带“界面优化版”后缀的候选包确认后应归档到项目外或删除，避免双份约 51MB EXE 常驻。 |
| `__pycache__/`、`.ruff_cache/`、`.coverage`、`.idea/` | 当前小于 1MB | 开发缓存/IDE 元数据 | 是 | 均已被 Git 忽略，删除不影响正式 EXE；体积很小，不是优先项。 |

发布一个可独立使用的目录时，不能只复制 EXE：当前实现还需要相邻项目根目录的 `private-data/schema-source/` 两份原始映射文件（或未来改造后的等价紧凑资源）。`modules/paths.py` 会让 `release/` 下的 EXE 回到其父目录寻找 `private-data/`；复制整个项目时要保持这一相对关系，或通过 `CS2_RENTAL_DATA_DIR` 指向私密数据目录。

### 3.2 源码、测试与文档的目录定位

当前仓库的源码定位为：根目录 `main.py` 负责应用装配与页面编排；`modules/` 放置可复用的领域规则、存储、平台客户端和主题；`tools/` 放置开发辅助脚本；`assets/` 放置打包资源；`.github/` 放置 CI。`private-data/`、`build/` 和 `release/` 虽然也在根目录，但不是源码目录，分别对应私密运行数据、可再生打包工作目录和发布输出。

测试和专项文档的第一阶段归位已经完成。后续继续拆分 UI 与平铺模块时，必须保持下面的构建、链接和 CI 约束。

| 迁移项 | 当前 | 目标 | 必须同步调整 |
| --- | --- | --- | --- |
| 单元测试 | `tests/test_*.py` | 保持 `tests/` | `build_exe.bat` 与 GitHub Actions 使用 `python -m unittest discover -s tests -v`；Ruff 检查 `tests`；覆盖率 omit 规则为 `tests/*`。 |
| 专项文档 | `docs/GITHUB_SETUP.md`、`docs/PLAN_OPTIMIZATION.md`、`docs/RENTAL_SELECTION_METHODOLOGY.md` | 保持 `docs/` | README 保持根目录入口；新增专项文档也放入 `docs/` 并使用正确的相对链接。`MAINTENANCE.md` 暂留根目录。 |
| 页面编排 | 根目录 `main.py` | 薄入口 + `modules/ui/` 子模块 | 入口只保留 QApplication、主窗口装配和启动/关闭流程；按资产页、行情页、设置页、对话框拆分，并为每次拆分保留导入/交互回归测试。 |
| 平铺模块 | `modules/*.py` | `modules/domain`、`storage`、`integrations`、`ui` | 一次集中更新绝对导入和 PyInstaller hidden import；不在功能修复中混入文件搬迁。 |

建议的职责归类是：`domain` 包含 `asset_import`、`dashboard_service`、`domain_models`、`rental_*`、`market_watch_service`、`cs2_item_schema` 和 `platform_time`；`storage` 包含 `db_manager`、`db_migrations`、`atomic_io`、`image_cache`、`eco_market_cache`、`paths`、`secret_store`；`integrations` 包含各平台客户端、`base_client`、`retry`、`exchange_rate_client`；`ui` 包含页面部件、`market_table_model`、`ui_theme` 与 UI 相关 Worker。`logger`、`version`、`startup_manager` 可暂保留在 `modules/` 顶层作为应用级基础设施。

运行时可重建数据均有容量边界：`logs/app.log` 使用 2MB × 3 份轮转，`images/` 最多保留 250MB 或 2,000 张图片并优先删除最旧文件，`eco_market_cache.db` 最多保留当前 ECO Partner ID 与最近一个旧账号的完整快照。观察列表、资产、订单和 `app.db` 不自动删除，避免丢失用户数据。

CSQAQ Token、CSFloat API Key、ECO Partner ID 与 RSA 私钥通过 Windows DPAPI 绑定当前用户后，再保存到 `app.db`/`configs.json`；程序读取时透明解密。旧版明文值会在首次启动时原地升级。DPAPI 降低文件被单独复制后的泄漏风险，但不防护已经登录同一 Windows 账户的恶意程序；仍不要把整个私密目录发给他人。设置页输入框默认掩码，只有用户主动勾选时才临时显示。

API 凭据应通过“系统设置”页面填写并保存，不要直接手工改 `configs.json`。程序会自动修复“CSFloat Key 缺少开头双引号”这一种常见格式错误；其他损坏的 JSON 会被忽略，并且不会再用空默认值覆盖数据库中已有的凭据。`items.json`、`configs.json` 与同步包均采用“同目录临时文件 + `os.replace`”的原子替换写入，降低中断后产生半截文件的风险；`market_cache.json` 已不再作为运行时写入文件。

Google Drive 手动同步只导出 `rental_orders`、行情观察分类/观察品和 API 相关配置，不上传资产主表、技术日志或行情快照。外层 `.cs2sync` 使用 PBKDF2-HMAC-SHA256（600,000 次）派生 256 位密钥，并用 AES-256-GCM 加密和认证；口令不落盘。导出与导入都在后台工作线程中执行；导入订单、配置和观察列表时使用一个 SQLite 事务，任一阶段失败会整体回滚。同步包成功生成后，`cloud-sync/outbox/` 只保留最新一份；生成失败不会清理原有文件，`inbox/` 下载目录不参与自动清理。导入前先用同一口令在 `cloud-sync/backups/` 生成本机同步数据备份并只保留最近 3 份，然后按订单唯一键和观察品标识合并。收藏同步不会用远端旧报价覆盖本机缓存价格。

## 4. 数据模型与资产页

### `items` 表

主要字段：`name`、`market_hash_name`、`phase`、`pattern`、`float_val`、`cost_cents`、`platform`、`status`、`rent_cents`、`days`、`income_cents`、`expire_hours`、`cooldown_until`、`note`、`asset_id`、`deleted_at`。原有 REAL 金额列为旧版兼容字段，业务读取以整数分列为准。

- 新增/修改表单先经过 `InventoryItemDraft` 领域校验，非法数字不会关闭对话框，编辑时保留稳定 `asset_id`。
- 资产页“AI 批量导入”复制 `AI_ASSET_IMPORT_PROMPT`，接收 `items` JSON 后逐行通过 `CS2ItemSchema`、`InventoryItemDraft` 与平台/状态白名单校验。中文 `name` 与英文 `market_hash_name` 先经本地 schema 归一为同一套标准身份；已知中文身份优先纠正 AI 生成的冲突英文名，并兼容历史错字“赤色迫风”到官方名“赤色追风”。`Sport Gloves | Slingshot`（弹弓）与 `Sport Gloves | Red Racer`（赤色追风）必须保持两个 schema 身份。无法安全归一的名称留给预览核对，不能只凭相似中文名猜测。
- AI 资产去重以“标准饰品身份 + 安全磨损精度”为实物键：磨损以十进制读取并保留来源精度；双方至少都提供 6 位小数时，将较长值截断或四舍五入到较短值的小数位数，两者任一结果与较短值相等才算匹配。因此 `0.02071962` 可与 `0.0207196157425642` 匹配，`0.04006363824` 也可与 `0.04006363824009895` 匹配。少于 6 位小数的信息区分度不足，不参与自动关联；不要先转为二进制浮点或固定补零后比较，以免引入舍入误判。
- 去重结果只有一个旧资产候选时，更新该记录而不是新建：必须复用原 `asset_id`，因此全部 `rental_orders.item_id` 关联与订单历史原样保留；新旧磨损中位数更多、信息更完整的一方写回资产。若同一身份与安全磨损命中多个候选，预览应标记歧义且不自动合并，不能按订单时间、成本、平台或列表顺序擅自选择。
- 新购饰品选择 `CD冷却` 时必须输入 `0 < cooldown_hours <= 720`；仓储以“确认导入时刻 + 剩余小时”保存绝对 ISO 时间 `cooldown_until`，因此重启不会重置倒计时。CD 不从订单起止时间、订单先后或历史状态推测；没有可靠剩余小时就不能用订单历史补算。`expire_hours` 仅保留输入/旧数据兼容。
- UI 的新增、修改、删除先更新 SQLite，再导出 `items.json`。删除写入 `deleted_at`，默认查询排除该记录；底部提示条提供 10 秒撤销并清空 `deleted_at`。
- `modules/startup_manager.py` 管理 Windows 当前用户 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 启动项。打包环境登记当前 EXE；源码环境使用 `pythonw.exe + main.py`，避免登录时弹出控制台。启动项属于本机系统状态，不写入数据库、不进入云同步；注册表权限错误必须在设置页明确提示。
- `market_hash_name` 优先使用本地饰品映射生成；手动填写的英文名可作为回退。
- 资产页按平台筛选，统计买入总资产、当前每日净收益、累计净收益、在租件数和年化估算。手动标记“已出租”但没有匹配订单时会计入在租件数并显示橙色“未导入订单”提示；没有真实订单到期时间时绝不伪造倒计时。
- 成本单元格右键可输入手续费百分比并直接更新资产成本，输入 `1` 表示在当前成本上增加 1%，结果按分四舍五入并同步写回 `items.json`。售价价差按“CSQAQ 全网最低售价 − 当前成本”计算；租金价差按“当前原始日租 − 最新订单所在平台最低日租”计算，没有订单时回退到资产平台。C5、悠悠和 IGXE 同时有短租/长租行情时取其中较低的正数，ECO 使用 `eco_min_rent`。
- 资产行默认先按有效的出租结束、待转租结束或 CD 结束时间升序排列，即剩余时间最短的优先；倒计时未知的出租/CD资产随后，无倒计时资产最后。相同倒计时层级继续按各平台当前在租数量、平台资产总数、平台名、饰品名称、最低成本、成本和资产 ID 稳定排序。用户主动点击表头后仍可临时改用列排序。

### `rental_orders` 表

主要字段：`platform`、`order_no`、`item_name`、`float_val`、`daily_rent_cents`、`rental_days`、`deposit_cents`、`income_cents`、`start_time`、`return_time`、`status`、`raw_text`、`transfer_reward_cents`、`item_id`、`match_method`、`match_confidence`、`synced_at`。

- 唯一键为 `(platform, order_no)`；同一订单再次同步会更新而不会重复插入。
- 新订单先尝试按稳定 `asset_id`/显式 `item_id` 关联；回退到标准化磨损值时只接受唯一候选。冲突磨损保持未关联，不能把一笔订单同时计入多件资产。
- 导入预览的“关联资产”列允许手工指定，选择结果写入 `item_id`，并记录 `match_method` 与置信度。订单历史按 `item_id` 聚合；仅为兼容尚未迁移的旧记录才使用唯一磨损回退。
- 导入预览与订单历史的“日租（原价）”不扣费；资产页的“日租（净）”、本单净收入和累计净收益会对全部历史订单重新按费率计算。新订单距离前一单**租赁到期**少于 7 天（含直接交接/时间重叠）时使用转租费率，否则使用首次出租费率。
- 已由订单详情核对的默认口径：C5 首次/转租服务费均为 15%；ECO 首次/转租费率默认为 0。IGXE 页面没有可靠字段区分一键/手动定价，因此导入预览必须由用户逐笔确认：`one_click` 为 5%，`manual` 为 10%，该值持久化在订单上，优先于设置中的 IGXE 首次/转租费率；历史未确认订单仍回退配置值。连续出租的 `9 折` 会在服务费前先应用，结果按分向下截断。已验证一键样例：`2.32 × 8 × 0.9 × 0.95 = 15.8688 → ¥15.86`、`2.10 × 8 × 0.9 × 0.95 = 14.364 → ¥14.36`；未结算的手动样例 `2.62 × 8 × 0.9 × 0.90` 预计为 `¥16.97`，待结算金额出现后可继续校准。C5 的“转租奖励”不是通用转租费率，而是出租方支付给原承租方的独立成本：只适用于 C5，且必须从 C5 **订单详情**读到 `待发放` 或 `已发放`，并找到同一饰品下一笔 C5 订单在原订单到期后的 12 小时内开始。成本扣除额为页面确认金额与该原订单金额 5% 的较小值；`最高奖励`、`已取消`、缺少后续 C5 订单、超出 12 小时及 ECO/IGXE 订单均为 0。这样不需要把奖励并入任何平台费率，也会在历史订单重算时自动纠正旧的误扣。
- 双击资产行可以打开订单历史与单条详情。
- 订单同步不会自动修改库存资产的 `status`，避免因为网页解析变化错误改动资产。

### 实时到期倒计时

资产页加载后创建 30 秒定时器，只更新“状态 / 倒计时”单元格，不重新读取数据库、不请求网络。

- 最新订单状态为“租赁中”且有**租赁到期时间**：先按平台页面时区换算为 UTC，再显示 `剩 X天X小时X分` 或 `剩 X小时X分`。C5 使用浏览器/本机时区；ECO 与 IGXE 使用北京时间（Asia/Shanghai）；原始网页文本不改写。
- “需更新订单”只显示在对应资产的状态栏：条件是该资产当前最新订单仍为“租赁中”、平台页面提供了明确的 `return_deadline`，且该时点已过。没有明确截止时间的历史订单不推测，不在全局状态栏汇总；状态提示的悬浮说明会显示平台、订单号和该截止时间。
- 没有租赁订单、但资产状态为 `CD冷却` 且 `cooldown_until` 有效：显示新购 CD 的同格式倒计时；截止后显示“新购 CD 已结束”。
- 剩余时间大于 12 小时为绿色。
- 剩余时间小于等于 12 小时、或已到期时为红色。
- “租赁中”但网页未提供可解析的租赁到期时间时为橙色提示。

## 5. 平台订单页与 C5 手动读取

代码位置：`modules/c5_rental_browser.py`、`modules/workers.py`、`modules/db_manager.py`。

资产页提供三个“打开订单页”按钮，均使用系统默认浏览器，因此能复用你已登录的正常浏览器会话：

| 平台 | 订单页 |
| --- | --- |
| C5 | `https://www.c5game.com/user/rent?actag=2` |
| ECO | `https://www.ecosteam.cn/html/person/rentrecordlist.html` |
| IGXE | `https://www.igxe.cn/lease/seller-order-list` |

### 剪贴板订单导入（C5、ECO、IGXE）

代码位置：`modules/rental_order_parsers.py`、`modules/c5_rental_browser.py`、`modules/db_manager.py`。

这是三个平台目前共同、最稳定的录入方式：在订单页全选并复制文本，回到“资产与出租管理”，点击“从剪贴板导入订单”。导入器先按照文字特征识别平台并显示预览；只有用户点击“确认导入”后才会写入数据库。一次导入一个平台，保存逻辑如下：

| 平台 | 识别特征 | 导入字段 |
| --- | --- | --- |
| C5 | `订单号`、`查看详情`；或单笔详情的 `订单编号`、`租赁价格` | 订单号、起止时间、饰品名、磨损、订单收入、状态；单笔详情还会读取已确认的转租奖励；页面时间按浏览器本地时区解释，列表未给出原始日租时按整天租期反算 |
| ECO | `订单编号` 加 ECO 商品链接、`前归还` 或 `ECO_` | 订单号、磨损、原始日租、长/短租天数、押金、状态；页面时间按北京时间解释，优先用“前归还 − 12 小时”作为租赁到期，不再用创建时间机械加租期 |
| IGXE | 交易链接；或无链接时至少四项 `订单类型`、`创建时间`、`租赁到期时间`、`归还截止时间`、`租赁租金` | 交易 ID、磨损、原始日租、出租天数、押金、可用时的订单金额、连续出租折扣、**租赁到期时间**；Chrome 纯文本复制可能丢失交易链接，此时用订单关键字段生成稳定本地键去重。未结算订单没有金额也可导入，预览必须选择一键定价 5% 或手动定价 10%；页面时间按北京时间解释 |

`rental_orders` 使用 `(platform, order_no)` 唯一键，因此再次复制同一页面只更新订单而不会重复计算。资产与订单优先使用显式 `item_id`；只有磨损匹配到唯一资产时才自动关联，冲突时由用户在预览中选择。与前一单**租赁到期**间隔少于 7 天的订单显示为“已转租”，间隔达到 7 天或没有前序订单则显示为“已出租”。任何解析失败或被用户在预览中取消的订单都不会写入数据库。


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

网络查询身份在映射找不到时仍可回退到已有英文 `market_hash_name`；但行情与求购表格的最终显示统一调用 `chinese_display_name()`，优先本地中文映射/接口中文名，绝不把英文查询名作为 UI 标签，无法映射时显示“未知饰品”。新饰品或新手套优先通过更新两个源 JSON 解决，不要持续扩充旧规则表。

`CS2ItemSchema.search()` 用于大盘的人类输入检索：会忽略 `★` 与空格，兼容“伽马/伽玛”、`Butterfly Knife` 等常见表达；它返回候选而不擅自猜测唯一物品。候选确认后才以其中完整的英文 `market_hash_name` 加入当前观察分类。

## 7. 行情页与接口实现

### 行情观察列表

- 冷启动以 SQLite 的 `market_categories`/`market_watch_items` 作为观察列表、报价和自定义链接的真源；仅在新版 SQLite 尚无对应报价时合并旧版 `market_cache.json`，不会主动发网络请求。
- “搜索并添加”先用本地映射做模糊候选匹配，再由用户选择后加入当前分类；只有没有本地映射的完整英文 `market_hash_name` 才回退到 CSQAQ 查询。可 Ctrl/Shift 多选后从观察列表移除，不会删除资产库存。
- “AI 批量添加”不会上传截图：它只提供固定提示词、接收用户粘贴的 JSON，再以本地 `CS2ItemSchema` 校验中文名/英文 `market_hash_name`。确认后写入 SQLite 观察列表，并更新本地报价缓存；需要点击“刷新行情”才请求报价接口。
- 搜索可使用中文、英文或别名，但观察表最终只渲染中文名；英文 `market_hash_name` 保留在提示信息与网络请求字段中。
- 行情表使用 `QTableView` + `MarketTableModel`。筛选、排序和右键/双击操作均通过模型行身份定位，不依赖重绘后的控件索引。
- 市场页内仅将多普勒 P1/P3 合并为一行 `P1 / P3`；资产记录本身不会合并，因为磨损不同仍是不同实物。
- 图片按 `market_hash_name` 缓存在 `private-data/images/`。

### CSQAQ

代码位置：`modules/csqaq_client.py`、`modules/workers.py`。

| 接口 | 用途 | 频率/缓存 |
| --- | --- | --- |
| `POST /api/v1/goods/getPriceByMarketHashName` | 批量获取 CSQAQ 最低售价及 `good_id` | 每批最多 50 个英文 `market_hash_name` |
| `GET /api/v1/info/good?id={good_id}` | 获取 C5、悠悠、IGXE 的短租/长租价及各平台 ID | 低频处理；明细缓存 10 分钟 |

页面显示：CSQAQ 最低售价及对应最低平台；C5、悠悠、IGXE 的短租/长租价。IGXE 的租金数据来自 CSQAQ 明细，**当前市场刷新不会直接调用 IGXE API**。

### CSFloat

代码位置：`modules/csfloat_client.py`、`modules/exchange_rate_client.py`、`modules/workers.py`、`main.py`。

- 设置页保存 `csfloat_api_key`、`auto_usd_cny_rate` 与手工备用 `usd_cny_rate`。密钥按 CSFloat 文档放在 `Authorization: <API-KEY>` 请求头中。当前仅用 `GET` 读取大盘行情报价，不读取账户、个人求购或成交历史；客户端也不暴露创建、修改或撤销求购的方法。
- 每个饰品请求 `GET https://csfloat.com/api/v1/listings`，参数固定为完整 `market_hash_name`、`type=buy_now`、`sort_by=lowest_price`、`limit=1`。返回后再次校验 `type == buy_now`、`state == listed` 和完整英文名，因此 `auction` 不会进入最低价。
- 响应的 `price` 是美分，程序除以 100 显示美元；为贴近 CSFloat 前端，人民币展示价按 `ceil(美元美分 × USD/CNY) / 100` 向上取到人民币分。相对 CSQAQ 国内最低价的公式为 `(CSF人民币价 - 国内最低价) / 国内最低价 × 100%`，负数表示 CSFloat 更低。
- 汇率优先请求 CSFloat 前端正在使用的第一方 `GET /api/v1/meta/exchange-rates`，读取 `data.cny`；官方没有披露该接口背后的数据供应商或时间戳。请求失败时改用 ECB `eurofxref-daily.xml`，按 `CNY_per_EUR / USD_per_EUR` 推导 USD/CNY；两者都失败时优先过期权威缓存，最后用手工备用值。
- `exchange_rate_cache.json` 缓存成功汇率 12 小时；无可用缓存且网络失败后冷却 1 小时，避免每 10 分钟自动刷新都重试。CSFloat 网站展示换算与 Stripe 提现换汇是两件事，不能用本列估算最终到账金额。
- CSFloat 列为独立列：显示美元价、人民币参考价、`较国内 ±N%`。该换算不包含汇兑、平台手续费或提现成本，不能直接当作最终套利收益。
- 成功报价与“无一口价”结果都按饰品持久化缓存 10 分钟；市场最高求购另用 `csfloat_buy_fetched_at` / `csfloat_buy_query_mhn` 缓存 30 分钟，刷新一口价时不会无条件重复调用 listing 级 buy-orders 端点。缓存保存精确 `market_hash_name`，名称改变时不会误用旧商品报价。
- 频控是进程级共享状态：大盘行情与手动同步创建的所有 `CSFloatClient` 共用最后请求时间、额度保留线和冷却截止时间。额度充足时保持 1.25 秒基础保护；`RateLimit-Remaining` 到达 10 次保留线、收到 `Retry-After` 或 HTTP 429 时暂停整个调度队列直到 `RateLimit-Reset`。
- 服务端冷却首先保存在 `CSFloatClient` 的进程级状态中，并同步写入 `private-data/csfloat_cooldown.json`。没有 `Retry-After`/`RateLimit-Reset` 的连续 HTTP 429 按归一化接口桶采用 60、120、300、600、1200、1800 秒退避；成功响应清零该接口计数。持久文件最多恢复未来 24 小时内的冷却，损坏或异常远期值会忽略，因此用户重启软件也不会绕过上一轮服务端限制。
- CSFloat 个人求购工作区及其 `/me`、`/me/buy-orders`、`/history/{market_hash_name}/sales` 请求已经移除。当前仅保留大盘行情所需的最低一口价与 30 分钟缓存的市场最高求购读取；客户端没有创建、修改或撤销订单的方法。
- CSFloat 与 Steam 都可能让不同多普勒相位共享同一个 `market_hash_name`。当前行情页本来就合并 P1/P3，CSF 列显示同名商品最低价；若以后要严格区分 Ruby/Sapphire/P2/P4，需从本地 schema 解析相位对应 `paint_index`，再向接口增加该筛选条件。

### ECO

代码位置：`modules/eco_client.py`、`modules/eco_market_cache.py`。

- ECO 接口返回的是全量 HashName/价格/起租价快照，正常约 4 万条，因此不能为每个观察物品单独调用。
- `eco_market_cache.db` 按 ECO Partner ID 保存全量快照；有效期为 **10 分钟**，最多保留当前账号和最近一个旧账号，避免切换账号后无限增长。
- 手动或定时刷新时，快照仍有效则按观察列表的 HashName 从 SQLite 定向查询，不把约 4 万行全部载入内存；过期或无快照才取全量接口数据并原子替换缓存。
- ECO 的相位查询先找相位精确记录；精确记录的租金为 0 而基础款有租金时，保留相位售价并回退基础款租金。
- `eco_min_rent = 0` 表示接口/快照没有有效最低日租，不代表一定是程序匹配错误。

### 行情刷新与相对时间

- “立即同步”、大盘 `F5` 和后台定时器全部调用 `_request_global_sync_now()` / `_run_rolling_market_refresh()`，跨分类重复的“`market_hash_name` + 相位”只请求一次。
- 资产页与大盘页顶部的“数据同步”按钮是同一个开关和同一进度状态。一次循环严格串行为：按 `_market_categories` 显示顺序逐分类批量行情 → 资产总览。循环成本按到期的一口价和 30 分钟市场最高求购估算，再与本轮实际请求量取较大值；额度不足一整轮则等到重置。
- 一轮手动或自动刷新完成时，所有分类的观察行写入同一个 ISO 成功时间；最右列显示 `N 分钟前更新成功`，随后立即重新计算资产首页的售价与平台租金价差。
- 相对时间每分钟仅更新模型显示文字，不请求 API。成功时间与报价一并写入 SQLite，重启后仍可计算。
- `W/S` 循环切换资产与大盘两个主工作区；资产页用 `A/D` 循环切换全部、出租中、待转租、CD冷却和在库分类，大盘页用 `A/D` 切观察分类。输入框、多行 JSON 区和下拉框有焦点时不拦截 WASD。键盘导航通过零延迟事件排队，在当前按键派发结束后才切页或重建表格。`Alt+1/2/3`、`Alt+←/→` 继续作为辅助快捷键；`Ctrl+N` 新增资产，`Ctrl+F` 聚焦搜索框，`F5` 手动刷新行情。
- 行情 `QThread` 从创建到精确的 `finished` 身份回调完成前始终占用唯一刷新槽；旧线程的延迟清理不得清空后来线程的引用。刷新结果只立即重绘当前可见页面，隐藏的大盘表标记为待渲染并在切回时更新。原生崩溃堆栈写入 `private-data/logs/fatal-crash.log`，普通业务日志仍写入 `app.log`。
- CSFloat 失败时会保留上一次成功价，但在单元格中明确标为“缓存价 · 频控/网络失败/待刷新”；不会把旧报价伪装成本轮新报价。
- 旧版 `market_cache.json` 的单列表会在首次启动迁移为“出租品”，并只用于补齐 SQLite 中缺少的历史报价；随后所有分类、观察项、报价和自定义链接都只写入 `app.db`。“出租品”是资产主表的行情镜像：启动以及资产新增、编辑、AI 导入、软删除或恢复后都会按标准 `market_hash_name + 相位` 对账，保留已有报价并补入缺失资产；自定义分类不受影响。若行情 Worker 正在运行则延迟到线程精确清理后再对账。

### 平台网页链接

双击行情表的名称/价格列会打开默认平台链接；右键可设置或清除自定义链接。自定义值保存在 `app.db`，迁移时复制主库即可保留。

默认规律如下（对应 ID 来自 CSQAQ 明细）：

| 平台 | 默认 URL |
| --- | --- |
| CSQAQ | `https://csqaq.com/goods/{csqaq_good_id}` |
| CSFloat | `https://csfloat.com/item/{csfloat_listing_id}` |
| ECO 租赁页 | `https://www.ecosteam.cn/goods/730-{eco_id}-1-laypageRent-0-1.html` |
| C5 | `https://www.c5game.com/csgo/{c5_id}/`；没有 ID 时使用中文名搜索 |
| 悠悠有品 | `https://www.youpin898.com/market/goods-list?listType=30&templateId={yyyp_id}&gameId=730` |
| IGXE | `https://www.igxe.cn/product/730/{igxe_id}?cur_page=6&sort_rule=1`；没有 ID 时使用中文名搜索 |

平台有 URL 结构调整、产品 ID 缺失或希望跳转销售页而非租赁页时，用右键自定义链接即可，无需改代码。

## 8. 代码地图

| 路径 | 责任 |
| --- | --- |
| `main.py` | UI 编排、定时器、表格渲染、链接与后台任务生命周期 |
| `modules/domain_models.py` | 资产表单领域模型、统一校验、金额分转换 |
| `modules/dashboard_service.py` | 首页收益、租期、生命周期、价差和排序纯计算 |
| `modules/market_table_model.py` | 行情模型视图的数据、筛选、排序与行身份 |
| `modules/market_watch_service.py` | 相位归一、观察项稳定身份与旧缓存迁移合并 |
| `modules/rental_matching.py` | 订单唯一匹配、稳定历史索引 |
| `modules/asset_import.py` | AI 资产标准身份、磨损精度去重、安全合并计划与执行 |
| `modules/startup_manager.py` | Windows `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` 当前用户启动项 |
| `modules/db_manager.py` | 串行 SQLite 仓储、事务、软删除与兼容 JSON 备份 |
| `modules/db_migrations.py` | `PRAGMA user_version` 驱动的有序迁移 |
| `modules/atomic_io.py` | 原子 JSON 写入 |
| `modules/secret_store.py` | Windows DPAPI 凭据保护 |
| `modules/ui_theme.py` | 全局 QSS 主题 |
| `modules/version.py` | 运行时版本号单一来源 |
| `modules/paths.py` | 私密数据目录与环境变量 |
| `modules/workers.py` | API 与市场刷新的后台线程 Worker |
| `modules/csqaq_client.py` | CSQAQ Token 接口、节流、批量/明细请求 |
| `modules/csfloat_client.py` | CSFloat 大盘行情只读查询、响应过滤与频控冷却；无账户、个人求购、成交历史或写入方法 |
| `modules/exchange_rate_client.py` | CSFloat 官网汇率、ECB 回退、缓存与失败冷却 |
| `modules/eco_client.py` | ECO 签名请求与快照获取 |
| `modules/eco_market_cache.py` | ECO 全量快照 SQLite 缓存、相位租金回退 |
| `modules/cs2_item_schema.py` | ByMykel 本地 JSON 映射及索引重建 |
| `modules/c5_rental_browser.py` | C5 剪贴板文本解析 |
| `modules/rental_order_parsers.py` | C5/ECO/IGXE 剪贴板格式识别与结构化解析 |
| `modules/platform_time.py` | C5 本机时区、ECO/IGXE 北京时间的订单页面时间规则与 UTC 换算 |
| `modules/image_cache.py` | 饰品图片缓存 |
| `modules/cloud_sync.py` | AES-GCM 同步包、Google Drive 手动传文件、订单/观察品合并 |
| `modules/logger.py` | 私密日志轮转 |
| `requirements.txt` | Python 依赖 |
| `requirements-dev.txt` | Ruff、Coverage、PyInstaller 与图标工具 |

所有网络请求、市场刷新、CSQAQ 绑定以及同步包导入/导出必须经 `modules/workers.py` 或通用后台启动器运行，GUI 线程只更新控件。应用关闭时先向 Worker 发取消请求，等待线程结束，再关闭单例 SQLite 连接；新增后台任务必须沿用这一退出协议。

`build_exe.bat` 会在构建前删除旧的 `build-*` PyInstaller 临时目录，并在成功生成 EXE 后删除当前 `build/`；若构建失败则保留当前目录供排障。`release/CS2租赁管理.exe` 为同名覆盖输出。不要把临时构建目录加入 Git。

SQLite 使用单例连接和 `RLock` 串行访问，启用 WAL、外键、`busy_timeout=10s` 与 `synchronous=NORMAL`。这适合当前单进程桌面应用；不要从绕过 `DBManager` 的新连接直接并发写主库。

当前 SQLite `user_version` 为 **7**：v1 补齐旧版订单字段，v2 新增整数分金额并回填，v3 新增稳定订单关联与资产软删除，v4 将观察分类/观察品迁入主库，v5 为所有资产回填全局唯一 `asset_id`，v6 新增 `cooldown_until` 并为仍有有限 `expire_hours` 的旧 CD 资产回填绝对截止时间，v7 为订单新增用户确认的 `pricing_mode`，用于保存 IGXE 一键/手动定价。新版本发现数据库版本高于自身支持范围时会拒绝启动，避免旧程序误写新结构。

## 9. 日常排障清单

### ECO 每次返回数万条是否异常？

不是。ECO 是全量行情接口。先检查 `private-data/eco_market_cache.db` 是否存在、`eco_cache_meta` 的时间是否在 10 分钟内，以及日志是否为“本地缓存”。没有缓存或缓存过期才会重新下载全量数据；缓存有效时只读取观察列表命中的少量 SQLite 行。

### ECO 租金为 0 或“暂无”

依次检查：

1. `market_hash_name` 是否来自本地 schema；
2. `phase` 是否是 `P1`、`P2`、`P3`、`P4` 等可识别值；
3. ECO 快照是否真有 `RentGoodsBottomPrice`；
4. 日志中是否出现“ECO 未匹配”。

不要仅凭售价存在就推断 ECO 一定提供了租赁价。

### 剪贴板导入后没有关联资产或日租为 0

先确认资产记录的磨损值与订单页的磨损一致；旧资产磨损被截短时，订单关联沿用原有的末位半单位严格容差，不按中文名称猜测。匹配到多个资产时仍保持未关联，需在预览中手工指定。C5 列表通常只给订单实际收入而不单列日租，因此程序按订单起止时间的整天数反算；ECO、IGXE 会优先保存订单页写明的原始日租。若平台改版导致“未解析到有效订单”，不要反复粘贴，先保存原始文本并据此更新 `rental_order_parsers.py`。

### AI 资产导入为何没有新增，或提示存在歧义

先核对预览中的标准中文名、英文 `market_hash_name` 与磨损。AI 资产导入允许不同小数位数按至少 6 位共同精度匹配，并兼容来源把末位截断或四舍五入的情况；唯一命中表示复用旧资产，原稳定 `asset_id` 与订单历史不会丢失，程序同时保留更完整的磨损。两个以上旧资产均满足条件时属于歧义，程序不会按订单日期自动归类，应先手工核对并修正重复资产。`cooldown_hours` 只用于从本次确认导入时刻建立新购 CD 倒计时，订单时间不会参与 CD 推断。

### 市场页没有刷新或显示旧数据

确认 CSQAQ Token、CSFloat API Key、美元汇率、ECO Partner ID 和 RSA 私钥已在设置页保存；检查 `private-data/logs/app.log`。若只想强制重新获取 ECO 全量快照，可等待缓存超过 10 分钟后刷新；不要频繁删除缓存或密集调用接口。

### CSFloat 显示“暂无”或缓存价

- `未配置 API Key`：在 CSFloat 个人资料的 Developer 页面创建 Key，粘贴到设置页并保存。
- `API Key 无效` / `访问被拒绝`：检查 Key 是否复制完整、账户权限及当前网络；程序遇到 401/403 会停止本轮剩余请求。
- `同步等待 Ns`：查看按钮提示中的来源。`额度保留线（剩余 N）` 表示成功响应已接近程序保留的最后 10 次额度；`Retry-After 响应头` 或 `HTTP 429` 表示服务端明确要求暂停。不同工作区不会各自重试，倒计时结束后由全局队列自动继续。
- `排队到下一轮`：当前分类未缓存项目超过每轮 40 条；后续自动或手动刷新会优先处理最久未更新的项目。
- `无一口价在售`：精确英文 `market_hash_name` 当前没有 `buy_now` 的 `listed` 商品；拍卖不会作为回退。
- 有美元价但标记“缓存价”：本轮请求失败或报价超过 10 分钟，仍显示上次成功值供参考，等待冷却结束后刷新即可。
- 汇率来源可在 CSF 单元格提示中查看：正常应为“CSFloat 官网汇率”；接口异常时显示 ECB 或手工备用值。缓存文件为 `exchange_rate_cache.json`，不要为了追求秒级变化频繁删除它。

### 更改字段/数据库时的规则

1. 在 `modules/db_migrations.py` 增加下一个编号迁移，并递增 `CURRENT_SCHEMA_VERSION`；迁移必须可在事务中失败回滚，不得修改已发布迁移。
2. 最新建库 SQL 同时补齐新字段；兼容旧 REAL 金额列时，业务计算仍统一使用整数分。
3. 同时更新仓储读取/写入、同步包格式、兼容 JSON、UI 和本文件。
4. 使用临时数据目录测试“旧版本升级”和“迁移失败回滚”，避免测试写入真实 `private-data/`。
5. 保持 Token、私钥、页面快照与真实订单不进入 Git diff。

## 10. 验证命令

每次改动至少运行：

```powershell
python -m compileall -q main.py modules
python -m ruff check main.py modules tests
$env:QT_QPA_PLATFORM = 'offscreen'
python -m coverage run -m unittest discover -s tests -v
python -m coverage report
git diff --check
```

涉及 UI 时可做无窗口冒烟检查：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
python -c "from PySide6.QtWidgets import QApplication; from main import CS2ManagerApp; a=QApplication([]); w=CS2ManagerApp(); print(w.tabs.count()); w.close()"
```

`tests/test_csfloat_client.py` 使用假响应验证一口价、市场最高求购过滤、缓存、429 和每轮预算，不访问真实接口；`tests/test_market_table_model.py`、`tests/test_market_watch_service.py` 与 `tests/test_image_cache.py` 覆盖行情模型的筛选/排序、报价身份校验、相位归一、旧缓存合并及图片缓存淘汰。测试还断言个人求购工作区及其请求方法已经移除。线上联调只允许只读行情请求。

## 11. 后续开发优先级

1. 将 `main.py` 中四个页面继续拆为独立 QWidget/Presenter；当前已经抽离领域模型、迁移、匹配和主题，但页面编排仍是最大维护单元。
2. 行情页已迁移为 `QAbstractTableModel`；后续可将资产/订单等仍使用 `QTableWidget` 的页面按同一模式逐步迁移。
3. 为市场刷新增加结构化、逐平台的成功/失败结果，让部分失败时可更精确地展示原因。
4. 用用户复制的真实 C5/ECO/IGXE 页面文字持续校准解析器，并为每次平台改版添加脱敏固定样例测试。
