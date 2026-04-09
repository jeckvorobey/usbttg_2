"""Централизованная настройка логирования приложения."""

import logging


_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Настраивает root logger для консольного вывода."""
    normalized_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()

    if not getattr(setup_logging, "_configured", False):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root_logger.addHandler(handler)
        setup_logging._configured = True

    root_logger.setLevel(normalized_level)
