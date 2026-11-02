"""Бизнес-логика ValutaTrade Hub: пользователи, портфели, сделки и курсы.

CLI вызывает только функции этого модуля и сам ничего не считает.
Хранилище — JSON-файлы в каталоге ``data/``.
"""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.core.utils import (
    BASE_CURRENCY,
    DEFAULT_EXCHANGE_RATES,
    make_pair_key,
    validate_amount,
    validate_currency_code,
)

# Пути и политика кеша (в части 3 переедут в SettingsLoader).
DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
PORTFOLIOS_FILE = DATA_DIR / "portfolios.json"
RATES_FILE = DATA_DIR / "rates.json"
RATES_TTL_SECONDS = 300
STUB_RATES_SOURCE = "StubRates"


# --- результаты операций ------------------------------------------------------


@dataclass(frozen=True)
class RateInfo:
    """Курс from_code→to_code: 1 from_code = rate to_code."""

    from_code: str
    to_code: str
    rate: float
    updated_at: datetime
    is_stale: bool = False

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
class PortfolioReport:
    """Отчёт show-portfolio."""

    username: str
    base_currency: str
    rows: tuple[PortfolioRow, ...]
    total: float


# --- JSON-хранилище -----------------------------------------------------------


def _load_json(path: Path, default: Any) -> Any:
    """Прочитать JSON-файл; если файла нет или он пуст — вернуть default."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Файл данных повреждён: {path}") from exc


def _save_json(path: Path, data: Any) -> None:
    """Атомарно записать JSON: сначала во временный файл, затем rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _load_users() -> list[dict[str, Any]]:
    return _load_json(USERS_FILE, [])


def _find_user_record(users: list[dict[str, Any]], username: str) -> dict[str, Any] | None:
    return next((record for record in users if record["username"] == username), None)


def _get_user(user_id: int) -> User:
    record = next((r for r in _load_users() if r["user_id"] == user_id), None)
    if record is None:
        raise ValueError(f"Пользователь с id={user_id} не найден")
    return User.from_dict(record)


def _load_portfolio(user: User) -> Portfolio:
    portfolios = _load_json(PORTFOLIOS_FILE, [])
    record = next((r for r in portfolios if r["user_id"] == user.user_id), None)
    if record is None:
        return Portfolio(user.user_id, user=user)
    return Portfolio.from_dict(record, user=user)


def _save_portfolio(portfolio: Portfolio) -> None:
    """Чтение → замена записи портфеля → запись."""
    portfolios = [
        record
        for record in _load_json(PORTFOLIOS_FILE, [])
        if record["user_id"] != portfolio.user_id
    ]
    portfolios.append(portfolio.to_dict())
    portfolios.sort(key=lambda record: record["user_id"])
    _save_json(PORTFOLIOS_FILE, portfolios)


# --- пользователи -------------------------------------------------------------


def register(username: str, password: str) -> User:
    """Зарегистрировать пользователя и создать ему пустой портфель.

    Raises:
        ValueError: имя пустое или занято, пароль короче 4 символов.
    """
    users = _load_users()
    name = username.strip() if isinstance(username, str) else username
    if name and _find_user_record(users, name) is not None:
        raise ValueError(f"Имя пользователя '{name}' уже занято")

    next_id = max((record["user_id"] for record in users), default=0) + 1
    user = User.create(next_id, name, password)

    users.append(user.to_dict())
    _save_json(USERS_FILE, users)
    _save_portfolio(Portfolio(user.user_id, user=user))
    return user


def login(username: str, password: str) -> User:
    """Проверить имя и пароль и вернуть пользователя.

    Raises:
        ValueError: пользователь не найден или пароль неверный.
    """
    record = _find_user_record(_load_users(), username)
    if record is None:
        raise ValueError(f"Пользователь '{username}' не найден")
    user = User.from_dict(record)
    if not user.verify_password(password):
        raise ValueError("Неверный пароль")
    return user


def _require_login(user_id: int | None) -> User:
    if user_id is None:
        raise ValueError("Сначала выполните login")
    return _get_user(user_id)


# --- курсы валют --------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _to_iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _load_rates_cache() -> dict[str, Any]:
    cache = _load_json(RATES_FILE, {})
    cache.setdefault("pairs", {})
    cache.setdefault("last_refresh", None)
    return cache


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
    return (_utc_now() - updated_at).total_seconds() <= RATES_TTL_SECONDS


def _fetch_rates_from_source() -> tuple[dict[str, float], str]:
    """Получить свежие курсы из внешнего источника.

    Пока Parser Service не подключён, возвращает курсы-заглушки.
    """
    return dict(DEFAULT_EXCHANGE_RATES), STUB_RATES_SOURCE


def _refresh_rates_cache() -> dict[str, Any] | None:
    """Обновить rates.json из источника. Вернуть новый кеш или None при сбое."""
    try:
        fresh_rates, source = _fetch_rates_from_source()
    except Exception:  # любой сбой источника = «курс недоступен»
        return None

    cache = _load_rates_cache()
    now = _to_iso(_utc_now())
    for pair, rate in fresh_rates.items():
        cache["pairs"][pair] = {"rate": rate, "updated_at": now, "source": source}
    cache["source"] = source
    cache["last_refresh"] = now
    _save_json(RATES_FILE, cache)
    return cache


def _resolve_rate(from_code: str, to_code: str) -> RateInfo | None:
    """Курс с учётом TTL: свежий из кеша → обновление кеша → устаревший из кеша."""
    if from_code == to_code:
        return RateInfo(from_code, to_code, 1.0, _utc_now())

    cached = _find_cached_rate(_load_rates_cache()["pairs"], from_code, to_code)
    if cached is not None and _is_fresh(cached[1]):
        return RateInfo(from_code, to_code, *cached)

    refreshed = _refresh_rates_cache()
    if refreshed is not None:
        found = _find_cached_rate(refreshed["pairs"], from_code, to_code)
        if found is not None:
            return RateInfo(from_code, to_code, *found)

    if cached is not None:
        return RateInfo(from_code, to_code, *cached, is_stale=True)
    return None


def get_rate(from_code: str, to_code: str) -> RateInfo:
    """Текущий курс from_code→to_code с меткой времени обновления.

    Raises:
        ValueError: некорректный код валюты или курс недоступен.
    """
    source = validate_currency_code(from_code)
    target = validate_currency_code(to_code)
    info = _resolve_rate(source, target)
    if info is None:
        raise ValueError(f"Курс {source}→{target} недоступен. Повторите попытку позже.")
    return info


def _rate_to_base(currency_code: str) -> float:
    """Курс валюты к USD для расчёта стоимости сделки."""
    info = _resolve_rate(currency_code, BASE_CURRENCY)
    if info is None:
        raise ValueError(f"Не удалось получить курс для {currency_code}→{BASE_CURRENCY}")
    return info.rate


# --- портфель и сделки --------------------------------------------------------


def show_portfolio(user_id: int | None, base_currency: str = BASE_CURRENCY) -> PortfolioReport:
    """Кошельки пользователя и их стоимость в базовой валюте.

    Raises:
        ValueError: не выполнен login или базовая валюта неизвестна.
    """
    user = _require_login(user_id)
    try:
        base = validate_currency_code(base_currency)
    except ValueError:
        raise ValueError(f"Неизвестная базовая валюта '{base_currency}'") from None
    if _resolve_rate(base, BASE_CURRENCY) is None:
        raise ValueError(f"Неизвестная базовая валюта '{base}'")

    portfolio = _load_portfolio(user)
    rates: dict[str, float] = {}
    rows = []
    for code, wallet in portfolio.wallets.items():
        info = _resolve_rate(code, base)
        if info is None:
            raise ValueError(f"Не удалось получить курс для {code}→{base}")
        rates[make_pair_key(code, base)] = info.rate
        rows.append(PortfolioRow(code, wallet.balance, wallet.balance * info.rate))

    total = portfolio.get_total_value(base, exchange_rates=rates)
    return PortfolioReport(user.username, base, tuple(rows), total)


def buy(user_id: int | None, currency_code: str, amount: float) -> TradeResult:
    """Купить amount единиц валюты; стоимость списывается с USD-кошелька.

    Покупка самой USD означает пополнение USD-кошелька.

    Raises:
        ValueError: не выполнен login, некорректный ввод, нет курса,
            недостаточно USD.
    """
    user = _require_login(user_id)
    amount = validate_amount(amount)
    code = validate_currency_code(currency_code)
    rate = _rate_to_base(code)
    cost = amount * rate

    portfolio = _load_portfolio(user)
    changes = []
    if code != BASE_CURRENCY:
        usd_wallet = portfolio.get_wallet(BASE_CURRENCY) or portfolio.add_currency(BASE_CURRENCY)
        usd_before = usd_wallet.balance
        usd_wallet.withdraw(cost)
        changes.append(WalletChange(BASE_CURRENCY, usd_before, usd_wallet.balance))

    wallet = portfolio.get_wallet(code) or portfolio.add_currency(code)
    before = wallet.balance
    wallet.deposit(amount)
    changes.insert(0, WalletChange(code, before, wallet.balance))

    _save_portfolio(portfolio)
    return TradeResult(code, amount, rate, BASE_CURRENCY, cost, tuple(changes))


def sell(user_id: int | None, currency_code: str, amount: float) -> TradeResult:
    """Продать amount единиц валюты; выручка начисляется на USD-кошелёк.

    Продажа самой USD означает вывод средств с USD-кошелька.

    Raises:
        ValueError: не выполнен login, некорректный ввод, нет кошелька,
            недостаточно средств, нет курса.
    """
    user = _require_login(user_id)
    amount = validate_amount(amount)
    code = validate_currency_code(currency_code)

    portfolio = _load_portfolio(user)
    wallet = portfolio.get_wallet(code)
    if wallet is None:
        raise ValueError(
            f"У вас нет кошелька '{code}'. Добавьте валюту: "
            "она создаётся автоматически при первой покупке."
        )
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
    return TradeResult(code, amount, rate, BASE_CURRENCY, revenue, tuple(changes))
