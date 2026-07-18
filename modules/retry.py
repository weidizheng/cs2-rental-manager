import time
import functools
import logging

logger = logging.getLogger("CS2Rental")


def retry(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    网络请求重试装饰器，支持指数退避。

    用法:
        @retry(max_retries=3, delay=1.0, backoff=2.0)
        def fetch_data():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    wait = delay * (backoff ** attempt)
                    logger.warning(
                        f"[重试] {func.__name__} 第 {attempt + 1}/{max_retries} 次失败: {e}，"
                        f"{wait:.1f}s 后重试"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(wait)
            logger.error(f"[重试] {func.__name__} 已耗尽 {max_retries} 次重试，最终失败: {last_exc}")
            raise last_exc
        return wrapper
    return decorator