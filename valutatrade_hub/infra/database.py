"""DatabaseManager — абстракция над JSON-хранилищем (Singleton).

Коллекции (users, portfolios, rates, exchange_rates) сопоставлены файлам
из SettingsLoader. Запись атомарная: временный файл → os.replace, поэтому
при сбое во время записи старый файл остаётся целым.
"""

import copy
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.infra.settings import SettingsLoader

TMP_SUFFIX = ".tmp"
JSON_INDENT = 2

# коллекция → (ключ пути в настройках, значение для пустого/отсутствующего файла)
COLLECTIONS: dict[str, tuple[str, Any]] = {
    "users": ("users_file", []),
    "portfolios": ("portfolios_file", []),
    "rates": ("rates_file", {"pairs": {}, "last_refresh": None}),
    "exchange_rates": ("exchange_rates_file", []),
}


class DatabaseManager:
    """Чтение и атомарная запись JSON-коллекций. Существует в одном экземпляре.

    Singleton через ``__new__`` — как и SettingsLoader: просто и читаемо.
    Пути берутся из SettingsLoader при каждой операции, поэтому
    SettingsLoader().reload() сразу влияет на хранилище.
    """

    _instance: ClassVar["DatabaseManager | None"] = None

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @staticmethod
    def _resolve(collection: str) -> tuple[Path, Any]:
        try:
            settings_key, empty = COLLECTIONS[collection]
        except KeyError:
            raise KeyError(f"Неизвестная коллекция: {collection}") from None
        return SettingsLoader().get_path(settings_key), copy.deepcopy(empty)

    def read(self, collection: str) -> Any:
        """Прочитать коллекцию; отсутствующий или пустой файл — пустое значение.

        Raises:
            StorageError: файл повреждён или недоступен.
        """
        path, empty = self._resolve(collection)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty
        except OSError as exc:
            raise StorageError(str(path), exc.strerror or str(exc)) from exc
        if not text.strip():
            return empty
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise StorageError(str(path), "некорректный JSON") from exc

    def write(self, collection: str, data: Any) -> None:
        """Атомарно записать коллекцию (tmp-файл → rename)."""
        path, _ = self._resolve(collection)
        tmp_path = path.with_name(path.name + TMP_SUFFIX)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=JSON_INDENT) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise StorageError(str(path), exc.strerror or str(exc)) from exc

    @contextmanager
    def transaction(self, collection: str) -> Iterator[Any]:
        """Безопасное «чтение → модификация → запись».

        Данные записываются, только если блок завершился без исключения::

            with db.transaction("users") as users:
                users.append(record)
        """
        data = self.read(collection)
        yield data
        self.write(collection, data)
