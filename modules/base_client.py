import time
import logging

logger = logging.getLogger("CS2Rental")


class BaseAPIClient:
    """
    通用 API 客户端基类，提供请求频率限制（Rate Limiting）能力。

    所有子类在 __init__ 中调用 super().__init__(min_interval=...) 来设定
    安全请求间隔，每次发起实际 HTTP 请求前调用 self._wait_rate_limit() 即可。
    """

    def __init__(self, min_interval: float):
        """
        Args:
            min_interval: 两次请求之间的最小间隔（秒）。
                          例如 1.05 表示每秒最多 1 次请求。
        """
        self.min_interval = min_interval
        self.last_request_time = 0.0

    def _wait_rate_limit(self):
        """
        阻塞等待，确保距离上一次请求至少过去了 self.min_interval 秒。
        应在每次实际的 HTTP 请求调用前执行。
        """
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed
            logger.debug(
                f"[RateLimit] 等待 {sleep_time:.3f}s "
                f"(min_interval={self.min_interval}s, elapsed={elapsed:.3f}s)"
            )
            time.sleep(sleep_time)
        self.last_request_time = time.time()