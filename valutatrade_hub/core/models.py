"""Модели: пользователь, кошелёк и портфель."""

import hashlib
import hmac
import secrets
from datetime import datetime
from numbers import Real
from typing import Any

from valutatrade_hub.core.utils import (
    BALANCE_PRECISION,
    BASE_CURRENCY,
    convert,
    validate_amount,
    validate_currency_code,
)

MIN_PASSWORD_LENGTH = 4
SALT_BYTES = 8


class User:
    """Пользователь системы.

    Пароль хранится только в виде одностороннего хеша SHA-256(password + salt).
    """

    def __init__(
        self,
        user_id: int,
        username: str,
        hashed_password: str,
        salt: str,
        registration_date: datetime,
    ) -> None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id должен быть положительным целым числом")
        if not isinstance(registration_date, datetime):
            raise ValueError("registration_date должна быть объектом datetime")
        if not hashed_password or not salt:
            raise ValueError("Хеш пароля и соль не могут быть пустыми")

        self._user_id = user_id
        self.username = username
        self._hashed_password = hashed_password
        self._salt = salt
        self._registration_date = registration_date

    # --- фабрика -------------------------------------------------------------

    @classmethod
    def create(cls, user_id: int, username: str, password: str) -> "User":
        """Создать нового пользователя из пароля в открытом виде."""
        cls._validate_password(password)
        salt = secrets.token_hex(SALT_BYTES)
        return cls(
            user_id=user_id,
            username=username,
            hashed_password=cls._hash_password(password, salt),
            salt=salt,
            registration_date=datetime.now().replace(microsecond=0),
        )

    # --- свойства ------------------------------------------------------------

    @property
    def user_id(self) -> int:
        """Уникальный идентификатор пользователя (только чтение)."""
        return self._user_id

    @property
    def username(self) -> str:
        """Имя пользователя."""
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        self._username = value.strip()

    @property
    def hashed_password(self) -> str:
        """Хеш пароля (только чтение; меняется через change_password)."""
        return self._hashed_password

    @property
    def salt(self) -> str:
        """Соль пользователя (только чтение)."""
        return self._salt

    @property
    def registration_date(self) -> datetime:
        """Дата регистрации (только чтение)."""
        return self._registration_date

    # --- пароль --------------------------------------------------------------

    @staticmethod
    def _validate_password(password: str) -> None:
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов")

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()

    def change_password(self, new_password: str) -> None:
        """Сменить пароль: новая соль и новый хеш."""
        self._validate_password(new_password)
        self._salt = secrets.token_hex(SALT_BYTES)
        self._hashed_password = self._hash_password(new_password, self._salt)

    def verify_password(self, password: str) -> bool:
        """Проверить введённый пароль на совпадение с сохранённым хешем."""
        if not isinstance(password, str):
            return False
        candidate = self._hash_password(password, self._salt)
        return hmac.compare_digest(candidate, self._hashed_password)

    # --- представление и сериализация ----------------------------------------

    def get_user_info(self) -> dict[str, Any]:
        """Информация о пользователе без пароля и соли."""
        return {
            "user_id": self._user_id,
            "username": self._username,
            "registration_date": self._registration_date.isoformat(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Словарь для сохранения в users.json."""
        return {
            "user_id": self._user_id,
            "username": self._username,
            "hashed_password": self._hashed_password,
            "salt": self._salt,
            "registration_date": self._registration_date.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "User":
        """Восстановить пользователя из записи users.json."""
        return cls(
            user_id=int(data["user_id"]),
            username=data["username"],
            hashed_password=data["hashed_password"],
            salt=data["salt"],
            registration_date=datetime.fromisoformat(data["registration_date"]),
        )

    def __repr__(self) -> str:
        return f"User(user_id={self._user_id}, username={self._username!r})"


class Wallet:
    """Кошелёк пользователя для одной конкретной валюты."""

    def __init__(self, currency_code: str, balance: float = 0.0) -> None:
        self.currency_code = validate_currency_code(currency_code)
        self.balance = balance

    @property
    def balance(self) -> float:
        """Текущий баланс."""
        return self._balance

    @balance.setter
    def balance(self, value: float) -> None:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("Баланс должен быть числом")
        if value < 0:
            raise ValueError("Баланс не может быть отрицательным")
        self._balance = float(value)

    def deposit(self, amount: float) -> None:
        """Пополнить баланс на положительную сумму."""
        self.balance = self._balance + validate_amount(amount)

    def withdraw(self, amount: float) -> None:
        """Снять средства, если баланс позволяет.

        Raises:
            ValueError: сумма некорректна или превышает баланс.
        """
        amount = validate_amount(amount)
        if amount > self._balance:
            raise ValueError(
                f"Недостаточно средств: доступно {self._balance:.4f} {self.currency_code}, "
                f"требуется {amount:.4f} {self.currency_code}"
            )
        self.balance = self._balance - amount

    def get_balance_info(self) -> str:
        """Строка с информацией о балансе, например ``BTC: 0.0500``."""
        return f"{self.currency_code}: {self._balance:.4f}"

    def to_dict(self) -> dict[str, Any]:
        """Словарь для сохранения в составе портфеля."""
        return {
            "currency_code": self.currency_code,
            "balance": round(self._balance, BALANCE_PRECISION),
        }

    def __repr__(self) -> str:
        return f"Wallet({self.currency_code!r}, balance={self._balance})"


class Portfolio:
    """Все кошельки одного пользователя."""

    def __init__(
        self,
        user_id: int,
        wallets: dict[str, Wallet] | None = None,
        user: User | None = None,
    ) -> None:
        if user is not None and user.user_id != user_id:
            raise ValueError("user_id портфеля не совпадает с пользователем")
        self._user_id = user_id
        self._user = user
        self._wallets: dict[str, Wallet] = dict(wallets) if wallets else {}

    @property
    def user_id(self) -> int:
        """Идентификатор владельца портфеля."""
        return self._user_id

    @property
    def user(self) -> User | None:
        """Владелец портфеля (без возможности перезаписи)."""
        return self._user

    @property
    def wallets(self) -> dict[str, Wallet]:
        """Копия словаря кошельков."""
        return dict(self._wallets)

    def add_currency(self, currency_code: str) -> Wallet:
        """Добавить новый пустой кошелёк.

        Raises:
            ValueError: кошелёк с таким кодом уже существует.
        """
        code = validate_currency_code(currency_code)
        if code in self._wallets:
            raise ValueError(f"Кошелёк '{code}' уже существует")
        wallet = Wallet(code)
        self._wallets[code] = wallet
        return wallet

    def get_wallet(self, currency_code: str) -> Wallet | None:
        """Вернуть кошелёк по коду валюты или None, если его нет."""
        return self._wallets.get(validate_currency_code(currency_code))

    def get_total_value(
        self,
        base_currency: str = BASE_CURRENCY,
        exchange_rates: dict[str, float] | None = None,
    ) -> float:
        """Общая стоимость всех кошельков в базовой валюте.

        Args:
            base_currency: валюта, в которую пересчитываются балансы.
            exchange_rates: словарь курсов вида {"BTC_USD": 59337.21};
                по умолчанию — фиксированные курсы-заглушки.
        """
        base = validate_currency_code(base_currency)
        return sum(
            convert(wallet.balance, code, base, exchange_rates)
            for code, wallet in self._wallets.items()
        )

    def to_dict(self) -> dict[str, Any]:
        """Словарь для сохранения в portfolios.json."""
        return {
            "user_id": self._user_id,
            "wallets": {
                code: {"balance": round(wallet.balance, BALANCE_PRECISION)}
                for code, wallet in self._wallets.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], user: User | None = None) -> "Portfolio":
        """Восстановить портфель из записи portfolios.json."""
        wallets = {
            code: Wallet(code, float(info.get("balance", 0.0)))
            for code, info in data.get("wallets", {}).items()
        }
        return cls(user_id=int(data["user_id"]), wallets=wallets, user=user)

    def __repr__(self) -> str:
        return f"Portfolio(user_id={self._user_id}, wallets={list(self._wallets)})"
