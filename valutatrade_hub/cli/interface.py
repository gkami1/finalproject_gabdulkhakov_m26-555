"""Командный интерфейс ValutaTrade Hub.

Интерактивная оболочка: разбирает строку команды, вызывает функции
из ``core.usecases`` и печатает результат. Бизнес-логики здесь нет —
только разбор аргументов, форматирование и перевод доменных исключений
в понятные сообщения.
"""

import argparse
import logging
import math
import shlex
import sys
from collections.abc import Callable

from prettytable import PrettyTable

from valutatrade_hub.core import usecases
from valutatrade_hub.core.currencies import CryptoCurrency, get_currency
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    ValutaTradeError,
)
from valutatrade_hub.core.models import User
from valutatrade_hub.core.utils import BASE_CURRENCY, format_rate
from valutatrade_hub.logging_config import setup_logging

PROMPT = "> "
EXIT_COMMANDS = frozenset({"exit", "quit"})
SEPARATOR = "-" * 33
QUOTES = "\"'"

DEFAULT_DECIMALS = 2
AMOUNT_ERROR = "'amount' должен быть положительным числом"
API_ERROR_HINT = "Повторите попытку позже или проверьте подключение к сети."
TOP_UP_HINT = "Пополните USD: buy --currency USD --amount <сумма>"

COMMANDS_HELP = (
    ("register --username <имя> --password <пароль>", "регистрация"),
    ("login --username <имя> --password <пароль>", "вход в систему"),
    ("logout", "выход из учётной записи"),
    ("show-portfolio [--base <код>]", "портфель и его стоимость"),
    ("buy --currency <код> --amount <кол-во>", "купить валюту (оплата из USD)"),
    ("sell --currency <код> --amount <кол-во>", "продать валюту (выручка в USD)"),
    ("get-rate --from <код> --to <код>", "текущий курс валют"),
    ("currencies", "список поддерживаемых валют"),
    ("help [команда]", "справка"),
    ("exit", "выход из программы"),
)

logger = logging.getLogger("valutatrade.cli")


class CliUsageError(Exception):
    """Ошибка разбора команды или её аргументов."""


class _ArgumentParser(argparse.ArgumentParser):
    """argparse, который не завершает процесс при ошибке ввода."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise CliUsageError(message)


class CliSession:
    """Состояние интерактивной сессии: текущий пользователь."""

    def __init__(self) -> None:
        self.current_user: User | None = None

    @property
    def user_id(self) -> int | None:
        """id вошедшего пользователя или None."""
        return self.current_user.user_id if self.current_user else None


# --- форматирование -----------------------------------------------------------


def _decimals(currency_code: str) -> int:
    try:
        return get_currency(currency_code).display_decimals
    except CurrencyNotFoundError:
        return DEFAULT_DECIMALS


def format_amount(value: float, currency_code: str) -> str:
    """Количество валюты: 4 знака для крипты, 2 — для фиата."""
    return f"{value:.{_decimals(currency_code)}f}"


def format_money(value: float) -> str:
    """Денежная сумма с разделителем тысяч: 2,965.00."""
    return f"{value:,.2f}"


def _parse_amount(raw: str) -> float:
    """Превратить строку в число; нечисловой ввод — понятная ошибка."""
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        raise ValueError(AMOUNT_ERROR) from None
    if not math.isfinite(value):
        raise ValueError(AMOUNT_ERROR)
    return value


# --- обработчики команд -------------------------------------------------------


def _cmd_register(args: argparse.Namespace, session: CliSession) -> None:
    user = usecases.register(args.username, args.password)
    print(
        f"Пользователь '{user.username}' зарегистрирован (id={user.user_id}). "
        f"Войдите: login --username {user.username} --password ****"
    )


def _cmd_login(args: argparse.Namespace, session: CliSession) -> None:
    session.current_user = usecases.login(args.username, args.password)
    print(f"Вы вошли как '{session.current_user.username}'")


def _cmd_logout(args: argparse.Namespace, session: CliSession) -> None:
    if session.current_user is None:
        print("Вы не вошли в систему")
        return
    print(f"Пользователь '{session.current_user.username}' вышел из системы")
    session.current_user = None


def _cmd_show_portfolio(args: argparse.Namespace, session: CliSession) -> None:
    report = usecases.show_portfolio(session.user_id, args.base)
    if not report.rows:
        print(
            f"Портфель пользователя '{report.username}' пуст. "
            "Пополните USD: buy --currency USD --amount 1000"
        )
        return
    print(f"Портфель пользователя '{report.username}' (база: {report.base_currency}):")
    for row in report.rows:
        balance = format_amount(row.balance, row.currency_code)
        value = f"{row.value_in_base:.2f}"
        print(f"- {row.currency_code}: {balance}  → {value} {report.base_currency}")
    print(SEPARATOR)
    print(f"ИТОГО: {format_money(report.total)} {report.base_currency}")


def _print_trade(result: usecases.TradeResult, action: str, total_label: str) -> None:
    code, base = result.currency_code, result.base_currency
    amount = format_amount(result.amount, code)
    if code == base:
        print(f"{action}: {amount} {code}")
    else:
        print(f"{action}: {amount} {code} по курсу {format_rate(result.rate)} {base}/{code}")
    print("Изменения в портфеле:")
    for change in result.changes:
        before = format_amount(change.before, change.currency_code)
        after = format_amount(change.after, change.currency_code)
        print(f"- {change.currency_code}: было {before} → стало {after}")
    if code != base:
        print(f"{total_label}: {format_money(result.base_amount)} {base}")


def _cmd_buy(args: argparse.Namespace, session: CliSession) -> None:
    result = usecases.buy(session.user_id, args.currency, _parse_amount(args.amount))
    is_top_up = result.currency_code == result.base_currency
    action = "Пополнение выполнено" if is_top_up else "Покупка выполнена"
    _print_trade(result, action, "Оценочная стоимость покупки")


def _cmd_sell(args: argparse.Namespace, session: CliSession) -> None:
    result = usecases.sell(session.user_id, args.currency, _parse_amount(args.amount))
    is_withdrawal = result.currency_code == result.base_currency
    action = "Вывод выполнен" if is_withdrawal else "Продажа выполнена"
    _print_trade(result, action, "Оценочная выручка")


def _cmd_get_rate(args: argparse.Namespace, session: CliSession) -> None:
    info = usecases.get_rate(args.from_code, args.to_code)
    updated = info.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    print(f"Курс {info.from_code}→{info.to_code}: {format_rate(info.rate)} (обновлено: {updated})")
    print(f"Обратный курс {info.to_code}→{info.from_code}: {format_rate(info.inverse_rate)}")


def _cmd_currencies(args: argparse.Namespace, session: CliSession) -> None:
    table = PrettyTable(["Код", "Тип", "Описание"])
    table.align["Описание"] = "l"
    for currency in usecases.supported_currencies():
        kind = "crypto" if isinstance(currency, CryptoCurrency) else "fiat"
        table.add_row([currency.code, kind, currency.get_display_info()])
    print(table)


Handler = Callable[[argparse.Namespace, CliSession], None]


# --- парсер -------------------------------------------------------------------


def build_parser() -> tuple[_ArgumentParser, dict[str, _ArgumentParser]]:
    """Собрать argparse-парсер команд; вернуть его и парсеры подкоманд."""
    parser = _ArgumentParser(prog="", add_help=False)
    subparsers = parser.add_subparsers(dest="command", parser_class=_ArgumentParser)
    commands: dict[str, _ArgumentParser] = {}

    def add(name: str, handler: Handler, description: str) -> _ArgumentParser:
        sub = subparsers.add_parser(name, prog=name, description=description)
        sub.set_defaults(handler=handler)
        commands[name] = sub
        return sub

    register = add("register", _cmd_register, "Зарегистрировать нового пользователя.")
    register.add_argument("--username", required=True, help="имя пользователя (уникальное)")
    register.add_argument("--password", required=True, help="пароль, не короче 4 символов")

    login = add("login", _cmd_login, "Войти в систему.")
    login.add_argument("--username", required=True, help="имя пользователя")
    login.add_argument("--password", required=True, help="пароль")

    add("logout", _cmd_logout, "Выйти из учётной записи.")

    portfolio = add("show-portfolio", _cmd_show_portfolio, "Показать портфель.")
    portfolio.add_argument("--base", default=None, help="базовая валюта (по умолчанию USD)")

    for name, handler, description in (
        ("buy", _cmd_buy, "Купить валюту. Стоимость списывается с USD-кошелька."),
        ("sell", _cmd_sell, "Продать валюту. Выручка зачисляется на USD-кошелёк."),
    ):
        trade = add(name, handler, description)
        trade.add_argument("--currency", required=True, help="код валюты, например BTC")
        trade.add_argument("--amount", required=True, help="количество валюты (> 0)")

    rate = add("get-rate", _cmd_get_rate, "Показать курс одной валюты к другой.")
    rate.add_argument("--from", dest="from_code", required=True, help="исходная валюта")
    rate.add_argument("--to", dest="to_code", required=True, help="целевая валюта")

    add("currencies", _cmd_currencies, "Показать поддерживаемые валюты.")

    return parser, commands


def split_command(line: str) -> list[str]:
    """Разбить строку на токены (без POSIX-экранирования — удобно на Windows)."""
    tokens = shlex.split(line, posix=False)
    return [
        token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in QUOTES else token
        for token in tokens
    ]


def print_help(commands: dict[str, _ArgumentParser], topic: str | None = None) -> None:
    """Общая справка или справка по конкретной команде."""
    if topic is not None:
        if topic not in commands:
            print(f"Неизвестная команда '{topic}'")
            return
        commands[topic].print_help()
        return
    print("Доступные команды:")
    width = max(len(usage) for usage, _ in COMMANDS_HELP)
    for usage, description in COMMANDS_HELP:
        print(f"  {usage:<{width}}  {description}")


# --- цикл оболочки ------------------------------------------------------------


def _configure_utf8() -> None:
    """Принудительно UTF-8 для консоли (иначе кириллица в Git Bash ломается)."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _run_command(
    name: str, tokens: list[str], session: CliSession, parser: _ArgumentParser
) -> None:
    """Разобрать аргументы, выполнить команду и показать ошибки по их типу."""
    try:
        args = parser.parse_args(tokens)
        args.handler(args, session)
    except CliUsageError as exc:
        print(f"Ошибка в аргументах: {exc}. Справка: help {name}")
    except SystemExit:
        pass  # argparse завершает работу после вывода --help
    except InsufficientFundsError as exc:
        print(exc)
        if name == "buy" and exc.code == BASE_CURRENCY:
            print(TOP_UP_HINT)
    except CurrencyNotFoundError as exc:
        print(exc)
        codes = ", ".join(usecases.supported_currency_codes())
        print(f"Поддерживаемые валюты: {codes}. Подробнее: currencies, help get-rate")
    except ApiRequestError as exc:
        print(exc)
        print(API_ERROR_HINT)
    except (ValutaTradeError, ValueError) as exc:
        print(exc)
    except Exception as exc:
        logger.exception("Непредвиденная ошибка в команде %s", name)
        print(f"Непредвиденная ошибка: {exc}. Подробности в logs/actions.log")


def execute(
    line: str,
    session: CliSession,
    parser: _ArgumentParser,
    commands: dict[str, _ArgumentParser],
) -> bool:
    """Выполнить одну строку. Вернуть False, если пора завершить работу."""
    try:
        tokens = split_command(line)
    except ValueError:
        print("Ошибка ввода: незакрытая кавычка")
        return True
    if not tokens:
        return True

    name = tokens[0]
    if name in EXIT_COMMANDS:
        return False
    if name == "help":
        print_help(commands, tokens[1] if len(tokens) > 1 else None)
    elif name in commands:
        _run_command(name, tokens, session, parser)
    else:
        print(f"Неизвестная команда '{name}'. Введите help для списка команд.")
    return True


def main() -> None:
    """Точка входа: интерактивная оболочка ValutaTrade Hub."""
    _configure_utf8()
    setup_logging()
    parser, commands = build_parser()
    session = CliSession()
    print("ValutaTrade Hub. Введите help для списка команд, exit — для выхода.")
    while True:
        try:
            line = input(PROMPT)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not execute(line, session, parser, commands):
            break
    print("До свидания!")
