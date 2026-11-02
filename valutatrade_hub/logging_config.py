"""Настройка логирования ValutaTrade Hub.

Формат — человекочитаемая строка (выбран за удобство чтения ``tail``-ом)::

    INFO 2025-10-09T12:05:22 BUY user='alice' currency='BTC' amount=0.0500 ...

Файлы: logs/actions.log — операции Core Service, logs/parser.log —
Parser Service (логгер valutatrade.parser не дублируется в actions.log).

Политика: ротация по размеру (log_max_bytes,
по умолчанию 1 МБ) и log_backup_count архивами (по умолчанию 5).
Уровень по умолчанию INFO; для отладки — VALUTATRADE_LOG_LEVEL=DEBUG.
В консоль логи не выводятся, чтобы не смешиваться с ответами CLI.
"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from valutatrade_hub.infra.settings import SettingsLoader

APP_LOGGER_NAME = "valutatrade"
ACTIONS_LOGGER_NAME = f"{APP_LOGGER_NAME}.actions"
PARSER_LOGGER_NAME = f"{APP_LOGGER_NAME}.parser"
CONSOLE_FORMAT = "%(levelname)s: %(message)s"


def build_file_handler(log_file_key: str) -> RotatingFileHandler:
    """Файловый обработчик с ротацией и форматом из настроек."""
    settings = SettingsLoader()
    path = settings.get_path(log_file_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=settings.get("log_max_bytes"),
        backupCount=settings.get("log_backup_count"),
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(settings.get("log_format"), datefmt=settings.get("log_date_format"))
    )
    return handler


def setup_logging(level: str | None = None) -> logging.Logger:
    """Настроить логгер приложения (повторный вызов ничего не дублирует).

    Args:
        level: уровень логирования; по умолчанию — log_level из настроек.
    """
    resolved = (level or SettingsLoader().get("log_level")).upper()
    for name, file_key in (
        (APP_LOGGER_NAME, "actions_log_file"),
        (PARSER_LOGGER_NAME, "parser_log_file"),
    ):
        logger = logging.getLogger(name)
        logger.setLevel(resolved)
        if not logger.handlers:
            logger.addHandler(build_file_handler(file_key))
            logger.propagate = False
    return logging.getLogger(APP_LOGGER_NAME)


@contextmanager
def parser_console_output() -> Iterator[None]:
    """Временно дублировать лог Parser Service в консоль («INFO: ...»)."""
    logger = logging.getLogger(PARSER_LOGGER_NAME)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
