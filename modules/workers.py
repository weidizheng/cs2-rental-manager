import logging
import math
import time
from PySide6.QtCore import QObject, Signal, Slot

from modules.csqaq_client import CSQAQClient
from modules.csfloat_client import CSFloatClient
from modules.exchange_rate_client import ExchangeRateClient
from modules.eco_client import ECOClient
from modules.igxe_client import IGXEClient
from modules.image_cache import ImageCache

logger = logging.getLogger("CS2Rental")
CSQAQ_DETAIL_CACHE_TTL_SECONDS = 10 * 60
CSFLOAT_CACHE_TTL_SECONDS = 10 * 60
CSFLOAT_BUY_QUOTE_TTL_SECONDS = 30 * 60
CSFLOAT_MAX_REQUESTS_PER_REFRESH = 40


def _number(value):
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def csfloat_cny_display_price(price_cents, usd_cny_rate) -> float:
    """Mirror CSFloat's display conversion: round upward to one CNY cent."""
    try:
        cents = int(price_cents or 0)
        rate = float(usd_cny_rate or 0)
    except (TypeError, ValueError):
        return 0.0
    if cents <= 0 or rate <= 0:
        return 0.0
    return math.ceil(cents * rate) / 100.0


def csfloat_quote_is_fresh(entry: dict, market_hash_name: str, now: float | None = None) -> bool:
    """A quote is reusable only for the same exact item name and within its TTL."""
    fetched_at = float(entry.get("csfloat_fetched_at", 0) or 0)
    query_name = str(entry.get("csfloat_query_mhn") or "")
    current_time = time.time() if now is None else now
    return (
        fetched_at > 0
        and query_name == market_hash_name
        and current_time - fetched_at < CSFLOAT_CACHE_TTL_SECONDS
    )


def csfloat_buy_quote_is_fresh(
    entry: dict,
    market_hash_name: str,
    now: float | None = None,
) -> bool:
    """Reuse the slower-moving highest buy quote for thirty minutes."""
    fetched_at = float(entry.get("csfloat_buy_fetched_at", 0) or 0)
    query_name = str(
        entry.get("csfloat_buy_query_mhn")
        or entry.get("csfloat_query_mhn")
        or ""
    )
    current_time = time.time() if now is None else now
    return (
        fetched_at > 0
        and query_name == market_hash_name
        and current_time - fetched_at < CSFLOAT_BUY_QUOTE_TTL_SECONDS
    )


def build_csqaq_market_detail(info: dict) -> dict:
    """Normalize documented CSQAQ per-item fields for the market grid."""
    sell_sources = {
        "BUFF": _number(info.get("buff_sell_price")),
        "悠悠有品": _number(info.get("yyyp_sell_price")),
        "C5": _number(info.get("c5_sell_price")),
        "IGXE": _number(info.get("igxe_sell_price")),
        "ECO": _number(info.get("eco_sell_price")),
        "R8": _number(info.get("r8_sell_price")),
    }
    valid_sources = {name: price for name, price in sell_sources.items() if price > 0}
    lowest_platform, lowest_price = ("", 0.0)
    if valid_sources:
        lowest_platform, lowest_price = min(valid_sources.items(), key=lambda item: item[1])

    return {
        "csqaq_good_id": info.get("id", info.get("good_id", "")),
        "csqaq_min_sell_price": lowest_price,
        "csqaq_min_sell_platform": lowest_platform,
        "c5_id": str(info.get("c5_id") or ""),
        "yyyp_id": str(info.get("yyyp_id") or ""),
        "igxe_id": str(info.get("igxe_id") or ""),
        "eco_id": str(info.get("eco_id") or ""),
        "c5_short_rent": _number(info.get("c5_lease_price")),
        "c5_long_rent": _number(info.get("c5_long_lease_price")),
        "yyyp_short_rent": _number(info.get("yyyp_lease_price")),
        "yyyp_long_rent": _number(info.get("yyyp_long_lease_price")),
        "igxe_short_rent": _number(info.get("igxe_lease_price")),
        "igxe_long_rent": _number(info.get("igxe_long_lease_price")),
        "c5_lease_num": int(info.get("c5_lease_num") or 0),
        "yyyp_lease_num": int(info.get("yyyp_lease_num") or 0),
        "igxe_lease_num": int(info.get("igxe_lease_num") or 0),
    }


class ApiWorker(QObject):
    """
    通用异步 API Worker，在 QThread 中运行，通过信号安全返回结果到 GUI 线程。

    调用方应先通过 ``configure_task`` 保存任务，再把 ``thread.started``
    连接到绑定槽 ``run``。不要用 lambda 包装 started 信号，否则 Python
    回调可能在创建 lambda 的 GUI 线程执行。
    """

    finished = Signal(object)  # 成功时返回 (tag, data) 元组
    error = Signal(str)        # 失败时返回错误消息字符串
    task_completed = Signal()

    def __init__(self):
        super().__init__()
        self._is_canceled = False
        self.eco_status_text = ""
        self._task = None

    def configure_task(self, task):
        """Store one task before moving the worker to its background thread."""
        if not callable(task):
            raise TypeError("task must be callable")
        self._task = task

    @Slot()
    def run(self):
        """Run the configured callable in this QObject's thread affinity."""
        task = self._task
        self._task = None
        try:
            if task is None:
                self.error.emit("API Worker 未配置任务")
                return
            task(self)
        except Exception as exc:
            logger.exception("Unhandled API worker task exception")
            self.error.emit(f"API 后台任务异常: {exc}")
        finally:
            # Lifecycle completion is separate from the public result signal:
            # API methods emit either ``finished`` or ``error``, while this
            # signal guarantees that the QThread is also stopped on failures.
            self.task_completed.emit()

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

    def fetch_eco_hash_price_list(self, partner_id: str, rsa_key: str, market_hash_name: str, phase: str = ""):
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
            item = client.get_price(market_hash_name, phase)

            result = {
                "success": bool(item),
                "min_rent": item.get("eco_rent_price", 0.0),
                "listings": [],
                "eco_sell_price": item.get("eco_sell_price", 0.0),
                "style_name": item.get("style_name", ""),
                "cache_source": client.last_price_source,
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


class ApiWorkerCallbackRelay(QObject):
    """Deliver arbitrary Python callbacks in the relay's (GUI) thread."""

    def __init__(self, on_finished, on_error, parent=None):
        super().__init__(parent)
        self._on_finished = on_finished
        self._on_error = on_error

    @Slot(object)
    def handle_finished(self, result):
        self._on_finished(result)

    @Slot(str)
    def handle_error(self, message):
        self._on_error(message)


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
    result_ready = Signal(object)     # isolated refreshed entries + status text
    finished = Signal()
    error = Signal(str)
    task_completed = Signal()

    def __init__(self):
        super().__init__()
        self._is_canceled = False
        self.eco_status_text = ""
        self.csfloat_status_text = ""
        self._refresh_args = None

    def configure_refresh(self, token: str, eco_partner: str, eco_rsa: str,
                          tracked_items: list, build_mhn_fn,
                          force_eco: bool = False,
                          csfloat_api_key: str = "",
                          usd_cny_rate: float = 7.2,
                          auto_usd_cny_rate: bool = False,
                          fast_only: bool = False,
                          force_csfloat: bool = False):
        """Store immutable launch arguments before entering the worker thread."""
        self._refresh_args = (
            token, eco_partner, eco_rsa, tracked_items, build_mhn_fn, force_eco,
            csfloat_api_key, usd_cny_rate, auto_usd_cny_rate,
            fast_only, force_csfloat,
        )

    @Slot()
    def run_refresh(self):
        """Start a configured refresh in the worker object's own QThread."""
        refresh_args = self._refresh_args
        self._refresh_args = None
        try:
            if refresh_args is None:
                self.error.emit("行情刷新 Worker 未配置任务")
                return
            self.refresh_all(*refresh_args)
        except Exception as exc:
            logger.exception("Unhandled market refresh task exception")
            self.error.emit(f"行情刷新异常: {exc}")
        finally:
            self.task_completed.emit()

    def cancel(self):
        self._is_canceled = True

    def refresh_all(self, token: str, eco_partner: str, eco_rsa: str,
                    tracked_items: list, build_mhn_fn, force_eco: bool = False,
                    csfloat_api_key: str = "", usd_cny_rate: float = 7.2,
                    auto_usd_cny_rate: bool = False,
                    fast_only: bool = False,
                    force_csfloat: bool = False):
        """
        顺序刷新所有饰品行情。

        Args:
            token: CSQAQ API Token
            eco_partner: ECO PartnerId
            eco_rsa: ECO RSA 私钥
            tracked_items: _market_tracked_items 列表
            build_mhn_fn: 用于构建 market_hash_name 的函数
            force_eco: True 时忽略本地 ECO 缓存并请求完整快照
            csfloat_api_key: CSFloat developer key; only read-only listing GETs are used
            usd_cny_rate: manual USD-to-CNY fallback for domestic price comparison
            auto_usd_cny_rate: prefer CSFloat's cached website display rate,
                with ECB and the manual setting as fallbacks
        """
        total = len(tracked_items)
        if total == 0:
            self.result_ready.emit({
                "items": tracked_items,
                "eco_status_text": self.eco_status_text,
                "csfloat_status_text": self.csfloat_status_text,
            })
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
                            entry["csqaq_min_sell_price"] = entry["csqaq_price"]
                            batch_sources = {
                                "BUFF": _number(prices.get("buff_price")),
                                "悠悠有品": _number(prices.get("yy_price")),
                            }
                            valid_batch_sources = {
                                name: price for name, price in batch_sources.items() if price > 0
                            }
                            if valid_batch_sources:
                                entry["csqaq_min_sell_platform"] = min(
                                    valid_batch_sources.items(), key=lambda item: item[1]
                                )[0]
                            entry.setdefault("detail", {}).update({
                                "buff_price": prices.get("buff_price", 0.0),
                                "yy_price": prices.get("yy_price", 0.0),
                                "steam_price": prices.get("steam_price", 0.0),
                                "min_sell_price": prices.get("min_sell_price", 0.0),
                                "name_zh": prices.get("name_zh", ""),
                            })
                            good_id = prices.get("good_id")
                            detail_is_fresh = (
                                time.time() - float(entry.get("csqaq_detail_fetched_at", 0) or 0)
                                <= CSQAQ_DETAIL_CACHE_TTL_SECONDS
                                and bool(entry.get("eco_id"))
                                and "csqaq_min_sell_price" in entry
                            )
                            if good_id and not detail_is_fresh and not fast_only:
                                detail_info = csqaq_client.get_good_detail(good_id)
                                if detail_info:
                                    normalized = build_csqaq_market_detail(detail_info)
                                    entry.update(normalized)
                                    entry["csqaq_price"] = normalized["csqaq_min_sell_price"]
                                    entry.setdefault("detail", {}).update(normalized)
                                    entry["csqaq_detail_fetched_at"] = int(time.time())
            except Exception as e:
                self.error.emit(f"CSQAQ 批量查询异常: {e}")

        # 3. 批量获取 ECO 全量行情（起租价 RentGoodsBottomPrice + 在售价 Price）
        #    ECO BatchGetGoodsDetail 暂不使用，图片/相位等信息由其他来源补充
        eco_price_mapping = {}
        if eco_partner and eco_rsa and not self._is_canceled:
            self.progress.emit(0, total, "正在获取 ECO 全量行情...")
            try:
                eco_client = ECOClient(partner_id=eco_partner, private_key_str=eco_rsa)
                eco_price_mapping = eco_client.get_prices_for_hash_names(
                    all_names, force_refresh=force_eco
                )
                source_text = {
                    "cache": "本地缓存",
                    "network": "ECO 全量更新",
                    "stale": "过期本地缓存",
                }.get(eco_client.last_price_source, "无可用数据")
                cache_status = eco_client.last_cache_status or {}
                item_count = cache_status.get("item_count", len(eco_price_mapping))
                matched_names = len({name for name, _style in eco_price_mapping})
                self.eco_status_text = (
                    f"ECO {source_text} · 观察命中 {matched_names}/{total}"
                    f" · 缓存 {item_count:,} 条"
                )
                self.progress.emit(
                    0, total, f"ECO：{source_text}，按需读取 {matched_names}/{total} 个饰品"
                )
                logger.info(
                    f"[ECO] 行情来源={eco_client.last_price_source}，共 {len(eco_price_mapping)} 条"
                )
            except Exception as e:
                self.error.emit(f"ECO 全量行情异常: {e}")

        # The fast layer deliberately stops after the one-shot CSQAQ batch (and
        # the cheap ECO cache lookup above).  Detailed platform rents and both
        # CSFloat requests are handled by the staggered per-item layer instead
        # of making every ten-minute refresh wait for every item.
        if fast_only:
            self.csfloat_status_text = "CSFloat 后台滚动刷新"
            if not self._is_canceled:
                self.result_ready.emit({
                    "items": tracked_items,
                    "eco_status_text": self.eco_status_text,
                    "csfloat_status_text": self.csfloat_status_text,
                })
                self.finished.emit()
            return

        csfloat_client = CSFloatClient(csfloat_api_key) if csfloat_api_key else None
        try:
            manual_fx_rate = float(usd_cny_rate)
        except (TypeError, ValueError):
            manual_fx_rate = 7.2
        if manual_fx_rate <= 0:
            manual_fx_rate = 7.2

        fx_result = {
            "rate": manual_fx_rate,
            "source": "manual",
            "reference_date": "",
            "status": "manual_disabled",
        }
        if csfloat_api_key and auto_usd_cny_rate and not self._is_canceled:
            self.progress.emit(0, total, "正在读取 ECB 美元兑人民币参考汇率...")
            try:
                fx_result = ExchangeRateClient().get_usd_cny(manual_fx_rate)
            except Exception as exc:
                logger.warning("[汇率] ECB 获取异常，使用手工备用值: %s", exc)
        csfloat_fx_rate = _number(fx_result.get("rate")) or manual_fx_rate
        if fx_result.get("source") == "CSFloat":
            fx_source = "csfloat"
        elif fx_result.get("source") == "ECB":
            fx_source = "ecb"
        else:
            fx_source = "manual"
        fx_reference_date = str(fx_result.get("reference_date") or "")
        fx_status = str(fx_result.get("status") or "")
        if fx_source == "csfloat":
            fx_status_text = f"官网汇率 {csfloat_fx_rate:.4f}"
        elif fx_source == "ecb":
            fx_status_text = (
                f"ECB {csfloat_fx_rate:.4f}"
                + (f"（{fx_reference_date}）" if fx_reference_date else "")
                + (" · 过期缓存" if fx_status.startswith("stale_") else "")
            )
        else:
            fx_status_text = f"手工汇率 {csfloat_fx_rate:.4f}"
        csfloat_network_attempts = 0
        csfloat_successes = 0
        csfloat_cache_hits = 0
        csfloat_deferred = 0
        csfloat_stop_reason = ""
        csfloat_stop_code = ""

        # The public documentation exposes per-endpoint limits through response
        # headers rather than publishing one fixed quota.  Cap each refresh as
        # an extra guard and prioritize the oldest cached rows so large lists
        # make forward progress over successive refreshes.
        pending_csfloat_entries = []
        if csfloat_api_key:
            prepared_at = time.time()
            pending_csfloat_entries = [
                entry for entry in tracked_items
                if not csfloat_quote_is_fresh(
                    entry, build_mhn_fn(entry), now=prepared_at
                )
            ]
            pending_csfloat_entries.sort(
                key=lambda entry: float(entry.get("csfloat_fetched_at", 0) or 0)
            )
        csfloat_query_entry_ids = {
            id(entry)
            for entry in pending_csfloat_entries[:CSFLOAT_MAX_REQUESTS_PER_REFRESH]
        }
        csfloat_deferred = max(
            0, len(pending_csfloat_entries) - len(csfloat_query_entry_ids)
        )

        # 4. Per-item cached CSFloat buy-now quote plus local ECO mapping.
        for idx, entry in enumerate(tracked_items):
            if self._is_canceled:
                break

            mhn = build_mhn_fn(entry)
            self.progress.emit(idx + 1, total, f"正在刷新 ({idx+1}/{total}) {mhn[:40]}...")

            # 5a. 从 ECO 全量行情缓存中提取起租价
            if eco_price_mapping and not self._is_canceled:
                eco_item = eco_client.market_cache.find_price(
                    eco_price_mapping, mhn, entry.get("phase", "")
                )
                if not eco_item:
                    logger.warning(
                        f"[ECO] 未匹配: mhn='{mhn}', phase='{entry.get('phase', '-')}'"
                    )
                else:
                    logger.debug(f"[ECO] 匹配成功: mhn='{mhn}', rent={eco_item.get('eco_rent_price', 0.0)}")
                    entry["eco_min_rent"] = eco_item.get("eco_rent_price", 0.0)
                    entry.setdefault("detail", {})["eco_sell_price"] = eco_item.get("eco_sell_price", 0.0)
                    entry.setdefault("detail", {})["eco_style_name"] = eco_item.get("style_name", "")

            csfloat_is_fresh = (
                not force_csfloat and csfloat_quote_is_fresh(entry, mhn)
            )
            entry["csfloat_fx_rate"] = csfloat_fx_rate
            entry["csfloat_fx_source"] = fx_source
            entry["csfloat_fx_reference_date"] = fx_reference_date
            cached_usd = _number(entry.get("csfloat_min_sell_usd"))
            cached_price_cents = int(_number(entry.get("csfloat_price_cents")))
            if cached_price_cents <= 0:
                cached_price_cents = int(round(cached_usd * 100))
            entry["csfloat_min_sell_cny"] = csfloat_cny_display_price(
                cached_price_cents, csfloat_fx_rate
            )
            if not csfloat_api_key:
                entry["csfloat_status"] = "missing_api_key"
            elif csfloat_is_fresh:
                csfloat_cache_hits += 1
            elif csfloat_stop_code:
                entry["csfloat_status"] = f"skipped_{csfloat_stop_code}"
            elif id(entry) not in csfloat_query_entry_ids:
                entry["csfloat_status"] = "deferred"
            elif csfloat_client is not None and not self._is_canceled:
                self.progress.emit(
                    idx + 1, total, f"正在查询 CSFloat ({idx + 1}/{total}) {mhn[:32]}..."
                )
                result = csfloat_client.get_lowest_buy_now(mhn)
                csfloat_network_attempts += int(bool(result.get("request_made")))
                entry["csfloat_last_attempt_at"] = int(time.time())
                if result.get("success"):
                    csfloat_successes += 1
                    entry["csfloat_fetched_at"] = int(time.time())
                    entry["csfloat_query_mhn"] = mhn
                    if result.get("found"):
                        price_cents = int(result.get("price_cents") or 0)
                        usd_price = _number(result.get("price_usd"))
                        entry["csfloat_price_cents"] = price_cents
                        entry["csfloat_min_sell_usd"] = usd_price
                        entry["csfloat_min_sell_cny"] = csfloat_cny_display_price(
                            price_cents, csfloat_fx_rate
                        )
                        entry["csfloat_listing_id"] = str(result.get("listing_id") or "")
                        entry["csfloat_float_value"] = result.get("float_value")
                        entry["csfloat_paint_seed"] = result.get("paint_seed")
                        entry["csfloat_status"] = "ok"

                        buy_is_fresh = (
                            not force_csfloat
                            and csfloat_buy_quote_is_fresh(entry, mhn)
                        )
                        if buy_is_fresh:
                            entry.setdefault("csfloat_buy_status", "ok")
                        else:
                            buy_result = csfloat_client.get_highest_buy_order(
                                entry["csfloat_listing_id"]
                            )
                            csfloat_network_attempts += int(bool(buy_result.get("request_made")))
                            entry["csfloat_buy_last_attempt_at"] = int(time.time())
                            if buy_result.get("success"):
                                entry["csfloat_buy_fetched_at"] = int(time.time())
                                entry["csfloat_buy_query_mhn"] = mhn
                                if buy_result.get("found"):
                                    buy_cents = int(buy_result.get("price_cents") or 0)
                                    entry["csfloat_highest_buy_price_cents"] = buy_cents
                                    entry["csfloat_highest_buy_usd"] = _number(
                                        buy_result.get("price_usd")
                                    )
                                    entry["csfloat_highest_buy_cny"] = csfloat_cny_display_price(
                                        buy_cents, csfloat_fx_rate
                                    )
                                    entry["csfloat_highest_buy_qty"] = int(
                                        buy_result.get("quantity") or 0
                                    )
                                    entry["csfloat_highest_buy_hybrid_properties"] = (
                                        buy_result.get("hybrid_properties") or {}
                                    )
                                    entry["csfloat_buy_status"] = "ok"
                                else:
                                    entry["csfloat_highest_buy_price_cents"] = 0
                                    entry["csfloat_highest_buy_usd"] = 0.0
                                    entry["csfloat_highest_buy_cny"] = 0.0
                                    entry["csfloat_highest_buy_qty"] = 0
                                    entry["csfloat_highest_buy_hybrid_properties"] = {}
                                    entry["csfloat_buy_status"] = "no_buy_order"
                            else:
                                entry["csfloat_buy_status"] = str(
                                    buy_result.get("error") or "unknown"
                                )
                    else:
                        entry["csfloat_min_sell_usd"] = 0.0
                        entry["csfloat_min_sell_cny"] = 0.0
                        entry["csfloat_price_cents"] = 0
                        entry["csfloat_listing_id"] = ""
                        entry["csfloat_status"] = "no_listing"
                        entry["csfloat_highest_buy_price_cents"] = 0
                        entry["csfloat_highest_buy_usd"] = 0.0
                        entry["csfloat_highest_buy_cny"] = 0.0
                        entry["csfloat_highest_buy_qty"] = 0
                        entry["csfloat_highest_buy_hybrid_properties"] = {}
                        entry["csfloat_buy_status"] = "no_listing"
                else:
                    error_code = str(result.get("error") or "unknown")
                    entry["csfloat_status"] = error_code
                    csfloat_stop_code = error_code
                    if error_code == "unauthorized":
                        csfloat_stop_reason = "API Key 无效"
                        self.error.emit("CSFloat API Key 无效，已停止本轮 CSFloat 查询。")
                    elif error_code == "forbidden":
                        csfloat_stop_reason = "访问被拒绝"
                        self.error.emit("CSFloat 拒绝访问，请检查 API Key 权限或网络环境。")
                    elif error_code == "rate_limited":
                        retry_after = int(result.get("retry_after") or 0)
                        rate_source = str(
                            result.get("rate_limit_source")
                            or CSFloatClient.cooldown_reason()
                            or "CSFloat 服务端频控"
                        )
                        csfloat_stop_reason = f"{rate_source} · 等待 {retry_after} 秒"
                        self.error.emit(
                            f"{rate_source}；全局同步暂停发起新请求，约 {retry_after} 秒后自动继续。"
                        )
                    elif error_code == "invalid_json":
                        csfloat_stop_reason = "响应格式异常"
                        self.error.emit("CSFloat 返回了无法解析的数据，已停止本轮后续查询。")
                    else:
                        csfloat_stop_reason = "网络或服务异常"
                        self.error.emit("CSFloat 请求失败，已停止本轮后续查询。")
                    csfloat_client = None

            # Download the standard schema image once; subsequent refreshes use
            # the local cache and do not download it again.
            image_url = entry.get("image_url", "")
            if image_url and not self._is_canceled:
                ImageCache.download(mhn, image_url)

            # 通知 GUI 线程该行已更新
            if not self._is_canceled:
                self.row_updated.emit(idx)

        if csfloat_api_key:
            suffix = f" · {csfloat_stop_reason}" if csfloat_stop_reason else ""
            self.csfloat_status_text = (
                f"CSFloat {fx_status_text} · 请求 {csfloat_network_attempts} 条"
                f" · 成功 {csfloat_successes} 条"
                f" · 缓存 {csfloat_cache_hits} 条"
                + (f" · 排队 {csfloat_deferred} 条" if csfloat_deferred else "")
                + suffix
            )
        else:
            self.csfloat_status_text = "CSFloat 未配置"

        if not self._is_canceled:
            # ``tracked_items`` is an isolated deep copy owned by this worker.
            # Hand it back through a queued signal so the GUI applies one atomic
            # snapshot instead of sharing mutable dictionaries across threads.
            self.result_ready.emit({
                "items": tracked_items,
                "eco_status_text": self.eco_status_text,
                "csfloat_status_text": self.csfloat_status_text,
            })
            self.finished.emit()
