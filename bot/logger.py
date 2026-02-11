import logging
from logging.handlers import RotatingFileHandler


def setup_logger(level: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger("derivbot")
    logger.setLevel(level.upper())
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

        if log_file:
            file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger
