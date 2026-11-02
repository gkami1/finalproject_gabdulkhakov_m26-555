"""RatesUpdater — координация обновления курсов.

Опрашивает всех клиентов по очереди, объединяет курсы, добавляет метаданные
(source, updated_at, last_refresh) и сохраняет их в rates.json (снимок)
и exchange_rates.json (история). Падение одного клиента не останавливает
обновление: ошибка логируется, данные остальных источников сохраняются.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import validate_currency_code
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import PARSER_LOGGER_NAME
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage, make_history_record

PAIR_SEPARATOR = "_"
CLIENT_CLASSES: tuple[type[BaseApiClient], ...] = (CoinGeckoClient, ExchangeRateApiClient)
SOURCE_KEYS = tuple(client.key for client in CLIENT_CLASSES)

logger = logging.getLogger(PARSER_LOGGER_NAME)


def utc_timestamp() -> str:
    """Текущее время UTC в ISO-формате с суффиксом Z."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class UpdateResult:
    """Итог run_update()."""

    total_rates: int = 0
    last_refresh: str | None = None
    rates_by_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        """Был ли хотя бы один неудачный источник."""
        return bool(self.errors)


class RatesUpdater:
    """Точка входа логики парсинга."""

    def __init__(self, clients: Sequence[BaseApiClient], storage: RatesStorage) -> None:
        self.clients = list(clients)
        self.storage = storage

    def _split_pair(self, pair: str) -> tuple[str, str] | None:
        """Разобрать и проверить ключ пары; некорректные пары отбрасываются."""
        try:
            from_code, to_code = pair.split(PAIR_SEPARATOR)
            return validate_currency_code(from_code), validate_currency_code(to_code)
        except ValueError:
            logger.warning("Skipping malformed pair %r", pair)
            return None

    def _collect(
        self, client: BaseApiClient, rates: dict[str, float]
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        """Превратить ответ клиента в записи снимка и истории."""
        timestamp = utc_timestamp()
        pairs: dict[str, dict[str, Any]] = {}
        history = []
        for pair, rate in rates.items():
            codes = self._split_pair(pair)
            if codes is None:
                continue
            from_code, to_code = codes
            pair_key = f"{from_code}{PAIR_SEPARATOR}{to_code}"
            pairs[pair_key] = {"rate": rate, "updated_at": timestamp, "source": client.name}
            meta = {"raw_id": client.raw_id(from_code), **client.last_meta}
            history.append(
                make_history_record(from_code, to_code, rate, timestamp, client.name, meta)
            )
        return pairs, history

    def run_update(self) -> UpdateResult:
        """Опросить всех клиентов и сохранить объединённые курсы.

        Raises:
            ApiRequestError: не удалось получить курсы ни из одного источника.
        """
        logger.info("Starting rates update...")
        result = UpdateResult()
        all_pairs: dict[str, dict[str, Any]] = {}
        all_history: list[dict[str, Any]] = []

        for client in self.clients:
            try:
                rates = client.fetch_rates()
            except ApiRequestError as exc:
                result.errors[client.name] = exc.reason
                logger.error("Failed to fetch from %s: %s", client.name, exc.reason)
                continue
            pairs, history = self._collect(client, rates)
            all_pairs.update(pairs)
            all_history.extend(history)
            result.rates_by_source[client.name] = len(pairs)
            logger.info("Fetching from %s... OK (%d rates)", client.name, len(pairs))

        if not all_pairs:
            reasons = "; ".join(f"{name} — {reason}" for name, reason in result.errors.items())
            logger.error("Rates update failed: no data from any source")
            raise ApiRequestError(reasons or "нет настроенных источников")

        result.last_refresh = utc_timestamp()
        logger.info("Writing %d rates to %s...", len(all_pairs), self._display_path())
        self.storage.save_snapshot(all_pairs, result.last_refresh)
        added = self.storage.append_history(all_history)
        logger.debug("History: %d new records", added)
        result.total_rates = len(all_pairs)

        if result.has_errors:
            logger.warning("Update completed with errors: %s", ", ".join(result.errors))
        else:
            logger.info("Update successful: %d rates", result.total_rates)
        return result

    def _display_path(self) -> str:
        path = self.storage.config.RATES_FILE_PATH
        try:
            return path.relative_to(SettingsLoader().root).as_posix()
        except ValueError:
            return str(path)


def create_updater(source: str | None = None, config: ParserConfig | None = None) -> RatesUpdater:
    """Собрать RatesUpdater со всеми клиентами или только с одним источником.

    Args:
        source: ключ источника (coingecko, exchangerate) или None — все.

    Raises:
        ValueError: неизвестный источник.
    """
    config = config or ParserConfig()
    classes = CLIENT_CLASSES
    if source is not None:
        classes = tuple(cls for cls in CLIENT_CLASSES if cls.key == source.lower())
        if not classes:
            raise ValueError(f"Неизвестный источник '{source}'. Доступны: {', '.join(SOURCE_KEYS)}")
    return RatesUpdater([cls(config) for cls in classes], RatesStorage(config))
