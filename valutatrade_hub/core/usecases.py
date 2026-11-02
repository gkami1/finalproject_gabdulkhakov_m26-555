"""Бизнес-логика ValutaTrade Hub: пользователи, портфели, сделки и курсы.

CLI вызывает только функции этого модуля и сам ничего не считает.

Где используются синглтоны:
    SettingsLoader — TTL курсов (rates_ttl_seconds) и базовая валюта
        по умолчанию (default_base_currency);
    DatabaseManager — всё чтение/запись users, portfolios и rates
        (безопасный цикл «чтение → модификация → запись» через transaction()).

Свежие курсы получает Parser Service (RatesUpdater) — по команде
update-rates или автоматически, когда кеш rates.json старше TTL.

Операции register/login/buy/sell логируются декоратором @log_action.
Ошибки — доменные исключения из core.exceptions; ошибки формата ввода
(сумма, длина пароля, пустое имя) — ValueError.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from valutatrade_hub.core.currencies import (
    CryptoCurrency,
    Currency,
    get_currency,
    get_supported_codes,
    list_currencies,
)
from valutatrade_hub.core.exceptions import (
    AuthRequiredError,
    CurrencyNotFoundError,
    InvalidPasswordError,
    RateNotInCacheError,
    RatesCacheEmptyError,
    RateUnavailableError,
    UnknownBaseCurrencyError,
    UserAlreadyExistsError,
    UserNotFoundError,
    WalletNotFoundError,
)
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.core.utils import (
    BASE_CURRENCY,
    make_pair_key,
    validate_amount,
    validate_currency_code,
)
from valutatrade_hub.decorators import log_action
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.updater import UpdateResult, create_updater

PAIR_SEPARATOR = "_"

logger = logging.getLogger(__name__.replace("valutatrade_hub", "valutatrade", 1))


# --- результаты операций ------------------------------------------------------


@dataclass(frozen=True)
class RateInfo:
    """Курс from_code→to_code: 1 from_code = rate to_code."""

    from_code: str
    to_code: str
    rate: float
    updated_at: datetime

    @property
    def inverse_rate(self) -> float:
        """Обратный курс to_code→from_code."""
        return 1.0 / self.rate


@dataclass(frozen=True)
class WalletChange:
    """Изменение баланса одного кошелька в результате сделки."""

    currency_code: str
    before: float
    after: float


@dataclass(frozen=True)
class TradeResult:
    """Итог покупки или продажи."""

    username: str
    currency_code: str
    amount: float
    rate: float
    base_currency: str
    base_amount: float
    changes: tuple[WalletChange, ...]


@dataclass(frozen=True)
class PortfolioRow:
    """Строка отчёта по портфелю: кошелёк и его стоимость в базовой валюте."""

    currency_code: str
    balance: float
    value_in_base: float


@dataclass(frozen=True)
class RateRow:
    """Строка show-rates: пара из кеша."""

    pair: str
    rate: float
    updated_at: datetime
    source: str


@dataclass(frozen=True)
class RatesView:
    """Отчёт show-rates."""

    rows: tuple[RateRow, ...]
    last_refresh: datetime | None
    is_stale: bool
    ttl_seconds: int


@dataclass(frozen=True)
class PortfolioReport:
    """Отчёт show-portfolio."""

    username: str
    base_currency: str
    rows: tuple[PortfolioRow, ...]
    total: float


# --- хранилище ----------------------------------------------------------------


def _find_user_record(users: list[dict[str, Any]], username: str) -> dict[str, Any] | None:
    return next((record for record in users if record["username"] == username), None)


def _get_user(user_id: int) -> User:
    users = DatabaseManager().read("users")
    record = next((r for r in users if r["user_id"] == user_id), None)
    if record is None:
        raise AuthRequiredError()
    return User.from_dict(record)


def _load_portfolio(user: User) -> Portfolio:
    portfolios = DatabaseManager().read("portfolios")
    record = next((r for r in portfolios if r["user_id"] == user.user_id), None)
    if record is None:
        return Portfolio(user.user_id, user=user)
    return Portfolio.from_dict(record, user=user)


def _save_portfolio(portfolio: Portfolio) -> None:
    """Заменить (или добавить) запись портфеля: чтение → модификация → запись."""
    new_record = portfolio.to_dict()
    with DatabaseManager().transaction("portfolios") as portfolios:
        for index, record in enumerate(portfolios):
            if record["user_id"] == portfolio.user_id:
                portfolios[index] = new_record
                break
        else:
            portfolios.append(new_record)


# --- пользователи -------------------------------------------------------------


@log_action("REGISTER")
def register(username: str, password: str) -> User:
    """Зарегистрировать пользователя и создать ему пустой портфель.

    Raises:
        UserAlreadyExistsError: имя занято.
        ValueError: имя пустое или пароль короче 4 символов.
    """
    name = username.strip() if isinstance(username, str) else username
    with DatabaseManager().transaction("users") as users:
        if name and _find_user_record(users, name) is not None:
            raise UserAlreadyExistsError(name)
        next_id = max((record["user_id"] for record in users), default=0) + 1
        user = User.create(next_id, name, password)
        users.append(user.to_dict())
    _save_portfolio(Portfolio(user.user_id, user=user))
    return user


@log_action("LOGIN")
def login(username: str, password: str) -> User:
    """Проверить имя и пароль и вернуть пользователя.

    Raises:
        UserNotFoundError: пользователь не найден.
        InvalidPasswordError: пароль неверный.
    """
    record = _find_user_record(DatabaseManager().read("users"), username)
    if record is None:
        raise UserNotFoundError(username)
    user = User.from_dict(record)
    if not user.verify_password(password):
        raise InvalidPasswordError()
    return user


def _require_login(user_id: int | None) -> User:
    if user_id is None:
        raise AuthRequiredError()
    return _get_user(user_id)


# --- валюты и курсы -----------------------------------------------------------


def supported_currency_codes() -> list[str]:
    """Коды поддерживаемых валют (для подсказок в CLI)."""
    return get_supported_codes()


def supported_currencies() -> list[Currency]:
    """Все поддерживаемые валюты."""
    return list_currencies()


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _to_iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _pair_entry(pairs: dict[str, Any], from_code: str, to_code: str) -> tuple[float, datetime]:
    """Курс пары из кеша: прямая или обратная запись. KeyError, если нет."""
    direct = pairs.get(make_pair_key(from_code, to_code))
    if direct and direct.get("rate"):
        return float(direct["rate"]), _parse_timestamp(direct["updated_at"])
    inverse = pairs.get(make_pair_key(to_code, from_code))
    if inverse and inverse.get("rate"):
        return 1.0 / float(inverse["rate"]), _parse_timestamp(inverse["updated_at"])
    raise KeyError(make_pair_key(from_code, to_code))


def _find_cached_rate(
    pairs: dict[str, Any], from_code: str, to_code: str
) -> tuple[float, datetime] | None:
    """Найти курс в кеше: прямая/обратная пара или кросс-курс через USD.

    Для кросс-курса метка времени — самая старая из двух использованных пар.
    """
    try:
        return _pair_entry(pairs, from_code, to_code)
    except KeyError:
        pass
    if BASE_CURRENCY in (from_code, to_code):
        return None
    try:
        to_base, first_ts = _pair_entry(pairs, from_code, BASE_CURRENCY)
        from_base, second_ts = _pair_entry(pairs, BASE_CURRENCY, to_code)
    except KeyError:
        return None
    return to_base * from_base, min(first_ts, second_ts)


def _is_fresh(updated_at: datetime) -> bool:
    ttl = SettingsLoader().get("rates_ttl_seconds")
    return (_utc_now() - updated_at).total_seconds() <= ttl


def _refresh_rates_cache() -> dict[str, Any]:
    """Обновить rates.json через Parser Service и вернуть новый кеш.

    Raises:
        ApiRequestError: ни один источник курсов не ответил.
    """
    create_updater().run_update()
    return DatabaseManager().read("rates")


def _resolve_rate(from_code: str, to_code: str) -> RateInfo | None:
    """Курс с учётом TTL: свежий из кеша, иначе — после обновления кеша.

    Returns:
        RateInfo или None, если пары нет даже после обновления.

    Raises:
        ApiRequestError: кеш устарел (или пуст), а источник недоступен.
    """
    if from_code == to_code:
        return RateInfo(from_code, to_code, 1.0, _utc_now())

    pairs = DatabaseManager().read("rates").get("pairs", {})
    cached = _find_cached_rate(pairs, from_code, to_code)
    if cached is not None and _is_fresh(cached[1]):
        logger.debug("Курс %s→%s взят из кеша", from_code, to_code)
        return RateInfo(from_code, to_code, *cached)

    logger.debug("Курс %s→%s устарел или отсутствует — обновляем кеш", from_code, to_code)
    found = _find_cached_rate(_refresh_rates_cache()["pairs"], from_code, to_code)
    return RateInfo(from_code, to_code, *found) if found is not None else None


def get_rate(from_code: str, to_code: str) -> RateInfo:
    """Текущий курс from_code→to_code с меткой времени обновления.

    Raises:
        CurrencyNotFoundError: код валюты неизвестен.
        ApiRequestError: кеш устарел, а источник курсов недоступен.
        RateUnavailableError: курса для пары нет.
    """
    source = get_currency(from_code).code
    target = get_currency(to_code).code
    info = _resolve_rate(source, target)
    if info is None:
        raise RateUnavailableError(source, target)
    return info


@log_action("UPDATE_RATES")
def update_rates(source: str | None = None) -> UpdateResult:
    """Немедленно обновить курсы из всех источников или из одного.

    Raises:
        ValueError: неизвестный источник.
        ApiRequestError: ни один источник не ответил.
    """
    return create_updater(source).run_update()


def _is_crypto(code: str) -> bool:
    try:
        return isinstance(get_currency(code), CryptoCurrency)
    except CurrencyNotFoundError:
        return False


def _cache_rows(pairs: dict[str, Any], base: str) -> list[RateRow]:
    """Пары кеша, при необходимости пересчитанные к другой базовой валюте."""
    rows = []
    if base == BASE_CURRENCY:
        for pair, entry in pairs.items():
            rate, updated_at = _pair_entry(pairs, *pair.split(PAIR_SEPARATOR))
            rows.append(RateRow(pair, rate, updated_at, entry.get("source", "")))
        return rows

    try:
        base_to_usd, base_ts = _pair_entry(pairs, base, BASE_CURRENCY)
    except KeyError:
        raise RateNotInCacheError(base) from None
    rows.append(RateRow(make_pair_key(BASE_CURRENCY, base), 1.0 / base_to_usd, base_ts, ""))
    for pair, entry in pairs.items():
        from_code, to_code = pair.split(PAIR_SEPARATOR)
        if to_code != BASE_CURRENCY or from_code == base:
            continue
        rate, updated_at = _pair_entry(pairs, from_code, BASE_CURRENCY)
        rows.append(
            RateRow(
                make_pair_key(from_code, base),
                rate / base_to_usd,
                min(updated_at, base_ts),
                entry.get("source", ""),
            )
        )
    return rows


def show_rates(
    currency: str | None = None, top: int | None = None, base: str | None = None
) -> RatesView:
    """Курсы из локального кеша с фильтрами (без обращения к API).

    Args:
        currency: показать только пары с этой валютой.
        top: показать N самых дорогих криптовалют.
        base: пересчитать курсы к другой базовой валюте.

    Raises:
        RatesCacheEmptyError: кеш пуст.
        RateNotInCacheError: для валюты нет курсов в кеше.
        UnknownBaseCurrencyError: базовая валюта неизвестна.
        ValueError: top не положительный.
    """
    cache = DatabaseManager().read("rates")
    pairs = cache.get("pairs") or {}
    if not pairs:
        raise RatesCacheEmptyError()
    if top is not None and top <= 0:
        raise ValueError("'top' должен быть положительным целым числом")

    base_code = BASE_CURRENCY
    if base is not None:
        try:
            base_code = get_currency(base).code
        except CurrencyNotFoundError as exc:
            raise UnknownBaseCurrencyError(exc.code) from None
    rows = _cache_rows(pairs, base_code)

    if currency is not None:
        try:
            code = validate_currency_code(currency)
        except ValueError:
            raise RateNotInCacheError(str(currency).strip()) from None
        rows = [row for row in rows if code in row.pair.split(PAIR_SEPARATOR)]
        if not rows:
            raise RateNotInCacheError(code)

    if top is not None:
        crypto = [row for row in rows if _is_crypto(row.pair.split(PAIR_SEPARATOR)[0])]
        rows = sorted(crypto, key=lambda row: row.rate, reverse=True)[:top]
    else:
        rows.sort(key=lambda row: row.pair)

    ttl = SettingsLoader().get("rates_ttl_seconds")
    last_refresh = cache.get("last_refresh")
    refreshed_at = _parse_timestamp(last_refresh) if last_refresh else None
    is_stale = refreshed_at is None or not _is_fresh(refreshed_at)
    return RatesView(tuple(rows), refreshed_at, is_stale, ttl)


def _rate_to_base(currency_code: str) -> float:
    """Курс валюты к USD для расчёта стоимости сделки."""
    info = _resolve_rate(currency_code, BASE_CURRENCY)
    if info is None:
        raise RateUnavailableError(
            currency_code,
            BASE_CURRENCY,
            f"Не удалось получить курс для {currency_code}→{BASE_CURRENCY}",
        )
    return info.rate


# --- портфель и сделки --------------------------------------------------------


def show_portfolio(user_id: int | None, base_currency: str | None = None) -> PortfolioReport:
    """Кошельки пользователя и их стоимость в базовой валюте.

    Args:
        user_id: id вошедшего пользователя (None — не выполнен login).
        base_currency: валюта пересчёта; по умолчанию — из настроек.

    Raises:
        AuthRequiredError: не выполнен login.
        UnknownBaseCurrencyError: базовая валюта неизвестна.
        ApiRequestError: курсы устарели, а источник недоступен.
    """
    user = _require_login(user_id)
    requested = base_currency or SettingsLoader().get("default_base_currency")
    try:
        base = get_currency(requested).code
    except CurrencyNotFoundError as exc:
        raise UnknownBaseCurrencyError(exc.code) from None

    portfolio = _load_portfolio(user)
    rates: dict[str, float] = {}
    rows = []
    for code, wallet in portfolio.wallets.items():
        info = _resolve_rate(code, base)
        if info is None:
            raise RateUnavailableError(code, base, f"Не удалось получить курс для {code}→{base}")
        rates[make_pair_key(code, base)] = info.rate
        rows.append(PortfolioRow(code, wallet.balance, wallet.balance * info.rate))

    total = portfolio.get_total_value(base, exchange_rates=rates)
    return PortfolioReport(user.username, base, tuple(rows), total)


@log_action("BUY", verbose=True)
def buy(user_id: int | None, currency_code: str, amount: float) -> TradeResult:
    """Купить amount единиц валюты; стоимость списывается с USD-кошелька.

    Покупка самой USD означает пополнение USD-кошелька.

    Raises:
        AuthRequiredError: не выполнен login.
        ValueError: amount не положительное число.
        CurrencyNotFoundError: неизвестная валюта.
        RateUnavailableError / ApiRequestError: курс получить не удалось.
        InsufficientFundsError: не хватает USD для оплаты.
    """
    user = _require_login(user_id)
    amount = validate_amount(amount)
    code = get_currency(currency_code).code
    rate = _rate_to_base(code)
    cost = amount * rate

    portfolio = _load_portfolio(user)
    changes = []
    wallet = portfolio.get_wallet(code) or portfolio.add_currency(code)
    if code != BASE_CURRENCY:
        usd_wallet = portfolio.get_wallet(BASE_CURRENCY) or portfolio.add_currency(BASE_CURRENCY)
        usd_before = usd_wallet.balance
        usd_wallet.withdraw(cost)
        changes.append(WalletChange(BASE_CURRENCY, usd_before, usd_wallet.balance))

    before = wallet.balance
    wallet.deposit(amount)
    changes.insert(0, WalletChange(code, before, wallet.balance))

    _save_portfolio(portfolio)
    return TradeResult(user.username, code, amount, rate, BASE_CURRENCY, cost, tuple(changes))


@log_action("SELL", verbose=True)
def sell(user_id: int | None, currency_code: str, amount: float) -> TradeResult:
    """Продать amount единиц валюты; выручка начисляется на USD-кошелёк.

    Продажа самой USD означает вывод средств с USD-кошелька.

    Raises:
        AuthRequiredError: не выполнен login.
        ValueError: amount не положительное число.
        CurrencyNotFoundError: неизвестная валюта.
        WalletNotFoundError: кошелька в этой валюте нет.
        InsufficientFundsError: на кошельке недостаточно средств.
        RateUnavailableError / ApiRequestError: курс получить не удалось.
    """
    user = _require_login(user_id)
    amount = validate_amount(amount)
    code = get_currency(currency_code).code

    portfolio = _load_portfolio(user)
    wallet = portfolio.get_wallet(code)
    if wallet is None:
        raise WalletNotFoundError(code)
    before = wallet.balance
    wallet.withdraw(amount)
    rate = _rate_to_base(code)
    revenue = amount * rate
    changes = [WalletChange(code, before, wallet.balance)]

    if code != BASE_CURRENCY:
        usd_wallet = portfolio.get_wallet(BASE_CURRENCY) or portfolio.add_currency(BASE_CURRENCY)
        usd_before = usd_wallet.balance
        usd_wallet.deposit(revenue)
        changes.append(WalletChange(BASE_CURRENCY, usd_before, usd_wallet.balance))

    _save_portfolio(portfolio)
    return TradeResult(user.username, code, amount, rate, BASE_CURRENCY, revenue, tuple(changes))
