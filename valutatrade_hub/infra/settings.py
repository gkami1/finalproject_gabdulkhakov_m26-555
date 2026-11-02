"""SettingsLoader — единая точка конфигурации (Singleton).

Источник настроек — секция ``[tool.valutatrade]`` в pyproject.toml.
Файл ищется вверх от текущего каталога; относительные пути считаются
от каталога, где он найден (корень проекта). Если pyproject.toml не найден
(например, пакет установлен как инструмент), используются DEFAULTS
относительно текущего каталога.

Любой ключ можно переопределить переменной окружения ``VALUTATRADE_<KEY>``,
например ``VALUTATRADE_LOG_LEVEL=DEBUG``.

Ключи конфигурации:
    data_dir, users_file, portfolios_file, rates_file, exchange_rates_file —
        пути к JSON-хранилищу;
    rates_ttl_seconds — срок свежести курсов в кеше rates.json;
    rates_update_interval_seconds — период планировщика Parser Service;
    default_base_currency — базовая валюта show-portfolio по умолчанию;
    log_dir, actions_log_file, parser_log_file, log_level, log_format, log_date_format,
    log_max_bytes, log_backup_count — параметры логирования.
"""

import os
import tomllib
from pathlib import Path
from typing import Any, ClassVar

PYPROJECT_FILE = "pyproject.toml"
TOOL_SECTION = "valutatrade"
ENV_PREFIX = "VALUTATRADE_"

DEFAULTS: dict[str, Any] = {
    "data_dir": "data",
    "users_file": "data/users.json",
    "portfolios_file": "data/portfolios.json",
    "rates_file": "data/rates.json",
    "exchange_rates_file": "data/exchange_rates.json",
    "rates_ttl_seconds": 300,
    "default_base_currency": "USD",
    "log_dir": "logs",
    "actions_log_file": "logs/actions.log",
    "parser_log_file": "logs/parser.log",
    "rates_update_interval_seconds": 300,
    "log_level": "INFO",
    "log_format": "%(levelname)s %(asctime)s %(message)s",
    "log_date_format": "%Y-%m-%dT%H:%M:%S",
    "log_max_bytes": 1_048_576,
    "log_backup_count": 5,
}

_MISSING = object()


class SettingsLoader:
    """Загружает и кеширует конфигурацию проекта. Существует в одном экземпляре.

    Singleton реализован через ``__new__``: это самый простой и читаемый
    способ — без метакласса, поведение видно прямо в классе. Повторный вызов
    ``SettingsLoader()`` (в том числе из разных модулей) возвращает тот же
    объект, а конфигурация читается с диска только при первом создании
    и при явном ``reload()``.
    """

    _instance: ClassVar["SettingsLoader | None"] = None

    def __new__(cls) -> "SettingsLoader":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._load()
            cls._instance = instance
        return cls._instance

    def _load(self) -> None:
        self._root, file_settings = self._read_pyproject()
        settings = {**DEFAULTS, **file_settings}
        for key, default in DEFAULTS.items():
            env_value = os.environ.get(ENV_PREFIX + key.upper())
            if env_value is not None:
                settings[key] = type(default)(env_value)
        self._settings = settings

    @staticmethod
    def _read_pyproject() -> tuple[Path, dict[str, Any]]:
        """Найти pyproject.toml с секцией [tool.valutatrade] вверх от cwd."""
        cwd = Path.cwd()
        for directory in (cwd, *cwd.parents):
            candidate = directory / PYPROJECT_FILE
            if not candidate.is_file():
                continue
            with candidate.open("rb") as file:
                section = tomllib.load(file).get("tool", {}).get(TOOL_SECTION)
            if section is not None:
                return directory, dict(section)
        return cwd, {}

    @property
    def root(self) -> Path:
        """Корень проекта, относительно которого считаются пути."""
        return self._root

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """Значение настройки.

        Raises:
            KeyError: ключа нет и default не передан.
        """
        if key in self._settings:
            return self._settings[key]
        if default is _MISSING:
            raise KeyError(f"Неизвестный ключ конфигурации: {key}")
        return default

    def get_path(self, key: str) -> Path:
        """Настройка-путь, приведённая к абсолютному пути от корня проекта."""
        path = Path(self.get(key))
        return path if path.is_absolute() else self._root / path

    def reload(self) -> None:
        """Перечитать конфигурацию с диска и из окружения."""
        self._load()
