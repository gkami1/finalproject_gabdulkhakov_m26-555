"""Настройка логирования ValutaTrade Hub.

Формат — человекочитаемая строка (выбран за удобство чтения ``tail``-ом)::

    INFO 2025-10-09T12:05:22 BUY user='alice' currency='BTC' amount=0.0500 ...

Политика: файл logs/actions.log с ротацией по размеру (log_max_bytes,
по умолчанию 1 МБ) и log_backup_count архивами (по умолчанию 5).
Уровень по умолчанию INFO; для отладки — VALUTATRADE_LOG_LEVEL=DEBUG.
В консоль логи не выводятся, чтобы не смешиваться с ответами CLI.
"""

import logging
from logging.handlers import RotatingFileHandler

from valutatrade_hub.infra.settings import SettingsLoader

APP_LOGGER_NAME = "valutatrade"
ACTIONS_LOGGER_NAME = f"{APP_LOGGER_NAME}.actions"


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
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel((level or SettingsLoader().get("log_level")).upper())
    if not logger.handlers:
        logger.addHandler(build_file_handler("actions_log_file"))
        logger.propagate = False
    return logger
