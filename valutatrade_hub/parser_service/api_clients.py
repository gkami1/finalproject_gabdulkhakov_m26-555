"""Клиенты внешних API курсов валют.

Каждый клиент возвращает курсы в едином формате ``{"BTC_USD": 59337.21}``,
где значение — сколько BASE_CURRENCY стоит 1 единица валюты. Любой сбой
(сеть, таймаут, HTTP-ошибка, неожиданный ответ) превращается в ApiRequestError
с причиной без имени источника — его добавляет RatesUpdater.
"""

import time
from abc import ABC, abstractmethod
from numbers import Real
from typing import Any

import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import make_pair_key
from valutatrade_hub.parser_service.config import ParserConfig

HTTP_OK = 200
HTTP_SERVER_ERROR = 500
MS_IN_SECOND = 1000

HTTP_ERROR_REASONS = {
    400: "некорректный запрос (HTTP 400)",
    401: "неверный или отсутствующий API-ключ (HTTP 401)",
    403: "доступ запрещён — проверьте API-ключ (HTTP 403)",
    404: "эндпоинт не найден (HTTP 404)",
    429: "превышен лимит запросов (HTTP 429), повторите позже",
}

# Коды ошибок в теле ответа ExchangeRate-API
EXCHANGERATE_ERROR_REASONS = {
    "invalid-key": "неверный API-ключ ExchangeRate-API",
    "inactive-account": "аккаунт ExchangeRate-API не активирован",
    "quota-reached": "исчерпан лимит запросов ExchangeRate-API",
    "unsupported-code": "неподдерживаемый код валюты",
    "malformed-request": "некорректный запрос",
}


def _is_valid_rate(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and value > 0


class BaseApiClient(ABC):
    """Общий интерфейс клиента курсов.

    Attributes:
        name: человекочитаемое имя источника (пишется в source).
        key: короткий идентификатор для CLI (--source).
        last_meta: метаданные последнего ответа (request_ms, status_code, etag).
    """

    name: str = ""
    key: str = ""

    def __init__(self, config: ParserConfig | None = None) -> None:
        self.config = config or ParserConfig()
        self.last_meta: dict[str, Any] = {}

    @abstractmethod
    def fetch_rates(self) -> dict[str, float]:
        """Получить курсы {"<CODE>_<BASE>": rate}.

        Raises:
            ApiRequestError: источник недоступен или вернул некорректные данные.
        """

    def raw_id(self, code: str) -> str:
        """Идентификатор валюты во внешнем API (по умолчанию — сам код)."""
        return code

    def _get_json(self, url: str, params: dict[str, str] | None = None) -> Any:
        """GET-запрос с таймаутом и проверкой статуса; вернуть разобранный JSON."""
        headers = {"Accept": "application/json", "User-Agent": self.config.USER_AGENT}
        started = time.perf_counter()
        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=self.config.REQUEST_TIMEOUT
            )
        except requests.exceptions.Timeout as exc:
            raise ApiRequestError(
                f"превышено время ожидания ({self.config.REQUEST_TIMEOUT} с)"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise ApiRequestError("сетевая ошибка — нет соединения") from exc
        except requests.exceptions.RequestException as exc:
            # Текст исключения содержит URL (а в нём API-ключ) — выводим только тип.
            raise ApiRequestError(f"ошибка запроса ({type(exc).__name__})") from exc

        self.last_meta = {
            "request_ms": round((time.perf_counter() - started) * MS_IN_SECOND),
            "status_code": response.status_code,
            "etag": response.headers.get("ETag"),
        }
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code != HTTP_OK:
            raise ApiRequestError(self._http_error_reason(response, payload))
        if payload is None:
            raise ApiRequestError("ответ не является корректным JSON")
        return payload

    def _http_error_reason(self, response: requests.Response, payload: Any) -> str:
        status = response.status_code
        if status in HTTP_ERROR_REASONS:
            return HTTP_ERROR_REASONS[status]
        if status >= HTTP_SERVER_ERROR:
            return f"сервис временно недоступен (HTTP {status})"
        return f"неожиданный ответ сервера (HTTP {status})"


class CoinGeckoClient(BaseApiClient):
    """Курсы криптовалют из CoinGecko (запрос по ID монет из CRYPTO_ID_MAP)."""

    name = "CoinGecko"
    key = "coingecko"

    def raw_id(self, code: str) -> str:
        return self.config.CRYPTO_ID_MAP.get(code, code.lower())

    def fetch_rates(self) -> dict[str, float]:
        base = self.config.BASE_CURRENCY
        ids = {code: self.raw_id(code) for code in self.config.CRYPTO_CURRENCIES}
        payload = self._get_json(
            self.config.COINGECKO_URL,
            params={"ids": ",".join(ids.values()), "vs_currencies": base.lower()},
        )
        if not isinstance(payload, dict):
            raise ApiRequestError("неожиданный формат ответа")

        rates = {}
        for code, coin_id in ids.items():
            value = (payload.get(coin_id) or {}).get(base.lower())
            if _is_valid_rate(value):
                rates[make_pair_key(code, base)] = float(value)
        if not rates:
            raise ApiRequestError("в ответе нет ни одного курса")
        return rates


class ExchangeRateApiClient(BaseApiClient):
    """Курсы фиатных валют из ExchangeRate-API (ключ подставляется в URL).

    API отдаёт «1 BASE = x CODE», поэтому курс CODE→BASE равен 1 / x.
    """

    name = "ExchangeRate-API"
    key = "exchangerate"

    def fetch_rates(self) -> dict[str, float]:
        api_key = self.config.EXCHANGERATE_API_KEY
        if not api_key:
            raise ApiRequestError("не задан ключ EXCHANGERATE_API_KEY (см. .env.example)")
        base = self.config.BASE_CURRENCY
        payload = self._get_json(f"{self.config.EXCHANGERATE_API_URL}/{api_key}/latest/{base}")
        if not isinstance(payload, dict) or payload.get("result") != "success":
            raise ApiRequestError(self._body_error_reason(payload))

        quoted = payload.get("rates")
        if not isinstance(quoted, dict):
            raise ApiRequestError("в ответе нет словаря rates")
        rates = {}
        for code in self.config.FIAT_CURRENCIES:
            value = quoted.get(code)
            if _is_valid_rate(value):
                rates[make_pair_key(code, base)] = 1.0 / float(value)
        if not rates:
            raise ApiRequestError("в ответе нет ни одного курса")
        return rates

    @staticmethod
    def _body_error_reason(payload: Any) -> str:
        error_type = payload.get("error-type") if isinstance(payload, dict) else None
        return EXCHANGERATE_ERROR_REASONS.get(error_type, f"ошибка API ({error_type})")

    def _http_error_reason(self, response: requests.Response, payload: Any) -> str:
        if isinstance(payload, dict) and payload.get("error-type"):
            return self._body_error_reason(payload) + f" (HTTP {response.status_code})"
        return super()._http_error_reason(response, payload)
