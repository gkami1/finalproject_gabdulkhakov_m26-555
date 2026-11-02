"""Вспомогательные функции: валидация входных данных, курсы и конвертация."""

from numbers import Real

BASE_CURRENCY = "USD"
MIN_CODE_LENGTH = 2
MAX_CODE_LENGTH = 5
BALANCE_PRECISION = 8  # знаков после запятой при сохранении баланса
LARGE_RATE_THRESHOLD = 100
UNIT_RATE_THRESHOLD = 1

DEFAULT_EXCHANGE_RATES: dict[str, float] = {
    "EUR_USD": 1.0786,
    "GBP_USD": 1.2650,
    "RUB_USD": 0.01016,
    "BTC_USD": 59337.21,
    "ETH_USD": 3720.00,
    "SOL_USD": 145.12,
}


def validate_currency_code(code: str) -> str:
    """Проверить код валюты и вернуть его в нормализованном виде (верхний регистр).

    Raises:
        ValueError: код пустой, содержит пробелы или имеет неверную длину.
    """
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Код валюты должен быть непустой строкой")
    normalized = code.strip().upper()
    if " " in normalized or not normalized.isalnum():
        raise ValueError(f"Некорректный код валюты '{code}'")
    if not MIN_CODE_LENGTH <= len(normalized) <= MAX_CODE_LENGTH:
        raise ValueError(
            f"Код валюты должен содержать {MIN_CODE_LENGTH}–{MAX_CODE_LENGTH} символов"
        )
    return normalized


def validate_amount(amount: float) -> float:
    """Проверить, что сумма — положительное число, и вернуть её как float.

    Raises:
        ValueError: сумма не число, не положительна.
    """
    if isinstance(amount, bool) or not isinstance(amount, Real):
        raise ValueError("'amount' должен быть положительным числом")
    if amount <= 0:
        raise ValueError("'amount' должен быть положительным числом")
    return float(amount)


def make_pair_key(from_code: str, to_code: str) -> str:
    """Сформировать ключ пары валют, например ``BTC_USD``."""
    return f"{from_code}_{to_code}"


def get_pair_rate(
    from_code: str,
    to_code: str,
    rates: dict[str, float] | None = None,
) -> float:
    """Вернуть курс from_code→to_code по словарю пар.

    Ищет прямую пару, обратную пару, а затем кросс-курс через USD.

    Raises:
        KeyError: курс для пары вычислить невозможно.
    """
    rates = DEFAULT_EXCHANGE_RATES if rates is None else rates
    if from_code == to_code:
        return 1.0

    direct = rates.get(make_pair_key(from_code, to_code))
    if direct:
        return float(direct)
    inverse = rates.get(make_pair_key(to_code, from_code))
    if inverse:
        return 1.0 / float(inverse)

    if BASE_CURRENCY not in (from_code, to_code):
        try:
            to_usd = get_pair_rate(from_code, BASE_CURRENCY, rates)
            from_usd = get_pair_rate(BASE_CURRENCY, to_code, rates)
        except KeyError:
            pass
        else:
            return to_usd * from_usd

    raise KeyError(make_pair_key(from_code, to_code))


def convert(
    amount: float,
    from_code: str,
    to_code: str,
    rates: dict[str, float] | None = None,
) -> float:
    """Перевести сумму из одной валюты в другую по словарю курсов."""
    return amount * get_pair_rate(from_code, to_code, rates)


def format_rate(rate: float) -> str:
    """Курс с точностью по величине: 59337.21, 1.0786, 0.00001685."""
    if rate >= LARGE_RATE_THRESHOLD:
        return f"{rate:.2f}"
    if rate >= UNIT_RATE_THRESHOLD:
        return f"{rate:.4f}"
    return f"{rate:.8f}"
