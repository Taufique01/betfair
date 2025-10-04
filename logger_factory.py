# logger_factory.py
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from datetime import datetime

_LOGGER_INSTANCE = None

def get_logger(directory_name="logs"):
    global _LOGGER_INSTANCE
    if _LOGGER_INSTANCE:
        return _LOGGER_INSTANCE

    # Directories
    ROOT_DIR = Path(__file__).parent.resolve()
    LOG_DIR = ROOT_DIR / directory_name
    LOG_DIR.mkdir(exist_ok=True)

    # Daily log file
    today_str = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"app_{today_str}.log"

    # Logger setup
    logger = logging.getLogger(f"logger_{id(log_file)}")  # unique per entry point
    logger.setLevel(logging.INFO)

    # Handlers
    if not logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        file_handler = TimedRotatingFileHandler(
            filename=str(log_file), when="midnight", interval=1, backupCount=7, encoding="utf-8"
        )
        file_handler.suffix = "%Y-%m-%d"

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    _LOGGER_INSTANCE = logger
    return _LOGGER_INSTANCE

