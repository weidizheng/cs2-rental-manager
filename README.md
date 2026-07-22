# CS2 饰品出租管理终端 v3.1

面向个人 CS2 饰品出租管理的本地桌面程序。它把资产、手动同步的出租订单和多平台行情放在一个界面中，同时将密钥、登录状态与个人数据完全留在本地私密目录。

> 这是快速使用说明。架构、缓存、数据迁移、接口字段和排障细节请阅读 [MAINTENANCE.md](MAINTENANCE.md)。

## 当前已实现

- 资产管理：新增、编辑、软删除饰品；删除后 10 秒内可从底部提示条撤销。支持名称/磨损搜索、状态/平台筛选、列排序和自选显示列；同时计算买入总资产、当前日租估算、累计净收益、在租数量和年化估算。成本单元格支持右键按百分比计入手续费（输入 `1` 即增加 1%）；资产表同时显示成本相对 CSQAQ 全网最低售价的价差，以及当前原始日租相对所在出租平台最低租金的价差。
- 订单剪贴板导入：复制 C5、ECO 或 IGXE 的出租订单页面文本后，一键识别平台、解析订单并按订单号去重保存。
- 订单历史：订单保存稳定的资产 ID 关联；唯一磨损自动匹配，磨损冲突时不猜测，可在导入预览中手工选择资产。资产页只显示该资产的最新订单，双击可查看完整出租历史、原始日租、订单金额和净收益。
- 收益核算：可为 C5、ECO、IGXE 分别配置首次出租/转租费率；资产页显示本单与累计净收益，统计卡片按净日租计算日收益和年化。C5 单笔订单详情中明确的转租奖励（待发放/已发放）会作为额外成本扣除；“最高奖励”不会被当作已支出。
- 到期提醒：租赁中饰品显示“剩 X天X小时X分”；剩余不超过 12 小时自动标红。刚买入但仍在交易 CD 的资产可单独填写剩余小时，程序保存绝对截止时间并持续倒计时。
- 桌面工作台：40px 无边框自定义标题栏提供同步状态及最小化/最大化/关闭；左侧导航在资产总览、大盘行情、CSF 求购之间切换，系统设置由底部小按钮单独打开。`W/S` 循环切换三个工作区，行情页用 `A/D` 切分类；输入控件与表格仍保留原生按键行为。
- 平台订单页：一键用默认浏览器打开 C5、ECO、IGXE 的出租/出售方订单页，复用你现有的正常登录会话。
- 导入确认预览：剪贴板内容只会先解析成可核对的订单表；点击“确认导入”后才写入本地数据。
- AI 协作批量添加大盘：软件提供固定提示词；将提示词和库存截图交给任意可识图 AI，再将其 JSON 结果粘贴回软件预览、校验并批量加入本地观察列表。图片不会上传或保存到本软件。
- AI 协作批量导入资产：资产页提供另一套截图提示词与 JSON 预览，可一次导入每件饰品的中文名、英文市场名、磨损、成本、平台和新购 CD；导入后沿用现有成本、行情价差、租金价差与收益计算。
- Google Drive 手动同步：生成口令加密的 `.cs2sync` 文件，同步出租订单、行情收藏分类/观察品和 API 配置；另一台电脑从 Google Drive 网页下载后合并导入。本机生成目录只保留最新同步包，导入前备份保留最近 3 份。
- 本地模糊搜索：大盘“搜索并添加”支持中文、英文、无 `★` 名称和常见别名；例如“蝴蝶刀”会给出候选列表，“伽马多普勒蝴蝶刀”会匹配 `★ Butterfly Knife | Gamma Doppler` 的不同磨损/StatTrak 选项，确认选择后才加入当前分类。
- 行情大盘：可创建、重命名、删除并切换“出租品”“Wishlist”等独立观察分类；每个分类独立保存饰品、行情缓存与自定义链接。列表显示 CSQAQ 国内最低售价、按 CSFloat 汇率换算的人民币底价、ECO 最低日租，以及 CSQAQ 返回的 C5/悠悠/IGXE 短租和长租价格。
- CSFloat 求购监测：读取账户可用/待结算余额与自己的有效求购；逐单比较市场最高求购，统计最近成交价与自己的求购价在 2%/5% 内的样本。页面以实时/缓存的 USD/CNY 汇率优先显示人民币并附美元；物品始终显示本地映射的中文名，单击名称直达对应饰品。顶部按钮打开 `https://csfloat.com/profile`，软件不创建、修改或撤销求购。
- 本地行情缓存：观察分类与观察品以 SQLite 持久保存，`market_cache.json` 只承担可重建报价缓存。ECO 全量行情快照缓存 10 分钟，日常刷新只从 SQLite 读取观察品；CSFloat 按饰品缓存 10 分钟。启动时只读取本地数据，不主动请求网络。
- 全局顺序同步：资产、大盘和求购页顶部的“数据同步”是同一个开关。切换工作区、`F5` 和“立即同步”都只进入同一个调度队列：到期的 CSFloat 求购先读取，再按最久未更新顺序逐件刷新全部行情分类；不会从另一个页面另起刷新任务。
- 平台跳转：双击价格或物品名打开 CSQAQ、CSFloat、ECO、C5、悠悠或 IGXE 页面；可右键保存自定义链接。
- 本地饰品映射：使用 ByMykel/CSGO-API 的中英文 JSON 自动建立中文名、Steam `market_hash_name` 与图片映射。

## 未实现或刻意限制

- 不自动登录、不绕过验证码、不读取普通 Chrome Profile。
- 当前不启用浏览器扩展或隔离浏览器读取；三个平台统一通过“打开订单页 + 剪贴板导入”操作。
- 行情页不会直接请求 IGXE API；IGXE 租金来自 CSQAQ 明细。
- CSFloat 当前按完整 `market_hash_name` 查询；Steam 名称相同的多普勒特殊相位会共享“同名最低一口价”。P1/P3 本来就在行情页合并；若以后要把 Ruby/Sapphire 等拆成严格相位价，需要再按 `paint_index` 查询。
- 不直接调用 AI/OCR 服务，也不在软件内上传截图；资产和行情的 AI 协作导入都由用户自行把提示词与截图交给 AI，再粘贴 JSON 回软件校验。

## 快速开始

### 1. 安装依赖

建议使用 Python 3.13（若系统同时安装多个版本，可将下列 `python` 替换为 `py -3.13`）：

```powershell
python -m pip install -r requirements.txt
```

### 2. 启动

```powershell
python main.py
```

### 3. 配置接口

在“系统与费率设置”填写并保存：

- CSQAQ ApiToken
- CSFloat Developer API Key
- 自动 CSFloat 官网汇率（失败回退 ECB），以及网络均不可用时的手工备用值
- ECO Partner ID
- ECO RSA 私钥
- C5、ECO、IGXE 的首次出租与转租费率（例如 `0.15` 表示 15%）

配置会写入私有目录，不会提交到 Git。CSQAQ Token、CSFloat API Key 与 ECO 凭据在 Windows 上会用当前用户的 DPAPI 加密后落盘；输入框默认隐藏内容，可临时勾选显示。

## 常用操作

### 管理资产和订单

1. 在“资产与出租管理”新增或编辑饰品，务必填写准确的唯一磨损值；英文 `market_hash_name` 用于行情匹配。
2. 也可点击“AI 批量导入”，复制提示词并把库存/购买记录截图交给可识图 AI；将返回 JSON 粘贴回来，预览通过后“一键导入资产”。新购 CD 需填写截图中的剩余小时。
3. 在任一平台的出租订单页全选并复制文本，回到程序点击“从剪贴板导入订单”。一次只粘贴一个平台；C5、ECO、IGXE 会自动识别。
4. 程序按 `(平台, 订单号)` 去重保存。只有唯一匹配时才按标准化磨损值自动关联；有多个候选或无法匹配时，在预览的“关联资产”列手工指定，程序会保存稳定的资产 ID。
5. 在“系统与费率设置”按真实平台规则填写费率；导入预览与订单历史保留“日租（原价）”，资产表的“日租（净）”、本单/累计净收益、日收益和年化会自动扣费。
6. 导入按钮会先展示平台、订单号、磨损、起止时间、租期、日租、订单金额、状态和关联资产；核对无误后点击“确认导入”。取消不会写入任何订单。

### 查看行情

1. 在“一览式大盘行情”先选择或新建观察分类，再输入饰品名称并“搜索并添加”；可输入中文、英文或不含 `★` 的简称。程序先显示本地候选，选择后才加入；首次没有行情缓存时，默认“出租品”会从已有资产生成观察列表。
2. 点击“立即同步”会请求全局队列执行下一步，不会绕过服务端频控。CSFloat 只取 `type=buy_now` 且仍为 `listed` 的最低固定售价，拍卖 `auction` 不参与比较。
3. 全局数据同步启动时默认开启；资产、大盘、求购页的同名按钮状态联动，点击任意一个都可暂停或继续。
4. 双击名称或价格打开对应平台；右键可设置/清除该物品的自定义链接。
5. 使用表格上方筛选框快速定位观察品；点击表头排序，“显示列”可隐藏暂时不用的报价列。

常用快捷键：`W/S` 循环切换资产、大盘、求购工作区，位于大盘时 `A/D` 切换观察分类；`Ctrl+N` 新增资产，`Ctrl+F` 聚焦当前页搜索框，`F5` 请求一次全局同步。`Alt+1/2/3/4` 与 `Alt+←/→` 仍作为辅助快捷键保留。焦点位于输入框、下拉框或多行文本时，WASD 不触发导航。

ECO 返回的是全量价格快照，正常情况下约数万条。程序会缓存它，缓存有效期内刷新不会重复下载全量数据。

CSFloat 的公开文档没有给出一个适用于所有接口的固定额度，限额以响应头为准。程序把大盘、求购和切页触发视为同一个全局请求池：1.25 秒只是最低保护值，收到 `RateLimit-Remaining/Reset` 后会自动放慢全部页面的请求间隔；收到 `Retry-After` 或 HTTP 429 后暂停整个调度队列，并在按钮中显示反馈来源与剩余时间。人民币换算优先使用 CSFloat 前端自己的 `/api/v1/meta/exchange-rates`，缓存 12 小时；该接口不可用时改用欧洲央行最近工作日的 EUR/USD 与 EUR/CNY 交叉汇率，最后才使用手工备用值。

账户、自己的求购和近期成交端点未收录在 CSFloat 公开 API 文档中，可能随网站更新而变化，因此本软件保持只读。建议价格按照 CSFloat FAQ 的美元分档增量计算：低于 `$5` 为 `$0.01`、`$5–10` 为 `$0.05`、`$10–100` 为 `$0.10`、`$100–500` 为 `$1`、`$500–1,000` 为 `$5`、高于 `$1,000` 为 `$10`；最终以官网输入框校验结果为准。

### 使用 Google Drive 手动同步

1. 在“系统设置 → Google Drive 手动同步”点击“生成加密同步包”，输入至少 8 位口令；文件生成在 `private-data/cloud-sync/outbox/`。
2. 点击“打开 Google Drive 网页”，将 `CS2RentalSync.cs2sync` 上传到自己的云盘。同步包整体使用 AES-256-GCM 加密，口令不会保存到软件或文件中。
3. 另一台电脑从 Google Drive 网页下载文件，放入 `private-data/cloud-sync/inbox/`，再点击“导入下载的同步包”并输入相同口令。
4. 导入按 `(平台, 订单号)` 合并订单，按分类与观察品标识合并收藏，不删除本机独有数据；导入前会在 `private-data/cloud-sync/backups/` 自动生成加密备份。

忘记同步口令时无法恢复同步包内容，只能在原电脑重新生成。不要把口令与同步包存放在同一个云盘目录。

## 私密数据与换电脑

所有个人数据默认保存在项目根目录的 `private-data/`，其中包含：

- `app.db`：资产、订单、设置、稳定订单关联、观察分类和观察品的 SQLite 主库
- `items.json`、`configs.json`：原子写入的恢复备份；`configs.json` 中的接口凭据为 Windows 当前用户加密值
- `market_cache.json`、`eco_market_cache.db`：可重建行情缓存、自定义链接和 ECO 全量快照
- `exchange_rate_cache.json`：CSFloat/ECB 汇率及网络失败冷却缓存
- `schema-source/`、`cs2_items_schema.json`：本地饰品映射资源
- `browser-profiles/c5game/`：旧版浏览器读取功能留下的登录档案；当前界面不使用，保留仅用于避免误删历史登录状态
- `logs/`：轮转日志
- `cloud-sync/`：Google Drive 手动同步的收件箱、发件箱与导入前加密备份

换电脑时：克隆私有仓库、安装依赖后，可复制完整 `private-data/` 以迁移资产、订单和缓存。DPAPI 凭据只能由原 Windows 用户解密，因此接口配置请通过口令加密的 `.cs2sync` 包迁移，或在新电脑重新填写。该目录已被 `.gitignore` 排除，绝不要提交到 GitHub。

如需把数据放到其他私密位置，可设置环境变量：

```powershell
$env:CS2_RENTAL_DATA_DIR = 'D:\Private\cs2-rental-manager'
```

## 项目结构

```text
main.py                    # PySide6 页面编排、表格渲染与用户交互
modules/domain_models.py   # 表单领域模型、校验和金额换算
modules/dashboard_service.py # 首页收益、租期、状态和排序计算
modules/csfloat_buy_analysis.py # CSFloat 求购价档与成交信号分析
modules/rental_matching.py # 稳定订单关联与历史索引
modules/db_manager.py      # SQLite 仓储、事务与兼容备份
modules/db_migrations.py   # 有序、可重复执行的数据库迁移
modules/ui_theme.py        # 集中维护的应用主题
modules/secret_store.py    # Windows DPAPI 本地凭据保护
modules/atomic_io.py       # JSON 原子替换写入
modules/cloud_sync.py      # 加密同步包导出、验证与事务合并
private-data/           # 私有运行数据（Git 忽略）
requirements.txt        # Python 依赖
requirements-dev.txt    # 测试、静态检查和打包依赖
MAINTENANCE.md          # 维护、迁移、缓存和排障说明
GITHUB_SETUP.md         # 私有 GitHub 仓库基础操作
```

## 开发前检查

先安装开发依赖：`python -m pip install -r requirements-dev.txt`。

```powershell
python -m compileall -q main.py modules
python -m ruff check main.py modules test_*.py
$env:QT_QPA_PLATFORM = 'offscreen'
python -m coverage run -m unittest discover -v
python -m coverage report
git diff --check
git status
```

GitHub Actions 会在 Windows/Python 3.13 上执行同一套编译、Ruff、测试和覆盖率检查。运行 `build_exe.bat` 会先测试，再通过已跟踪的 PyInstaller spec 生成 `release/CS2租赁管理.exe`；`build/`、`release/` 与 `dist/` 不进入 Git。

提交代码前确认 `private-data/`、密钥、Token、浏览器 Profile、HTML 快照和日志均未出现在 `git status` 中。

## 维护文档

- [维护与实现说明](MAINTENANCE.md)：数据模型、缓存机制、接口、链接规律、排障与后续开发建议。
- [GitHub 设置说明](GITHUB_SETUP.md)：私有仓库与同步操作。
