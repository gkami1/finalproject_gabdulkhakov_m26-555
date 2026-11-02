"""Пользовательские исключения ValutaTrade Hub.

Все доменные ошибки наследуются от ValutaTradeError, поэтому CLI может
перехватить их одним обработчиком, а текст исключения — готовое сообщение
для пользователя. Ошибки формата ввода (сумма, длина пароля) остаются
ValueError.
"""


class ValutaTradeError(Exception):
    """Базовая доменная ошибка: str(exc) — сообщение для пользователя."""


# --- ошибки из спецификации (часть 3.2) ---------------------------------------


class InsufficientFundsError(ValutaTradeError):
    """Недостаточно средств на кошельке.

    Возникает в Wallet.withdraw() (а значит, и в usecases.buy/sell).
    """

    def __init__(self, available: float, required: float, code: str) -> None:
        self.available = available
        self.required = required
        self.code = code
        super().__init__(
            f"Недостаточно средств: доступно {available:.4f} {code}, "
            f"требуется {required:.4f} {code}"
        )


class CurrencyNotFoundError(ValutaTradeError):
    """Неизвестная валюта.

    Возникает в currencies.get_currency() и при валидации входа usecases.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or f"Неизвестная валюта '{code}'")


class UnknownBaseCurrencyError(CurrencyNotFoundError):
    """Неизвестная базовая валюта в show-portfolio."""

    def __init__(self, code: str) -> None:
        super().__init__(code, f"Неизвестная базовая валюта '{code}'")


class ApiRequestError(ValutaTradeError):
    """Сбой внешнего API (заглушки или Parser Service)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Ошибка при обращении к внешнему API: {reason}")


# --- остальные доменные ошибки ------------------------------------------------


class RateUnavailableError(ValutaTradeError):
    """Курса для пары нет ни в кеше, ни в источнике."""

    def __init__(self, from_code: str, to_code: str, message: str | None = None) -> None:
        self.from_code = from_code
        self.to_code = to_code
        super().__init__(
            message or f"Курс {from_code}→{to_code} недоступен. Повторите попытку позже."
        )


class UserAlreadyExistsError(ValutaTradeError):
    """Имя пользователя уже занято."""

    def __init__(self, username: str) -> None:
        super().__init__(f"Имя пользователя '{username}' уже занято")


class UserNotFoundError(ValutaTradeError):
    """Пользователь с таким именем не зарегистрирован."""

    def __init__(self, username: str) -> None:
        super().__init__(f"Пользователь '{username}' не найден")


class InvalidPasswordError(ValutaTradeError):
    """Пароль не совпал с сохранённым хешем."""

    def __init__(self) -> None:
        super().__init__("Неверный пароль")


class AuthRequiredError(ValutaTradeError):
    """Операция доступна только после login."""

    def __init__(self) -> None:
        super().__init__("Сначала выполните login")


class WalletNotFoundError(ValutaTradeError):
    """У пользователя нет кошелька в указанной валюте."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(
            f"У вас нет кошелька '{code}'. Добавьте валюту: "
            "она создаётся автоматически при первой покупке."
        )


class StorageError(ValutaTradeError):
    """Файл данных повреждён или недоступен."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"Ошибка хранилища {path}: {reason}")


class RatesCacheEmptyError(ValutaTradeError):
    """Локальный кеш курсов пуст или не найден."""

    def __init__(self) -> None:
        super().__init__(
            "Локальный кеш курсов пуст. Выполните 'update-rates', чтобы загрузить данные."
        )


class RateNotInCacheError(ValutaTradeError):
    """В кеше нет курсов для запрошенной валюты."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Курс для '{code}' не найден в кеше.")
