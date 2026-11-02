"""Хранилище Parser Service.

* exchange_rates.json — история всех замеров (список записей с уникальным id
  ``<FROM>_<TO>_<ISO-UTC>``; повторный замер с тем же id не дублируется);
* rates.json — снимок последних курсов для Core Service
  (``{"pairs": {...}, "last_refresh": ...}``).

Запись атомарная (временный файл → rename) через DatabaseManager.
"""

from datetime import datetime
from typing import Any

from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.parser_service.config import ParserConfig

SNAPSHOT_SOURCE = "ParserService"


def make_record_id(from_code: str, to_code: str, timestamp: str) -> str:
    """Уникальный id замера, например ``BTC_USD_2025-10-10T12:00:00Z``."""
    return f"{from_code}_{to_code}_{timestamp}"


def make_history_record(
    from_code: str,
    to_code: str,
    rate: float,
    timestamp: str,
    source: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Запись exchange_rates.json для одной пары."""
    return {
        "id": make_record_id(from_code, to_code, timestamp),
        "from_currency": from_code,
        "to_currency": to_code,
        "rate": rate,
        "timestamp": timestamp,
        "source": source,
        "meta": meta or {},
    }


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


class RatesStorage:
    """Чтение и запись rates.json и exchange_rates.json."""

    def __init__(self, config: ParserConfig | None = None) -> None:
        self.config = config or ParserConfig()
        self._db = DatabaseManager()

    def load_snapshot(self) -> dict[str, Any]:
        """Текущий снимок курсов (пустой, если файла нет)."""
        snapshot = self._db.read_file(self.config.RATES_FILE_PATH, {})
        snapshot.setdefault("pairs", {})
        snapshot.setdefault("last_refresh", None)
        return snapshot

    def load_history(self) -> list[dict[str, Any]]:
        """Все записи истории замеров."""
        return self._db.read_file(self.config.HISTORY_FILE_PATH, [])

    def append_history(self, records: list[dict[str, Any]]) -> int:
        """Добавить записи в историю, пропуская уже существующие id.

        Returns:
            Количество реально добавленных записей.
        """
        with self._db.file_transaction(self.config.HISTORY_FILE_PATH, []) as history:
            known = {record.get("id") for record in history}
            fresh = [record for record in records if record["id"] not in known]
            history.extend(fresh)
        return len(fresh)

    def save_snapshot(self, pairs: dict[str, dict[str, Any]], last_refresh: str) -> int:
        """Обновить снимок: запись пары «побеждает», только если она свежее.

        Args:
            pairs: {"BTC_USD": {"rate": ..., "updated_at": ..., "source": ...}}.
            last_refresh: время завершения обновления (ISO-UTC).

        Returns:
            Количество обновлённых пар.
        """
        updated = 0
        with self._db.file_transaction(self.config.RATES_FILE_PATH, {}) as snapshot:
            current_pairs = snapshot.setdefault("pairs", {})
            for pair, entry in pairs.items():
                current = current_pairs.get(pair)
                if current and _parse(current["updated_at"]) > _parse(entry["updated_at"]):
                    continue
                current_pairs[pair] = entry
                updated += 1
            snapshot["source"] = SNAPSHOT_SOURCE
            snapshot["last_refresh"] = last_refresh
        return updated
