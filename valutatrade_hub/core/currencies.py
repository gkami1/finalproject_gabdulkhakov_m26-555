"""Иерархия валют: абстрактная Currency и наследники FiatCurrency/CryptoCurrency.

Реестр поддерживаемых валют доступен через get_currency(code).
"""

from abc import ABC, abstractmethod

from valutatrade_hub.core.exceptions import CurrencyNotFoundError
from valutatrade_hub.core.utils import validate_currency_code

FIAT_DISPLAY_DECIMALS = 2
CRYPTO_DISPLAY_DECIMALS = 4


class Currency(ABC):
    """Валюта с человекочитаемым именем и кодом.

    Инварианты: code — верхний регистр, 2–5 символов без пробелов;
    name — непустая строка.
    """

    display_decimals: int = FIAT_DISPLAY_DECIMALS

    def __init__(self, name: str, code: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Название валюты не может быть пустым")
        if validate_currency_code(code) != code:
            raise ValueError(f"Код валюты '{code}' должен быть в верхнем регистре")
        self.name = name.strip()
        self.code = code

    @abstractmethod
    def get_display_info(self) -> str:
        """Строковое представление для UI и логов."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, name={self.name!r})"


class FiatCurrency(Currency):
    """Фиатная валюта, выпускаемая государством или валютной зоной."""

    def __init__(self, name: str, code: str, issuing_country: str) -> None:
        super().__init__(name, code)
        if not isinstance(issuing_country, str) or not issuing_country.strip():
            raise ValueError("Страна эмиссии не может быть пустой")
        self.issuing_country = issuing_country.strip()

    def get_display_info(self) -> str:
        """Например: ``[FIAT] USD — US Dollar (Issuing: United States)``."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """Криптовалюта с алгоритмом консенсуса и рыночной капитализацией."""

    display_decimals = CRYPTO_DISPLAY_DECIMALS

    def __init__(self, name: str, code: str, algorithm: str, market_cap: float) -> None:
        super().__init__(name, code)
        if not isinstance(algorithm, str) or not algorithm.strip():
            raise ValueError("Алгоритм не может быть пустым")
        if isinstance(market_cap, bool) or not isinstance(market_cap, int | float):
            raise ValueError("Капитализация должна быть числом")
        if market_cap < 0:
            raise ValueError("Капитализация не может быть отрицательной")
        self.algorithm = algorithm.strip()
        self.market_cap = float(market_cap)

    def get_display_info(self) -> str:
        """Например: ``[CRYPTO] BTC — Bitcoin (Algo: SHA-256, MCAP: 1.12e12)``."""
        mcap = f"{self.market_cap:.2e}".replace("e+", "e")
        return f"[CRYPTO] {self.code} — {self.name} (Algo: {self.algorithm}, MCAP: {mcap})"


_REGISTRY: dict[str, Currency] = {
    currency.code: currency
    for currency in (
        FiatCurrency("US Dollar", "USD", "United States"),
        FiatCurrency("Euro", "EUR", "Eurozone"),
        FiatCurrency("British Pound", "GBP", "United Kingdom"),
        FiatCurrency("Russian Ruble", "RUB", "Russia"),
        CryptoCurrency("Bitcoin", "BTC", "SHA-256", 1.12e12),
        CryptoCurrency("Ethereum", "ETH", "Ethash", 4.47e11),
        CryptoCurrency("Solana", "SOL", "Proof of History", 6.8e10),
    )
}


def get_currency(code: str) -> Currency:
    """Вернуть валюту из реестра по коду (регистр не важен).

    Raises:
        CurrencyNotFoundError: код некорректен или не поддерживается.
    """
    try:
        normalized = validate_currency_code(code)
    except ValueError:
        raise CurrencyNotFoundError(str(code).strip()) from None
    currency = _REGISTRY.get(normalized)
    if currency is None:
        raise CurrencyNotFoundError(normalized)
    return currency


def get_supported_codes() -> list[str]:
    """Коды всех поддерживаемых валют в порядке реестра."""
    return list(_REGISTRY)


def list_currencies() -> list[Currency]:
    """Все поддерживаемые валюты."""
    return list(_REGISTRY.values())
