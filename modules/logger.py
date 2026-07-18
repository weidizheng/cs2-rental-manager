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

    # 文件 Handler (带轮转，最大 5MB，保留 3 个备份)
    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "app.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
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
