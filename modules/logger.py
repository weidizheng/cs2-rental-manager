import logging
import os
from logging.handlers import RotatingFileHandler

from modules.paths import get_private_path

LOG_DIR = str(get_private_path("logs"))
os.makedirs(LOG_DIR, exist_ok=True)


def setup_logger(name: str = "CS2Rental") -> logging.Logger:
    """统一日志配置：同时输出到文件和控制台"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 避免重复添加 Handler
    if logger.handlers:
        return logger

    # File logs are intentionally bounded: current file + two backups can use
    # at most about 6 MB, even if the program runs for years.
    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "app.log"),
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    fh.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(fh)

    # 控制台 Handler
    ch = logging.StreamHandler()
    ch.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(ch)

    return logger


logger = setup_logger()
