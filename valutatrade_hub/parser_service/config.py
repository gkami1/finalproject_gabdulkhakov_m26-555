"""Конфигурация Parser Service: API, списки валют, сеть и пути.

API-ключ ExchangeRate-API не хранится в коде: он берётся из переменной
окружения EXCHANGERATE_API_KEY, а если её нет — из файла ``.env`` в корне
проекта (файл исключён из git, образец — ``.env.example``).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from valutatrade_hub.infra.settings import SettingsLoader

API_KEY_ENV = "EXCHANGERATE_API_KEY"
ENV_FILE_NAME = ".env"


def load_env_file(path: Path) -> None:
    """Загрузить переменные KEY=VALUE из файла в os.environ (не перезаписывая)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _api_key() -> str | None:
    load_env_file(SettingsLoader().root / ENV_FILE_NAME)
    return os.getenv(API_KEY_ENV) or None


def _settings_path(key: str) -> Path:
    return SettingsLoader().get_path(key)


@dataclass(frozen=True)
class ParserConfig:
    """Все изменяемые параметры Parser Service в одном месте."""

    # Ключ загружается из окружения / .env
    EXCHANGERATE_API_KEY: str | None = field(default_factory=_api_key)

    # Эндпоинты
    COINGECKO_URL: str = "https://api.coingecko.com/api/v3/simple/price"
    EXCHANGERATE_API_URL: str = "https://v6.exchangerate-api.com/v6"

    # Списки валют
    BASE_CURRENCY: str = "USD"
    FIAT_CURRENCIES: tuple[str, ...] = ("EUR", "GBP", "RUB")
    CRYPTO_CURRENCIES: tuple[str, ...] = ("BTC", "ETH", "SOL")
    CRYPTO_ID_MAP: dict[str, str] = field(
        default_factory=lambda: {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
    )

    # Пути (по умолчанию — из [tool.valutatrade] через SettingsLoader)
    RATES_FILE_PATH: Path = field(default_factory=lambda: _settings_path("rates_file"))
    HISTORY_FILE_PATH: Path = field(default_factory=lambda: _settings_path("exchange_rates_file"))

    # Сетевые параметры
    REQUEST_TIMEOUT: int = 10
    USER_AGENT: str = "ValutaTradeHub/0.1"

    # Планировщик
    UPDATE_INTERVAL_SECONDS: int = field(
        default_factory=lambda: SettingsLoader().get("rates_update_interval_seconds")
    )
