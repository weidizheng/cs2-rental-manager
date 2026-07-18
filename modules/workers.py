import logging
import time
from PySide6.QtCore import QObject, Signal, QThread

from modules.csqaq_client import CSQAQClient
from modules.eco_client import ECOClient
from modules.igxe_client import IGXEClient
from modules.image_cache import ImageCache

logger = logging.getLogger("CS2Rental")


class ApiWorker(QObject):
    """
    通用异步 API Worker，在 QThread 中运行，通过信号安全返回结果到 GUI 线程。

    用法:
        thread = QThread()
        worker = ApiWorker()
        worker.moveToThread(thread)
        thread.started.connect(lambda: worker.batch_price_csqaq(token, names))
        worker.finished.connect(self.on_result)
        worker.error.connect(self.on_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
    """

    finished = Signal(object)  # 成功时返回 (tag, data) 元组
    error = Signal(str)        # 失败时返回错误消息字符串

    def __init__(self):
        super().__init__()
        self._is_canceled = False

    def cancel(self):
        """取消正在进行的操作（需在任务函数中检查）"""
        self._is_canceled = True

    # ────────────── CSQAQ 批量价格查询 ──────────────

    def batch_price_csqaq(self, token: str, market_hash_name_list: list):
        """
        CSQAQ 批量查询：通过 marketHashName 列表获取多平台在售底价。
        接口: POST /api/v1/goods/getPriceByMarketHashName
        返回 tag="batch_price"

        返回格式（兼容旧版 consumers）:
            {"success": True/False, "data": {mhn: {...}}}
        """
        try:
            client = CSQAQClient(token)
            raw = client.get_prices_by_hash_names(market_hash_name_list)
            # 包装为兼容格式
            result = {
                "success": bool(raw),
                "data": raw,
            }
            if not self._is_canceled:
                self.finished.emit(("batch_price", result))
        except Exception as e:
            self.error.emit(f"CSQAQ 批量价格查询失败: {e}")

    # ────────────── ECO ──────────────

    def fetch_eco_hash_price_list(self, partner_id: str, rsa_key: str, market_hash_name: str):
        """
        ECO 全量行情查询（含起租价），通过 GetHashNameAndPriceList 获取。
        返回 tag="eco_rental"

        返回格式（兼容旧版 consumers）:
            {
                "success": True/False,
                "min_rent": 0.0,
                "listings": [],
                "eco_sell_price": 0.0,
                "style_name": "",
            }
        """
        try:
            client = ECOClient(partner_id=partner_id, private_key_str=rsa_key)
            mapping = client.get_hash_name_and_price_list()
            item = mapping.get(market_hash_name, {})

            result = {
                "success": bool(item),
                "min_rent": item.get("eco_rent_price", 0.0),
                "listings": [],
                "eco_sell_price": item.get("eco_sell_price", 0.0),
                "style_name": item.get("style_name", ""),
            }
            if not self._is_canceled:
                self.finished.emit(("eco_rental", result))
        except Exception as e:
            self.error.emit(f"ECO 全量行情查询失败: {e}")

    def fetch_eco_goods_detail(self, partner_id: str, rsa_key: str, market_hash_name_list: list):
        """
        ECO 批量获取饰品详情（图片 URL + PaintIndexLabel）。
        接口: POST /Api/Market/BatchGetGoodsDetail
        返回 tag="eco_goods_detail"
        """
        try:
            client = ECOClient(partner_id=partner_id, private_key_str=rsa_key)
            result = client.batch_get_goods_detail(market_hash_name_list)
            if not self._is_canceled:
                self.finished.emit(("eco_goods_detail", result))
        except Exception as e:
            self.error.emit(f"ECO 饰品详情查询失败: {e}")

    # ────────────── IGXE ──────────────

    def search_igxe_product(self, keyword: str):
        """IGXE 商品搜索，获取 product_id"""
        try:
            client = IGXEClient()
            result = client.search_product(keyword)
            if not self._is_canceled:
                self.finished.emit(("igxe_search", result))
        except Exception as e:
            self.error.emit(f"IGXE 搜索失败: {e}")

    def fetch_igxe_lease(self, product_id):
        """IGXE 租赁行情（含多条磨损/租金列表）"""
        try:
            client = IGXEClient()
            result = client.get_lease_market_info(product_id)
            if not self._is_canceled:
                self.finished.emit(("igxe_lease", result))
        except Exception as e:
            self.error.emit(f"IGXE 行情失败: {e}")


class MarketRefreshWorker(QObject):
    """
    顺序队列行情刷新 Worker：在单个 QThread 中依次刷新所有饰品的行情，
    严格遵循 CSQAQ 1.05s / ECO 0.3s 的请求间隔，避免并发频控。

    信号:
        progress: (current, total, message) 进度更新
        row_updated: (row_index) 单行刷新完成
        finished: () 全部完成
        error: (message) 错误消息
    """

    progress = Signal(int, int, str)  # current, total, message
    row_updated = Signal(int)         # row index
    finished = Signal()
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self._is_canceled = False

    def cancel(self):
        self._is_canceled = True

    def refresh_all(self, token: str, eco_partner: str, eco_rsa: str,
                    tracked_items: list, build_mhn_fn):
        """
        顺序刷新所有饰品行情。

        Args:
            token: CSQAQ API Token
            eco_partner: ECO PartnerId
            eco_rsa: ECO RSA 私钥
            tracked_items: _market_tracked_items 列表
            build_mhn_fn: 用于构建 market_hash_name 的函数
        """
        total = len(tracked_items)
        if total == 0:
            self.finished.emit()
            return

        # 1. 收集所有 market_hash_name
        all_names = [build_mhn_fn(e) for e in tracked_items]

        # 2. CSQAQ 批量价格查询（一次性批量请求）
        if token and not self._is_canceled:
            self.progress.emit(0, total, "正在查询 CSQAQ 批量价格...")
            try:
                csqaq_client = CSQAQClient(token)
                # get_prices_by_hash_names 返回扁平 dict {mhn: {...}}
                price_data = csqaq_client.get_prices_by_hash_names(all_names)
                if price_data and not self._is_canceled:
                    for entry in tracked_items:
                        mhn = build_mhn_fn(entry)
                        prices = price_data.get(mhn, {})
                        if prices:
                            entry["csqaq_price"] = float(prices.get("min_sell_price", prices.get("buff_price", 0.0)))
                            entry.setdefault("detail", {}).update({
                                "buff_price": prices.get("buff_price", 0.0),
                                "yy_price": prices.get("yy_price", 0.0),
                                "steam_price": prices.get("steam_price", 0.0),
                                "min_sell_price": prices.get("min_sell_price", 0.0),
                                "name_zh": prices.get("name_zh", ""),
                            })
            except Exception as e:
                self.error.emit(f"CSQAQ 批量查询异常: {e}")

        # 3. 批量获取 ECO 全量行情（起租价 RentGoodsBottomPrice + 在售价 Price）
        #    ECO BatchGetGoodsDetail 暂不使用，图片/相位等信息由其他来源补充
        eco_price_mapping = {}
        if eco_partner and eco_rsa and not self._is_canceled:
            self.progress.emit(0, total, "正在获取 ECO 全量行情...")
            try:
                eco_client = ECOClient(partner_id=eco_partner, private_key_str=eco_rsa)
                eco_price_mapping = eco_client.get_hash_name_and_price_list()
                logger.info(
                    f"[ECO] 全量行情获取完成，共 {len(eco_price_mapping)} 个饰品"
                )
            except Exception as e:
                self.error.emit(f"ECO 全量行情异常: {e}")

        # 5. 逐个查询 IGXE 租赁（严格顺序，带间隔）
        for idx, entry in enumerate(tracked_items):
            if self._is_canceled:
                break

            mhn = build_mhn_fn(entry)
            self.progress.emit(idx + 1, total, f"正在刷新 ({idx+1}/{total}) {mhn[:40]}...")

            # 5a. 从 ECO 全量行情缓存中提取起租价
            if eco_price_mapping and not self._is_canceled:
                eco_item = eco_price_mapping.get(mhn, {})
                # 调试日志：帮助排查匹配问题
                if not eco_item:
                    logger.warning(f"[DEBUG] ECO 未匹配: mhn='{mhn}'")
                    # 尝试模糊匹配：打印前5个可能的key
                    possible = [k for k in list(eco_price_mapping.keys())[:5] if mhn.split('|')[0].strip() in k]
                    if possible:
                        logger.warning(f"[DEBUG] 可能的 ECO key: {possible}")
                else:
                    logger.info(f"[DEBUG] ECO 匹配成功: mhn='{mhn}', rent={eco_item.get('eco_rent_price', 0.0)}")
                    entry["eco_min_rent"] = eco_item.get("eco_rent_price", 0.0)
                    entry.setdefault("detail", {})["eco_sell_price"] = eco_item.get("eco_sell_price", 0.0)
                    entry.setdefault("detail", {})["eco_style_name"] = eco_item.get("style_name", "")

            # 5b. IGXE 租赁查询（搜索 + 获取详情）
            if not self._is_canceled:
                try:
                    igxe_client = IGXEClient()
                    search_result = igxe_client.search_product(mhn)
                    if search_result.get("success") and search_result.get("results"):
                        product_id = search_result["results"][0].get("product_id")
                        if product_id:
                            lease_result = igxe_client.get_lease_market_info(product_id)
                            if lease_result.get("success"):
                                entry["igxe_min_rent"] = lease_result.get("min_rent", 0.0)
                                entry.setdefault("detail", {})["igxe_listings"] = lease_result.get("listings", [])
                except Exception as e:
                    self.error.emit(f"IGXE 查询异常 ({mhn}): {e}")

            # Download the standard schema image once; subsequent refreshes use
            # the local cache and do not download it again.
            image_url = entry.get("image_url", "")
            if image_url and not self._is_canceled:
                ImageCache.download(mhn, image_url)

            # 通知 GUI 线程该行已更新
            if not self._is_canceled:
                self.row_updated.emit(idx)

        if not self._is_canceled:
            self.finished.emit()
