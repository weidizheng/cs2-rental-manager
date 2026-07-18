# CS2 饰品出租管理终端 v3.0

面向个人 CS2 饰品出租管理的本地桌面程序。它把资产、手动同步的出租订单和多平台行情放在一个界面中，同时将密钥、登录状态与个人数据完全留在本地私密目录。

> 这是快速使用说明。架构、缓存、数据迁移、接口字段和排障细节请阅读 [MAINTENANCE.md](MAINTENANCE.md)。

## 当前已实现

- 资产管理：新增、编辑、删除饰品；按平台筛选；计算买入总资产、当前日租估算、在租数量和年化估算。
- 订单历史：同一磨损值的订单自动归组；资产页只显示最新订单，双击可查看首次出租/转租历史。
- 到期提醒：租赁中饰品显示秒级倒计时；剩余不超过 12 小时自动标红。
- 平台订单页：一键用默认浏览器打开 C5、ECO、IGXE 的出租/出售方订单页，复用你现有的正常登录会话。
- C5 默认浏览器同步：通过用户安装的本地扩展读取已打开的 C5 订单页，按订单号去重导入，再以唯一磨损值关联资产。
- 行情大盘：显示 CSQAQ 最低售价、ECO 最低日租，以及 CSQAQ 返回的 C5/悠悠/IGXE 短租和长租价格。
- 本地行情缓存：ECO 全量行情快照缓存 10 分钟；启动时直接读取本地观察列表和缓存，不主动请求网络。
- 行情自动刷新：启动后行情页默认开始每 10 分钟循环刷新 CSQAQ 与 ECO；按钮可暂停/重新开启，表格显示“`N 分钟前更新成功`”。
- 平台跳转：双击价格或物品名打开 CSQAQ、ECO、C5、悠悠或 IGXE 页面；可右键保存自定义链接。
- 本地饰品映射：使用 ByMykel/CSGO-API 的中英文 JSON 自动建立中文名、Steam `market_hash_name` 与图片映射。

## 未实现或刻意限制

- 不自动登录、不绕过验证码、不读取普通 Chrome Profile。
- ECO 和 IGXE 的出租订单页解析器尚未接入；当前默认浏览器同步仅支持 C5。
- 行情页不会直接请求 IGXE API；IGXE 租金来自 CSQAQ 明细。
- 不包含 AI OCR 自动录入功能。
- 浏览器扩展不读取 Cookie、密码或浏览历史；只在你点击桌面端同步后读取已打开的授权订单页文本。

## 快速开始

### 1. 安装依赖

建议使用 Python 3.13（若系统同时安装多个版本，可将下列 `python` 替换为 `py -3.13`）：

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

第二条命令会下载 C5 手动同步所需的 Chromium；未安装时，C5 浏览器功能无法打开。

### 2. 启动

```powershell
python main.py
```

### 3. 配置接口

在“系统与费率设置”填写并保存：

- CSQAQ ApiToken
- ECO Partner ID
- ECO RSA 私钥

配置会写入私有目录，不会提交到 Git。

## 常用操作

### 管理资产和订单

1. 在“资产与出租管理”新增或编辑饰品，尽量填写准确的磨损值和英文 `market_hash_name`。
2. 按 [browser-extension/README.md](browser-extension/README.md) 加载本地扩展，并点击“复制扩展配对令牌”完成配对（仅需首次）。
3. 点击“打开 C5 订单页”，在你已登录的默认浏览器中打开订单页，然后点击“通过浏览器同步 C5”。
4. 程序按订单号去重保存并按磨损值关联资产；双击资产行查看历史订单与到期倒计时。
5. 如果 C5 弹出安全验证，直接在该浏览器标签页人工完成，然后再次点击同步。

### 查看行情

1. 在“一览式大盘行情”输入饰品名称并“搜索并添加”；首次没有行情缓存时，程序会从已有资产生成观察列表。
2. 点击“刷新行情”获取一轮 CSQAQ/ECO 数据。
3. 行情自动刷新启动时默认开启；点击倒计时按钮可暂停，再次点击重新开启。
4. 双击名称或价格打开对应平台；右键可设置/清除该物品的自定义链接。

ECO 返回的是全量价格快照，正常情况下约数万条。程序会缓存它，缓存有效期内刷新不会重复下载全量数据。

## 私密数据与换电脑

所有个人数据默认保存在项目根目录的 `private-data/`，其中包含：

- `app.db`：资产、订单、设置的 SQLite 主库
- `items.json`、`configs.json`：可携带备份（含敏感配置）
- `market_cache.json`、`eco_market_cache.db`：行情缓存、自定义链接和 ECO 全量快照
- `schema-source/`、`cs2_items_schema.json`：本地饰品映射资源
- `browser-profiles/c5game/`：隔离的 C5 登录状态
- `browser-snapshots/`、`logs/`：C5 页面快照与日志

换电脑时：克隆私有仓库、安装依赖后，将**完整 `private-data/` 文件夹**从自己的加密云盘复制到新电脑项目根目录即可。该目录已被 `.gitignore` 排除，绝不要提交到 GitHub。

如需把数据放到其他私密位置，可设置环境变量：

```powershell
$env:CS2_RENTAL_DATA_DIR = 'D:\Private\cs2-rental-manager'
```

## 项目结构

```text
main.py                 # PySide6 界面、资产/行情逻辑与定时器
modules/                # 数据库、接口客户端、缓存、映射和 C5 浏览器适配器
browser-extension/      # 与已登录浏览器配对的本地 Chrome/Edge 扩展
private-data/           # 私有运行数据（Git 忽略）
requirements.txt        # Python 依赖
MAINTENANCE.md          # 维护、迁移、缓存和排障说明
GITHUB_SETUP.md         # 私有 GitHub 仓库基础操作
```

## 开发前检查

```powershell
python -m compileall -q main.py modules
git diff --check
git status
```

提交代码前确认 `private-data/`、密钥、Token、浏览器 Profile、HTML 快照和日志均未出现在 `git status` 中。

## 维护文档

- [维护与实现说明](MAINTENANCE.md)：数据模型、缓存机制、接口、链接规律、排障与后续开发建议。
- [GitHub 设置说明](GITHUB_SETUP.md)：私有仓库与同步操作。
