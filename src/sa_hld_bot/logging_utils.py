from __future__ import annotations

import logging
from pathlib import Path


_LOGGER_CACHE: dict[str, logging.Logger] = {}


def get_logger(name: str, logs_dir: Path) -> logging.Logger:
    key = f"{name}:{logs_dir}"
    if key in _LOGGER_CACHE:
        return _LOGGER_CACHE[key]

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "app.log"

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _LOGGER_CACHE[key] = logger
    return logger
